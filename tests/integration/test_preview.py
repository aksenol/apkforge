"""Integration tests: Compose @Preview rendering to PNG."""
import struct
import pathlib
import pytest
import sys

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
import build as build_module

pytestmark = pytest.mark.integration

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _read_png_dimensions(png_path):
    """Parse PNG IHDR chunk to get width and height."""
    with open(png_path, "rb") as f:
        sig = f.read(8)
        assert sig == PNG_MAGIC, "Not a PNG file"
        # Read IHDR chunk: 4 bytes length, 4 bytes type, data, 4 bytes CRC
        length = struct.unpack(">I", f.read(4))[0]
        chunk_type = f.read(4)
        assert chunk_type == b"IHDR", f"Expected IHDR, got {chunk_type}"
        width = struct.unpack(">I", f.read(4))[0]
        height = struct.unpack(">I", f.read(4))[0]
    return width, height


@pytest.fixture(scope="module")
def preview_pngs(compose_app_project, compose_app_config):
    """Run preview pipeline and return list of PNG paths."""
    layoutlib_link = compose_app_project / ".layoutlib"
    if not layoutlib_link.exists():
        pytest.skip(".layoutlib not present; skipping preview tests")

    layoutlib_mgr = build_module.LayoutlibManager(compose_app_config)
    layoutlib_mgr.setup()  # no-op if already downloaded via symlink

    builder = build_module.Builder(compose_app_config)
    builder.preview(layoutlib_mgr)

    preview_dir = pathlib.Path(compose_app_config.preview_output_dir)
    return list(preview_dir.glob("*.png"))


class TestPreviewFunctionDiscovery:
    def test_finds_main_preview(self, compose_app_config):
        builder = build_module.Builder(compose_app_config)
        fqns = builder._find_preview_functions()
        assert "com.example.composetest.MainActivityKt.MainPreview" in fqns

    def test_preview_count(self, compose_app_config):
        builder = build_module.Builder(compose_app_config)
        fqns = builder._find_preview_functions()
        assert len(fqns) >= 1


class TestPreviewOutput:
    def test_at_least_one_png_produced(self, preview_pngs):
        assert len(preview_pngs) >= 1, "No PNG files produced by preview renderer"

    def test_png_filename_corresponds_to_function(self, preview_pngs):
        names = [p.stem for p in preview_pngs]
        assert "MainPreview" in names, (
            f"Expected MainPreview.png in output, got: {names}"
        )

    def test_png_files_are_valid_images(self, preview_pngs):
        for png_path in preview_pngs:
            with open(png_path, "rb") as f:
                magic = f.read(8)
            assert magic == PNG_MAGIC, f"{png_path} is not a valid PNG"

    def test_png_dimensions_nonzero(self, preview_pngs):
        for png_path in preview_pngs:
            w, h = _read_png_dimensions(str(png_path))
            assert w > 0, f"{png_path} has zero width"
            assert h > 0, f"{png_path} has zero height"

    def test_png_files_nontrivial_size(self, preview_pngs):
        for png_path in preview_pngs:
            size = png_path.stat().st_size
            assert size > 1024, (
                f"{png_path} is too small ({size} bytes), likely an empty render"
            )
