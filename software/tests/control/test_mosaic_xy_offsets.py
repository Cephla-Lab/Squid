"""The sign round trip of spec C §9 (C2): with XY offsets saved, a feature imaged by the fake microscope,
placed on the Full View mosaic, drawn around and turned into FOVs comes back at the stage position where
the fake's objective centres that feature. The ground truth is the fake's own rendering (snap), never
a production conversion helper: a sign error in the mosaic cannot agree with itself."""

import types

import numpy as np
import pytest

import control._def
from control.core.objective_store import ObjectiveStore
from control.core.scan_coordinates import ScanCoordinates
from control.widgets_mosaic import DisplayMode, UnifiedMosaicWidget
from squid.objective_calibration.synthetic import FakeCalibrationHardware, FakeObjective, FakeScene
from tests.control.test_objective_store_offsets import CAMERA, OBJECTIVES, TURRET, _config

PX_UM = {"4x": 1.625, "20x": 0.325}
PARCENTRIC_20X_UM = (-9.0, 6.0)  # offset_20x of spec C §4, as C1 saves it (test_widgets_objective_offsets)
STAGE_UM = (25000.0, 30000.0)
FRAME = (480, 640)  # rows, cols; the 20x field is 156 x 208 um
TARGET_PX_UM = 0.5  # the mosaic's canvas pixel, so a canvas pixel is 1.5 px at 20x
# The checks must separate the right sign from the wrong one: a sign error misplaces the 20x content by
# 2 * |offset| = 18 um in x. The tolerance covers the canvas quantization (1 px = 0.5 um on each of the
# tile placement and the drawn vertex) and the fake's 0.1 um stage microstep.
TOL_UM = 1.5
SPOT_SIGMA_UM = 3.0


class _SpotScene(FakeScene):
    """One bright Gaussian spot at centre_um on a dark field: its image centroid is the feature."""

    def __init__(self, centre_um):
        super().__init__()
        self.centre_um = np.asarray(centre_um, dtype=float)

    def render(self, ux, uy, sigma_um, px_um):
        r2 = (ux - self.centre_um[0]) ** 2 + (uy - self.centre_um[1]) ** 2
        return np.exp(-0.5 * r2 / SPOT_SIGMA_UM**2)


def _fake(spot_um, objective, stage_um=STAGE_UM):
    objectives = {
        name: FakeObjective(
            name, mag, na, pixel_um=PX_UM[name], parcentric_um=PARCENTRIC_20X_UM if name == "20x" else (0, 0)
        )
        for name, mag, na in (("4x", 4, 0.13), ("20x", 20, 0.8))
    }
    return FakeCalibrationHardware(
        objectives, _SpotScene(spot_um), shape=FRAME, start_objective=objective, start_xy_um=stage_um, noise=0.0
    )


def _centroid_px(image):
    """(col, row) of the brightest spot, to sub-pixel accuracy, from the image alone."""
    image = np.asarray(image, dtype=float)
    r, c = np.unravel_index(int(np.argmax(image)), image.shape)
    window = image[max(r - 8, 0) : r + 9, max(c - 8, 0) : c + 9]
    rows, cols = np.mgrid[
        max(r - 8, 0) : max(r - 8, 0) + window.shape[0], max(c - 8, 0) : max(c - 8, 0) + window.shape[1]
    ]
    weights = window - window.min()
    return (float((cols * weights).sum() / weights.sum()), float((rows * weights).sum() / weights.sum()))


def _store(current="20x"):
    store = ObjectiveStore(objectives_dict=OBJECTIVES, default_objective="4x")
    config = _config("4x", TURRET["4x"], {"20x": (TURRET["20x"], PARCENTRIC_20X_UM[0], PARCENTRIC_20X_UM[1], 0.0)})
    store.set_offset_calibration(config, TURRET, CAMERA, 0.0)
    assert store.offset_validity.xy
    store.set_current_objective(current)
    return store


class _Camera:
    def __init__(self, binned_px_um=6.5):
        self.binned_px_um = binned_px_um

    def get_pixel_size_binned_um(self):
        return self.binned_px_um

    def get_fov_size_mm(self):
        return 1.0  # x the store's factor (0.18 at 20x): a 0.18 mm FOV for the manual-region grid


class _Contrast:
    def get_scaled_limits(self, name, dtype):
        return (0, 65535)

    def update_limits(self, *args, **kwargs):
        pass


class _Stage:
    def get_pos(self):
        return types.SimpleNamespace(x_mm=STAGE_UM[0] / 1000, y_mm=STAGE_UM[1] / 1000, z_mm=1.0)


def _tile(image, stage_um, objective):
    return types.SimpleNamespace(
        image=image,
        x_mm=stage_um[0] / 1000,
        y_mm=stage_um[1] / 1000,
        channel_name="BF",
        well_origin_mm=None,
        well_id="",
        well_row=0,
        well_col=0,
        objective=objective,
        pixel_size_um=PX_UM[objective],
    )


