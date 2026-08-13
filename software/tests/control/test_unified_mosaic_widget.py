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


class TestContrastManagerWriteBack:
    """The mosaic renders at mosaic_dtype - latched from whichever tile arrives first, which
    on a dual-camera run may be the other camera's depth. It must not write that view's
    numbers back over a channel's stored limits.
    """

    @pytest.fixture
    def widget_with_real_contrast(self, qtbot, monkeypatch):
        from control.core.contrast_manager import ContrastManager

        monkeypatch.setattr(control._def, "MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM", 2.0)
        contrast = ContrastManager()
        widget = UnifiedMosaicWidget(_FakeObjectiveStore(factor=1.85), _FakeCamera(), contrast)
        qtbot.addWidget(widget)
        widget.mode = DisplayMode.MOSAIC
        return widget, contrast

    def test_derived_limits_are_not_written_back_to_the_manager(self, widget_with_real_contrast):
        """A uint16 channel's contrast selection must survive being displayed in a uint8
        mosaic. Without the event blocker the assignment echoed through _on_contrast_change
        and stored the uint8 equivalent, so (800, 1600) came back as (3.1, 6.2)."""
        widget, contrast = widget_with_real_contrast
        contrast.update_limits("BF", 800.0, 1600.0, dtype=np.uint16)

        # A uint8 tile latches mosaic_dtype to uint8, so the displayed limits are rescaled.
        widget.updateTile(_tile_update(np.full((100, 100), 200, dtype=np.uint8), 10.0, 10.0))
        assert widget.mosaic_dtype == np.uint8

        assert contrast.contrast_limits["BF"] == (800.0, 1600.0), "the mosaic overwrote the channel's limits"
        assert contrast.limit_dtypes["BF"] == np.dtype(np.uint16), "the channel was re-anchored to the view's dtype"
        # The layer itself still shows the rescaled values, so the view looks right.
        assert tuple(widget.viewer.layers["BF"].contrast_limits) == pytest.approx((3.11, 6.22), abs=0.01)

    def test_a_user_drag_in_the_mosaic_is_recorded_with_the_view_dtype(self, widget_with_real_contrast):
        """A real contrast change here is expressed in mosaic_dtype, so it must be labelled
        that way - otherwise it would later be read as if it were the channel's own depth."""
        widget, contrast = widget_with_real_contrast
        widget.updateTile(_tile_update(np.full((100, 100), 200, dtype=np.uint8), 10.0, 10.0))

        widget.viewer.layers["BF"].contrast_limits = (20.0, 240.0)  # unblocked: a user drag

        assert contrast.contrast_limits["BF"] == (20.0, 240.0)
        assert contrast.limit_dtypes["BF"] == np.dtype(np.uint8)
        # ...and read back at the channel's acquisition depth it converts, rather than being
        # taken literally as uint16 values.
        converted = contrast.get_limits_for_dtype("BF", np.uint16)
        assert converted == pytest.approx((5140.0, 61680.0), rel=0.01)


class TestRgbSaveAsPng:
    """Colour layers are saved as PNG.

    They cannot join the overview's (C, Y, X) OME-TIFF stack - a colour plane is
    (H, W, 3) - so they are written alongside it instead of being dropped.
    """

    @staticmethod
    def _snapshot(mono, rgb, per_well=False, plate=None):
        snapshot = {
            "mode": "plate",
            "resolution_um": 5.0,
            "channels": mono,
            "rgb_channels": rgb,
            "saved_at": "now",
            "save_overview": True,
            "save_per_well": per_well,
        }
        if plate:
            snapshot["plate"] = plate
        return snapshot

    @staticmethod
    def _sidecar(target):
        import yaml

        name = next(p for p in target.iterdir() if p.suffix == ".yaml")
        return yaml.safe_load(name.read_text())

    def test_rgb_layer_is_written_as_png_beside_the_mono_tiff(self, mosaic_widget, tmp_path):
        widget, _ = mosaic_widget
        mono = [("Fluorescence 405 nm Ex", np.zeros((8, 12), np.uint16))]
        rgb = [("BF LED matrix full", np.zeros((8, 12, 3), np.uint8))]

        widget._write_save_snapshot(str(tmp_path), self._snapshot(mono, rgb))

        pngs = sorted(p.name for p in tmp_path.glob("*.png"))
        assert pngs == ["mosaic_plate_5um_BF_LED_matrix_full.png"]
        assert list(tmp_path.glob("*.ome.tiff")), "the mono stack must still be written"
        sidecar = self._sidecar(tmp_path)
        assert sidecar["rgb_channel_names"] == ["BF LED matrix full"]
        assert sidecar["rgb_view_files"] == pngs

    def test_colour_only_acquisition_still_saves(self, mosaic_widget, tmp_path):
        """Previously this produced nothing: no mono layers meant the save was skipped."""
        widget, _ = mosaic_widget
        rgb = [("BF LED matrix full", np.zeros((8, 12, 3), np.uint8))]

        widget._write_save_snapshot(str(tmp_path), self._snapshot([], rgb))

        assert list(tmp_path.glob("*.png"))
        assert not list(tmp_path.glob("*.ome.tiff")), "nothing to stack, so no TIFF"
        assert self._sidecar(tmp_path)["channel_names"] == []

    def test_mono_only_save_is_unchanged(self, mosaic_widget, tmp_path):
        widget, _ = mosaic_widget
        mono = [("Fluorescence 405 nm Ex", np.zeros((8, 12), np.uint16))]

        widget._write_save_snapshot(str(tmp_path), self._snapshot(mono, []))

        assert list(tmp_path.glob("*.ome.tiff"))
        assert not list(tmp_path.glob("*.png"))
        sidecar = self._sidecar(tmp_path)
        assert "rgb_channel_names" not in sidecar
        assert "rgb_view_files" not in sidecar

    def test_png_keeps_channel_order(self, mosaic_widget, tmp_path):
        """squid holds colour as RGB and cv2 writes BGR, so the conversion must happen."""
        import cv2

        widget, _ = mosaic_widget
        image = np.zeros((3, 4, 3), dtype=np.uint8)
        image[0, :] = (255, 0, 0)  # red
        image[2, :] = (0, 0, 255)  # blue

        widget._write_save_snapshot(str(tmp_path), self._snapshot([], [("BF", image)]))

        written = next(tmp_path.glob("*.png"))
        read_back = cv2.cvtColor(cv2.imread(str(written), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        assert np.array_equal(read_back, image), "red and blue must not be swapped"

    def test_per_well_writes_a_png_per_well(self, mosaic_widget, tmp_path):
        widget, _ = mosaic_widget
        mono = [("Fluorescence 405 nm Ex", np.zeros((8, 12), np.uint16))]
        rgb = [("BF LED matrix full", np.zeros((8, 12, 3), np.uint8))]
        plate = {"well_slot_shape_px": [4, 6], "well_ids": ["A1", "A2"]}

        widget._write_save_snapshot(str(tmp_path), self._snapshot(mono, rgb, per_well=True, plate=plate))

        wells = tmp_path / "wells"
        assert sorted(p.name for p in wells.glob("*.png")) == [
            "A1_5um_BF_LED_matrix_full.png",
            "A2_5um_BF_LED_matrix_full.png",
        ]
        assert sorted(p.name for p in wells.glob("*.tiff")) == ["A1_5um.tiff", "A2_5um.tiff"]
