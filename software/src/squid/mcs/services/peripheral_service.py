# squid/services/peripheral_service.py
"""Service for peripheral hardware (DAC, pins, etc.)."""

import threading

from squid.mcs.services.base import BaseService
from squid.core.events import (
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

    def __init__(self, microcontroller, event_bus: EventBus, mode_gate=None):
        """
        Initialize peripheral service.

        Args:
            microcontroller: Microcontroller instance
            event_bus: EventBus for communication
        """
        super().__init__(event_bus, mode_gate=mode_gate)
        self._microcontroller = microcontroller
        self._lock = threading.RLock()

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
        if self._blocked_for_ui_hardware_commands():
            self._log.info("Ignoring %s due to global mode gate", type(event).__name__)
            return
        self.set_dac(event.channel, event.value)

    def set_dac(self, channel: int, percentage: float) -> None:
        """
        Set DAC output value.

        Args:
            channel: DAC channel (0 or 1)
            percentage: Output value as percentage (0-100) or normalized (0-1)
        """
        # Accept normalized values and convert to percentage for backward compatibility
        if 0.0 <= percentage <= 1.0:
            percentage = percentage * 100.0
        # Clamp to valid range
        percentage = max(0.0, min(100.0, percentage))

        # Convert percentage to 16-bit value
        value = round(percentage * 65535 / 100)

        self._log.debug(f"Setting DAC{channel} to {percentage}% ({value})")
        with self._lock:
            self._microcontroller.analog_write_onboard_DAC(channel, value)

        # Notify listeners
        self.publish(DACValueChanged(channel=channel, value=percentage))

    def _on_start_trigger_command(self, event: StartCameraTriggerCommand) -> None:
        """Handle start trigger command."""
        if self._blocked_for_ui_hardware_commands():
            self._log.info("Ignoring %s due to global mode gate", type(event).__name__)
            return
        with self._lock:
            self._microcontroller.start_camera_trigger()

    def _on_stop_trigger_command(self, event: StopCameraTriggerCommand) -> None:
        """Handle stop trigger command."""
        if self._blocked_for_ui_hardware_commands():
            self._log.info("Ignoring %s due to global mode gate", type(event).__name__)
            return
        with self._lock:
            self._microcontroller.stop_camera_trigger()

    def _on_set_trigger_frequency_command(
        self, event: SetCameraTriggerFrequencyCommand
    ) -> None:
        """Handle trigger frequency command."""
        if self._blocked_for_ui_hardware_commands():
            self._log.info("Ignoring %s due to global mode gate", type(event).__name__)
            return
        with self._lock:
            self._microcontroller.set_camera_trigger_frequency(event.fps)

    def _on_turn_on_af_laser_command(self, event: TurnOnAFLaserCommand) -> None:
        """Handle AF laser on command."""
        if self._blocked_for_ui_hardware_commands():
            self._log.info("Ignoring %s due to global mode gate", type(event).__name__)
            return
        with self._lock:
            self._microcontroller.turn_on_AF_laser()
            if event.wait_for_completion:
                self._microcontroller.wait_till_operation_is_completed()

    def _on_turn_off_af_laser_command(self, event: TurnOffAFLaserCommand) -> None:
        """Handle AF laser off command."""
        if self._blocked_for_ui_hardware_commands():
            self._log.info("Ignoring %s due to global mode gate", type(event).__name__)
            return
        with self._lock:
            self._microcontroller.turn_off_AF_laser()
            if event.wait_for_completion:
                self._microcontroller.wait_till_operation_is_completed()

    # Direct access helpers (non-event) to avoid widgets touching hardware
    def add_joystick_button_listener(self, listener):
        """Register joystick button listener on microcontroller."""
        with self._lock:
            self._microcontroller.add_joystick_button_listener(listener)

    def enable_joystick(self, enabled: bool) -> None:
        """Enable or disable joystick control."""
        with self._lock:
            if self._microcontroller is not None:
                self._microcontroller.enable_joystick(enabled)

    def wait_till_operation_is_completed(self, timeout_s: float = 10.0) -> None:
        """Wait for microcontroller operations to complete."""
        with self._lock:
            if self._microcontroller is not None:
                self._microcontroller.wait_till_operation_is_completed(timeout_s)

    def send_hardware_trigger(
        self,
        control_illumination: bool = True,
        illumination_on_time_us: float = 0,
    ) -> None:
        """Send hardware trigger for camera acquisition.

        Args:
            control_illumination: Whether to control illumination with trigger
            illumination_on_time_us: Duration of illumination in microseconds
        """
        with self._lock:
            if self._microcontroller is not None:
                self._microcontroller.send_hardware_trigger(
                    control_illumination=control_illumination,
                    illumination_on_time_us=illumination_on_time_us,
                )

    def turn_on_af_laser(self, wait_for_completion: bool = True) -> None:
        """Turn on the autofocus laser.

        Args:
            wait_for_completion: Whether to wait for operation to complete
        """
        with self._lock:
            if self._microcontroller is not None:
                self._microcontroller.turn_on_AF_laser()
                if wait_for_completion:
                    self._microcontroller.wait_till_operation_is_completed()

    def turn_off_af_laser(self, wait_for_completion: bool = True) -> None:
        """Turn off the autofocus laser.

        Args:
            wait_for_completion: Whether to wait for operation to complete
        """
        with self._lock:
            if self._microcontroller is not None:
                self._microcontroller.turn_off_AF_laser()
                if wait_for_completion:
                    self._microcontroller.wait_till_operation_is_completed()
