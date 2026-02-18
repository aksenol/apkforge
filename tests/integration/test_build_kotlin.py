"""Integration tests: Kotlin APK build."""
import os
import zipfile
import pathlib
import pytest
import sys

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
import build as build_module

pytestmark = pytest.mark.integration


class TestKotlinConfigSanity:
    """Fast config-level checks — no build needed."""

    def test_kotlin_version(self, kotlin_app_config):
        assert kotlin_app_config.kotlin_version == "2.1.20"

    def test_compose_disabled(self, kotlin_app_config):
        assert kotlin_app_config.compose_enabled is False

    def test_kotlinc_bin_exists(self, kotlin_app_project, kotlin_app_config):
        kotlinc = kotlin_app_config.kotlinc_bin
        assert kotlinc is not None
        assert os.path.isfile(kotlinc), (
            f"kotlinc not found at {kotlinc}. Is .kotlin symlink valid?"
        )

    def test_kotlin_stdlib_exists(self, kotlin_app_config):
        stdlib = kotlin_app_config.kotlin_stdlib
        assert stdlib is not None
        assert os.path.isfile(stdlib), f"kotlin-stdlib.jar not found at {stdlib}"

    def test_compose_plugin_jar_is_none(self, kotlin_app_config):
        assert kotlin_app_config.compose_plugin_jar is None

    def test_has_kotlin_stdlib_dependency(self, kotlin_app_config):
        assert any("kotlin-stdlib" in d for d in kotlin_app_config.dependencies)

    def test_android_jar_exists(self, kotlin_app_config):
        assert os.path.isfile(kotlin_app_config.android_jar)


@pytest.fixture(scope="class")
def built_kotlin_apk(kotlin_app_project, kotlin_app_config):
    """Build the Kotlin APK once and return its path."""
    builder = build_module.Builder(kotlin_app_config)
    builder.build()
    return pathlib.Path(builder.final_apk)


class TestKotlinBuildOutput:
    """Tests that verify a successfully-built Kotlin APK."""

    def test_apk_file_created(self, built_kotlin_apk):
        assert built_kotlin_apk.exists(), f"APK not found: {built_kotlin_apk}"

    def test_apk_size_nonzero(self, built_kotlin_apk):
        assert built_kotlin_apk.stat().st_size > 0

    def test_apk_is_valid_zip(self, built_kotlin_apk):
        assert zipfile.is_zipfile(str(built_kotlin_apk))

    def test_apk_contains_classes_dex(self, built_kotlin_apk):
        with zipfile.ZipFile(str(built_kotlin_apk)) as zf:
            assert "classes.dex" in zf.namelist()

    def test_apk_contains_android_manifest(self, built_kotlin_apk):
        with zipfile.ZipFile(str(built_kotlin_apk)) as zf:
            assert "AndroidManifest.xml" in zf.namelist()

    def test_apk_has_signing_metadata(self, built_kotlin_apk):
        with zipfile.ZipFile(str(built_kotlin_apk)) as zf:
            names = zf.namelist()
            meta_inf = [n for n in names if n.startswith("META-INF/")]
            assert len(meta_inf) > 0

    def test_classes_dir_contains_class_files(self, kotlin_app_config):
        classes_dir = pathlib.Path(kotlin_app_config.build_dir) / "classes"
        class_files = list(classes_dir.rglob("*.class"))
        assert len(class_files) > 0

    def test_classes_dir_contains_main_activity_class(self, kotlin_app_config):
        classes_dir = pathlib.Path(kotlin_app_config.build_dir) / "classes"
        # kotlinc compiles MainActivity.kt → MainActivity.class
        activity_classes = list(classes_dir.rglob("MainActivity.class"))
        assert len(activity_classes) > 0, "No MainActivity.class found"

    def test_dex_size_reflects_kotlin_stdlib(self, built_kotlin_apk):
        # Kotlin APK should be larger than a trivial Java APK due to stdlib
        size = built_kotlin_apk.stat().st_size
        assert size > 10_000, f"APK too small ({size} bytes), stdlib may be missing"
