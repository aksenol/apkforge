"""Tests for the Config class."""
import os
import pathlib
import pytest
import sys

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
import build as build_module

Config = build_module.Config


def _make_config(tmp_path, yaml_content, create_dirs=True):
    """Write build.yaml to tmp_path and return a Config instance."""
    build_yaml = tmp_path / "build.yaml"
    build_yaml.write_text(yaml_content)
    if create_dirs:
        # Create the default paths so validate() passes
        (tmp_path / "AndroidManifest.xml").write_text(
            '<?xml version="1.0" encoding="utf-8"?><manifest package="x" />'
        )
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "res").mkdir(exist_ok=True)
    return Config(str(build_yaml))


MINIMAL_YAML = """\
app:
  package: com.example.test
  name: TestApp
  version_code: 1
  version_name: "1.0"
"""


class TestConfigDefaults:
    def test_package(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.package == "com.example.test"

    def test_min_sdk_default(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.min_sdk == 21

    def test_target_sdk_default(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.target_sdk == 34

    def test_build_tools_default(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.build_tools_version == "34.0.0"

    def test_apk_name_default(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.apk_name == "app-debug.apk"

    def test_compose_disabled_by_default(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.compose_enabled is False

    def test_empty_dependencies_by_default(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.dependencies == []

    def test_kotlin_version_none_by_default(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.kotlin_version is None

    def test_app_name(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.app_name == "TestApp"

    def test_version_code(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.version_code == 1

    def test_version_name(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.version_name == "1.0"

    def test_default_repositories_non_empty(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert len(cfg.repositories) >= 1


class TestConfigPathResolution:
    def test_sdk_dir_is_absolute(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert os.path.isabs(cfg.sdk_dir)

    def test_sdk_dir_relative_to_config(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.sdk_dir == str(tmp_path / ".android-sdk")

    def test_deps_cache_dir_is_absolute(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert os.path.isabs(cfg.deps_cache_dir)

    def test_deps_cache_dir_relative_to_config(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.deps_cache_dir == str(tmp_path / ".deps")

    def test_manifest_is_absolute(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert os.path.isabs(cfg.manifest)

    def test_manifest_path(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.manifest == str(tmp_path / "AndroidManifest.xml")

    def test_sources_are_absolute(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert all(os.path.isabs(s) for s in cfg.sources)

    def test_sources_default(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.sources == [str(tmp_path / "src")]

    def test_build_dir_is_absolute(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert os.path.isabs(cfg.build_dir)

    def test_build_dir_default(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.build_dir == str(tmp_path / ".build")

    def test_project_root_is_tmp_dir(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.project_root == str(tmp_path)

    def test_absolute_manifest_not_double_joined(self, tmp_path):
        abs_manifest = str(tmp_path / "AndroidManifest.xml")
        yaml = f"""\
paths:
  manifest: {abs_manifest}
"""
        cfg = _make_config(tmp_path, yaml)
        assert cfg.manifest == abs_manifest


class TestConfigProperties:
    def test_build_tools_dir(self, tmp_path):
        yaml = """\
sdk:
  build_tools: "35.0.1"
"""
        cfg = _make_config(tmp_path, yaml)
        assert cfg.build_tools_dir == str(tmp_path / ".android-sdk" / "build-tools" / "35.0.1")

    def test_android_jar(self, tmp_path):
        yaml = """\
sdk:
  target_sdk: 34
"""
        cfg = _make_config(tmp_path, yaml)
        assert cfg.android_jar == str(
            tmp_path / ".android-sdk" / "platforms" / "android-34" / "android.jar"
        )

    def test_tool_returns_build_tools_binary(self, tmp_path):
        yaml = """\
sdk:
  build_tools: "35.0.1"
"""
        cfg = _make_config(tmp_path, yaml)
        assert cfg.tool("aapt2") == str(
            tmp_path / ".android-sdk" / "build-tools" / "35.0.1" / "aapt2"
        )

    def test_kotlinc_bin_none_when_no_kotlin(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.kotlinc_bin is None

    def test_kotlin_stdlib_none_when_no_kotlin(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.kotlin_stdlib is None

    def test_kotlin_version_when_configured(self, tmp_path):
        yaml = """\
kotlin:
  version: "2.1.20"
"""
        cfg = _make_config(tmp_path, yaml)
        assert cfg.kotlin_version == "2.1.20"

    def test_kotlinc_bin_when_configured(self, tmp_path):
        yaml = """\
kotlin:
  version: "2.1.20"
"""
        cfg = _make_config(tmp_path, yaml)
        assert cfg.kotlinc_bin is not None
        assert "kotlinc" in cfg.kotlinc_bin
        assert "2.1.20" not in cfg.kotlinc_bin  # kotlinc bin path doesn't embed version

    def test_kotlin_stdlib_when_configured(self, tmp_path):
        yaml = """\
kotlin:
  version: "2.1.20"
"""
        cfg = _make_config(tmp_path, yaml)
        assert cfg.kotlin_stdlib is not None
        assert "kotlin-stdlib.jar" in cfg.kotlin_stdlib

    def test_compose_plugin_jar_none_when_compose_disabled(self, tmp_path):
        yaml = """\
kotlin:
  version: "2.1.20"
  compose: false
"""
        cfg = _make_config(tmp_path, yaml)
        assert cfg.compose_plugin_jar is None

    def test_compose_plugin_jar_contains_version(self, tmp_path):
        yaml = """\
kotlin:
  version: "2.1.20"
  compose: true
"""
        cfg = _make_config(tmp_path, yaml)
        assert cfg.compose_plugin_jar is not None
        assert "2.1.20" in cfg.compose_plugin_jar
        assert "compose" in cfg.compose_plugin_jar.lower()

    def test_compose_plugin_jar_none_when_no_kotlin(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.compose_plugin_jar is None

    def test_preview_output_dir(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML)
        assert cfg.preview_output_dir == str(tmp_path / ".build" / "previews")


class TestConfigValidate:
    def test_validate_passes_when_all_present(self, tmp_path):
        cfg = _make_config(tmp_path, MINIMAL_YAML, create_dirs=True)
        # Should not raise
        cfg.validate()

    def test_validate_fails_when_manifest_missing(self, tmp_path):
        yaml = """\
paths:
  manifest: nonexistent.xml
  sources:
    - src
  resources:
    - res
"""
        cfg = _make_config(tmp_path, yaml, create_dirs=False)
        (tmp_path / "src").mkdir()
        (tmp_path / "res").mkdir()
        with pytest.raises(SystemExit):
            cfg.validate()

    def test_validate_fails_when_source_dir_missing(self, tmp_path):
        yaml = """\
paths:
  manifest: AndroidManifest.xml
  sources:
    - nonexistent_src
  resources:
    - res
"""
        cfg = _make_config(tmp_path, yaml, create_dirs=False)
        (tmp_path / "AndroidManifest.xml").write_text('<manifest package="x" />')
        (tmp_path / "res").mkdir()
        with pytest.raises(SystemExit):
            cfg.validate()

    def test_validate_fails_when_resource_dir_missing(self, tmp_path):
        yaml = """\
paths:
  manifest: AndroidManifest.xml
  sources:
    - src
  resources:
    - nonexistent_res
"""
        cfg = _make_config(tmp_path, yaml, create_dirs=False)
        (tmp_path / "AndroidManifest.xml").write_text('<manifest package="x" />')
        (tmp_path / "src").mkdir()
        with pytest.raises(SystemExit):
            cfg.validate()
