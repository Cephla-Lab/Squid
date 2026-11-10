"""The laser AF settings panel, live spot overlay and sweep fit, against a simulated microscope."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

import control.core.core as core
import control.microscope
from control import widgets
from control._def import LaserAFConfirmMotionMode, SpotDetectionMode
from control.core.laser_auto_focus_controller import LaserAutofocusController, SweepSample
from control.core.live_controller import LiveController
from control.models import LaserAFConfig


@pytest.fixture
def rig(qtbot, monkeypatch):
    """Settings panel + focus image window + overlay, wired the way gui_hcs wires them."""
    scope = control.microscope.Microscope.build_from_global_config(True, skip_init=True)
    live = LiveController(microscope=scope, camera=scope.addons.camera_focus)
    # keep the test's crops and settings out of the checkout's user profile
    monkeypatch.setattr(scope.config_repo, "save_laser_af_config", lambda *args: None)
    monkeypatch.setattr(scope.config_repo, "get_laser_af_config", lambda *args, **kwargs: None)
    controller = LaserAutofocusController(
        microcontroller=scope.low_level_drivers.microcontroller,
        camera=scope.addons.camera_focus,
        liveController=live,
        stage=scope.stage,
        piezo=None,
        objectiveStore=scope.objective_store,
    )
    controller.initialize_manual(
        LaserAFConfig(
            x_offset=200,
            y_offset=100,
            width=800,
            height=256,
            pixel_to_um=0.5,
            spot_detection_mode=SpotDetectionMode.DUAL_RIGHT,
            filter_sigma=None,
        )
    )
    panel = widgets.LaserAutofocusSettingWidget(core.QtStreamHandler(), live, controller, stretch=False)
    qtbot.addWidget(panel)
    window = core.ImageDisplayWindow(liveController=live)
    qtbot.addWidget(window)
    overlay = widgets.LaserAFSpotOverlay(controller, window, rate_hz=20)
    overlay.signal_spot_detected.connect(panel.on_live_spot_detected)
    overlay.signal_detection_result.connect(panel.on_live_detection_result)
    overlay.signal_status.connect(panel.show_live_detection_status)
    yield SimpleNamespace(scope=scope, controller=controller, panel=panel, window=window, overlay=overlay)
    scope.close()


def frame_with_blocks(camera, blocks):
    """A frame the size of the camera's current crop with uniform (x, y, width, height) blocks on black."""
    _, _, width, height = camera.get_region_of_interest()
    image = np.zeros((height, width), dtype=np.uint8)
    for x, y, w, h in blocks:
        image[y : y + h, x : x + w] = 200
    return image


def test_panel_shows_the_stored_settings(rig):
    panel = rig.panel

    assert panel.spinboxes["cc_threshold"].value() == rig.controller.laser_af_properties.cc_threshold
    assert panel.spinboxes["width"].value() == 800
    assert panel.spot_mode_combo.currentData() == SpotDetectionMode.DUAL_RIGHT
    assert panel.spot_mode_combo.currentText() == "Rightmost spot"
    assert {panel.spot_mode_combo.itemData(i) for i in range(panel.spot_mode_combo.count())} == set(SpotDetectionMode)
    assert panel.confirm_mode_combo.currentData() == LaserAFConfirmMotionMode.OFF


def test_apply_settings_writes_detection_and_search_settings_without_losing_the_calibration(rig):
    panel, controller = rig.panel, rig.controller
    panel.spinboxes["cc_threshold"].setValue(21)
    panel.spinboxes["cc_min_area"].setValue(12)
    panel.spinboxes["laser_af_search_step_um"].setValue(2.5)
    panel.spot_mode_combo.setCurrentIndex(panel.spot_mode_combo.findData(SpotDetectionMode.SINGLE))
    panel.confirm_mode_combo.setCurrentIndex(panel.confirm_mode_combo.findData(LaserAFConfirmMotionMode.SEARCH_ONLY))
    panel.iterative_correction_checkbox.setChecked(True)

    panel.update_threshold_settings()

    properties = controller.laser_af_properties
    assert (properties.cc_threshold, properties.cc_min_area) == (21, 12)
    assert properties.laser_af_search_step_um == 2.5
    assert properties.spot_detection_mode == SpotDetectionMode.SINGLE
    assert properties.confirm_motion_mode == LaserAFConfirmMotionMode.SEARCH_ONLY
    assert properties.iterative_correction_enabled is True
    assert properties.pixel_to_um == 0.5
    assert controller.is_initialized


