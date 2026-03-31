"""Integration tests: Java-only APK build."""
import os
import shutil
import zipfile
import pathlib
import pytest
import sys

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
import build as build_module

pytestmark = pytest.mark.integration


class TestJavaConfigSanity:
    """Fast config-level checks — no build needed."""

    def test_package(self, minimal_java_config):
        assert minimal_java_config.package == "com.example.minimal"

    def test_kotlin_version_is_none(self, minimal_java_config):
        assert minimal_java_config.kotlin_version is None

    def test_sdk_dir_exists(self, minimal_java_project):
        sdk = minimal_java_project / ".android-sdk"
        assert sdk.exists()

    def test_android_jar_exists(self, minimal_java_config):
        assert os.path.isfile(minimal_java_config.android_jar), (
            f"android.jar not found at {minimal_java_config.android_jar}"
        )

    def test_aapt2_exists(self, minimal_java_config):
        assert os.path.isfile(minimal_java_config.tool("aapt2")), (
            f"aapt2 not found at {minimal_java_config.tool('aapt2')}"
        )

    def test_d8_exists(self, minimal_java_config):
        assert os.path.isfile(minimal_java_config.tool("d8")), (
            f"d8 not found at {minimal_java_config.tool('d8')}"
        )

    def test_zipalign_exists(self, minimal_java_config):
        assert os.path.isfile(minimal_java_config.tool("zipalign")), (
            f"zipalign not found at {minimal_java_config.tool('zipalign')}"
        )

    def test_apksigner_exists(self, minimal_java_config):
        assert os.path.isfile(minimal_java_config.tool("apksigner")), (
            f"apksigner not found at {minimal_java_config.tool('apksigner')}"
        )


@pytest.fixture(scope="class")
def built_java_apk(minimal_java_project, minimal_java_config):
    """Build the minimal Java APK once and return its path."""
    builder = build_module.Builder(minimal_java_config)
    builder.build()
    return pathlib.Path(builder.final_apk)


class TestJavaBuildOutput:
    """Tests that verify a successfully-built Java APK."""

    def test_apk_file_created(self, built_java_apk):
        assert built_java_apk.exists(), f"APK not found: {built_java_apk}"

    def test_apk_size_nonzero(self, built_java_apk):
        assert built_java_apk.stat().st_size > 0

    def test_apk_is_valid_zip(self, built_java_apk):
        assert zipfile.is_zipfile(str(built_java_apk))

    def test_apk_contains_classes_dex(self, built_java_apk):
        with zipfile.ZipFile(str(built_java_apk)) as zf:
            assert "classes.dex" in zf.namelist()

    def test_apk_contains_android_manifest(self, built_java_apk):
        with zipfile.ZipFile(str(built_java_apk)) as zf:
            assert "AndroidManifest.xml" in zf.namelist()

    def test_apk_has_signing_metadata(self, built_java_apk):
        with zipfile.ZipFile(str(built_java_apk)) as zf:
            names = zf.namelist()
            meta_inf = [n for n in names if n.startswith("META-INF/")]
            assert len(meta_inf) > 0, "No META-INF entries found in APK"

    def test_gen_dir_contains_r_java(self, minimal_java_config):
        gen_dir = pathlib.Path(minimal_java_config.build_dir) / "gen"
        r_java_files = list(gen_dir.rglob("R.java"))
        assert len(r_java_files) > 0, f"No R.java found in {gen_dir}"

    def test_classes_dir_contains_class_files(self, minimal_java_config):
        classes_dir = pathlib.Path(minimal_java_config.build_dir) / "classes"
        class_files = list(classes_dir.rglob("*.class"))
        assert len(class_files) > 0, f"No .class files found in {classes_dir}"

    def test_aligned_apk_intermediate_exists(self, minimal_java_config):
        aligned = pathlib.Path(minimal_java_config.build_dir) / "aligned.apk"
        assert aligned.exists(), f"aligned.apk not found at {aligned}"


class TestJavaClean:
    """Test that clean removes the build directory."""

    def test_clean_removes_build_dir(self, minimal_java_project, minimal_java_config):
        # Ensure build exists first
        build_dir = pathlib.Path(minimal_java_config.build_dir)
        if not build_dir.exists():
            builder = build_module.Builder(minimal_java_config)
            builder.build()
        assert build_dir.exists()

        # Run clean
        if build_dir.exists():
            shutil.rmtree(str(build_dir))
        assert not build_dir.exists()
