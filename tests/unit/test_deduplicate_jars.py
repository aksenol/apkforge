"""Tests for _deduplicate_jars()."""
import pathlib
import zipfile
import sys
import pytest

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
import build as build_module

_deduplicate_jars = build_module._deduplicate_jars


def _make_jar(path, class_names):
    """Create a mock JAR file containing the given .class entries."""
    path = pathlib.Path(path)
    with zipfile.ZipFile(str(path), "w") as zf:
        for name in class_names:
            # Write empty content for each class entry
            zf.writestr(name, b"")
    return str(path)


class TestDeduplicateJars:
    def test_empty_input(self, tmp_path):
        assert _deduplicate_jars([]) == []

    def test_single_jar_returned_unchanged(self, tmp_path):
        jar = _make_jar(tmp_path / "a.jar", ["com/example/A.class"])
        result = _deduplicate_jars([jar])
        assert result == [jar]

    def test_disjoint_jars_both_kept(self, tmp_path):
        jar_a = _make_jar(tmp_path / "a.jar", ["com/example/A.class"])
        jar_b = _make_jar(tmp_path / "b.jar", ["com/example/B.class"])
        result = _deduplicate_jars([jar_a, jar_b])
        assert jar_a in result
        assert jar_b in result
        assert len(result) == 2

    def test_equal_class_jars_both_kept(self, tmp_path):
        # Equal sets — neither is a STRICT subset, so both kept
        classes = ["com/example/A.class", "com/example/B.class"]
        jar_a = _make_jar(tmp_path / "a.jar", classes)
        jar_b = _make_jar(tmp_path / "b.jar", classes)
        result = _deduplicate_jars([jar_a, jar_b])
        assert jar_a in result
        assert jar_b in result

    def test_subset_jar_removed(self, tmp_path):
        # jar_a has A.class only; jar_b has A.class + B.class → jar_a is subset
        jar_a = _make_jar(tmp_path / "a.jar", ["com/example/A.class"])
        jar_b = _make_jar(tmp_path / "b.jar", [
            "com/example/A.class",
            "com/example/B.class",
        ])
        result = _deduplicate_jars([jar_a, jar_b])
        assert jar_b in result
        assert jar_a not in result

    def test_superset_kept_when_subset_removed(self, tmp_path):
        jar_small = _make_jar(tmp_path / "small.jar", ["com/example/X.class"])
        jar_large = _make_jar(tmp_path / "large.jar", [
            "com/example/X.class",
            "com/example/Y.class",
            "com/example/Z.class",
        ])
        result = _deduplicate_jars([jar_small, jar_large])
        assert jar_large in result
        assert len(result) == 1

    def test_empty_jar_kept(self, tmp_path):
        # Empty JAR (no .class entries) — kept because `if not jar_classes[jar_a]: continue`
        empty_jar = _make_jar(tmp_path / "empty.jar", [])
        normal_jar = _make_jar(tmp_path / "normal.jar", ["com/example/A.class"])
        result = _deduplicate_jars([empty_jar, normal_jar])
        assert empty_jar in result
        assert normal_jar in result

    def test_bad_zip_file_kept(self, tmp_path):
        # A file that is not a valid ZIP is treated as empty → kept
        bad_jar = tmp_path / "bad.jar"
        bad_jar.write_bytes(b"this is not a zip file")
        normal_jar = _make_jar(tmp_path / "normal.jar", ["com/example/A.class"])
        result = _deduplicate_jars([str(bad_jar), str(normal_jar)])
        assert str(bad_jar) in result
        assert str(normal_jar) in result

    def test_order_preserved(self, tmp_path):
        jar_a = _make_jar(tmp_path / "a.jar", ["com/a/A.class"])
        jar_b = _make_jar(tmp_path / "b.jar", ["com/b/B.class"])
        jar_c = _make_jar(tmp_path / "c.jar", ["com/c/C.class"])
        result = _deduplicate_jars([jar_a, jar_b, jar_c])
        # Order should be preserved (all disjoint, all kept)
        assert result.index(jar_a) < result.index(jar_b)
        assert result.index(jar_b) < result.index(jar_c)

    def test_only_class_entries_counted(self, tmp_path):
        # JAR with resources but no .class files is treated as empty
        resource_jar = _make_jar(tmp_path / "resources.jar", [
            "META-INF/MANIFEST.MF",
            "res/drawable/icon.png",
        ])
        class_jar = _make_jar(tmp_path / "classes.jar", ["com/example/A.class"])
        result = _deduplicate_jars([resource_jar, class_jar])
        # resource_jar has no .class entries, treated as empty → kept
        assert resource_jar in result
        assert class_jar in result

    def test_androidx_ktx_merge_pattern(self, tmp_path):
        # Simulates AndroidX -ktx merge: old-ktx JAR is a subset of merged JAR
        common_classes = [
            "androidx/collection/ArrayMap.class",
            "androidx/collection/ArraySet.class",
        ]
        old_ktx = _make_jar(tmp_path / "collection-ktx-1.3.0.jar", common_classes)
        merged = _make_jar(tmp_path / "collection-jvm-1.4.0.jar", common_classes + [
            "androidx/collection/ScatterMap.class",
            "androidx/collection/MutableScatterMap.class",
        ])
        result = _deduplicate_jars([old_ktx, merged])
        assert merged in result
        assert old_ktx not in result

    def test_multiple_subsets_all_removed(self, tmp_path):
        big = _make_jar(tmp_path / "big.jar", [
            "com/A.class", "com/B.class", "com/C.class"
        ])
        sub1 = _make_jar(tmp_path / "sub1.jar", ["com/A.class"])
        sub2 = _make_jar(tmp_path / "sub2.jar", ["com/B.class"])
        result = _deduplicate_jars([sub1, sub2, big])
        assert big in result
        assert sub1 not in result
        assert sub2 not in result
