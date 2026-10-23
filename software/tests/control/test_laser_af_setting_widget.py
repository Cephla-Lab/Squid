"""LaserAutofocusSettingWidget: the spot chooser (mode + N) and the full-sensor live view."""

import pytest

import control.microscope
from control._def import SpotDetectionMode
from control.core.core import QtStreamHandler
from control.core.laser_auto_focus_controller import LaserAutofocusController
from control.core.live_controller import LiveController
from control.models import LaserAFConfig
from control.widgets import LaserAutofocusSettingWidget
from tests.control.test_laser_af_settings import spots_image
from tests.tools import NonInteractiveMatplotlib

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


def test_update_values_shows_the_saved_choice(rig):
    widget, controller = rig
    controller.laser_af_properties = LaserAFConfig(spot_detection_mode=SpotDetectionMode.NTH_FROM_LEFT, spot_index=3)

    widget.update_values()

    assert widget.spot_mode_combo.currentData() == SpotDetectionMode.NTH_FROM_LEFT
    assert widget.spot_index_spinbox.value() == 3


def test_run_spot_detection_marks_the_chosen_spot(rig, monkeypatch):
    """Three reflections; 2nd from the right is the middle one."""
    widget, _ = rig
    three_spots = spots_image([(400.0, 1.0, 5.0), (522.0, 1.0, 5.0), (700.0, 1.0, 5.0)])
    monkeypatch.setattr(widget, "illuminate_and_get_frame", lambda: three_spots)
    select_mode(widget, SpotDetectionMode.NTH_FROM_RIGHT)
    widget.spot_index_spinbox.setValue(2)
    marked = []
    widget.signal_laser_spot_location.connect(lambda image, x, y: marked.append(x))

    with NonInteractiveMatplotlib():
        widget.run_spot_detection()

    assert marked == [pytest.approx(522.0, abs=0.5)]


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
