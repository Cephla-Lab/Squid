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

    # ============================================================
    # Task 1.1: ROI methods
    # ============================================================

    def test_set_region_of_interest(self):
        """set_region_of_interest should call camera and publish event."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus, ROIChanged

        mock_camera = Mock()
        mock_camera.get_resolution.return_value = (2048, 2048)
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        received = []
        bus.subscribe(ROIChanged, lambda e: received.append(e))

        service.set_region_of_interest(100, 100, 800, 600)

        mock_camera.set_region_of_interest.assert_called_once_with(100, 100, 800, 600)
        assert len(received) == 1
        assert received[0].x_offset == 100
        assert received[0].y_offset == 100
        assert received[0].width == 800
        assert received[0].height == 600

    def test_get_region_of_interest(self):
        """get_region_of_interest should return camera value."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        mock_camera.get_region_of_interest.return_value = (0, 0, 1024, 768)
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_region_of_interest()

        assert result == (0, 0, 1024, 768)

    def test_get_resolution(self):
        """get_resolution should return camera value."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        mock_camera.get_resolution.return_value = (2048, 2048)
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_resolution()

        assert result == (2048, 2048)

    # ============================================================
    # Task 1.2: Binning methods
    # ============================================================

    def test_set_binning(self):
        """set_binning should call camera and publish event."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus, BinningChanged

        mock_camera = Mock()
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        received = []
        bus.subscribe(BinningChanged, lambda e: received.append(e))

        service.set_binning(2, 2)

        mock_camera.set_binning.assert_called_once_with(2, 2)
        assert len(received) == 1
        assert received[0].binning_x == 2
        assert received[0].binning_y == 2

    def test_get_binning(self):
        """get_binning should return camera value."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        mock_camera.get_binning.return_value = (2, 2)
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_binning()

        assert result == (2, 2)

    def test_get_binning_options(self):
        """get_binning_options should return camera value."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        mock_camera.get_binning_options.return_value = [(1, 1), (2, 2), (4, 4)]
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_binning_options()

        assert result == [(1, 1), (2, 2), (4, 4)]

    # ============================================================
    # Task 1.3: Pixel format methods
    # ============================================================

    def test_set_pixel_format(self):
        """set_pixel_format should call camera and publish event."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus, PixelFormatChanged
        from squid.config import CameraPixelFormat

        mock_camera = Mock()
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        received = []
        bus.subscribe(PixelFormatChanged, lambda e: received.append(e))

        service.set_pixel_format(CameraPixelFormat.MONO16)

        mock_camera.set_pixel_format.assert_called_once_with(CameraPixelFormat.MONO16)
        assert len(received) == 1
        assert received[0].pixel_format == CameraPixelFormat.MONO16

    def test_get_pixel_format(self):
        """get_pixel_format should return camera value."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus
        from squid.config import CameraPixelFormat

        mock_camera = Mock()
        mock_camera.get_pixel_format.return_value = CameraPixelFormat.MONO8
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_pixel_format()

        assert result == CameraPixelFormat.MONO8

    def test_get_available_pixel_formats(self):
        """get_available_pixel_formats should return camera value."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus
        from squid.config import CameraPixelFormat

        mock_camera = Mock()
        mock_camera.get_available_pixel_formats.return_value = [CameraPixelFormat.MONO8, CameraPixelFormat.MONO16]
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_available_pixel_formats()

        assert result == [CameraPixelFormat.MONO8, CameraPixelFormat.MONO16]

    # ============================================================
    # Task 1.4: Temperature methods
    # ============================================================

    def test_set_temperature(self):
        """set_temperature should call camera."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        service.set_temperature(-20.0)

        mock_camera.set_temperature.assert_called_once_with(-20.0)

    def test_set_temperature_reading_callback(self):
        """set_temperature_reading_callback should call camera."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        callback = Mock()
        service.set_temperature_reading_callback(callback)

        mock_camera.set_temperature_reading_callback.assert_called_once_with(callback)

    # ============================================================
    # Task 1.5: White balance methods
    # ============================================================

    def test_set_white_balance_gains(self):
        """set_white_balance_gains should call camera."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        service.set_white_balance_gains(1.2, 1.0, 1.5)

        mock_camera.set_white_balance_gains.assert_called_once_with(1.2, 1.0, 1.5)

    def test_get_white_balance_gains(self):
        """get_white_balance_gains should return camera value."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        mock_camera.get_white_balance_gains.return_value = (1.2, 1.0, 1.5)
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_white_balance_gains()

        assert result == (1.2, 1.0, 1.5)

    def test_set_auto_white_balance(self):
        """set_auto_white_balance should call camera."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        service.set_auto_white_balance(True)

        mock_camera.set_auto_white_balance_gains.assert_called_once_with(on=True)

    # ============================================================
    # Task 1.6: Black level method
    # ============================================================

    def test_set_black_level(self):
        """set_black_level should call camera."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        service.set_black_level(10.0)

        mock_camera.set_black_level.assert_called_once_with(10.0)

    # ============================================================
    # Task 2: Read-only camera properties
    # ============================================================

    def test_get_gain_range(self):
        """get_gain_range should return camera value."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        mock_gain_range = Mock()
        mock_gain_range.min_gain = 0.0
        mock_gain_range.max_gain = 24.0
        mock_camera.get_gain_range.return_value = mock_gain_range
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_gain_range()

        assert result.min_gain == 0.0
        assert result.max_gain == 24.0

    def test_get_acquisition_mode(self):
        """get_acquisition_mode should return camera value."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus
        from squid.abc import CameraAcquisitionMode

        mock_camera = Mock()
        mock_camera.get_acquisition_mode.return_value = CameraAcquisitionMode.SOFTWARE_TRIGGER
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_acquisition_mode()

        assert result == CameraAcquisitionMode.SOFTWARE_TRIGGER

    def test_get_pixel_size_binned_um(self):
        """get_pixel_size_binned_um should return camera value."""
        from squid.services.camera_service import CameraService
        from squid.events import EventBus

        mock_camera = Mock()
        mock_camera.get_pixel_size_binned_um.return_value = 6.5
        bus = EventBus()
        service = CameraService(mock_camera, bus)

        result = service.get_pixel_size_binned_um()

        assert result == 6.5