@pytest.fixture
def widget(qtbot, monkeypatch):
    monkeypatch.setattr(control._def, "MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM", TARGET_PX_UM)
    store = _store()
    w = UnifiedMosaicWidget(store, _Camera(), _Contrast())
    qtbot.addWidget(w)
    w.mode = DisplayMode.MOSAIC
    return w, store


def _spot_at(objective, offset_px, stage_um=STAGE_UM):
    """A fake whose spot sits offset_px = (cols, rows) from the centre of `objective`'s image with the stage at
    stage_um, found by rendering: the spot is placed, imaged, and its centroid verified."""
    h, w = FRAME
    centre_px = ((w - 1) / 2, (h - 1) / 2)
    # First guess from the fake's documented geometry (u = -S + parcentric - px * p), then verify by rendering.
    parcentric = PARCENTRIC_20X_UM if objective == "20x" else (0.0, 0.0)
    guess = tuple(-s + c - PX_UM[objective] * d for s, c, d in zip(stage_um, parcentric, offset_px))
    hw = _fake(guess, objective, stage_um)
    image = hw.snap(objective, "BF")
    col, row = _centroid_px(image)
    assert (col - centre_px[0], row - centre_px[1]) == pytest.approx(offset_px, abs=0.3)
    return hw, image


def _stage_where_centred(hw, objective):
    """Ground truth from the rendering: the stage position where `objective` images the spot at its centre,
    found by moving the fake's stage until the centroid sits on the image centre."""
    h, w = FRAME
    hw._objective = objective
    for _ in range(3):  # Newton on a linear map converges at once; iterate for the microstep rounding
        image = hw.snap(objective, "BF")
        col, row = _centroid_px(image)
        error_px = np.array([col - (w - 1) / 2, row - (h - 1) / 2])
        if np.abs(error_px).max() < 0.05:
            break
        x, y = hw.get_xy_um()
        # +col in the image is +x of the stage (the fake's a = -M on u = -S): move the stage by +error
        hw.move_xy_to_um(x + error_px[0] * PX_UM[objective], y + error_px[1] * PX_UM[objective])
    return np.array(hw.get_xy_um())


def _feature_on_canvas_mm(widget):
    """Where the mosaic shows the spot, in its world frame, from the canvas pixels alone."""
    layer = widget.viewer.layers["BF"]
    col, row = _centroid_px(layer.data)
    vps = widget.viewer_pixel_size_mm
    return np.array(
        [widget.top_left_coordinate[1] + (col + 0.5) * vps, widget.top_left_coordinate[0] + (row + 0.5) * vps]
    )


def _draw_square_from(widget, corner_mm, side_mm):
    """A square whose (min x, min y) vertex is corner_mm, drawn as napari would hand it over: canvas world
    coordinates (um), converted by the widget's own shape conversion."""
    vps_um = widget.viewer_pixel_size_mm * 1000
    tl_y, tl_x = widget.top_left_coordinate
    vertices_mm = [corner_mm, corner_mm + (side_mm, 0), corner_mm + (side_mm, side_mm), corner_mm + (0, side_mm)]
    world = [
        [(y - tl_y) / widget.viewer_pixel_size_mm * vps_um, (x - tl_x) / widget.viewer_pixel_size_mm * vps_um]
        for x, y in vertices_mm
    ]
    return widget._convert_shape_to_mm(np.array(world))


def _fov_nearest(scan, target_um):
    fovs = np.array([fov[:2] for fov in scan.region_fov_coordinates["manual"]]) * 1000
    return fovs[np.argmin(np.hypot(*(fovs - target_um).T))]


