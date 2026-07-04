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
