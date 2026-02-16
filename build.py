#!/usr/bin/env python3
"""Minimal Android APK build system.

Single-file build tool that produces debug APKs from a YAML config.
Handles SDK setup, resource compilation, Java/Kotlin compilation, DEX conversion,
packaging, alignment, and signing.

Usage:
    ./build.py setup   - Download and install Android SDK components
    ./build.py build   - Build the APK
    ./build.py clean   - Remove build artifacts
"""

import argparse
import glob
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

# ---------------------------------------------------------------------------
# Minimal YAML parser (fallback when PyYAML is not installed)
# ---------------------------------------------------------------------------

def mini_yaml_load(text):
    """Parse a simple subset of YAML: nested mappings, scalars, and lists.

    Supports:
      - Nested mappings (indentation-based)
      - Scalar values (strings, ints, bools)
      - Sequence items (  - value)
      - Quoted strings
      - Empty lists written as []
      - Comments (lines starting with #)

    Does NOT support: anchors, multi-line strings, flow mappings, etc.
    """
    root = {}
    stack = [(-1, root)]  # (indent_level, current_dict)

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1

        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        # Pop stack to find parent at correct indent level
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()

        parent = stack[-1][1]

        # List item
        if stripped.startswith("- "):
            value = _parse_scalar(stripped[2:].strip())
            if isinstance(parent, list):
                parent.append(value)
            continue

        # Key-value pair
        if ":" in stripped:
            colon_pos = stripped.index(":")
            key = stripped[:colon_pos].strip()
            rest = stripped[colon_pos + 1:].strip()

            if rest == "" or rest == "":
                # Nested mapping — will be filled by subsequent lines
                child = {}
                parent[key] = child
                stack.append((indent, child))
            elif rest == "[]":
                parent[key] = []
            else:
                # Check if next lines are list items at deeper indent
                peek = i
                while peek < len(lines) and not lines[peek].strip():
                    peek += 1
                if peek < len(lines) and lines[peek].strip().startswith("- "):
                    peek_indent = len(lines[peek]) - len(lines[peek].lstrip())
                    if peek_indent > indent:
                        lst = []
                        parent[key] = lst
                        stack.append((indent, lst))
                        continue

                parent[key] = _parse_scalar(rest)

    return root


def _parse_scalar(value):
    """Convert a YAML scalar string to a Python type."""
    if not value:
        return ""

    # Strip quotes
    if (value.startswith('"') and value.endswith('"')) or \
       (value.startswith("'") and value.endswith("'")):
        return value[1:-1]

    # Strip inline comments
    for sep in ("  #", "\t#"):
        if sep in value:
            value = value[:value.index(sep)].rstrip()

    low = value.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low == "null" or low == "~":
        return None

    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass

    return value


