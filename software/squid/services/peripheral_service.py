# squid/services/peripheral_service.py
"""Service for peripheral hardware (DAC, pins, etc.)."""
from squid.services.base import BaseService
from squid.events import EventBus, SetDACCommand, DACValueChanged


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

    def _on_set_dac_command(self, event: SetDACCommand):
        """Handle SetDACCommand event."""
        self.set_dac(event.channel, event.value)

    def set_dac(self, channel: int, percentage: float):
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
