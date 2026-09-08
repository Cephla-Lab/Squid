import types

import numpy as np
import pytest

import control._def
from control.widgets_mosaic import DisplayMode, UnifiedMosaicWidget, blit_tiles_to_canvas


class TestCanvasBlit:
    def test_blit_single_tile(self):
        canvas = np.zeros((200, 200), dtype=np.uint16)
        tile = np.ones((50, 50), dtype=np.uint16) * 100
        blit_tiles_to_canvas(canvas, [(tile, 10, 20)])
        assert canvas[10, 20] == 100
        assert canvas[59, 69] == 100
        assert canvas[0, 0] == 0  # Outside tile

    def test_blit_tile_at_canvas_edge(self):
        """Tile extending past canvas edge should be clipped, not crash."""
        canvas = np.zeros((100, 100), dtype=np.uint16)
        tile = np.ones((50, 50), dtype=np.uint16) * 42
        blit_tiles_to_canvas(canvas, [(tile, 80, 80)])
        assert canvas[80, 80] == 42
        assert canvas[99, 99] == 42  # Clipped region

    def test_blit_multiple_tiles(self):
        canvas = np.zeros((200, 400), dtype=np.uint16)
        tile1 = np.ones((100, 100), dtype=np.uint16) * 10
        tile2 = np.ones((100, 100), dtype=np.uint16) * 20
        blit_tiles_to_canvas(canvas, [(tile1, 0, 0), (tile2, 0, 200)])
        assert canvas[50, 50] == 10
        assert canvas[50, 250] == 20

    def test_blit_negative_offset_clips(self):
        """Negative offsets must clip both src+dst, not wrap via NumPy slicing."""
        canvas = np.zeros((100, 100), dtype=np.uint16)
        tile = np.ones((50, 50), dtype=np.uint16) * 7
        # Tile would extend from (-20, -20) to (30, 30); only [0:30, 0:30] should land.
        blit_tiles_to_canvas(canvas, [(tile, -20, -20)])
        assert canvas[0, 0] == 7
        assert canvas[29, 29] == 7
        assert canvas[30, 30] == 0  # outside the visible portion
        # The far end of the canvas must be untouched (no NumPy wrap-around).
        assert canvas[99, 99] == 0

    def test_blit_fully_outside_is_noop(self):
        canvas = np.zeros((100, 100), dtype=np.uint16)
        tile = np.ones((50, 50), dtype=np.uint16) * 7
        blit_tiles_to_canvas(canvas, [(tile, -100, -100), (tile, 200, 200)])
        assert canvas.sum() == 0

    def test_display_mode_values(self):
        assert DisplayMode.MOSAIC.value == "mosaic"
        assert DisplayMode.PLATE.value == "plate"


class _FakeObjectiveStore:
    def __init__(self, factor):
        self.factor = factor

    def get_pixel_size_factor(self):
        return self.factor


class _FakeCamera:
    def get_pixel_size_binned_um(self):
        return 1.0  # live_pixel_size_um == objective factor


class _FakeContrast:
    def get_scaled_limits(self, name, dtype):
        return (0, 255)

    def update_limits(self, *args, **kwargs):
        pass


def _tile_update(image, x_mm, y_mm, channel="BF", **extra):
    u = types.SimpleNamespace(
        image=image,
        x_mm=x_mm,
        y_mm=y_mm,
        channel_name=channel,
        well_origin_mm=None,
        well_id=None,
        well_row=0,
        well_col=0,
    )
    for k, v in extra.items():
        setattr(u, k, v)
    return u


@pytest.fixture
def mosaic_widget(qtbot, monkeypatch):
    monkeypatch.setattr(control._def, "MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM", 2.0)
    obj = _FakeObjectiveStore(factor=1.85)
    widget = UnifiedMosaicWidget(obj, _FakeCamera(), _FakeContrast())
    qtbot.addWidget(widget)
    widget.mode = DisplayMode.MOSAIC
    return widget, obj


