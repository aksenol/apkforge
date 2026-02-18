"""Tests for DebugKeystore."""
import os
import pathlib
import sys
import pytest

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
import build as build_module

DebugKeystore = build_module.DebugKeystore
Config = build_module.Config


def _make_config(tmp_path):
    """Create a minimal Config pointing to tmp_path."""
    build_yaml = tmp_path / "build.yaml"
    build_yaml.write_text("app:\n  package: com.example.test\n")
    return Config(str(build_yaml))


class TestDebugKeystoreConstants:
    def test_store_pass(self):
        assert DebugKeystore.STORE_PASS == "android"

    def test_key_alias(self):
        assert DebugKeystore.KEY_ALIAS == "androiddebugkey"

    def test_key_pass(self):
        assert DebugKeystore.KEY_PASS == "android"


class TestDebugKeystoreEnsure:
    def test_ensure_creates_keystore(self, tmp_path):
        cfg = _make_config(tmp_path)
        ks = DebugKeystore(cfg)
        result = ks.ensure()
        assert os.path.isfile(result)
        assert os.path.isfile(ks.keystore_path)

    def test_keystore_path_in_project_root(self, tmp_path):
        cfg = _make_config(tmp_path)
        ks = DebugKeystore(cfg)
        assert ks.keystore_path == str(tmp_path / "debug.keystore")

    def test_ensure_idempotent(self, tmp_path):
        cfg = _make_config(tmp_path)
        ks = DebugKeystore(cfg)
        # First call creates the file
        ks.ensure()
        mtime1 = os.path.getmtime(ks.keystore_path)
        # Second call should not recreate it
        ks.ensure()
        mtime2 = os.path.getmtime(ks.keystore_path)
        assert mtime1 == mtime2

    def test_ensure_returns_keystore_path(self, tmp_path):
        cfg = _make_config(tmp_path)
        ks = DebugKeystore(cfg)
        result = ks.ensure()
        assert result == ks.keystore_path

    def test_ensure_skips_if_exists(self, tmp_path):
        cfg = _make_config(tmp_path)
        ks = DebugKeystore(cfg)
        # Pre-create the file
        (tmp_path / "debug.keystore").write_bytes(b"fake keystore content")
        # Should return path without trying to run keytool
        result = ks.ensure()
        assert result == ks.keystore_path
        # Content should be unchanged (no overwrite)
        assert (tmp_path / "debug.keystore").read_bytes() == b"fake keystore content"
