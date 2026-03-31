"""Tests for Builder._find_preview_functions()."""
import os
import pathlib
import sys
import pytest

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
import build as build_module

Config = build_module.Config
Builder = build_module.Builder


def _make_project(tmp_path, kt_files=None):
    """Create a minimal project structure with given .kt source files.

    kt_files: dict of relative path -> content, e.g.:
        {"src/com/example/Foo.kt": "package com.example\\n..."}
    """
    build_yaml = tmp_path / "build.yaml"
    build_yaml.write_text("app:\n  package: com.example\n")
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_text('<manifest package="com.example" />')
    src_dir = tmp_path / "src"
    src_dir.mkdir(exist_ok=True)
    res_dir = tmp_path / "res"
    res_dir.mkdir(exist_ok=True)

    if kt_files:
        for rel_path, content in kt_files.items():
            fpath = tmp_path / rel_path
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content)

    return Config(str(build_yaml))


class TestFindPreviewFunctions:
    def test_no_kt_files_returns_empty(self, tmp_path):
        cfg = _make_project(tmp_path)
        builder = Builder(cfg)
        result = builder._find_preview_functions()
        assert result == []

    def test_preview_before_composable(self, tmp_path):
        cfg = _make_project(tmp_path, {
            "src/com/example/Foo.kt": """\
package com.example

import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview

@Preview
@Composable
fun MyPreview() {}
"""
        })
        builder = Builder(cfg)
        result = builder._find_preview_functions()
        assert "com.example.FooKt.MyPreview" in result

    def test_composable_before_preview(self, tmp_path):
        cfg = _make_project(tmp_path, {
            "src/com/example/Bar.kt": """\
package com.example

import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview

@Composable
@Preview
fun AnotherPreview() {}
"""
        })
        builder = Builder(cfg)
        result = builder._find_preview_functions()
        assert "com.example.BarKt.AnotherPreview" in result

    def test_preview_with_params(self, tmp_path):
        cfg = _make_project(tmp_path, {
            "src/com/example/Ui.kt": """\
package com.example

import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview

@Preview(showBackground = true)
@Composable
fun UiPreview() {}
"""
        })
        builder = Builder(cfg)
        result = builder._find_preview_functions()
        assert "com.example.UiKt.UiPreview" in result

    def test_multiple_previews_in_one_file(self, tmp_path):
        cfg = _make_project(tmp_path, {
            "src/com/example/Screen.kt": """\
package com.example

import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview

@Preview
@Composable
fun LightPreview() {}

@Preview
@Composable
fun DarkPreview() {}
"""
        })
        builder = Builder(cfg)
        result = builder._find_preview_functions()
        assert "com.example.ScreenKt.LightPreview" in result
        assert "com.example.ScreenKt.DarkPreview" in result

    def test_composable_without_preview_not_included(self, tmp_path):
        cfg = _make_project(tmp_path, {
            "src/com/example/Component.kt": """\
package com.example

import androidx.compose.runtime.Composable

@Composable
fun MyComponent() {}
"""
        })
        builder = Builder(cfg)
        result = builder._find_preview_functions()
        assert result == []

    def test_fqn_format(self, tmp_path):
        cfg = _make_project(tmp_path, {
            "src/com/example/ui/HomeScreen.kt": """\
package com.example.ui

import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview

@Preview
@Composable
fun HomePreview() {}
"""
        })
        builder = Builder(cfg)
        result = builder._find_preview_functions()
        assert "com.example.ui.HomeScreenKt.HomePreview" in result

    def test_no_package_declaration(self, tmp_path):
        cfg = _make_project(tmp_path, {
            "src/NoPackage.kt": """\
import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview

@Preview
@Composable
fun RootPreview() {}
"""
        })
        builder = Builder(cfg)
        result = builder._find_preview_functions()
        # No package → "FileKt.FunctionName" (no leading dot)
        assert "NoPackageKt.RootPreview" in result

    def test_deduplication_same_fqn_not_duplicated(self, tmp_path):
        # Both regex patterns could match @Preview @Composable; ensure no duplicate
        cfg = _make_project(tmp_path, {
            "src/com/example/Dup.kt": """\
package com.example

import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview

@Preview
@Composable
fun DupPreview() {}
"""
        })
        builder = Builder(cfg)
        result = builder._find_preview_functions()
        fqn = "com.example.DupKt.DupPreview"
        assert result.count(fqn) == 1

    def test_preview_with_extra_annotation_between(self, tmp_path):
        """@Preview @SomeOtherAnnotation @Composable fun ..."""
        cfg = _make_project(tmp_path, {
            "src/com/example/Extra.kt": """\
package com.example

import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview

@Preview
@Suppress("unused")
@Composable
fun ExtraAnnotationPreview() {}
"""
        })
        builder = Builder(cfg)
        result = builder._find_preview_functions()
        assert "com.example.ExtraKt.ExtraAnnotationPreview" in result
