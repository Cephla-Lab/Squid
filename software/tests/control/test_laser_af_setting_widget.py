"""LaserAutofocusSettingWidget: the spot chooser (mode + N) and the full-sensor live view."""

import matplotlib
import pytest

import control._def
import control.microscope
from control._def import SpotDetectionMode
from control.core.core import QtStreamHandler
from control.core.laser_auto_focus_controller import LaserAutofocusController
from control.core.live_controller import LiveController
from control.models import LaserAFConfig
from control.widgets import LaserAutofocusSettingWidget
from tests.control.test_laser_af_settings import roi_frame, spots_image

STRIP = LaserAFConfig(x_offset=800, y_offset=900, width=1536, height=256)


@pytest.fixture(scope="module")
def scope():
    microscope = control.microscope.Microscope.build_from_global_config(True, skip_init=True)
    yield microscope
    microscope.close()


@pytest.fixture
def rig(scope, qtbot, monkeypatch):
    """The settings widget on a controller that shares its live controller, as in the GUI."""
    live = LiveController(microscope=scope, camera=scope.addons.camera_focus)
    controller = LaserAutofocusController(
        microcontroller=scope.low_level_drivers.microcontroller,
        camera=scope.addons.camera_focus,
        liveController=live,
        stage=scope.stage,
        piezo=scope.addons.piezo_stage,
        objectiveStore=scope.objective_store,
    )
    # keep the test's settings out of the checkout's user profile
    monkeypatch.setattr(controller._config_repo, "save_laser_af_config", lambda *args: None)
    widget = LaserAutofocusSettingWidget(QtStreamHandler(), live, controller)
    qtbot.addWidget(widget)
    return widget, controller


def select_mode(widget, mode):
    widget.spot_mode_combo.setCurrentIndex(widget.spot_mode_combo.findData(mode))


def test_n_is_editable_only_for_the_nth_modes(rig):
    widget, _ = rig

    select_mode(widget, SpotDetectionMode.SINGLE)
    assert not widget.spot_index_spinbox.isEnabled()

    select_mode(widget, SpotDetectionMode.NTH_FROM_RIGHT)
    assert widget.spot_index_spinbox.isEnabled()


def test_initialize_hands_the_chosen_spot_to_the_controller(rig, monkeypatch):
    widget, controller = rig
    monkeypatch.setattr(controller, "initialize_auto", lambda: True)  # the hardware search is not the point here
    select_mode(widget, SpotDetectionMode.NTH_FROM_RIGHT)
    widget.spot_index_spinbox.setValue(2)

    widget.initialize_button.click()

    assert controller.laser_af_properties.spot_detection_mode == SpotDetectionMode.NTH_FROM_RIGHT
    assert controller.laser_af_properties.spot_index == 2


def test_failed_initialize_leaves_the_apply_button_disabled(rig, monkeypatch):
    """Apply-without-re-initialization only makes sense once Initialize has succeeded."""
    widget, controller = rig
    monkeypatch.setattr(controller, "initialize_auto", lambda: False)

    widget.initialize_button.click()

    assert not controller.is_initialized
    assert not widget.update_threshold_button.isEnabled()


def test_update_values_shows_the_saved_choice(rig):
    widget, controller = rig
    controller.laser_af_properties = LaserAFConfig(spot_detection_mode=SpotDetectionMode.NTH_FROM_LEFT, spot_index=3)

    widget.update_values()

    assert widget.spot_mode_combo.currentData() == SpotDetectionMode.NTH_FROM_LEFT
    assert widget.spot_index_spinbox.value() == 3


def test_run_spot_detection_marks_the_chosen_spot(rig, monkeypatch):
    """Three reflections on a whole-sensor frame; 2nd from the right is the middle one."""
    widget, _ = rig
    three_spots = spots_image([(400.0, 1.0, 5.0), (522.0, 1.0, 5.0), (700.0, 1.0, 5.0)], shape=(1080, 1920), y_px=540)
    monkeypatch.setattr(widget, "illuminate_and_get_frame", lambda: three_spots)
    select_mode(widget, SpotDetectionMode.NTH_FROM_RIGHT)
    widget.spot_index_spinbox.setValue(2)
    marked = []
    widget.signal_laser_spot_location.connect(lambda image, x, y, other_x: marked.append((x, list(other_x))))

    # Run Spot Detection opens a debug figure; Agg keeps it headless. Not restored afterwards:
    # matplotlib refuses to switch back to an interactive backend while Qt's event loop is running.
    matplotlib.use("Agg")
    widget.run_spot_detection()

    assert len(marked) == 1
    x, other_x = marked[0]
    assert x == pytest.approx(522.0, abs=0.5)
    assert other_x == [pytest.approx(400.0, abs=2.0), pytest.approx(700.0, abs=2.0)]  # every other reflection


