"""Integration tests for PeripheralService with simulated microcontroller."""

from unittest.mock import MagicMock

import pytest

from squid.events import (
    EventBus,
    SetDACCommand,
    DACValueChanged,
    StartCameraTriggerCommand,
    StopCameraTriggerCommand,
    SetCameraTriggerFrequencyCommand,
)
from squid.services import PeripheralService


@pytest.mark.integration
def test_dac_command_calls_micro_and_publishes_event(simulated_microcontroller):
    bus = EventBus()
    service = PeripheralService(simulated_microcontroller, bus)

    dac_events = []
    bus.subscribe(DACValueChanged, lambda e: dac_events.append(e))

    original = simulated_microcontroller.analog_write_onboard_DAC
    spy = MagicMock(wraps=original)
    simulated_microcontroller.analog_write_onboard_DAC = spy

    bus.publish(SetDACCommand(channel=0, value=50.0))

    spy.assert_called_once_with(0, 32768)
    assert dac_events and dac_events[0].value == pytest.approx(50.0)


@pytest.mark.integration
def test_trigger_commands_reach_microcontroller(simulated_microcontroller):
    bus = EventBus()
    service = PeripheralService(simulated_microcontroller, bus)

    start_spy = MagicMock()
    stop_spy = MagicMock()
    freq_spy = MagicMock()

    simulated_microcontroller.start_camera_trigger = start_spy
    simulated_microcontroller.stop_camera_trigger = stop_spy
    simulated_microcontroller.set_camera_trigger_frequency = freq_spy

    bus.publish(StartCameraTriggerCommand())
    bus.publish(SetCameraTriggerFrequencyCommand(fps=12.5))
    bus.publish(StopCameraTriggerCommand())

    start_spy.assert_called_once_with()
    stop_spy.assert_called_once_with()
    freq_spy.assert_called_once_with(12.5)
