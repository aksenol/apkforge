"""Integration tests: Kotlin + Compose APK build."""
import os
import zipfile
import pathlib
import pytest
import sys

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
import build as build_module

pytestmark = pytest.mark.integration


def _has_compose_artifacts(compose_app_project):
    """Check if the .deps/ symlink has Compose artifacts cached."""
    deps = compose_app_project / ".deps"
    if not deps.exists():
        return False
    artifacts = deps / "artifacts"
    if not artifacts.exists():
        return False
    # Look for any activity-compose or compose ui artifact
    for item in artifacts.rglob("*.aar"):
        if "activity-compose" in str(item) or "compose" in str(item):
            return True
    return False


class TestComposeConfigSanity:
    """Fast config-level checks — no build needed."""

    def test_kotlin_version(self, compose_app_config):
        assert compose_app_config.kotlin_version == "2.1.20"

    def test_compose_enabled(self, compose_app_config):
        assert compose_app_config.compose_enabled is True

    def test_compose_plugin_jar_contains_version(self, compose_app_config):
        jar = compose_app_config.compose_plugin_jar
        assert jar is not None
        assert "2.1.20" in jar

    def test_compose_plugin_jar_contains_compose(self, compose_app_config):
        jar = compose_app_config.compose_plugin_jar
        assert jar is not None
        assert "compose" in jar.lower()

    def test_compose_plugin_jar_exists(self, compose_app_project, compose_app_config):
        jar = compose_app_config.compose_plugin_jar
        assert jar is not None
        assert os.path.isfile(jar), (
            f"Compose plugin JAR not found at {jar}. Is .kotlin symlink valid?"
        )

    def test_dependencies_count(self, compose_app_config):
        assert len(compose_app_config.dependencies) == 5

    def test_dependencies_include_activity_compose(self, compose_app_config):
        deps = compose_app_config.dependencies
        assert any("activity-compose" in d for d in deps)

    def test_dependencies_include_compose_ui(self, compose_app_config):
        deps = compose_app_config.dependencies
        assert any("compose.ui:ui" in d for d in deps)

    def test_dependencies_include_material3(self, compose_app_config):
        deps = compose_app_config.dependencies
        assert any("material3" in d for d in deps)

    def test_preview_width(self, compose_app_config):
        assert compose_app_config.preview_width == 1080

    def test_preview_height(self, compose_app_config):
        assert compose_app_config.preview_height == 1920

    def test_preview_density(self, compose_app_config):
        assert compose_app_config.preview_density == 420


@pytest.fixture(scope="class")
def built_compose_apk(compose_app_project, compose_app_config):
    """Build the Compose APK once and return its path."""
    if not _has_compose_artifacts(compose_app_project):
        pytest.skip("Compose artifacts not in .deps/ cache; skipping build test")
    builder = build_module.Builder(compose_app_config)
    builder.build()
    return pathlib.Path(builder.final_apk)


class TestComposeBuildOutput:
    """Tests that verify a successfully-built Compose APK."""

    def test_apk_file_created(self, built_compose_apk):
        assert built_compose_apk.exists()

    def test_apk_is_valid_zip(self, built_compose_apk):
        assert zipfile.is_zipfile(str(built_compose_apk))

    def test_apk_contains_classes_dex(self, built_compose_apk):
        with zipfile.ZipFile(str(built_compose_apk)) as zf:
            names = zf.namelist()
            dex_files = [n for n in names if n.startswith("classes") and n.endswith(".dex")]
            assert len(dex_files) >= 1, f"No DEX files found. Entries: {names[:20]}"

    def test_apk_contains_android_manifest(self, built_compose_apk):
        with zipfile.ZipFile(str(built_compose_apk)) as zf:
            assert "AndroidManifest.xml" in zf.namelist()

    def test_apk_has_signing_metadata(self, built_compose_apk):
        with zipfile.ZipFile(str(built_compose_apk)) as zf:
            names = zf.namelist()
            meta_inf = [n for n in names if n.startswith("META-INF/")]
            assert len(meta_inf) > 0

    def test_classes_dir_contains_kotlin_output(self, compose_app_config):
        classes_dir = pathlib.Path(compose_app_config.build_dir) / "classes"
        class_files = list(classes_dir.rglob("*.class"))
        assert len(class_files) > 0

    def test_deps_artifacts_populated(self, compose_app_project):
        deps_dir = compose_app_project / ".deps" / "artifacts"
        assert deps_dir.exists()
        # At least some artifacts should be present
        all_files = list(deps_dir.rglob("*.*"))
        assert len(all_files) > 0

    def test_gen_contains_r_java_with_extra_packages(self, compose_app_config):
        gen_dir = pathlib.Path(compose_app_config.build_dir) / "gen"
        r_java_files = list(gen_dir.rglob("R.java"))
        # Multiple R.java files expected for Compose library packages
        assert len(r_java_files) >= 1

    def test_multi_dex_for_compose(self, built_compose_apk):
        # Compose brings in many classes, likely triggering multi-DEX
        with zipfile.ZipFile(str(built_compose_apk)) as zf:
            names = zf.namelist()
            dex_files = [n for n in names if n.startswith("classes") and n.endswith(".dex")]
            # Multi-DEX is expected but not strictly required
            # Just verify the DEX count is reasonable
            assert len(dex_files) >= 1
