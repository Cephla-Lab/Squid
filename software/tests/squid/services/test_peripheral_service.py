# tests/squid/services/test_peripheral_service.py
"""Tests for PeripheralService."""
import pytest
from unittest.mock import Mock, MagicMock


class TestPeripheralService:
    """Test suite for PeripheralService."""

    def test_set_dac_calls_hardware(self):
        """set_dac should call microcontroller.analog_write_onboard_DAC."""
        from squid.services.peripheral_service import PeripheralService
        from squid.events import EventBus

        mock_mcu = Mock()
        bus = EventBus()

        service = PeripheralService(mock_mcu, bus)
        service.set_dac(channel=0, percentage=50.0)

        # 50% of 65535 = 32767 (rounded)
        mock_mcu.analog_write_onboard_DAC.assert_called_once_with(0, 32768)

    def test_set_dac_clamps_percentage(self):
        """set_dac should clamp percentage to 0-100."""
        from squid.services.peripheral_service import PeripheralService
        from squid.events import EventBus

        mock_mcu = Mock()
        bus = EventBus()
        service = PeripheralService(mock_mcu, bus)

        # Over 100%
        service.set_dac(channel=0, percentage=150.0)
        mock_mcu.analog_write_onboard_DAC.assert_called_with(0, 65535)

        # Under 0%
        service.set_dac(channel=1, percentage=-10.0)
        mock_mcu.analog_write_onboard_DAC.assert_called_with(1, 0)

    def test_set_dac_publishes_event(self):
        """set_dac should publish DACValueChanged event."""
        from squid.services.peripheral_service import PeripheralService
        from squid.events import EventBus, DACValueChanged

        mock_mcu = Mock()
        bus = EventBus()
        service = PeripheralService(mock_mcu, bus)

        received = []
        bus.subscribe(DACValueChanged, lambda e: received.append(e))

        service.set_dac(channel=0, percentage=50.0)

        assert len(received) == 1
        assert received[0].channel == 0
        assert received[0].value == 50.0

    def test_handles_set_dac_command(self):
        """Should respond to SetDACCommand events."""
        from squid.services.peripheral_service import PeripheralService
        from squid.events import EventBus, SetDACCommand

        mock_mcu = Mock()
        bus = EventBus()
        service = PeripheralService(mock_mcu, bus)

        # Publish command
        bus.publish(SetDACCommand(channel=1, value=75.0))

        # Should have called hardware (75% of 65535 = 49151.25 → 49151)
        mock_mcu.analog_write_onboard_DAC.assert_called_once_with(1, 49151)
