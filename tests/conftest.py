import sys
import os
import shutil
import pathlib
import pytest

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
import build as build_module


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires Android SDK")
    config.addinivalue_line("markers", "network: requires internet access")


@pytest.fixture(scope="session")
def real_project_root():
    return PROJECT_ROOT


def _setup_fixture_project(tmp_path_factory, real_project_root, fixture_name, extra_links=()):
    """Copy fixture dir to tmp, symlink SDK/kotlin/deps."""
    fixture_src = real_project_root / "tests" / "fixtures" / fixture_name
    dest = tmp_path_factory.mktemp(fixture_name)
    shutil.copytree(str(fixture_src), str(dest), dirs_exist_ok=True)
    links = {
        ".android-sdk": real_project_root / ".android-sdk",
        ".kotlin":      real_project_root / ".kotlin",
        ".deps":        real_project_root / ".deps",
    }
    for name in extra_links:
        links[name] = real_project_root / name
    for link_name, target in links.items():
        link = dest / link_name
        if target.exists() and not link.exists():
            link.symlink_to(target, target_is_directory=True)
    real_ks = real_project_root / "debug.keystore"
    if real_ks.exists():
        shutil.copy2(str(real_ks), str(dest / "debug.keystore"))
    return dest


@pytest.fixture(scope="session")
def minimal_java_project(tmp_path_factory, real_project_root):
    return _setup_fixture_project(tmp_path_factory, real_project_root, "minimal_java")


@pytest.fixture(scope="session")
def minimal_java_config(minimal_java_project):
    return build_module.Config(str(minimal_java_project / "build.yaml"))


@pytest.fixture(scope="session")
def kotlin_app_project(tmp_path_factory, real_project_root):
    return _setup_fixture_project(tmp_path_factory, real_project_root, "kotlin_app")


@pytest.fixture(scope="session")
def kotlin_app_config(kotlin_app_project):
    return build_module.Config(str(kotlin_app_project / "build.yaml"))


@pytest.fixture(scope="session")
def compose_app_project(tmp_path_factory, real_project_root):
    return _setup_fixture_project(
        tmp_path_factory, real_project_root, "compose_app",
        extra_links=[".layoutlib"]
    )


@pytest.fixture(scope="session")
def compose_app_config(compose_app_project):
    return build_module.Config(str(compose_app_project / "build.yaml"))