class TestSignRoundTrip:
    @pytest.mark.parametrize(
        "offset_px", [(0, 0), (80, 120), (-130, -100)], ids=["centre", "off-centre", "off-centre-neg"]
    )
    def test_20x_tile_shape_and_fov(self, widget, offset_px):
        """A feature on a 20x tile, drawn around and acquired at 20x, lands where the fake's 20x centres it."""
        w, store = widget
        hw, image = _spot_at("20x", offset_px)
        truth_um = _stage_where_centred(_fake(hw.scene.centre_um, "20x"), "20x")
        # The reference frame: where the 4x (offset 0) centres the same spot.
        reference_um = _stage_where_centred(_fake(hw.scene.centre_um, "4x"), "4x")
        assert truth_um - reference_um == pytest.approx(PARCENTRIC_20X_UM, abs=0.2)  # the fake's offset_20x

        w.updateTile(_tile(image, STAGE_UM, "20x"))
        drawn_um = _feature_on_canvas_mm(w) * 1000
        assert drawn_um == pytest.approx(reference_um, abs=TOL_UM)  # drawn in the reference objective's frame

        shape_mm = _draw_square_from(w, drawn_um / 1000, side_mm=0.15)
        scan = ScanCoordinates(store, _Stage(), _Camera())
        scan.set_manual_coordinates([shape_mm], overlap_percent=10)
        assert scan.live_drawn_region_ids == {"manual"}
        fov_um = _fov_nearest(scan, truth_um)
        assert fov_um == pytest.approx(truth_um, abs=TOL_UM)
        # Close the loop on the fake: at that FOV the 20x images the spot at its centre.
        hw.move_xy_to_um(*fov_um)
        col, row = _centroid_px(hw.snap("20x", "BF"))
        assert (col, row) == pytest.approx(((FRAME[1] - 1) / 2, (FRAME[0] - 1) / 2), abs=TOL_UM / PX_UM["20x"])

    def test_feature_on_a_4x_tile_acquired_and_clicked_at_20x(self, widget):
        """Across objectives: the overview is the reference's, the acquisition and the double-click are the 20x's."""
        w, store = widget
        # 30 x 25 px off the 4x centre is 150 x 125 px off the 20x centre, inside the 20x field
        hw, image = _spot_at("4x", (30, 25))
        truth_20x_um = _stage_where_centred(_fake(hw.scene.centre_um, "20x"), "20x")
        w.updateTile(_tile(image, STAGE_UM, "4x"))
        drawn_um = _feature_on_canvas_mm(w) * 1000
        assert drawn_um == pytest.approx(
            STAGE_UM + np.array((30, 25)) * PX_UM["4x"], abs=TOL_UM
        )  # the reference tile does not move

        scan = ScanCoordinates(store, _Stage(), _Camera())
        scan.set_manual_coordinates([_draw_square_from(w, drawn_um / 1000, side_mm=0.15)], overlap_percent=10)
        assert _fov_nearest(scan, truth_20x_um) == pytest.approx(truth_20x_um, abs=TOL_UM)

        clicked = []
        w.signal_coordinates_clicked.connect(lambda x, y: clicked.append((x * 1000, y * 1000)))
        col, row = _centroid_px(w.viewer.layers["BF"].data)
        layer = types.SimpleNamespace(world_to_data=lambda position: position)
        w._on_double_click(layer, types.SimpleNamespace(position=(row + 0.5, col + 0.5)))
        assert clicked[-1] == pytest.approx(truth_20x_um, abs=TOL_UM)  # 20x active: the click is where the 20x goes
        store.set_current_objective("4x")
        w._on_double_click(layer, types.SimpleNamespace(position=(row + 0.5, col + 0.5)))
        assert clicked[-1] == pytest.approx(drawn_um, abs=TOL_UM)  # the reference: the click is the stage position

    def test_a_drained_tile_keeps_the_objective_and_binning_it_was_taken_with(self, widget):
        """The tag, not the live store and camera, sets the shift and the scale (spec C §7.3)."""
        w, store = widget
        hw, image = _spot_at("20x", (80, 120))
        reference_um = _stage_where_centred(_fake(hw.scene.centre_um, "4x"), "4x")
        # The user switched to 4x and binned 2x2 before the tile drained.
        store.set_current_objective("4x")
        w.camera.binned_px_um = 13.0
        w.updateTile(_tile(image, STAGE_UM, "20x"))
        assert _feature_on_canvas_mm(w) * 1000 == pytest.approx(reference_um, abs=TOL_UM)
        tile_px = w.viewer.layers["BF"].data.shape
        assert tile_px == (round(FRAME[0] * PX_UM["20x"] / TARGET_PX_UM), round(FRAME[1] * PX_UM["20x"] / TARGET_PX_UM))


class TestWithoutOffsets:
    def test_uncalibrated_store_places_tiles_at_the_stage_position(self, qtbot, monkeypatch):
        monkeypatch.setattr(control._def, "MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM", TARGET_PX_UM)
        store = ObjectiveStore(objectives_dict=OBJECTIVES, default_objective="20x")
        w = UnifiedMosaicWidget(store, _Camera(), _Contrast())
        qtbot.addWidget(w)
        w.mode = DisplayMode.MOSAIC
        hw, image = _spot_at("20x", (0, 0))
        w.updateTile(_tile(image, STAGE_UM, "20x"))
        assert _feature_on_canvas_mm(w) * 1000 == pytest.approx(STAGE_UM, abs=TOL_UM)
        scan = ScanCoordinates(store, _Stage(), _Camera())
        shape = _draw_square_from(w, np.array(STAGE_UM) / 1000, side_mm=0.15)
        scan.set_manual_coordinates([shape], overlap_percent=10)
        assert _fov_nearest(scan, np.array(STAGE_UM)) == pytest.approx(STAGE_UM, abs=TOL_UM)
