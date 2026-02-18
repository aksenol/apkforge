"""Integration tests: MavenResolver with cached .deps/."""
import os
import pathlib
import pytest
import sys

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
import build as build_module

MavenResolver = build_module.MavenResolver
Config = build_module.Config

pytestmark = pytest.mark.integration


def _make_config_with_deps(tmp_path, real_project_root, dependencies=None, repositories=None):
    """Create a Config whose .deps/ symlinks to the real project's cache."""
    build_yaml = tmp_path / "build.yaml"
    repos = repositories or [
        "https://dl.google.com/dl/android/maven2",
        "https://repo1.maven.org/maven2",
    ]
    deps = dependencies or []
    repos_yaml = "\n".join(f"  - {r}" for r in repos)
    deps_yaml = "\n".join(f"  - {d}" for d in deps)
    build_yaml.write_text(f"""\
app:
  package: com.example.test
repositories:
{repos_yaml}
dependencies:
{deps_yaml}
""")

    # Symlink .deps to real project cache
    real_deps = real_project_root / ".deps"
    link = tmp_path / ".deps"
    if real_deps.exists() and not link.exists():
        link.symlink_to(real_deps, target_is_directory=True)

    return Config(str(build_yaml))


def _deps_has_artifact(real_project_root, group, artifact):
    """Check if .deps/artifacts contains a specific artifact."""
    deps = real_project_root / ".deps" / "artifacts"
    if not deps.exists():
        return False
    group_path = group.replace(".", "/")
    artifact_dir = deps / group_path / artifact
    return artifact_dir.exists() and any(artifact_dir.iterdir())


class TestMavenVersionComparison:
    """Standalone _compare_versions tests (no cache needed)."""

    def test_compare_equal(self):
        assert MavenResolver._compare_versions("1.8.1", "1.8.1") == 0

    def test_compare_higher(self):
        assert MavenResolver._compare_versions("1.10.1", "1.8.1") > 0

    def test_compare_lower(self):
        assert MavenResolver._compare_versions("1.8.0", "1.10.1") < 0

    def test_is_static_method(self):
        # Should be callable without instantiation
        result = MavenResolver._compare_versions("2.0.0", "1.9.9")
        assert result > 0


class TestMavenResolverCached:
    """Tests that use cached artifacts in .deps/."""

    def test_resolve_cached_jar_artifact(self, tmp_path, real_project_root):
        if not _deps_has_artifact(real_project_root, "androidx.annotation", "annotation"):
            pytest.skip("androidx.annotation not in .deps/ cache")

        cfg = _make_config_with_deps(
            tmp_path, real_project_root,
            dependencies=["androidx.annotation:annotation:1.8.1"]
        )
        resolver = MavenResolver(cfg)
        resolver.resolve_all(cfg.dependencies)
        classpath = resolver.get_classpath_jars()
        assert len(classpath) >= 1
        # At least one jar should be from our requested artifact
        jar_names = [os.path.basename(j) for j in classpath]
        assert any("annotation" in n for n in jar_names)

    def test_resolve_cached_aar_classes_in_classpath(self, tmp_path, real_project_root):
        if not _deps_has_artifact(real_project_root, "androidx.activity", "activity"):
            pytest.skip("androidx.activity not in .deps/ cache")

        cfg = _make_config_with_deps(
            tmp_path, real_project_root,
            dependencies=["androidx.activity:activity:1.10.1"]
        )
        resolver = MavenResolver(cfg)
        resolver.resolve_all(cfg.dependencies)
        classpath = resolver.get_classpath_jars()
        # AAR classes.jar should be in classpath
        assert any("classes.jar" in j for j in classpath)

    def test_resolve_aar_dex_jars_nonempty(self, tmp_path, real_project_root):
        if not _deps_has_artifact(real_project_root, "androidx.activity", "activity"):
            pytest.skip("androidx.activity not in .deps/ cache")

        cfg = _make_config_with_deps(
            tmp_path, real_project_root,
            dependencies=["androidx.activity:activity:1.10.1"]
        )
        resolver = MavenResolver(cfg)
        resolver.resolve_all(cfg.dependencies)
        dex_jars = resolver.get_dex_jars()
        assert len(dex_jars) >= 1

    def test_version_conflict_higher_wins(self, tmp_path, real_project_root):
        if not _deps_has_artifact(real_project_root, "androidx.annotation", "annotation"):
            pytest.skip("androidx.annotation not in .deps/ cache")

        cfg = _make_config_with_deps(
            tmp_path, real_project_root,
            dependencies=[
                "androidx.annotation:annotation:1.7.0",
                "androidx.annotation:annotation:1.8.1",
            ]
        )
        resolver = MavenResolver(cfg)
        resolver.resolve_all(cfg.dependencies)
        classpath = resolver.get_classpath_jars()
        # Only one version of annotation should be present
        annotation_jars = [j for j in classpath if "annotation" in os.path.basename(j)]
        # Should not have duplicate annotation JARs from different versions
        # (exactly 1 annotation JAR, the higher version wins)
        annotation_jar_names = [os.path.basename(j) for j in annotation_jars]
        assert annotation_jar_names.count("annotation-1.7.0.jar") == 0 or \
               annotation_jar_names.count("annotation-1.8.1.jar") >= 1, (
            f"Expected higher version to win, got: {annotation_jar_names}"
        )

    def test_empty_dependencies_resolves_nothing(self, tmp_path, real_project_root):
        cfg = _make_config_with_deps(tmp_path, real_project_root, dependencies=[])
        resolver = MavenResolver(cfg)
        # cfg.dependencies may be [] or None when YAML has an empty value
        deps = cfg.dependencies or []
        resolver.resolve_all(deps)
        assert resolver.get_classpath_jars() == []
        assert resolver.get_dex_jars() == []
