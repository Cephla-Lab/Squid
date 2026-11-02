"""Tests for SpinningDiskConfocalWidget: what it shows must match the X-Light hardware."""

import tests.control.gui_test_stubs  # noqa: F401 - ensures GUI modules import cleanly
import control.widgets
from control.serial_peripherals import SerialDeviceError, XLight_Simulation


class UnresponsiveXLight(XLight_Simulation):
    """An X-Light that reports its state but does not carry out any command."""

    def set_disk_position(self, position):
        raise SerialDeviceError("Max attempts reached without receiving expected response.")

    def set_disk_motor_state(self, state):
        raise SerialDeviceError("Max attempts reached without receiving expected response.")

    def set_illumination_iris(self, value):
        raise SerialDeviceError("Max attempts reached without receiving expected response.")


def make_widget(qtbot, xlight):
    widget = control.widgets.SpinningDiskConfocalWidget(xlight)
    qtbot.addWidget(widget)
    return widget


def test_motor_button_is_checked_when_disk_motor_is_already_running(qtbot):
    xlight = XLight_Simulation()
    xlight.disk_motor_state = True

    widget = make_widget(qtbot, xlight)

    assert widget.btn_toggle_motor.isChecked()


def test_dichroic_dropdown_offers_only_positions_the_wheel_has(qtbot):
    xlight = XLight_Simulation()
    xlight.dichroic_positions = 3

    widget = make_widget(qtbot, xlight)

    dropdown = widget.dropdown_dichroic
    assert [dropdown.itemText(i) for i in range(dropdown.count())] == ["1", "2", "3"]


def test_filter_slider_ends_at_the_last_slider_position(qtbot):
    xlight = XLight_Simulation()
    xlight.filter_slider_positions = 3

    widget = make_widget(qtbot, xlight)

    assert widget.filter_slider.minimum() == 0
    assert widget.filter_slider.maximum() == 2


def test_failed_disk_move_leaves_widget_in_widefield(qtbot):
    widget = make_widget(qtbot, UnresponsiveXLight())
    modes = []
    widget.signal_toggle_confocal_widefield.connect(modes.append)

    widget.btn_toggle_widefield.click()
    qtbot.waitUntil(widget.btn_toggle_widefield.isEnabled, timeout=5000)

    assert widget.get_confocal_mode() is False
    assert widget.btn_toggle_widefield.text() == "Switch to Confocal"
    assert modes == []


def test_failed_motor_start_leaves_motor_button_unchecked(qtbot):
    widget = make_widget(qtbot, UnresponsiveXLight())

    widget.btn_toggle_motor.click()
    qtbot.waitUntil(widget.btn_toggle_motor.isEnabled, timeout=5000)

    assert not widget.btn_toggle_motor.isChecked()


def test_failed_iris_command_keeps_controls_usable_and_is_not_saved(qtbot):
    widget = make_widget(qtbot, UnresponsiveXLight())
    saved_values = []
    widget.signal_illumination_iris_changed.connect(saved_values.append)
    widget.spinbox_illumination_iris.setValue(80)

    widget.update_illumination_iris(False)

    assert widget.slider_illumination_iris.isEnabled()
    assert not widget.slider_illumination_iris.signalsBlocked()
    assert saved_values == []
