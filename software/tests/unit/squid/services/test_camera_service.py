# tests/squid/services/test_camera_service.py
"""Tests for CameraService."""
import pytest
from unittest.mock import Mock, MagicMock, PropertyMock


class TestCameraService:
    """Test suite for CameraService."""

    def test_set_exposure_time_calls_camera(self):
        """set_exposure_time should call camera.set_exposure_time."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        mock_camera.get_exposure_limits.return_value = (0.1, 1000.0)
        bus = EventBus()

        service = CameraService(mock_camera, bus)
        service.set_exposure_time(100.0)

        mock_camera.set_exposure_time.assert_called_once_with(100.0)

    def test_set_exposure_clamps_to_limits(self):
        """set_exposure_time should clamp to camera limits."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        mock_camera.get_exposure_limits.return_value = (1.0, 500.0)
        bus = EventBus()

        service = CameraService(mock_camera, bus)

        # Over max
        service.set_exposure_time(1000.0)
        mock_camera.set_exposure_time.assert_called_with(500.0)

        # Under min
        service.set_exposure_time(0.1)
        mock_camera.set_exposure_time.assert_called_with(1.0)

    def test_set_exposure_publishes_event(self):
        """set_exposure_time should publish ExposureTimeChanged."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus, ExposureTimeChanged

        mock_camera = Mock()
        mock_camera.get_exposure_limits.return_value = (0.1, 1000.0)
        bus = EventBus()

        service = CameraService(mock_camera, bus)

        received = []
        bus.subscribe(ExposureTimeChanged, lambda e: received.append(e))

        service.set_exposure_time(100.0)

        assert len(received) == 1
        assert received[0].exposure_time_ms == 100.0

    def test_handles_set_exposure_command(self):
        """Should respond to SetExposureTimeCommand events."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus, SetExposureTimeCommand

        mock_camera = Mock()
        mock_camera.get_exposure_limits.return_value = (0.1, 1000.0)
        bus = EventBus()

        service = CameraService(mock_camera, bus)

        bus.publish(SetExposureTimeCommand(exposure_time_ms=200.0))

        mock_camera.set_exposure_time.assert_called_once_with(200.0)

    def test_get_exposure_time(self):
        """get_exposure_time should return camera value."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        mock_camera.get_exposure_time.return_value = 50.0
        bus = EventBus()

        service = CameraService(mock_camera, bus)

        assert service.get_exposure_time() == 50.0

    def test_set_analog_gain(self):
        """set_analog_gain should call camera and publish event."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus, AnalogGainChanged

        mock_camera = Mock()
        mock_gain_range = Mock()
        mock_gain_range.min_gain = 0.0
        mock_gain_range.max_gain = 24.0
        mock_camera.get_gain_range.return_value = mock_gain_range
        bus = EventBus()

        service = CameraService(mock_camera, bus)

        received = []
        bus.subscribe(AnalogGainChanged, lambda e: received.append(e))

        service.set_analog_gain(12.0)

        mock_camera.set_analog_gain.assert_called_once_with(12.0)
        assert len(received) == 1
        assert received[0].gain == 12.0