def test_apply_crop_reprograms_the_camera_and_reports_to_the_control_widget(rig, qtbot):
    panel, controller = rig.panel, rig.controller
    panel.spinboxes["x_offset"].setValue(403)  # snapped down to the camera's 8 px grid
    panel.spinboxes["y_offset"].setValue(60)
    panel.spinboxes["width"].setValue(640)
    panel.spinboxes["height"].setValue(200)

    with qtbot.waitSignal(panel.signal_apply_settings, timeout=1000):
        panel.apply_crop()

    assert controller.camera.get_region_of_interest() == (400, 60, 640, 200)
    assert panel.spinboxes["x_offset"].value() == 400
    assert controller.laser_af_properties.has_reference is False


def test_reset_crop_centres_a_default_crop_on_the_sensor(rig):
    sensor_width, sensor_height = rig.controller.get_sensor_size()

    rig.panel.reset_crop_to_default()

    x, y, width, height = rig.controller.camera.get_region_of_interest()
    assert (width, height) == (min(1000, sensor_width), min(1000, sensor_height))
    assert abs((x + width / 2) - sensor_width / 2) <= 8
    assert abs((y + height / 2) - sensor_height / 2) <= 2


def test_drawn_box_fills_the_crop_fields_in_sensor_coordinates(rig):
    panel = rig.panel
    panel.select_crop_button.setChecked(True)

    panel.on_crop_selection_changed(50, 20, 300, 120)

    # the displayed frame starts at the camera crop's offset (200, 100)
    assert panel.spinboxes["x_offset"].value() == 250
    assert panel.spinboxes["y_offset"].value() == 120
    assert (panel.spinboxes["width"].value(), panel.spinboxes["height"].value()) == (300, 120)
    # nothing is applied until Apply Crop
    assert rig.controller.camera.get_region_of_interest() == (200, 100, 800, 256)


def test_overlay_marks_the_selected_spot_and_lets_the_crop_be_centred_on_it(rig):
    camera = rig.controller.camera
    frame = frame_with_blocks(camera, [(100, 123, 11, 11), (600, 123, 11, 11)])
    rig.overlay.set_enabled(True)

    rig.window.display_image(frame)
    rig.overlay.on_frame(frame)

    # both candidates are drawn, and the rightmost (the configured mode) is the selected one
    assert len(rig.window.spot_candidates_item.getData()[0]) == 2
    selected_x, selected_y = rig.window.spot_selected_item.getData()
    assert selected_x[0] == pytest.approx(605) and selected_y[0] == pytest.approx(128)
    assert rig.panel.live_detection_status_label.text() == ""
    assert rig.panel.center_crop_button.isEnabled()

    rig.panel.center_crop_on_last_detection()

    # crop centred on sensor x = 200 + 605, y = 100 + 128, snapped to the 8 x 2 grid
    assert camera.get_region_of_interest() == (400, 100, 800, 256)


def test_overlay_reports_why_a_frame_fails(rig):
    rig.controller.laser_af_properties = rig.controller.laser_af_properties.model_copy(
        update={"spot_detection_mode": SpotDetectionMode.SINGLE}
    )
    frame = frame_with_blocks(rig.controller.camera, [(100, 123, 11, 11), (600, 123, 11, 11)])
    rig.overlay.set_enabled(True)
    statuses = []
    rig.overlay.signal_status.connect(statuses.append)

    rig.window.display_image(frame)
    rig.overlay.on_frame(frame)

    assert statuses and statuses[-1]  # a non-empty reason
    assert rig.panel.live_detection_status_label.text() == statuses[-1]


def test_disabling_the_overlay_clears_the_markers(rig):
    frame = frame_with_blocks(rig.controller.camera, [(600, 123, 11, 11)])
    rig.overlay.set_enabled(True)
    rig.window.display_image(frame)
    rig.overlay.on_frame(frame)
    assert len(rig.window.spot_selected_item.getData()[0]) == 1

    rig.overlay.set_enabled(False)

    assert len(rig.window.spot_selected_item.getData()[0]) == 0


