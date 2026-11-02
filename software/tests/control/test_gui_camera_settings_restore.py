"""Tests for HighContentScreeningGui's cached camera settings restore.

The gui-level helper is thin glue over CameraSettingsWidget.restore_sensor_mode, so it
is exercised unbound with a real widget and simulated camera instead of constructing
the full (CI-skipped) HighContentScreeningGui.
"""

from types import SimpleNamespace

import squid.logging
from squid.abc import CameraError
from control.gui_hcs import HighContentScreeningGui
from control.widgets import CameraSettingsWidget
from tests.tools import get_test_camera


def _make_gui_stub(camera, qtbot):
    widget = CameraSettingsWidget(camera)
    qtbot.add_widget(widget)
    return SimpleNamespace(
        camera=camera,
        cameraSettingWidget=widget,
        log=squid.logging.get_logger("test_gui_camera_settings_restore"),
    )


class TestRestoreSensorMode:
    def test_applies_mode_and_syncs_dropdown(self, qtbot):
        camera = get_test_camera()
        gui = _make_gui_stub(camera, qtbot)

        restored = HighContentScreeningGui._restore_sensor_mode(gui, "fast")

        assert restored is True
        assert camera.get_sensor_mode() == "fast"
        assert gui.cameraSettingWidget.dropdown_sensorMode.currentText() == "fast"

    def test_none_mode_is_skipped(self, qtbot):
        camera = get_test_camera()
        gui = _make_gui_stub(camera, qtbot)

        restored = HighContentScreeningGui._restore_sensor_mode(gui, None)

        assert restored is False
        assert camera.get_sensor_mode() == "standard"

    def test_unknown_mode_is_rejected(self, qtbot):
        camera = get_test_camera()
        gui = _make_gui_stub(camera, qtbot)

        restored = HighContentScreeningGui._restore_sensor_mode(gui, "bogus")

        assert restored is False
        assert camera.get_sensor_mode() == "standard"

    def test_camera_without_sensor_modes(self, qtbot):
        camera = get_test_camera()
        camera.get_available_sensor_modes = lambda: []
        camera.get_sensor_mode = lambda: None
        gui = _make_gui_stub(camera, qtbot)

        assert gui.cameraSettingWidget.dropdown_sensorMode is None

        restored = HighContentScreeningGui._restore_sensor_mode(gui, "fast")

        assert restored is False

    def test_readback_camera_error_fails_safe(self, qtbot):
        """A device error while reading back the mode must fail the restore, not crash startup."""
        camera = get_test_camera()
        gui = _make_gui_stub(camera, qtbot)

        def raise_camera_error():
            raise CameraError("device read failed")

        camera.get_sensor_mode = raise_camera_error

        restored = HighContentScreeningGui._restore_sensor_mode(gui, "fast")

        assert restored is False

    def test_already_active_mode_sends_no_camera_command(self, qtbot):
        """Restoring the mode the camera is already in must not re-issue the (expensive)
        mode-change command — on real hardware it pauses streaming and re-applies exposure."""
        camera = get_test_camera()
        gui = _make_gui_stub(camera, qtbot)

        def fail(mode):
            raise AssertionError("set_sensor_mode must not be called for the already-active mode")

        camera.set_sensor_mode = fail

        restored = HighContentScreeningGui._restore_sensor_mode(gui, "standard")

        assert restored is True
        assert camera.get_sensor_mode() == "standard"


class TestRestoreBinning:
    def test_applies_binning_and_syncs_dropdown(self, qtbot):
        camera = get_test_camera()
        gui = _make_gui_stub(camera, qtbot)

        restored = HighContentScreeningGui._restore_binning(gui, (2, 2))

        assert restored
        assert tuple(camera.get_binning()) == (2, 2)
        assert gui.cameraSettingWidget.dropdown_binning.currentText() == "2x2"
        assert gui.cameraSettingWidget.dropdown_pixelFormat.currentText() == camera.get_pixel_format().name

    def test_unsupported_binning_is_rejected_and_camera_unchanged(self, qtbot):
        camera = get_test_camera()
        gui = _make_gui_stub(camera, qtbot)
        before = tuple(camera.get_binning())

        restored = HighContentScreeningGui._restore_binning(gui, (7, 7))

        assert not restored
        assert tuple(camera.get_binning()) == before


class TestRestorePixelFormat:
    def test_applies_format_and_shows_what_the_camera_delivers(self, qtbot):
        camera = get_test_camera()
        gui = _make_gui_stub(camera, qtbot)

        restored = HighContentScreeningGui._restore_pixel_format(gui, "MONO8")

        assert restored
        assert camera.get_pixel_format().name == "MONO8"
        assert gui.cameraSettingWidget.dropdown_pixelFormat.currentText() == "MONO8"

    def test_unknown_or_empty_format_is_rejected(self, qtbot):
        camera = get_test_camera()
        gui = _make_gui_stub(camera, qtbot)
        before = camera.get_pixel_format()

        assert not HighContentScreeningGui._restore_pixel_format(gui, "BOGUS")
        assert not HighContentScreeningGui._restore_pixel_format(gui, None)
        assert camera.get_pixel_format() == before
