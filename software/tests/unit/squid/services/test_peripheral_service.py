# tests/squid/services/test_peripheral_service.py
"""Tests for PeripheralService."""

from unittest.mock import Mock


class TestPeripheralService:
    """Test suite for PeripheralService."""

    def test_set_dac_calls_hardware(self):
        """set_dac should call microcontroller.analog_write_onboard_DAC."""
        from squid.mcs.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus

        mock_mcu = Mock()
        bus = EventBus()

        service = PeripheralService(mock_mcu, bus)
        service.set_dac(channel=0, percentage=50.0)

        # 50% of 65535 = 32767 (rounded)
        mock_mcu.analog_write_onboard_DAC.assert_called_once_with(0, 32768)

    def test_set_dac_clamps_percentage(self):
        """set_dac should clamp percentage to 0-100."""
        from squid.mcs.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus

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
        from squid.mcs.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus, DACValueChanged

        mock_mcu = Mock()
        bus = EventBus()
        service = PeripheralService(mock_mcu, bus)

        received = []
        bus.subscribe(DACValueChanged, lambda e: received.append(e))

        service.set_dac(channel=0, percentage=50.0)
        bus.drain()

        assert len(received) == 1
        assert received[0].channel == 0
        assert received[0].value == 50.0

    def test_handles_set_dac_command(self):
        """Should respond to SetDACCommand events."""
        from squid.mcs.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus, SetDACCommand

        mock_mcu = Mock()
        bus = EventBus()
        PeripheralService(mock_mcu, bus)

        # Publish command
        bus.publish(SetDACCommand(channel=1, value=75.0))
        bus.drain()

        # Should have called hardware (75% of 65535 = 49151.25 → 49151)
        mock_mcu.analog_write_onboard_DAC.assert_called_once_with(1, 49151)

    def test_handles_start_trigger_command(self):
        """Should respond to StartCameraTriggerCommand events."""
        from squid.mcs.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus, StartCameraTriggerCommand

        mock_mcu = Mock()
        bus = EventBus()
        PeripheralService(mock_mcu, bus)

        bus.publish(StartCameraTriggerCommand())
        bus.drain()

        mock_mcu.start_camera_trigger.assert_called_once()

    def test_handles_stop_trigger_command(self):
        """Should respond to StopCameraTriggerCommand events."""
        from squid.mcs.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus, StopCameraTriggerCommand

        mock_mcu = Mock()
        bus = EventBus()
        PeripheralService(mock_mcu, bus)

        bus.publish(StopCameraTriggerCommand())
        bus.drain()

        mock_mcu.stop_camera_trigger.assert_called_once()

    def test_handles_set_trigger_frequency_command(self):
        """Should respond to SetCameraTriggerFrequencyCommand events."""
        from squid.mcs.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus, SetCameraTriggerFrequencyCommand

        mock_mcu = Mock()
        bus = EventBus()
        PeripheralService(mock_mcu, bus)

        bus.publish(SetCameraTriggerFrequencyCommand(fps=12.5))
        bus.drain()

        mock_mcu.set_camera_trigger_frequency.assert_called_once_with(12.5)

    def test_handles_af_laser_commands(self):
        """Should respond to AF laser on/off commands and wait when requested."""
        from squid.mcs.services.peripheral_service import PeripheralService
        from squid.core.events import (
            EventBus,
            TurnOnAFLaserCommand,
            TurnOffAFLaserCommand,
        )

        mock_mcu = Mock()
        bus = EventBus()
        PeripheralService(mock_mcu, bus)

        bus.publish(TurnOnAFLaserCommand())
        bus.publish(TurnOffAFLaserCommand())
        bus.drain()

        mock_mcu.turn_on_AF_laser.assert_called_once()
        mock_mcu.turn_off_AF_laser.assert_called_once()
        assert mock_mcu.wait_till_operation_is_completed.call_count == 2

    def test_add_joystick_button_listener(self):
        """add_joystick_button_listener should delegate to microcontroller."""
        from squid.mcs.services.peripheral_service import PeripheralService
        from squid.core.events import EventBus

        mock_mcu = Mock()
        bus = EventBus()
        service = PeripheralService(mock_mcu, bus)

        listener = Mock()
        service.add_joystick_button_listener(listener)

        mock_mcu.add_joystick_button_listener.assert_called_once_with(listener)