class TestFullViewMagnificationPersistence:
    def test_persists_tiles_across_magnification_change(self, mosaic_widget):
        widget, obj = mosaic_widget
        widget.updateTile(_tile_update(np.full((100, 100), 200, dtype=np.uint8), 10.0, 10.0))
        assert widget.viewer_pixel_size_mm == pytest.approx(0.002)
        low_nonzero = int(np.count_nonzero(widget.viewer.layers["BF"].data))
        assert low_nonzero > 0

        # Higher magnification, different location.
        obj.factor = 0.37
        widget.updateTile(_tile_update(np.full((100, 100), 150, dtype=np.uint8), 12.0, 12.0))

        assert widget.layers_initialized is True
        assert widget.viewer_pixel_size_mm == pytest.approx(0.002)  # constant, no re-init
        total_nonzero = int(np.count_nonzero(widget.viewer.layers["BF"].data))
        assert total_nonzero > low_nonzero  # low-mag tile retained AND high-mag tile added

    def test_clears_when_target_pixel_size_changes(self, mosaic_widget, monkeypatch):
        widget, obj = mosaic_widget
        widget.updateTile(_tile_update(np.full((100, 100), 200, dtype=np.uint8), 10.0, 10.0))
        assert widget.viewer_pixel_size_mm == pytest.approx(0.002)

        monkeypatch.setattr(control._def, "MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM", 5.0)
        widget.updateTile(_tile_update(np.full((100, 100), 150, dtype=np.uint8), 30.0, 30.0))

        assert widget.viewer_pixel_size_mm == pytest.approx(0.005)  # re-init at new target
        # Only the new tile remains (round(100 * 1.85 / 5) == 37), not a canvas spanning 10..30 mm.
        assert widget.viewer.layers["BF"].data.shape == (37, 37)

    def test_plate_view_still_uses_integer_downsample(self, qtbot, monkeypatch):
        monkeypatch.setattr(control._def, "MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM", 2.0)
        obj = _FakeObjectiveStore(factor=0.74)  # int(round(2/0.74)) == 3 -> 2.22 um
        widget = UnifiedMosaicWidget(obj, _FakeCamera(), _FakeContrast())
        qtbot.addWidget(widget)
        widget.mode = DisplayMode.PLATE
        widget.setPlateLayout(
            types.SimpleNamespace(
                num_rows=2, num_cols=2, well_slot_shape=(200, 200), fov_grid_shape=(1, 1), well_ids=["A1"]
            )
        )
        widget.updateTile(
            _tile_update(
                np.full((100, 100), 200, dtype=np.uint8),
                10.0,
                10.0,
                well_origin_mm=(10.0, 10.0),
                well_id="A1",
                well_row=0,
                well_col=0,
            )
        )
        # Integer factor 3 -> 2.22 um, NOT the exact target 2.0 um.
        assert widget.viewer_pixel_size_mm == pytest.approx(0.00222, abs=1e-5)


