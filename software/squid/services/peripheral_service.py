# squid/services/peripheral_service.py
"""Service for peripheral hardware (DAC, pins, etc.)."""

from squid.services.base import BaseService
from squid.events import (
    EventBus,
    SetDACCommand,
    DACValueChanged,
    StartCameraTriggerCommand,
    StopCameraTriggerCommand,
    SetCameraTriggerFrequencyCommand,
    TurnOnAFLaserCommand,
    TurnOffAFLaserCommand,
)


class PeripheralService(BaseService):
    """
    Service layer for peripheral hardware operations.

    Handles DAC control, pin settings, and other microcontroller peripherals.
    Widgets should use this service instead of calling microcontroller directly.
    """

    def __init__(self, microcontroller, event_bus: EventBus):
        """
        Initialize peripheral service.

        Args:
            microcontroller: Microcontroller instance
            event_bus: EventBus for communication
        """
        super().__init__(event_bus)
        self._microcontroller = microcontroller

        # Subscribe to commands

        self.subscribe(SetDACCommand, self._on_set_dac_command)
        self.subscribe(StartCameraTriggerCommand, self._on_start_trigger_command)
        self.subscribe(StopCameraTriggerCommand, self._on_stop_trigger_command)
        self.subscribe(
            SetCameraTriggerFrequencyCommand, self._on_set_trigger_frequency_command
        )
        self.subscribe(TurnOnAFLaserCommand, self._on_turn_on_af_laser_command)
        self.subscribe(TurnOffAFLaserCommand, self._on_turn_off_af_laser_command)

    def _on_set_dac_command(self, event: SetDACCommand):
        """Handle SetDACCommand event."""
        self.set_dac(event.channel, event.value)

    def set_dac(self, channel: int, percentage: float) -> None:
        """
        Set DAC output value.

        Args:
            channel: DAC channel (0 or 1)
            percentage: Output value as percentage (0-100)
        """
        # Clamp to valid range
        percentage = max(0.0, min(100.0, percentage))

        # Convert percentage to 16-bit value
        value = round(percentage * 65535 / 100)

        self._log.debug(f"Setting DAC{channel} to {percentage}% ({value})")
        self._microcontroller.analog_write_onboard_DAC(channel, value)

        # Notify listeners
        self.publish(DACValueChanged(channel=channel, value=percentage))

    def _on_start_trigger_command(self, event: StartCameraTriggerCommand) -> None:
        """Handle start trigger command."""
        self._microcontroller.start_camera_trigger()

    def _on_stop_trigger_command(self, event: StopCameraTriggerCommand) -> None:
        """Handle stop trigger command."""
        self._microcontroller.stop_camera_trigger()

    def _on_set_trigger_frequency_command(
        self, event: SetCameraTriggerFrequencyCommand
    ) -> None:
        """Handle trigger frequency command."""
        self._microcontroller.set_camera_trigger_frequency(event.fps)

    def _on_turn_on_af_laser_command(self, event: TurnOnAFLaserCommand) -> None:
        """Handle AF laser on command."""
        self._microcontroller.turn_on_AF_laser()
        if event.wait_for_completion:
            self._microcontroller.wait_till_operation_is_completed()

    def _on_turn_off_af_laser_command(self, event: TurnOffAFLaserCommand) -> None:
        """Handle AF laser off command."""
        self._microcontroller.turn_off_AF_laser()
        if event.wait_for_completion:
            self._microcontroller.wait_till_operation_is_completed()

    # Direct access helpers (non-event) to avoid widgets touching hardware
    def add_joystick_button_listener(self, listener):
        """Register joystick button listener on microcontroller."""
        self._microcontroller.add_joystick_button_listener(listener)
