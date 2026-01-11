"""Unit tests for ImageCaptureExecutor, CaptureContext, and related classes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from unittest.mock import MagicMock, call
import time

import pytest

from squid.backend.controllers.multipoint.image_capture import (
    CaptureContext,
    build_capture_info,
    ImageCaptureExecutor,
    CaptureSequenceBuilder,
)
from _def import TriggerMode


@dataclass
class FakePos:
    """Fake position for testing."""

    x_mm: float = 1.0
    y_mm: float = 2.0
    z_mm: float = 0.05


@dataclass
class FakeChannelMode:
    """Fake channel mode for testing."""

    name: str = "BF"
    exposure_time: float = 10.0
    analog_gain: float = 1.0
    illumination_source: int = 0
    illumination_intensity: float = 50.0


class FakeCameraService:
    """Fake CameraService for testing."""

    def __init__(self):
        self._exposure_time = 10.0
        self._analog_gain = 1.0
        self.trigger_calls = []
        self.set_exposure_calls = []
        self.set_gain_calls = []

    def get_exposure_time(self) -> float:
        return self._exposure_time

    def set_exposure_time(self, exposure_ms: float) -> None:
        self.set_exposure_calls.append(exposure_ms)
        self._exposure_time = exposure_ms

    def set_analog_gain(self, gain: float) -> None:
        self.set_gain_calls.append(gain)
        self._analog_gain = gain

    def get_total_frame_time(self) -> float:
        return self._exposure_time + 5.0  # exposure + readout

    def send_trigger(self, illumination_time: Optional[float] = None) -> None:
        self.trigger_calls.append(illumination_time)


class FakeAcquisitionService:
    """Fake AcquisitionService for testing."""

    def __init__(self):
        self.apply_calls = []
        self.on_calls = []
        self.off_calls = []

    def apply_configuration(
        self,
        config: Any,
        trigger_mode: str,
        enable_filter_switching: bool = True,
    ) -> None:
        self.apply_calls.append((config, trigger_mode))

    def turn_on_illumination(self, config: Any) -> bool:
        self.on_calls.append(config)
        return True

    def turn_off_illumination(self, config: Any) -> bool:
        self.off_calls.append(config)
        return True


class TestCaptureContext:
    """Tests for CaptureContext dataclass."""

    def test_create_context(self):
        """Test creating a CaptureContext."""
        config = FakeChannelMode()
        context = CaptureContext(
            config=config,
            file_id="region_0_0_0_0_BF",
            save_directory="/path/to/data",
            z_index=0,
            region_id="region_0",
            fov=1,
            config_idx=0,
            time_point=0,
            z_piezo_um=50.0,
            pixel_size_um=0.325,
        )

        assert context.config == config
        assert context.file_id == "region_0_0_0_0_BF"
        assert context.save_directory == "/path/to/data"
        assert context.z_index == 0
        assert context.region_id == "region_0"
        assert context.fov == 1
        assert context.config_idx == 0
        assert context.time_point == 0
        assert context.z_piezo_um == 50.0
        assert context.pixel_size_um == 0.325

    def test_context_defaults(self):
        """Test CaptureContext default values."""
        context = CaptureContext(
            config=FakeChannelMode(),
            file_id="test",
            save_directory="/path",
            z_index=0,
            region_id="region_0",
            fov=0,
            config_idx=0,
            time_point=0,
        )

        assert context.z_piezo_um is None
        assert context.pixel_size_um is None


class TestBuildCaptureInfo:
    """Tests for build_capture_info function."""

    def test_builds_capture_info(self):
        """Test that build_capture_info creates CaptureInfo correctly."""
        config = FakeChannelMode()
        context = CaptureContext(
            config=config,
            file_id="test_file",
            save_directory="/path/to/data",
            z_index=2,
            region_id="region_0",
            fov=5,
            config_idx=1,
            time_point=3,
            z_piezo_um=25.0,
            pixel_size_um=0.5,
        )
        pos = FakePos(x_mm=1.0, y_mm=2.0, z_mm=0.05)
        capture_time = 12345.0

        info = build_capture_info(context, pos, capture_time)

        assert info.position == pos
        assert info.z_index == 2
        assert info.capture_time == 12345.0
        assert info.z_piezo_um == 25.0
        assert info.configuration == config
        assert info.save_directory == "/path/to/data"
        assert info.file_id == "test_file"
        assert info.region_id == "region_0"
        assert info.fov == 5
        assert info.configuration_idx == 1
        assert info.time_point == 3
        assert info.pixel_size_um == 0.5

    def test_uses_current_time_if_not_provided(self):
        """Test that capture_time defaults to current time."""
        context = CaptureContext(
            config=FakeChannelMode(),
            file_id="test",
            save_directory="/path",
            z_index=0,
            region_id="region_0",
            fov=0,
            config_idx=0,
            time_point=0,
        )
        pos = FakePos()

        before = time.time()
        info = build_capture_info(context, pos)
        after = time.time()

        assert before <= info.capture_time <= after


class TestImageCaptureExecutor:
    """Tests for ImageCaptureExecutor class."""

    def test_init(self):
        """Test ImageCaptureExecutor initialization."""
        camera = FakeCameraService()
        executor = ImageCaptureExecutor(camera)

        assert executor.trigger_mode == TriggerMode.SOFTWARE

    def test_trigger_mode_property(self):
        """Test trigger mode getter/setter."""
        camera = FakeCameraService()
        executor = ImageCaptureExecutor(camera, trigger_mode=TriggerMode.SOFTWARE)

        assert executor.trigger_mode == TriggerMode.SOFTWARE

        executor.trigger_mode = TriggerMode.HARDWARE
        assert executor.trigger_mode == TriggerMode.HARDWARE

    def test_apply_config_with_acquisition_service(self):
        """Test applying config via AcquisitionService."""
        camera = FakeCameraService()
        acquisition = FakeAcquisitionService()
        executor = ImageCaptureExecutor(
            camera, acquisition_service=acquisition, trigger_mode=TriggerMode.HARDWARE
        )
        config = FakeChannelMode()

        executor.apply_config(config)

        assert len(acquisition.apply_calls) == 1
        assert acquisition.apply_calls[0][0] == config
        assert acquisition.apply_calls[0][1] == TriggerMode.HARDWARE

    def test_apply_config_fallback(self):
        """Test applying config directly to camera when no AcquisitionService."""
        camera = FakeCameraService()
        executor = ImageCaptureExecutor(camera)
        config = FakeChannelMode(exposure_time=15.0, analog_gain=2.0)

        executor.apply_config(config)

        assert 15.0 in camera.set_exposure_calls
        assert 2.0 in camera.set_gain_calls

    def test_get_illumination_time_hardware(self):
        """Test getting illumination time for hardware trigger."""
        camera = FakeCameraService()
        camera._exposure_time = 25.0
        executor = ImageCaptureExecutor(camera, trigger_mode=TriggerMode.HARDWARE)

        time_ms = executor.get_illumination_time()

        assert time_ms == 25.0

    def test_get_illumination_time_software(self):
        """Test getting illumination time for software trigger."""
        camera = FakeCameraService()
        executor = ImageCaptureExecutor(camera, trigger_mode=TriggerMode.SOFTWARE)

        time_ms = executor.get_illumination_time()

        assert time_ms is None

    def test_turn_on_illumination(self):
        """Test turning on illumination."""
        camera = FakeCameraService()
        acquisition = FakeAcquisitionService()
        executor = ImageCaptureExecutor(camera, acquisition_service=acquisition)
        config = FakeChannelMode()

        executor.turn_on_illumination(config)

        assert config in acquisition.on_calls

    def test_turn_off_illumination(self):
        """Test turning off illumination."""
        camera = FakeCameraService()
        acquisition = FakeAcquisitionService()
        executor = ImageCaptureExecutor(camera, acquisition_service=acquisition)
        config = FakeChannelMode()

        executor.turn_off_illumination(config)

        assert config in acquisition.off_calls

    def test_send_trigger(self):
        """Test sending camera trigger."""
        camera = FakeCameraService()
        executor = ImageCaptureExecutor(camera)

        executor.send_trigger()

        assert len(camera.trigger_calls) == 1
        assert camera.trigger_calls[0] is None

    def test_send_trigger_with_illumination_time(self):
        """Test sending trigger with illumination time."""
        camera = FakeCameraService()
        executor = ImageCaptureExecutor(camera)

        executor.send_trigger(illumination_time=15.0)

        assert len(camera.trigger_calls) == 1
        assert camera.trigger_calls[0] == 15.0

    def test_get_frame_wait_timeout(self):
        """Test frame wait timeout calculation."""
        camera = FakeCameraService()
        camera._exposure_time = 100.0  # 100ms exposure
        executor = ImageCaptureExecutor(
            camera, frame_timeout_multiplier=5.0, frame_timeout_base_s=2.0
        )

        timeout = executor.get_frame_wait_timeout()

        # (5.0 * 105.0 / 1000.0) + 2.0 = 0.525 + 2.0 = 2.525
        expected = 5.0 * 105.0 / 1000.0 + 2.0
        assert timeout == pytest.approx(expected)

    def test_execute_software_capture(self):
        """Test software trigger capture turns on illumination."""
        camera = FakeCameraService()
        acquisition = FakeAcquisitionService()
        executor = ImageCaptureExecutor(
            camera, acquisition_service=acquisition, trigger_mode=TriggerMode.SOFTWARE
        )
        config = FakeChannelMode()

        executor.execute_software_capture(config)

        assert config in acquisition.on_calls
        assert len(camera.trigger_calls) == 1

    def test_execute_hardware_capture(self):
        """Test hardware trigger capture sends illumination time."""
        camera = FakeCameraService()
        camera._exposure_time = 20.0
        executor = ImageCaptureExecutor(camera, trigger_mode=TriggerMode.HARDWARE)
        config = FakeChannelMode()

        executor.execute_hardware_capture(config, wait_for_exposure=False)

        assert len(camera.trigger_calls) == 1
        assert camera.trigger_calls[0] == 20.0

    def test_finalize_capture_software(self):
        """Test finalize turns off illumination for software trigger."""
        camera = FakeCameraService()
        acquisition = FakeAcquisitionService()
        executor = ImageCaptureExecutor(
            camera, acquisition_service=acquisition, trigger_mode=TriggerMode.SOFTWARE
        )
        config = FakeChannelMode()

        executor.finalize_capture(config)

        assert config in acquisition.off_calls

    def test_finalize_capture_hardware(self):
        """Test finalize does not turn off illumination for hardware trigger."""
        camera = FakeCameraService()
        acquisition = FakeAcquisitionService()
        executor = ImageCaptureExecutor(
            camera, acquisition_service=acquisition, trigger_mode=TriggerMode.HARDWARE
        )
        config = FakeChannelMode()

        executor.finalize_capture(config)

        assert len(acquisition.off_calls) == 0


class TestCaptureSequenceBuilder:
    """Tests for CaptureSequenceBuilder class."""

    def test_init(self):
        """Test CaptureSequenceBuilder initialization."""
        builder = CaptureSequenceBuilder(
            save_directory="/path/to/data",
            region_id="region_0",
            fov=1,
            time_point=0,
        )

        assert builder._save_directory == "/path/to/data"
        assert builder._region_id == "region_0"
        assert builder._fov == 1
        assert builder._time_point == 0

    def test_build_context(self):
        """Test building a single capture context."""
        builder = CaptureSequenceBuilder(
            save_directory="/path",
            region_id="region_0",
            fov=1,
            time_point=0,
        )
        config = FakeChannelMode(name="DAPI")

        context = builder.build_context(
            config=config,
            config_idx=0,
            z_index=2,
            file_id="region_0_1_0_2_DAPI",
            z_piezo_um=50.0,
            pixel_size_um=0.325,
        )

        assert context.config == config
        assert context.config_idx == 0
        assert context.z_index == 2
        assert context.file_id == "region_0_1_0_2_DAPI"
        assert context.save_directory == "/path"
        assert context.region_id == "region_0"
        assert context.fov == 1
        assert context.time_point == 0
        assert context.z_piezo_um == 50.0
        assert context.pixel_size_um == 0.325

    def test_generate_file_id(self):
        """Test file ID generation."""
        builder = CaptureSequenceBuilder(
            save_directory="/path",
            region_id="region_0",
            fov=1,
            time_point=0,
        )

        file_id = builder.generate_file_id(
            region_id="region_0",
            fov=5,
            time_point=3,
            z_index=2,
            config_name="DAPI",
        )

        assert file_id == "region_0_5_3_2_DAPI"