class TestRGBMosaicSave:
    """Plan R7's overview half: RGB layers are saved as colored PNGs with the
    rgb_channel_names / rgb_view_files sidecar keys older Squid wrote, which
    SquidXplorer's stain-color reconstruction consumes."""

    @pytest.fixture
    def save_flags(self, monkeypatch):
        monkeypatch.setattr(control._def, "SAVE_DOWNSAMPLED_OVERVIEW", True)
        monkeypatch.setattr(control._def, "SAVE_DOWNSAMPLED_WELL_IMAGES", False)

    def test_snapshot_collects_rgb_alongside_mono(self, mosaic_widget, save_flags):
        widget, _ = mosaic_widget
        widget.updateTile(_tile_update(np.full((100, 100), 200, dtype=np.uint8), 10.0, 10.0))
        rgb = np.zeros((50, 50, 3), dtype=np.uint8)
        rgb[..., 0] = 255
        widget.viewer.add_image(rgb, rgb=True, name="BF LED matrix full RGB")

        snapshot = widget._snapshot_for_save()
        assert snapshot is not None
        assert [name for name, _ in snapshot["channels"]] == ["BF"]
        assert [name for name, _ in snapshot["rgb_channels"]] == ["BF LED matrix full RGB"]
        # The mono path is untouched: mono arrays stay 2-D.
        assert snapshot["channels"][0][1].ndim == 2

    def test_write_saves_png_and_sidecar_keys(self, mosaic_widget, save_flags, tmp_path):
        import imageio
        import yaml

        widget, _ = mosaic_widget
        widget.updateTile(_tile_update(np.full((100, 100), 200, dtype=np.uint8), 10.0, 10.0))
        rgb = np.zeros((50, 50, 3), dtype=np.uint8)
        rgb[..., 1] = 128
        widget.viewer.add_image(rgb, rgb=True, name="BF LED matrix full")

        snapshot = widget._snapshot_for_save()
        target = tmp_path / "mosaic_view"
        widget._write_save_snapshot(str(target), snapshot)

        png = target / "mosaic_mosaic_2um_BF_LED_matrix_full.png"
        assert png.is_file()
        assert (target / "mosaic_mosaic_2um.ome.tiff").is_file()  # mono overview untouched
        with open(target / "mosaic_mosaic_2um.yaml") as f:
            sidecar = yaml.safe_load(f)
        assert sidecar["channel_names"] == ["BF"]
        assert sidecar["rgb_channel_names"] == ["BF LED matrix full"]
        assert sidecar["rgb_view_files"] == ["mosaic_mosaic_2um_BF_LED_matrix_full.png"]
        # The PNG round-trips as 8-bit RGB with the layer's pixels.
        back = imageio.imread(png)
        assert back.dtype == np.uint8
        assert back.shape == (50, 50, 3)
        assert back[0, 0, 1] == 128

    def test_rgb_only_snapshot_still_saves(self, mosaic_widget, save_flags, tmp_path):
        import imageio
        import yaml

        widget, _ = mosaic_widget
        widget.viewer_pixel_size_mm = 0.002
        rgb = (np.ones((20, 20, 3)) * 65535).astype(np.uint16)  # RGB48 canvas
        widget.viewer.add_image(rgb, rgb=True, name="BF LED matrix full RGB")

        snapshot = widget._snapshot_for_save()
        assert snapshot is not None  # no-layers early return needs BOTH lists empty
        target = tmp_path / "mosaic_view"
        widget._write_save_snapshot(str(target), snapshot)

        png = target / "mosaic_mosaic_2um_BF_LED_matrix_full_RGB.png"
        assert png.is_file()
        assert not (target / "mosaic_mosaic_2um.ome.tiff").exists()  # no mono channels
        with open(target / "mosaic_mosaic_2um.yaml") as f:
            sidecar = yaml.safe_load(f)
        assert sidecar["channel_names"] == []
        assert sidecar["rgb_view_files"] == ["mosaic_mosaic_2um_BF_LED_matrix_full_RGB.png"]
        back = imageio.imread(png)
        assert back.dtype == np.uint8
        assert int(back.max()) == 255  # uint16 full-scale maps to 255

    def test_mono_only_sidecar_carries_no_rgb_keys(self, mosaic_widget, save_flags, tmp_path):
        import yaml

        widget, _ = mosaic_widget
        widget.updateTile(_tile_update(np.full((100, 100), 200, dtype=np.uint8), 10.0, 10.0))
        snapshot = widget._snapshot_for_save()
        target = tmp_path / "mosaic_view"
        widget._write_save_snapshot(str(target), snapshot)
        with open(target / "mosaic_mosaic_2um.yaml") as f:
            sidecar = yaml.safe_load(f)
        assert "rgb_channel_names" not in sidecar
        assert "rgb_view_files" not in sidecar
        assert "rgb_channels" not in sidecar  # arrays never leak into the sidecar
