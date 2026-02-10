#!/usr/bin/env python3
"""Minimal Android APK build system.

Single-file build tool that produces debug APKs from a YAML config.
Handles SDK setup, resource compilation, Java compilation, DEX conversion,
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

        # Intermediate directories
        self.compiled_res_dir = os.path.join(config.build_dir, "compiled_res")
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

        self._step1_compile_resources()
        self._step2_link_resources()
        self._step3_compile_java()
        self._step4_dex()
        self._step5_inject_dex()
        self._step6_zipalign()
        self._step7_sign()

        print(f"\nBuild successful: {self.final_apk}")

    def _prepare_dirs(self):
        for d in (self.compiled_res_dir, self.gen_dir, self.classes_dir, self.dex_dir):
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
        cmd += flat_files
        _run(cmd, "aapt2 link")

    # Step 3: javac
    def _step3_compile_java(self):
        print("[3/7] Compiling Java sources...")

        # Collect all .java files from source dirs and generated R.java
        java_files = []
        for src_dir in self.cfg.sources:
            for root, _dirs, files in os.walk(src_dir):
                for fname in files:
                    if fname.endswith(".java"):
                        java_files.append(os.path.join(root, fname))

        # Add generated R.java
        for root, _dirs, files in os.walk(self.gen_dir):
            for fname in files:
                if fname.endswith(".java"):
                    java_files.append(os.path.join(root, fname))

        if not java_files:
            print("  No Java source files found!", file=sys.stderr)
            sys.exit(1)

        classpath = self.cfg.android_jar
        if self.cfg.libs:
            sep = ";" if platform.system() == "Windows" else ":"
            classpath = sep.join([classpath] + self.cfg.libs)

        cmd = [
            "javac",
            "-source", "1.8",
            "-target", "1.8",
            "-bootclasspath", self.cfg.android_jar,
            "-classpath", classpath,
            "-d", self.classes_dir,
        ]
        cmd += java_files
        _run(cmd, "javac")

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
        _run(cmd, "d8")

    # Step 5: Inject classes.dex into APK via Python zipfile
    def _step5_inject_dex(self):
        print("[5/7] Injecting DEX into APK...")
        dex_file = os.path.join(self.dex_dir, "classes.dex")
        if not os.path.isfile(dex_file):
            print(f"  DEX file not found: {dex_file}", file=sys.stderr)
            sys.exit(1)

        shutil.copy2(self.base_apk, self.dex_apk)

        with zipfile.ZipFile(self.dex_apk, "a") as zf:
            zf.write(dex_file, "classes.dex")

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
    """Download and install Android SDK components."""
    config = Config(args.config)
    sdk = SDKManager(config)
    sdk.setup()


def cmd_build(args):
    """Run the full APK build pipeline."""
    config = Config(args.config)

    # Verify SDK is set up
    if not os.path.isfile(config.android_jar):
        print("Android SDK not found. Run './build.py setup' first.", file=sys.stderr)
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
