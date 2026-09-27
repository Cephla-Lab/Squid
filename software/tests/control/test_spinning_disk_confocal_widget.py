"""Tests for SpinningDiskConfocalWidget: what it shows must match the X-Light hardware."""

import pytest

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


def dropdown_items(dropdown):
    return [dropdown.itemText(i) for i in range(dropdown.count())]


class TestStartupState:
    def test_motor_button_is_checked_when_disk_motor_is_already_running(self, qtbot):
        xlight = XLight_Simulation()
        xlight.disk_motor_state = True

        widget = make_widget(qtbot, xlight)

        assert widget.btn_toggle_motor.isChecked()

    def test_motor_button_is_unchecked_when_disk_motor_is_stopped(self, qtbot):
        xlight = XLight_Simulation()
        xlight.disk_motor_state = False

        widget = make_widget(qtbot, xlight)

        assert not widget.btn_toggle_motor.isChecked()

    def test_confocal_mode_is_read_from_disk_position(self, qtbot):
        xlight = XLight_Simulation()
        xlight.spinning_disk_pos = 1

        widget = make_widget(qtbot, xlight)

        assert widget.get_confocal_mode() is True
        assert widget.btn_toggle_widefield.text() == "Switch to Widefield"


class TestPositions:
    def test_dichroic_dropdown_offers_only_positions_the_wheel_has(self, qtbot):
        xlight = XLight_Simulation()
        xlight.dichroic_positions = 3

        widget = make_widget(qtbot, xlight)

        assert dropdown_items(widget.dropdown_dichroic) == ["1", "2", "3"]

    def test_dichroic_dropdown_offers_five_positions_for_a_five_position_wheel(self, qtbot):
        xlight = XLight_Simulation()
        xlight.dichroic_positions = 5

        widget = make_widget(qtbot, xlight)

        assert dropdown_items(widget.dropdown_dichroic) == ["1", "2", "3", "4", "5"]

    def test_filter_slider_ends_at_the_last_slider_position(self, qtbot):
        xlight = XLight_Simulation()
        xlight.filter_slider_positions = 3

        widget = make_widget(qtbot, xlight)

        assert widget.filter_slider.minimum() == 0
        assert widget.filter_slider.maximum() == 2


class TestFailedCommands:
    def test_failed_disk_move_leaves_widget_in_widefield(self, qtbot):
        widget = make_widget(qtbot, UnresponsiveXLight())
        modes = []
        widget.signal_toggle_confocal_widefield.connect(modes.append)

        widget.btn_toggle_widefield.click()
        qtbot.waitUntil(widget.btn_toggle_widefield.isEnabled, timeout=5000)

        assert widget.get_confocal_mode() is False
        assert widget.btn_toggle_widefield.text() == "Switch to Confocal"
        assert modes == []

    def test_successful_disk_move_switches_widget_to_confocal(self, qtbot):
        xlight = XLight_Simulation()
        widget = make_widget(qtbot, xlight)
        modes = []
        widget.signal_toggle_confocal_widefield.connect(modes.append)

        widget.btn_toggle_widefield.click()
        qtbot.waitUntil(widget.btn_toggle_widefield.isEnabled, timeout=5000)

        assert xlight.spinning_disk_pos == 1
        assert widget.get_confocal_mode() is True
        assert modes == [True]

    def test_failed_motor_start_leaves_motor_button_unchecked(self, qtbot):
        widget = make_widget(qtbot, UnresponsiveXLight())

        widget.btn_toggle_motor.click()
        qtbot.waitUntil(widget.btn_toggle_motor.isEnabled, timeout=5000)

        assert not widget.btn_toggle_motor.isChecked()

    def test_successful_motor_start_leaves_motor_button_checked(self, qtbot):
        xlight = XLight_Simulation()
        widget = make_widget(qtbot, xlight)

        widget.btn_toggle_motor.click()
        qtbot.waitUntil(widget.btn_toggle_motor.isEnabled, timeout=5000)

        assert xlight.disk_motor_state is True
        assert widget.btn_toggle_motor.isChecked()

    def test_failed_iris_command_keeps_controls_usable_and_is_not_saved(self, qtbot):
        widget = make_widget(qtbot, UnresponsiveXLight())
        saved_values = []
        widget.signal_illumination_iris_changed.connect(saved_values.append)
        widget.spinbox_illumination_iris.setValue(80)

        widget.update_illumination_iris(False)

        assert widget.slider_illumination_iris.isEnabled()
        assert not widget.slider_illumination_iris.signalsBlocked()
        assert saved_values == []