def load_yaml(path):
    """Load a YAML file, preferring PyYAML if available."""
    with open(path, "r") as f:
        text = f.read()
    try:
        import yaml
        return yaml.safe_load(text)
    except ImportError:
        return mini_yaml_load(text)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class Config:
    """Loads and validates build.yaml, resolves paths, applies defaults."""

    def __init__(self, config_path="build.yaml"):
        self.project_root = os.path.dirname(os.path.abspath(config_path)) or os.getcwd()
        raw = load_yaml(config_path)

        app = raw.get("app", {})
        self.package = app.get("package", "com.example.app")
        self.app_name = app.get("name", "App")
        self.version_code = int(app.get("version_code", 1))
        self.version_name = str(app.get("version_name", "1.0"))

        sdk = raw.get("sdk", {})
        self.min_sdk = int(sdk.get("min_sdk", 21))
        self.target_sdk = int(sdk.get("target_sdk", 34))
        self.build_tools_version = str(sdk.get("build_tools", "34.0.0"))

        paths = raw.get("paths", {})
        self.manifest = self._resolve(paths.get("manifest", "AndroidManifest.xml"))
        self.sources = [self._resolve(s) for s in paths.get("sources", ["src"])]
        self.resources = [self._resolve(r) for r in paths.get("resources", ["res"])]
        self.libs = [self._resolve(l) for l in paths.get("libs", [])]

        output = raw.get("output", {})
        self.build_dir = self._resolve(output.get("dir", ".build"))
        self.apk_name = output.get("apk_name", "app-debug.apk")

        self.sdk_dir = self._resolve(".android-sdk")

        self.repositories = raw.get("repositories", [
            "https://dl.google.com/dl/android/maven2",
            "https://repo1.maven.org/maven2",
        ])
        self.dependencies = raw.get("dependencies", [])

        kotlin = raw.get("kotlin", {})
        self.kotlin_version = str(kotlin["version"]) if kotlin.get("version") else None
        self.kotlin_dir = self._resolve(".kotlin") if self.kotlin_version else None
        self.compose_enabled = bool(kotlin.get("compose", False))

    def _resolve(self, path):
        if os.path.isabs(path):
            return path
        return os.path.join(self.project_root, path)

    @property
    def build_tools_dir(self):
        return os.path.join(self.sdk_dir, "build-tools", self.build_tools_version)

    @property
    def platform_dir(self):
        return os.path.join(self.sdk_dir, "platforms", f"android-{self.target_sdk}")

    @property
    def android_jar(self):
        return os.path.join(self.platform_dir, "android.jar")

    @property
    def kotlin_home(self):
        if not self.kotlin_dir:
            return None
        return os.path.join(self.kotlin_dir, "kotlinc")

    @property
    def kotlinc_bin(self):
        if not self.kotlin_home:
            return None
        return os.path.join(self.kotlin_home, "bin", "kotlinc")

    @property
    def kotlin_stdlib(self):
        if not self.kotlin_home:
            return None
        return os.path.join(self.kotlin_home, "lib", "kotlin-stdlib.jar")

    @property
    def compose_plugin_jar(self):
        if not self.kotlin_dir or not self.compose_enabled:
            return None
        return os.path.join(self.kotlin_dir, "compose-plugin",
            f"kotlin-compose-compiler-plugin-{self.kotlin_version}.jar")

    @property
    def deps_cache_dir(self):
        return os.path.join(self.project_root, ".deps")

    def tool(self, name):
        """Return full path to a build-tools binary."""
        return os.path.join(self.build_tools_dir, name)

    def validate(self):
        errors = []
        if not os.path.isfile(self.manifest):
            errors.append(f"Manifest not found: {self.manifest}")
        for src in self.sources:
            if not os.path.isdir(src):
                errors.append(f"Source directory not found: {src}")
        for res in self.resources:
            if not os.path.isdir(res):
                errors.append(f"Resource directory not found: {res}")
        if errors:
            print("Configuration errors:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            sys.exit(1)


# ---------------------------------------------------------------------------
# SDK Manager
# ---------------------------------------------------------------------------

# Download URLs for Android command-line tools
_CMDLINE_TOOLS_URLS = {
    "Linux": "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip",
    "Darwin": "https://dl.google.com/android/repository/commandlinetools-mac-11076708_latest.zip",
    "Windows": "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip",
}


class SDKManager:
    """Detects, downloads, and installs Android SDK components."""

    def __init__(self, config):
        self.config = config
        self.sdk_dir = config.sdk_dir
        system = platform.system()
        self.cmdline_tools_url = _CMDLINE_TOOLS_URLS.get(system)
        if not self.cmdline_tools_url:
            print(f"Unsupported platform: {system}", file=sys.stderr)
            sys.exit(1)

        self.cmdline_tools_dir = os.path.join(self.sdk_dir, "cmdline-tools", "latest")

        ext = ".bat" if system == "Windows" else ""
        self.sdkmanager_bin = os.path.join(self.cmdline_tools_dir, "bin", f"sdkmanager{ext}")

    def setup(self):
        """Download command-line tools and install required SDK packages."""
        self._download_cmdline_tools()
        self._install_packages()
        print("\nSDK setup complete.")

    def _download_cmdline_tools(self):
        if os.path.isfile(self.sdkmanager_bin):
            print("Command-line tools already present, skipping download.")
            return

        print("Downloading Android command-line tools...")
        os.makedirs(self.sdk_dir, exist_ok=True)

        zip_path = os.path.join(self.sdk_dir, "cmdline-tools.zip")
        _download_with_progress(self.cmdline_tools_url, zip_path)

        print("Extracting command-line tools...")
        extract_dir = os.path.join(self.sdk_dir, "cmdline-tools")
        os.makedirs(extract_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        # The zip extracts to cmdline-tools/cmdline-tools/ — move to latest/
        extracted = os.path.join(extract_dir, "cmdline-tools")
        if os.path.isdir(extracted) and not os.path.isdir(self.cmdline_tools_dir):
            os.rename(extracted, self.cmdline_tools_dir)

        os.remove(zip_path)

        # Make binaries executable on Unix
        if platform.system() != "Windows":
            bin_dir = os.path.join(self.cmdline_tools_dir, "bin")
            if os.path.isdir(bin_dir):
                for f in os.listdir(bin_dir):
                    fp = os.path.join(bin_dir, f)
                    st = os.stat(fp)
                    os.chmod(fp, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        print("Command-line tools installed.")

    def _install_packages(self):
        packages = [
            f"build-tools;{self.config.build_tools_version}",
            f"platforms;android-{self.config.target_sdk}",
        ]

        # Check which packages are already installed
        to_install = []
        for pkg in packages:
            # Infer expected directory from package name
            pkg_path = os.path.join(self.sdk_dir, pkg.replace(";", os.sep))
            if os.path.isdir(pkg_path):
                print(f"Package already installed: {pkg}")
            else:
                to_install.append(pkg)

        if not to_install:
            print("All SDK packages already installed.")
            return

        print(f"Installing SDK packages: {', '.join(to_install)}")
        env = os.environ.copy()
        env["ANDROID_SDK_ROOT"] = self.sdk_dir
        env["ANDROID_HOME"] = self.sdk_dir

        cmd = [self.sdkmanager_bin, f"--sdk_root={self.sdk_dir}"]
        cmd += to_install

        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        # Auto-accept licenses by feeding 'y' repeatedly
        try:
            out, _ = proc.communicate(input=b"y\n" * 20, timeout=600)
            print(out.decode(errors="replace"))
        except subprocess.TimeoutExpired:
            proc.kill()
            print("SDK manager timed out!", file=sys.stderr)
            sys.exit(1)

        if proc.returncode != 0:
            print("SDK manager failed!", file=sys.stderr)
            sys.exit(1)

        print("SDK packages installed successfully.")


# ---------------------------------------------------------------------------
# Kotlin Manager
# ---------------------------------------------------------------------------

class KotlinManager:
    """Downloads and installs the Kotlin compiler."""

    _URL_TEMPLATE = (
        "https://github.com/JetBrains/kotlin/releases/download/"
        "v{version}/kotlin-compiler-{version}.zip"
    )

    def __init__(self, config):
        self.config = config
        self.version = config.kotlin_version
        self.kotlin_dir = config.kotlin_dir
        self.kotlin_home = config.kotlin_home
        self.kotlinc_bin = config.kotlinc_bin

    def setup(self):
        """Download and extract the Kotlin compiler if not already present."""
        if self.kotlinc_bin and os.path.isfile(self.kotlinc_bin):
            print("Kotlin compiler already present, skipping download.")
        else:
            url = self._URL_TEMPLATE.format(version=self.version)
            print(f"Downloading Kotlin compiler {self.version}...")

            os.makedirs(self.kotlin_dir, exist_ok=True)
            zip_path = os.path.join(self.kotlin_dir, "kotlin-compiler.zip")
            _download_with_progress(url, zip_path)

            print("Extracting Kotlin compiler...")
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(self.kotlin_dir)

            os.remove(zip_path)

            # Make binaries executable on Unix
            if platform.system() != "Windows":
                bin_dir = os.path.join(self.kotlin_home, "bin")
                if os.path.isdir(bin_dir):
                    for f in os.listdir(bin_dir):
                        fp = os.path.join(bin_dir, f)
                        st = os.stat(fp)
                        os.chmod(fp, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

            print("Kotlin compiler installed.")

        if self.config.compose_enabled:
            self._setup_compose()

    def _setup_compose(self):
        """Download the Compose compiler plugin JAR from Maven Central."""
        jar_path = self.config.compose_plugin_jar
        if os.path.isfile(jar_path):
            print("Compose compiler plugin already present, skipping download.")
            return

        url = (
            f"https://repo1.maven.org/maven2/org/jetbrains/kotlin/"
            f"kotlin-compose-compiler-plugin/{self.version}/"
            f"kotlin-compose-compiler-plugin-{self.version}.jar"
        )
        print(f"Downloading Compose compiler plugin {self.version}...")

        os.makedirs(os.path.dirname(jar_path), exist_ok=True)
        _download_with_progress(url, jar_path)
        print("Compose compiler plugin installed.")


# ---------------------------------------------------------------------------
# Maven Dependency Resolver
# ---------------------------------------------------------------------------

class MavenResolver:
    """Resolves Maven dependencies (including transitive), downloads JARs/AARs."""

    _NS = "{http://maven.apache.org/POM/4.0.0}"

    def __init__(self, config):
        self.config = config
        self.repos = config.repositories
        self.cache_dir = config.deps_cache_dir
        self.pom_dir = os.path.join(self.cache_dir, "poms")
        self.artifact_dir = os.path.join(self.cache_dir, "artifacts")
        self.extracted_dir = os.path.join(self.cache_dir, "extracted")

        self._resolved = {}       # coord_key -> packaging
        self._resolved_versions = {}  # coord_key -> version (for highest-wins)
        self._version_map = {}    # (group, artifact) -> version from BOM/depMgmt
        self._classpath_jars = []
        self._dex_jars = []
        self._aar_res_dirs = []
        self._aar_packages = []

    # -- public API ----------------------------------------------------------

    def resolve_all(self, coords):
        """Resolve a list of 'group:artifact:version' strings and all transitive deps."""
        print("\nResolving Maven dependencies...")
        for coord in coords:
            self._resolve(coord)
        print(f"  Resolved {len(self._resolved)} dependencies.")

    def get_classpath_jars(self):
        return list(self._classpath_jars)

    def get_dex_jars(self):
        return list(self._dex_jars)

    def get_aar_res_dirs(self):
        return list(self._aar_res_dirs)

    def get_aar_packages(self):
        return list(self._aar_packages)

    # -- resolution ----------------------------------------------------------

    @staticmethod
    def _compare_versions(v1, v2):
        """Compare two version strings. Returns >0 if v1>v2, 0 if equal, <0 if v1<v2."""
        def parts(v):
            return [int(p) if p.isdigit() else 0 for p in v.split(".")]
        p1, p2 = parts(v1), parts(v2)
        max_len = max(len(p1), len(p2))
        p1 += [0] * (max_len - len(p1))
        p2 += [0] * (max_len - len(p2))
        for a, b in zip(p1, p2):
            if a != b:
                return a - b
        return 0

    def _unregister_artifact(self, group, artifact, version, packaging):
        """Remove a previously registered artifact's entries (for version upgrades)."""
        gpath = self._group_path(group)
        if packaging == "aar":
            extract_base = os.path.join(self.extracted_dir, gpath, artifact, version)
            classes_jar = os.path.join(extract_base, "classes.jar")
            if classes_jar in self._classpath_jars:
                self._classpath_jars.remove(classes_jar)
            if classes_jar in self._dex_jars:
                self._dex_jars.remove(classes_jar)
            res_dir = os.path.join(extract_base, "res")
            if res_dir in self._aar_res_dirs:
                self._aar_res_dirs.remove(res_dir)
            manifest = os.path.join(extract_base, "AndroidManifest.xml")
            if os.path.isfile(manifest):
                pkg = self._read_aar_package(manifest)
                if pkg and pkg in self._aar_packages:
                    self._aar_packages.remove(pkg)
        else:
            rel = f"{gpath}/{artifact}/{version}/{artifact}-{version}.jar"
            jar_path = os.path.join(self.artifact_dir, rel)
            if jar_path in self._classpath_jars:
                self._classpath_jars.remove(jar_path)
            if jar_path in self._dex_jars:
                self._dex_jars.remove(jar_path)

    def _resolve(self, coord):
        """Recursively resolve a single coordinate and its transitive deps."""
        group, artifact, version = coord.split(":")
        key = f"{group}:{artifact}"

        if key in self._resolved:
            old_ver = self._resolved_versions.get(key, "0.0.0")
            if self._compare_versions(version, old_ver) <= 0:
                return
            # Higher version requested — unregister old artifact and re-resolve
            self._unregister_artifact(group, artifact, old_ver, self._resolved[key])
            del self._resolved[key]
            del self._resolved_versions[key]

        # Download and parse POM
        pom_path = self._download_pom(group, artifact, version)
        if not pom_path:
            print(f"  WARNING: Could not download POM for {coord}", file=sys.stderr)
            return

        tree = ET.parse(pom_path)
        root = tree.getroot()
        packaging = self._pom_text(root, "packaging") or "jar"

        # Merge parent dependencyManagement
        self._process_parent(root)
        # Merge local dependencyManagement (including BOM imports)
        self._process_dep_management(root)

        self._resolved[key] = packaging
        self._resolved_versions[key] = version

        # Download the actual artifact
        self._download_artifact(group, artifact, version, packaging)

        # Register classpath/resource entries
        self._register_artifact(group, artifact, version, packaging)

        # Resolve transitive dependencies
        deps_el = root.find(f"{self._NS}dependencies")
        if deps_el is not None:
            for dep in deps_el.findall(f"{self._NS}dependency"):
                dep_group = self._pom_text(dep, "groupId")
                dep_artifact = self._pom_text(dep, "artifactId")
                dep_scope = self._pom_text(dep, "scope") or "compile"
                dep_optional = self._pom_text(dep, "optional") or "false"
                dep_type = self._pom_text(dep, "type") or "jar"

                # Skip non-compile scopes and optional deps
                if dep_scope in ("test", "provided", "system"):
                    continue
                if dep_optional.lower() == "true":
                    continue
                # BOM imports are handled in depMgmt, not as real deps
                if dep_type == "pom" and dep_scope == "import":
                    continue

                dep_version = self._pom_text(dep, "version")
                if not dep_version:
                    dep_version = self._version_map.get((dep_group, dep_artifact))
                if not dep_version:
                    print(f"  WARNING: No version for {dep_group}:{dep_artifact}, skipping",
                          file=sys.stderr)
                    continue

                dep_version = self._clean_version(dep_version)
                self._resolve(f"{dep_group}:{dep_artifact}:{dep_version}")

    # -- POM helpers ---------------------------------------------------------

    def _pom_text(self, element, tag):
        """Get text of a direct child element, handling Maven namespace."""
        el = element.find(f"{self._NS}{tag}")
        if el is None:
            # Try without namespace (some POMs omit it)
            el = element.find(tag)
        return el.text.strip() if el is not None and el.text else None

    def _clean_version(self, version):
        """Handle version ranges like [1.7.0] -> 1.7.0."""
        if version and version.startswith("[") and version.endswith("]"):
            version = version[1:-1]
            if "," in version:
                version = version.split(",")[0]
        if version and version.startswith("["):
            version = version[1:]
        if version and version.endswith("]"):
            version = version[:-1]
        return version

    def _process_parent(self, root):
        """Fetch parent POM and merge its dependencyManagement."""
        parent = root.find(f"{self._NS}parent")
        if parent is None:
            return
        p_group = self._pom_text(parent, "groupId")
        p_artifact = self._pom_text(parent, "artifactId")
        p_version = self._pom_text(parent, "version")
        if not all([p_group, p_artifact, p_version]):
            return

        pom_path = self._download_pom(p_group, p_artifact, p_version)
        if not pom_path:
            return

        tree = ET.parse(pom_path)
        parent_root = tree.getroot()
        # Recursively process grandparent
        self._process_parent(parent_root)
        self._process_dep_management(parent_root)

    def _process_dep_management(self, root):
        """Extract version pins from <dependencyManagement>."""
        dm = root.find(f"{self._NS}dependencyManagement")
        if dm is None:
            return
        deps_el = dm.find(f"{self._NS}dependencies")
        if deps_el is None:
            return

        for dep in deps_el.findall(f"{self._NS}dependency"):
            g = self._pom_text(dep, "groupId")
            a = self._pom_text(dep, "artifactId")
            v = self._pom_text(dep, "version")
            scope = self._pom_text(dep, "scope") or ""
            dep_type = self._pom_text(dep, "type") or "jar"

            if not all([g, a, v]):
                continue

            v = self._clean_version(v)

            # BOM import: fetch that POM's dependencyManagement
            if dep_type == "pom" and scope == "import":
                bom_path = self._download_pom(g, a, v)
                if bom_path:
                    bom_tree = ET.parse(bom_path)
                    bom_root = bom_tree.getroot()
                    self._process_parent(bom_root)
                    self._process_dep_management(bom_root)
                continue

            if (g, a) not in self._version_map:
                self._version_map[(g, a)] = v

    # -- downloads -----------------------------------------------------------

    def _group_path(self, group):
        return group.replace(".", "/")

    def _download_pom(self, group, artifact, version):
        """Download a POM file, returning its local path (or None on failure)."""
        gpath = self._group_path(group)
        rel = f"{gpath}/{artifact}/{version}/{artifact}-{version}.pom"
        local_path = os.path.join(self.pom_dir, rel)

        if os.path.isfile(local_path):
            return local_path

        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        for repo in self.repos:
            url = f"{repo.rstrip('/')}/{rel}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                resp = urllib.request.urlopen(req)
                with open(local_path, "wb") as f:
                    f.write(resp.read())
                return local_path
            except urllib.error.HTTPError:
                continue
            except urllib.error.URLError:
                continue

        return None

    def _download_artifact(self, group, artifact, version, packaging):
        """Download a JAR or AAR artifact."""
        ext = packaging if packaging in ("jar", "aar") else "jar"
        gpath = self._group_path(group)
        rel = f"{gpath}/{artifact}/{version}/{artifact}-{version}.{ext}"
        local_path = os.path.join(self.artifact_dir, rel)

        if os.path.isfile(local_path):
            return local_path

        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        for repo in self.repos:
            url = f"{repo.rstrip('/')}/{rel}"
            try:
                print(f"  Downloading {group}:{artifact}:{version} ({ext})...")
                _download_with_progress(url, local_path)
                return local_path
            except urllib.error.HTTPError:
                if os.path.isfile(local_path):
                    os.remove(local_path)
                continue
            except urllib.error.URLError:
                if os.path.isfile(local_path):
                    os.remove(local_path)
                continue
            except Exception:
                if os.path.isfile(local_path):
                    os.remove(local_path)
                continue

        print(f"  WARNING: Could not download {group}:{artifact}:{version}.{ext}",
              file=sys.stderr)
        return None

    # -- artifact registration -----------------------------------------------

    def _register_artifact(self, group, artifact, version, packaging):
        """Register a downloaded artifact for classpath/dex/resource use."""
        gpath = self._group_path(group)

        if packaging == "aar":
            self._extract_aar(group, artifact, version)
            extract_base = os.path.join(
                self.extracted_dir, gpath, artifact, version)
            classes_jar = os.path.join(extract_base, "classes.jar")
            if os.path.isfile(classes_jar):
                self._classpath_jars.append(classes_jar)
                self._dex_jars.append(classes_jar)
            res_dir = os.path.join(extract_base, "res")
            if os.path.isdir(res_dir) and os.listdir(res_dir):
                self._aar_res_dirs.append(res_dir)
            manifest = os.path.join(extract_base, "AndroidManifest.xml")
            if os.path.isfile(manifest):
                pkg = self._read_aar_package(manifest)
                if pkg:
                    self._aar_packages.append(pkg)
        else:
            rel = f"{gpath}/{artifact}/{version}/{artifact}-{version}.jar"
            jar_path = os.path.join(self.artifact_dir, rel)
            if os.path.isfile(jar_path):
                self._classpath_jars.append(jar_path)
                self._dex_jars.append(jar_path)

    def _extract_aar(self, group, artifact, version):
        """Extract classes.jar, res/, and AndroidManifest.xml from an AAR."""
        gpath = self._group_path(group)
        aar_path = os.path.join(
            self.artifact_dir, gpath, artifact, version,
            f"{artifact}-{version}.aar")
        extract_base = os.path.join(
            self.extracted_dir, gpath, artifact, version)

        if os.path.isdir(extract_base) and os.path.isfile(
                os.path.join(extract_base, "classes.jar")):
            return  # Already extracted

        os.makedirs(extract_base, exist_ok=True)

        if not os.path.isfile(aar_path):
            return

        with zipfile.ZipFile(aar_path, "r") as zf:
            for entry in zf.namelist():
                if entry == "classes.jar":
                    zf.extract(entry, extract_base)
                elif entry == "AndroidManifest.xml":
                    zf.extract(entry, extract_base)
                elif entry.startswith("res/"):
                    zf.extract(entry, extract_base)

    def _read_aar_package(self, manifest_path):
        """Read the package name from an AAR's AndroidManifest.xml."""
        try:
            tree = ET.parse(manifest_path)
            root = tree.getroot()
            return root.get("package")
        except ET.ParseError:
            return None


def _deduplicate_jars(jar_paths):
    """Remove JARs whose .class entries are all contained in other JARs.

    AndroidX merged many -ktx artifacts into their main modules (e.g.
    collection-ktx was merged into collection-jvm at version 1.4.0).
    When both old and new artifacts are pulled in as transitive deps,
    d8 fails on duplicate class definitions.  This function detects
    JARs that are strict subsets of other JARs and removes them.
    """
    # Build a map: jar_path -> set of .class entry names
    jar_classes = {}
    for jar in jar_paths:
        try:
            with zipfile.ZipFile(jar, "r") as zf:
                classes = {n for n in zf.namelist() if n.endswith(".class")}
            jar_classes[jar] = classes
        except (zipfile.BadZipFile, OSError):
            jar_classes[jar] = set()

    # Find JARs whose classes are a strict subset of another JAR's classes
    to_remove = set()
    jars = list(jar_classes.keys())
    for i, jar_a in enumerate(jars):
        if not jar_classes[jar_a]:
            continue
        for j, jar_b in enumerate(jars):
            if i == j or jar_b in to_remove:
                continue
            if not jar_classes[jar_b]:
                continue
            # If all of A's classes exist in B, and B has more, A is redundant
            if jar_classes[jar_a] <= jar_classes[jar_b] and len(jar_classes[jar_a]) < len(jar_classes[jar_b]):
                to_remove.add(jar_a)
                break

    if to_remove:
        for jar in sorted(to_remove):
            print(f"  Excluding redundant JAR: {os.path.basename(jar)}")

    return [j for j in jar_paths if j not in to_remove]


def _download_with_progress(url, dest):
    """Download a URL to a local file with a simple progress indicator."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req)
    total = int(resp.headers.get("Content-Length", 0))
    downloaded = 0
    chunk_size = 1024 * 256

    with open(dest, "wb") as f:
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if total > 0:
                pct = downloaded * 100 // total
                mb = downloaded / (1024 * 1024)
                total_mb = total / (1024 * 1024)
                print(f"\r  {mb:.1f} / {total_mb:.1f} MB ({pct}%)", end="", flush=True)
    print()


# ---------------------------------------------------------------------------
# Debug Keystore
# ---------------------------------------------------------------------------

class DebugKeystore:
    """Auto-generates a debug.keystore via keytool on first build."""

    STORE_PASS = "android"
    KEY_ALIAS = "androiddebugkey"
    KEY_PASS = "android"

    def __init__(self, config):
        self.keystore_path = os.path.join(config.project_root, "debug.keystore")

    def ensure(self):
        """Create debug keystore if it doesn't exist."""
        if os.path.isfile(self.keystore_path):
            return self.keystore_path

        print("Generating debug keystore...")
        cmd = [
            "keytool", "-genkeypair",
            "-keystore", self.keystore_path,
            "-storepass", self.STORE_PASS,
            "-alias", self.KEY_ALIAS,
            "-keypass", self.KEY_PASS,
            "-keyalg", "RSA",
            "-keysize", "2048",
            "-validity", "10000",
            "-dname", "CN=Android Debug,O=Android,C=US",
        ]
        _run(cmd, "keytool")
        print(f"Debug keystore created: {self.keystore_path}")
        return self.keystore_path


# ---------------------------------------------------------------------------
# Builder — 7-step APK build pipeline
# ---------------------------------------------------------------------------

class Builder:
    """Executes the 7-step APK build pipeline."""

    def __init__(self, config):
        self.cfg = config
        self.keystore = DebugKeystore(config)
        self.resolver = None

        # Intermediate directories
        self.compiled_res_dir = os.path.join(config.build_dir, "compiled_res")
        self.aar_compiled_res_dir = os.path.join(config.build_dir, "aar_compiled_res")
        self.gen_dir = os.path.join(config.build_dir, "gen")
        self.classes_dir = os.path.join(config.build_dir, "classes")
        self.dex_dir = os.path.join(config.build_dir, "dex")

        # Output files
        self.base_apk = os.path.join(config.build_dir, "base.apk")
        self.dex_apk = os.path.join(config.build_dir, "dex.apk")
        self.aligned_apk = os.path.join(config.build_dir, "aligned.apk")
        self.final_apk = os.path.join(config.build_dir, config.apk_name)

    def build(self):
        self.cfg.validate()
        self._prepare_dirs()

        # Step 0: Resolve Maven dependencies
        if self.cfg.dependencies:
            self.resolver = MavenResolver(self.cfg)
            self.resolver.resolve_all(self.cfg.dependencies)

        self._step1_compile_resources()
        self._step2_link_resources()
        self._step3_compile_sources()
        self._step4_dex()
        self._step5_inject_dex()
        self._step6_zipalign()
        self._step7_sign()

        print(f"\nBuild successful: {self.final_apk}")

    def _prepare_dirs(self):
        for d in (self.compiled_res_dir, self.aar_compiled_res_dir):
            if os.path.isdir(d):
                shutil.rmtree(d)
        for d in (self.compiled_res_dir, self.aar_compiled_res_dir,
                  self.gen_dir, self.classes_dir, self.dex_dir):
            os.makedirs(d, exist_ok=True)

    # Step 1: aapt2 compile
    def _step1_compile_resources(self):
        print("\n[1/7] Compiling resources...")
        aapt2 = self.cfg.tool("aapt2")

        for res_dir in self.cfg.resources:
            # Collect all resource files
            for root, _dirs, files in os.walk(res_dir):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    _run(
                        [aapt2, "compile", "-o", self.compiled_res_dir, fpath],
                        "aapt2 compile",
                    )

        # Compile AAR resources: one zip archive per library to avoid filename collisions
        if self.resolver:
            for idx, res_dir in enumerate(self.resolver.get_aar_res_dirs()):
                zip_out = os.path.join(self.aar_compiled_res_dir, f"aar_{idx}.zip")
                _run(
                    [aapt2, "compile", "--dir", res_dir, "-o", zip_out],
                    "aapt2 compile (AAR)",
                )

    # Step 2: aapt2 link
    def _step2_link_resources(self):
        print("[2/7] Linking resources...")
        aapt2 = self.cfg.tool("aapt2")

        # Collect all .flat files
        flat_files = glob.glob(os.path.join(self.compiled_res_dir, "*.flat"))

        cmd = [
            aapt2, "link",
            "-I", self.cfg.android_jar,
            "--manifest", self.cfg.manifest,
            "--java", self.gen_dir,
            "--min-sdk-version", str(self.cfg.min_sdk),
            "--target-sdk-version", str(self.cfg.target_sdk),
            "--version-code", str(self.cfg.version_code),
            "--version-name", self.cfg.version_name,
            "-o", self.base_apk,
        ]

        # Include AAR compiled resources as overlays to resolve cross-library conflicts
        if self.resolver:
            aar_zips = glob.glob(os.path.join(self.aar_compiled_res_dir, "*.zip"))
            if aar_zips:
                cmd.append("--auto-add-overlay")
            for pkg in self.resolver.get_aar_packages():
                cmd += ["--extra-packages", pkg]

        cmd += flat_files
        if self.resolver and aar_zips:
            for z in aar_zips:
                cmd += ["-R", z]
        _run(cmd, "aapt2 link")

    # Step 3: compile sources (Java and/or Kotlin)
    def _step3_compile_sources(self):
        # Collect .java and .kt files from source dirs
        user_java_files = []
        kt_files = []
        for src_dir in self.cfg.sources:
            for root, _dirs, files in os.walk(src_dir):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    if fname.endswith(".java"):
                        user_java_files.append(fpath)
                    elif fname.endswith(".kt"):
                        kt_files.append(fpath)

        # Collect generated R.java
        gen_java_files = []
        for root, _dirs, files in os.walk(self.gen_dir):
            for fname in files:
                if fname.endswith(".java"):
                    gen_java_files.append(os.path.join(root, fname))

        sep = ";" if platform.system() == "Windows" else ":"
        classpath_parts = [self.cfg.android_jar] + self.cfg.libs
        if self.resolver:
            classpath_parts += self.resolver.get_classpath_jars()

        if not kt_files:
            # Java-only path (backward compatible)
            print("[3/7] Compiling Java sources...")
            all_java = user_java_files + gen_java_files
            if not all_java:
                print("  No source files found!", file=sys.stderr)
                sys.exit(1)

            cmd = [
                "javac",
                "-source", "1.8",
                "-target", "1.8",
                "-bootclasspath", self.cfg.android_jar,
                "-classpath", sep.join(classpath_parts),
                "-d", self.classes_dir,
            ]
            cmd += all_java
            _run(cmd, "javac")
        else:
            # Kotlin (or mixed) path
            print("[3/7] Compiling Kotlin sources...")

            # 3a: Compile generated R.java with javac so kotlinc can reference R
            if gen_java_files:
                print("  [3a] Compiling R.java...")
                cmd = [
                    "javac",
                    "-source", "1.8",
                    "-target", "1.8",
                    "-bootclasspath", self.cfg.android_jar,
                    "-classpath", sep.join(classpath_parts),
                    "-d", self.classes_dir,
                ]
                cmd += gen_java_files
                _run(cmd, "javac (R.java)")

            # 3b: Compile .kt (and user .java) with kotlinc
            print("  [3b] Compiling with kotlinc...")
            kt_classpath_parts = classpath_parts + [self.classes_dir]
            cmd = [
                self.cfg.kotlinc_bin,
                "-classpath", sep.join(kt_classpath_parts),
                "-d", self.classes_dir,
                "-jvm-target", "1.8",
                "-no-stdlib",
            ]
            if self.cfg.compose_enabled:
                cmd.append("-Xplugin=" + self.cfg.compose_plugin_jar)
            cmd += kt_files + user_java_files
            _run(cmd, "kotlinc")

    # Step 4: d8 (DEX)
    def _step4_dex(self):
        print("[4/7] Converting to DEX...")
        d8 = self.cfg.tool("d8")

        # Collect all .class files
        class_files = []
        for root, _dirs, files in os.walk(self.classes_dir):
            for fname in files:
                if fname.endswith(".class"):
                    class_files.append(os.path.join(root, fname))

        cmd = [
            d8,
            "--min-api", str(self.cfg.min_sdk),
            "--output", self.dex_dir,
            "--lib", self.cfg.android_jar,
        ]
        cmd += class_files

        # Include kotlin-stdlib.jar so stdlib classes are DEXed into the APK
        if self.cfg.kotlin_version and self.cfg.kotlin_stdlib:
            cmd.append(self.cfg.kotlin_stdlib)

        # Include dependency JARs and manual libs
        if self.resolver:
            dep_jars = self.resolver.get_dex_jars()
            # Exclude transitive kotlin-stdlib JARs — the compiler bundles its own
            if self.cfg.kotlin_stdlib:
                dep_jars = [j for j in dep_jars
                            if not os.path.basename(j).startswith("kotlin-stdlib")]
            # Deduplicate JARs that contain overlapping classes (e.g. AndroidX
            # merged -ktx artifacts into main modules in newer versions)
            dep_jars = _deduplicate_jars(dep_jars)
            cmd += dep_jars
        cmd += self.cfg.libs

        _run(cmd, "d8")

    # Step 5: Inject classes.dex (and classesN.dex for multi-DEX) into APK
    def _step5_inject_dex(self):
        print("[5/7] Injecting DEX into APK...")

        # Glob for all DEX files (classes.dex, classes2.dex, ...)
        dex_files = sorted(glob.glob(os.path.join(self.dex_dir, "classes*.dex")))
        if not dex_files:
            print(f"  No DEX files found in {self.dex_dir}", file=sys.stderr)
            sys.exit(1)

        shutil.copy2(self.base_apk, self.dex_apk)

        with zipfile.ZipFile(self.dex_apk, "a") as zf:
            for dex_file in dex_files:
                arcname = os.path.basename(dex_file)
                zf.write(dex_file, arcname)

        if len(dex_files) > 1:
            print(f"  Multi-DEX: injected {len(dex_files)} DEX files.")

    # Step 6: zipalign
    def _step6_zipalign(self):
        print("[6/7] Aligning APK...")
        zipalign = self.cfg.tool("zipalign")

        # Remove target if it exists (zipalign won't overwrite)
        if os.path.isfile(self.aligned_apk):
            os.remove(self.aligned_apk)

        cmd = [zipalign, "-f", "-p", "4", self.dex_apk, self.aligned_apk]
        _run(cmd, "zipalign")

    # Step 7: apksigner
    def _step7_sign(self):
        print("[7/7] Signing APK...")
        apksigner = self.cfg.tool("apksigner")
        ks = self.keystore.ensure()

        # Remove final target if it exists
        if os.path.isfile(self.final_apk):
            os.remove(self.final_apk)

        shutil.copy2(self.aligned_apk, self.final_apk)

        cmd = [
            apksigner, "sign",
            "--ks", ks,
            "--ks-pass", f"pass:{DebugKeystore.STORE_PASS}",
            "--ks-key-alias", DebugKeystore.KEY_ALIAS,
            "--key-pass", f"pass:{DebugKeystore.KEY_PASS}",
            self.final_apk,
        ]
        _run(cmd, "apksigner")


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _run(cmd, label="command"):
    """Run a subprocess, printing stderr on failure."""
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return result
    except FileNotFoundError:
        print(f"\nError: '{cmd[0]}' not found. Is it installed and on PATH?", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"\nError running {label}:", file=sys.stderr)
        print(f"  Command: {' '.join(cmd)}", file=sys.stderr)
        if e.stdout:
            print(f"  stdout: {e.stdout.decode(errors='replace')}", file=sys.stderr)
        if e.stderr:
            print(f"  stderr: {e.stderr.decode(errors='replace')}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_setup(args):
    """Download and install Android SDK components (and Kotlin compiler if configured)."""
    config = Config(args.config)
    sdk = SDKManager(config)
    sdk.setup()

    if config.kotlin_version:
        kotlin = KotlinManager(config)
        kotlin.setup()


def cmd_build(args):
    """Run the full APK build pipeline."""
    config = Config(args.config)

    # Verify SDK is set up
    if not os.path.isfile(config.android_jar):
        print("Android SDK not found. Run './build.py setup' first.", file=sys.stderr)
        sys.exit(1)

    # Verify Kotlin compiler is set up (when configured)
    if config.kotlin_version and not os.path.isfile(config.kotlinc_bin):
        print("Kotlin compiler not found. Run './build.py setup' first.", file=sys.stderr)
        sys.exit(1)

    # Verify Compose setup
    if config.compose_enabled:
        if not config.kotlin_version:
            print("Compose requires kotlin.version to be set in build.yaml.", file=sys.stderr)
            sys.exit(1)
        if not os.path.isfile(config.compose_plugin_jar):
            print("Compose compiler plugin not found. Run './build.py setup' first.",
                  file=sys.stderr)
            sys.exit(1)

    builder = Builder(config)
    builder.build()


def cmd_clean(args):
    """Remove the build output directory."""
    config = Config(args.config)
    if os.path.isdir(config.build_dir):
        shutil.rmtree(config.build_dir)
        print(f"Removed {config.build_dir}")
    else:
        print("Nothing to clean.")


def main():
    parser = argparse.ArgumentParser(
        description="Minimal Android APK build system",
    )
    parser.add_argument(
        "--config", "-c",
        default="build.yaml",
        help="Path to build config file (default: build.yaml)",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("setup", help="Download and install Android SDK")
    sub.add_parser("build", help="Build the APK")
    sub.add_parser("clean", help="Remove build artifacts")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        "setup": cmd_setup,
        "build": cmd_build,
        "clean": cmd_clean,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