def test_roi_selection_is_confined_to_the_displayed_frame(rig, qtbot):
    window = rig.window
    assert window.start_roi_selection() is False  # nothing displayed yet

    window.display_image(np.zeros((256, 800), dtype=np.uint8))
    reported = []
    window.signal_roi_bounds_changed.connect(lambda *bounds: reported.append(bounds))
    assert window.start_roi_selection()

    # the 1000 px default is clamped to the 800 x 256 frame
    assert reported[-1] == (0, 0, 800, 256)

    # an explicit box that runs off the frame is pulled back inside it
    assert window.start_roi_selection(x=700, y=200, width=300, height=100)
    assert reported[-1] == (500, 156, 300, 100)

    window.stop_roi_selection()
    assert not window.ROI.isVisible()


def test_false_color_lut_can_be_set_and_cleared(rig):
    window = rig.window
    window.display_image(np.zeros((64, 64), dtype=np.uint8))

    for name in core.ImageDisplayWindow.FALSE_COLOR_LUTS:
        window.set_false_color_lut(name)
    window.set_false_color_lut("inferno")
    assert window.graphics_widget.img.lut is not None

    window.set_false_color_lut("Grayscale")
    assert window.graphics_widget.img.lut is None


def test_control_widget_refuses_to_move_during_an_acquisition(rig, qtbot, monkeypatch):
    acquiring = MagicMock()
    acquiring.acquisition_in_progress.return_value = True
    control_widget = widgets.LaserAutofocusControlWidget(
        rig.controller, rig.panel.liveController, multipointController=acquiring
    )
    qtbot.addWidget(control_widget)
    warnings = []
    monkeypatch.setattr(widgets.QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args))
    rig.controller.move_to_target = MagicMock()
    rig.controller.measure_displacement = MagicMock()

    control_widget.move_to_target()
    control_widget.on_measure_displacement_clicked()

    rig.controller.move_to_target.assert_not_called()
    rig.controller.measure_displacement.assert_not_called()
    assert len(warnings) == 2


def test_control_widget_restores_live_when_the_measurement_raises(rig, qtbot):
    live = MagicMock()
    live.is_live = True
    control_widget = widgets.LaserAutofocusControlWidget(rig.controller, live, multipointController=None)
    qtbot.addWidget(control_widget)
    rig.controller.measure_displacement = MagicMock(side_effect=RuntimeError("camera dropped a frame"))

    with pytest.raises(RuntimeError):
        control_widget.on_measure_displacement_clicked()

    live.stop_live.assert_called_once()
    live.start_live.assert_called_once()


# -- sweep fit (pure) --------------------------------------------------------------------------


def sweep(points):
    return [SweepSample(z_um=1000 + dz, dz_um=dz, selected_x=x) for dz, x in points]


def test_sweep_fit_recovers_the_slope():
    fit = widgets._fit_sweep_slope(sweep([(dz, 500 + 2.0 * dz) for dz in range(-20, 21, 5)]))

    assert fit.slope_px_per_um == pytest.approx(2.0)
    assert fit.um_per_px == pytest.approx(0.5)
    assert fit.n_points == 9 and fit.dz_span_um == 40
    assert fit.is_usable_calibration and not fit.residual_is_high


def test_sweep_fit_ignores_positions_without_a_spot():
    fit = widgets._fit_sweep_slope(sweep([(-10, None), (0, 500.0), (10, 480.0), (20, None)]))

    assert fit.n_points == 2
    assert fit.um_per_px == pytest.approx(-0.5)


@pytest.mark.parametrize("points", [[], [(0, 500.0)], [(0, None), (10, None)], [(5, 500.0), (5, 510.0)]])
def test_sweep_fit_needs_two_distinct_z_positions(points):
    assert widgets._fit_sweep_slope(sweep(points)) is None


def test_sweep_fit_of_a_static_reflection_is_not_a_calibration():
    fit = widgets._fit_sweep_slope(sweep([(dz, 500.0) for dz in range(-20, 21, 5)]))

    assert not fit.is_usable_calibration


def test_sweep_fit_flags_a_bent_branch():
    # linear near focus, flattening out beyond +/-10 um
    fit = widgets._fit_sweep_slope(sweep([(dz, 500 + 2.0 * max(-10, min(10, dz))) for dz in range(-40, 41, 5)]))

    assert fit.residual_is_high
