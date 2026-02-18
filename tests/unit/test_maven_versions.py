"""Tests for MavenResolver._compare_versions()."""
import pathlib
import sys
import pytest

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
import build as build_module

compare = build_module.MavenResolver._compare_versions


class TestCompareVersions:
    def test_equal_versions(self):
        assert compare("1.0.0", "1.0.0") == 0

    def test_higher_major(self):
        assert compare("2.0.0", "1.0.0") > 0

    def test_lower_major(self):
        assert compare("1.0.0", "2.0.0") < 0

    def test_higher_minor(self):
        assert compare("1.1.0", "1.0.0") > 0

    def test_lower_minor(self):
        assert compare("1.0.0", "1.1.0") < 0

    def test_higher_patch(self):
        assert compare("1.0.1", "1.0.0") > 0

    def test_lower_patch(self):
        assert compare("1.0.0", "1.0.1") < 0

    def test_different_segment_counts_shorter_wins(self):
        # "1.0" vs "1.0.0" — zero-padded, should be equal
        assert compare("1.0", "1.0.0") == 0

    def test_different_segment_counts_longer_higher(self):
        # "1.0.1" > "1.0"
        assert compare("1.0.1", "1.0") > 0

    def test_numeric_comparison_not_lexicographic(self):
        # 10 > 9 numerically
        assert compare("1.10.0", "1.9.0") > 0

    def test_single_segment_equal(self):
        assert compare("3", "3") == 0

    def test_single_segment_higher(self):
        assert compare("4", "3") > 0

    def test_single_segment_lower(self):
        assert compare("2", "3") < 0

    def test_realistic_androidx_versions(self):
        assert compare("1.10.1", "1.8.0") > 0
        assert compare("1.8.0", "1.10.1") < 0

    def test_same_major_minor_different_patch(self):
        assert compare("2.1.20", "2.1.10") > 0
