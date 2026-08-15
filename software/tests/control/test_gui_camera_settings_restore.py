"""Tests for HighContentScreeningGui's cached camera settings restore helpers.

The helpers are thin glue between the settings cache and the camera/widgets, so they
are exercised unbound with a lightweight stand-in for the GUI instance instead of
constructing the full (CI-skipped) HighContentScreeningGui.
"""

from types import SimpleNamespace

import pytest
from qtpy.QtWidgets import QComboBox

import squid.logging
from control.gui_hcs import HighContentScreeningGui
from tests.tools import get_test_camera


def _make_gui_stub(camera, dropdown=None):
    widget_attrs = {}
    if dropdown is not None:
        widget_attrs["dropdown_sensorMode"] = dropdown
    return SimpleNamespace(
        camera=camera,
        cameraSettingWidget=SimpleNamespace(**widget_attrs),
        log=squid.logging.get_logger("test_gui_camera_settings_restore"),
    )


class TestRestoreSensorMode:
    def test_applies_mode_and_syncs_dropdown(self, qtbot):
        camera = get_test_camera()
        dropdown = QComboBox()
        qtbot.add_widget(dropdown)
        dropdown.addItems(camera.get_available_sensor_modes())
        gui = _make_gui_stub(camera, dropdown)

        restored = HighContentScreeningGui._restore_sensor_mode(gui, "fast")

        assert restored is True
        assert camera.get_sensor_mode() == "fast"
        assert dropdown.currentText() == "fast"

    def test_none_mode_is_skipped(self, qtbot):
        camera = get_test_camera()
        gui = _make_gui_stub(camera)

        restored = HighContentScreeningGui._restore_sensor_mode(gui, None)

        assert restored is False
        assert camera.get_sensor_mode() == "standard"

    def test_unknown_mode_is_rejected(self, qtbot):
        camera = get_test_camera()
        gui = _make_gui_stub(camera)

        restored = HighContentScreeningGui._restore_sensor_mode(gui, "bogus")

        assert restored is False
        assert camera.get_sensor_mode() == "standard"

    def test_unsupported_camera_is_handled(self, qtbot):
        class NoSensorModeCamera:
            def set_sensor_mode(self, mode):
                raise NotImplementedError("Sensor mode selection is not supported by this camera.")

        gui = _make_gui_stub(NoSensorModeCamera())

        restored = HighContentScreeningGui._restore_sensor_mode(gui, "fast")

        assert restored is False

    def test_success_without_dropdown_widget(self, qtbot):
        """The dropdown only exists when the camera reports modes at widget build time."""
        camera = get_test_camera()
        gui = _make_gui_stub(camera, dropdown=None)

        restored = HighContentScreeningGui._restore_sensor_mode(gui, "fast")

        assert restored is True
        assert camera.get_sensor_mode() == "fast"
