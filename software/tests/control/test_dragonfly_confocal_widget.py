"""Tests for DragonflyConfocalWidget: what it shows must match the Dragonfly hardware."""

import logging

import tests.control.gui_test_stubs  # noqa: F401 - ensures GUI modules import cleanly
import control.widgets
from control.serial_peripherals import SerialDeviceError, Dragonfly_Simulation


class MuteDragonfly(Dragonfly_Simulation):
    """A Dragonfly that does not answer the modality query."""

    def get_modality(self):
        raise SerialDeviceError("Max attempts reached without receiving response.")


class StuckDragonfly(Dragonfly_Simulation):
    """A Dragonfly that reports its state but refuses to switch modality."""

    def set_modality(self, modality):
        raise SerialDeviceError("Dragonfly command failed: AT_MODALITY,CONFOCAL -> :N")


def make_widget(qtbot, dragonfly):
    widget = control.widgets.DragonflyConfocalWidget(dragonfly)
    qtbot.addWidget(widget)
    return widget


def test_dropdowns_show_the_positions_the_unit_reports(qtbot):
    sim = Dragonfly_Simulation()
    sim.dichroic_position = 3
    sim.emission_filter_positions[1] = 4
    sim.emission_filter_positions[2] = 2
    sim.field_aperture_positions[1] = 7
    sim.disk_motor_running = True
    sim.current_modality = "CONFOCAL"

    widget = make_widget(qtbot, sim)

    assert widget.dropdown_dichroic.currentIndex() == 2
    assert widget.dropdown_port1_emission_filter.currentIndex() == 3
    assert widget.dropdown_port2_emission_filter.currentIndex() == 1
    assert widget.dropdown_field_aperture.currentIndex() == 6
    assert widget.btn_disk_motor.isChecked()
    assert widget.get_confocal_mode() is True
    assert widget.btn_toggle_confocal.text() == "Switch to Widefield"


def test_showing_the_initial_positions_does_not_move_anything(qtbot):
    sim = Dragonfly_Simulation()
    sim.dichroic_position = 3
    moved = []
    sim.set_port_selection_dichroic = lambda position: moved.append(position)

    make_widget(qtbot, sim)

    assert moved == []


def test_widget_builds_when_the_unit_cannot_report_its_modality(qtbot):
    widget = make_widget(qtbot, MuteDragonfly())

    assert widget.get_confocal_mode() is False
    assert widget.btn_toggle_confocal.text() == "Switch to Confocal"


def test_toggle_switches_the_unit_and_emits_the_new_mode(qtbot):
    sim = Dragonfly_Simulation()
    widget = make_widget(qtbot, sim)
    modes = []
    widget.signal_toggle_confocal_widefield.connect(modes.append)

    widget.btn_toggle_confocal.click()

    assert sim.get_modality() == "CONFOCAL"
    assert modes == [True]
    assert widget.btn_toggle_confocal.text() == "Switch to Widefield"


def test_failed_modality_switch_keeps_widefield_and_emits_nothing(qtbot, caplog):
    widget = make_widget(qtbot, StuckDragonfly())
    modes = []
    widget.signal_toggle_confocal_widefield.connect(modes.append)

    with caplog.at_level(logging.ERROR):
        widget.btn_toggle_confocal.click()

    assert widget.get_confocal_mode() is False
    assert modes == []
    assert widget.btn_toggle_confocal.isEnabled()
    assert any("AT_MODALITY,CONFOCAL" in record.getMessage() for record in caplog.records)


def test_field_aperture_dropdown_moves_the_wheel(qtbot):
    sim = Dragonfly_Simulation()
    widget = make_widget(qtbot, sim)

    widget.dropdown_field_aperture.setCurrentIndex(4)

    assert sim.get_field_aperture_wheel_position() == 5


def test_emission_filter_dropdowns_move_their_own_port(qtbot):
    sim = Dragonfly_Simulation()
    widget = make_widget(qtbot, sim)

    widget.dropdown_port1_emission_filter.setCurrentIndex(2)
    widget.dropdown_port2_emission_filter.setCurrentIndex(5)

    assert sim.get_emission_filter(1) == 3
    assert sim.get_emission_filter(2) == 6


class NoPort2WheelDragonfly(Dragonfly_Simulation):
    """A Dragonfly with no emission filter wheel on camera port 2."""

    def get_emission_filter(self, port):
        if port == 2:
            raise SerialDeviceError("Dragonfly command failed: AT_FW_POS,2,? -> :N")
        return super().get_emission_filter(port)


def test_one_unreadable_wheel_does_not_hide_the_rest_of_the_state(qtbot):
    sim = NoPort2WheelDragonfly()
    sim.field_aperture_positions[1] = 7
    sim.disk_motor_running = True

    widget = make_widget(qtbot, sim)

    assert widget.dropdown_field_aperture.currentIndex() == 6
    assert widget.btn_disk_motor.isChecked()


# ------------------------------------------------------------------ Refresh


def test_refresh_shows_positions_changed_outside_the_widget(qtbot):
    sim = Dragonfly_Simulation()
    widget = make_widget(qtbot, sim)

    # e.g. the live controller moved the filter for a channel, and someone switched modality
    sim.set_emission_filter(1, 4)
    sim.set_field_aperture_wheel_position(7)
    sim.set_port_selection_dichroic(4)
    sim.set_modality("CONFOCAL")
    sim.set_disk_motor_state(True)
    widget.btn_refresh.click()

    assert widget.dropdown_port1_emission_filter.currentIndex() == 3
    assert widget.dropdown_field_aperture.currentIndex() == 6
    assert widget.dropdown_dichroic.currentIndex() == 3
    assert widget.get_confocal_mode() is True
    assert widget.btn_toggle_confocal.text() == "Switch to Widefield"
    assert widget.btn_disk_motor.isChecked()


def test_refresh_does_not_move_anything(qtbot):
    sim = Dragonfly_Simulation()
    widget = make_widget(qtbot, sim)
    moved = []
    for name in ("set_emission_filter", "set_port_selection_dichroic", "set_field_aperture_wheel_position"):
        setattr(sim, name, lambda *args, name=name: moved.append(name))
    sim.emission_filter_positions[1] = 5  # changed behind the widget's back

    widget.btn_refresh.click()

    assert moved == []
    assert widget.dropdown_port1_emission_filter.currentIndex() == 4


def test_refresh_reports_a_modality_change_to_the_live_controller(qtbot):
    sim = Dragonfly_Simulation()
    widget = make_widget(qtbot, sim)
    modes = []
    widget.signal_toggle_confocal_widefield.connect(modes.append)

    widget.btn_refresh.click()  # nothing changed: nothing to report
    sim.set_modality("CONFOCAL")
    widget.btn_refresh.click()

    assert modes == [True]
