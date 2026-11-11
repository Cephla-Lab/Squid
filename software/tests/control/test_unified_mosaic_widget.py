import types

import numpy as np
import pytest

import control._def
from control.core.multi_point_utils import MosaicTileUpdate
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
        z_index=0,
        time_point=0,
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


class TestZProjection:
    def test_mip_keeps_the_brighter_plane(self, mosaic_widget, monkeypatch):
        monkeypatch.setattr(control._def, "MOSAIC_Z_PROJECTION", control._def.ZProjectionMode.MIP)
        widget, _ = mosaic_widget
        widget.updateTile(_tile_update(np.full((100, 100), 200, dtype=np.uint8), 10.0, 10.0, z_index=0))
        widget.updateTile(_tile_update(np.full((100, 100), 50, dtype=np.uint8), 10.0, 10.0, z_index=1))
        assert int(widget.viewer.layers["BF"].data.max()) == 200
        widget.updateTile(_tile_update(np.full((100, 100), 230, dtype=np.uint8), 10.0, 10.0, z_index=2))
        assert int(widget.viewer.layers["BF"].data.max()) == 230

    def test_last_plane_overwrites(self, mosaic_widget, monkeypatch):
        monkeypatch.setattr(control._def, "MOSAIC_Z_PROJECTION", control._def.ZProjectionMode.LAST_PLANE)
        widget, _ = mosaic_widget
        widget.updateTile(_tile_update(np.full((100, 100), 200, dtype=np.uint8), 10.0, 10.0, z_index=0))
        widget.updateTile(_tile_update(np.full((100, 100), 50, dtype=np.uint8), 10.0, 10.0, z_index=1))
        assert int(widget.viewer.layers["BF"].data.max()) == 50

    def test_first_plane_of_the_next_timepoint_replaces(self, mosaic_widget, monkeypatch):
        monkeypatch.setattr(control._def, "MOSAIC_Z_PROJECTION", control._def.ZProjectionMode.MIP)
        widget, _ = mosaic_widget
        widget.updateTile(_tile_update(np.full((100, 100), 200, dtype=np.uint8), 10.0, 10.0, z_index=0, time_point=0))
        widget.updateTile(_tile_update(np.full((100, 100), 50, dtype=np.uint8), 10.0, 10.0, z_index=0, time_point=1))
        assert int(widget.viewer.layers["BF"].data.max()) == 50

    def test_blit_max_combines_only_the_overlap(self):
        canvas = np.zeros((100, 100), dtype=np.uint8)
        canvas[0:50, 0:50] = 100
        blit_tiles_to_canvas(canvas, [(np.full((50, 50), 30, dtype=np.uint8), 0, 0)], combine="max")
        assert int(canvas[0:50, 0:50].max()) == 100 and int(canvas[0:50, 0:50].min()) == 100
        blit_tiles_to_canvas(canvas, [(np.full((50, 50), 120, dtype=np.uint8), 0, 0)], combine="max")
        assert int(canvas[0:50, 0:50].min()) == 120
        assert int(canvas[50:, :].max()) == 0 and int(canvas[:, 50:].max()) == 0

    def test_blit_rejects_unknown_combine(self):
        with pytest.raises(ValueError):
            blit_tiles_to_canvas(
                np.zeros((10, 10), dtype=np.uint8), [(np.ones((2, 2), dtype=np.uint8), 0, 0)], combine="sum"
            )


def test_z_projection_enum_round_trips():
    assert control._def.ZProjectionMode.convert_to_enum("last_plane") is control._def.ZProjectionMode.LAST_PLANE
    with pytest.raises(ValueError):
        control._def.ZProjectionMode.convert_to_enum("middle")


def test_mosaic_tile_update_carries_z_and_time():
    img = np.zeros((4, 4), dtype=np.uint8)
    u = MosaicTileUpdate(image=img, x_mm=0, y_mm=0, channel_name="BF", z_index=3, time_point=2)
    assert (u.z_index, u.time_point) == (3, 2)
    d = MosaicTileUpdate(image=img, x_mm=0, y_mm=0, channel_name="BF")
    assert (d.z_index, d.time_point) == (0, 0)


def test_write_save_snapshot_emits_view_saved(mosaic_widget, qtbot, tmp_path):
    widget, _ = mosaic_widget
    widget.updateTile(_tile_update(np.full((100, 100), 200, dtype=np.uint8), 10.0, 10.0))
    target = str(tmp_path / "v")
    with qtbot.waitSignal(widget.signal_view_saved, timeout=10000) as blocker:
        widget._dispatch_save(target)
    assert blocker.args == [target]