def test_full_sensor_checkbox_drives_the_camera(rig):
    widget, controller = rig
    controller.initialize_manual(STRIP)

    widget.full_sensor_checkbox.setChecked(True)
    assert controller.camera.get_region_of_interest() == (0, 0, 1920, 1080)  # the simulated focus camera

    widget.full_sensor_checkbox.setChecked(False)
    assert controller.camera.get_region_of_interest() == (800, 900, 1536, 256)


def test_stopping_live_leaves_the_full_sensor_view(rig):
    widget, controller = rig
    controller.initialize_manual(STRIP)
    widget.full_sensor_checkbox.setChecked(True)

    widget.stop_live()

    assert not widget.full_sensor_checkbox.isChecked()
    assert controller.camera.get_region_of_interest() == (800, 900, 1536, 256)


def test_checkbox_follows_the_controller(rig):
    """Measurements and initialization put the strip back on their own; the box must show that."""
    widget, controller = rig
    controller.initialize_manual(STRIP)
    widget.full_sensor_checkbox.setChecked(True)

    controller.initialize_manual(STRIP)

    assert not widget.full_sensor_checkbox.isChecked()


@pytest.fixture
def initialized_on_three_reflections(rig, monkeypatch):
    """Sensor reflections at x = 1000 / 1600 / 1800, "2nd from left" chosen and Initialize run: the 1024 px
    strip around 1600 starts at 1088 and drops the 1000 spot, so 1600 is the 1st from the left inside it."""
    widget, controller = rig
    monkeypatch.setattr(control._def, "LASER_AF_CROP_WIDTH", 1024)
    monkeypatch.setattr(control._def, "LASER_AF_CROP_HEIGHT", 224)
    monkeypatch.setattr(control._def, "LASER_AF_INITIALIZE_CROP_WIDTH", 2800)
    monkeypatch.setattr(control._def, "LASER_AF_INITIALIZE_CROP_HEIGHT", 1800)
    monkeypatch.setattr(controller.camera, "get_resolution", lambda: (3088, 2064))
    sensor = spots_image([(1000.0, 1.0, 5.0), (1600.0, 1.0, 5.0), (1800.0, 1.0, 5.0)], shape=(2064, 3088), y_px=1032)
    controller.get_new_frame = lambda: roi_frame(controller.camera, sensor)
    monkeypatch.setattr(widget, "illuminate_and_get_frame", lambda: roi_frame(controller.camera, sensor))
    select_mode(widget, SpotDetectionMode.NTH_FROM_LEFT)
    widget.spot_index_spinbox.setValue(2)
    widget.initialize_button.click()
    assert controller.camera.get_region_of_interest() == (1088, 920, 1024, 224)
    assert controller.laser_af_properties.strip_spot_index == 1
    marked = []
    widget.signal_laser_spot_location.connect(lambda image, x, y, other_x: marked.append(x))
    matplotlib.use("Agg")  # Run Spot Detection opens a debug figure
    return widget, controller, marked


def test_preview_on_the_strip_marks_the_reflection_initialize_chose(initialized_on_three_reflections):
    widget, controller, marked = initialized_on_three_reflections

    widget.run_spot_detection()

    assert marked == [pytest.approx(1600.0 - 1088, abs=1.0)]  # strip coordinates of the 1600 reflection


def test_preview_on_the_full_sensor_counts_from_the_sensor_edge(initialized_on_three_reflections):
    widget, controller, marked = initialized_on_three_reflections
    widget.full_sensor_checkbox.setChecked(True)

    widget.run_spot_detection()

    assert marked == [pytest.approx(1600.0, abs=1.0)]


def test_preview_refuses_a_new_n_on_the_strip(initialized_on_three_reflections):
    """The strip cannot be ranked by a sensor N Initialize has not placed it for."""
    widget, controller, marked = initialized_on_three_reflections
    widget.spot_index_spinbox.setValue(3)

    widget.run_spot_detection()

    assert marked == []
    assert "full sensor" in widget.spot_detection_error_label.text()
