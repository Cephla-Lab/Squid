"""Unit tests for AcquisitionService."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from unittest.mock import MagicMock

import pytest

from squid.backend.services.acquisition_service import AcquisitionService
from _def import TriggerMode


@dataclass
class FakeChannelMode:
    """Fake ChannelMode for testing."""

    name: str = "Test Channel"
    exposure_time: float = 100.0
    analog_gain: float = 1.0
    illumination_source: int = 1
    illumination_intensity: float = 50.0
    emission_filter_position: int = 2
    z_offset: float = 0.0


class FakeCameraService:
    """Fake camera service for testing."""

    def __init__(self):
        self.calls: List[Tuple[str, ...]] = []
        self._ready_for_trigger = True
        self._strobe_time = 10.0

    def set_exposure_time(self, exposure: float) -> None:
        self.calls.append(("set_exposure_time", exposure))

    def set_analog_gain(self, gain: float) -> None:
        self.calls.append(("set_analog_gain", gain))

    def get_ready_for_trigger(self) -> bool:
        return self._ready_for_trigger

    def get_strobe_time(self) -> float:
        return self._strobe_time


class FakeIlluminationService:
    """Fake illumination service for testing."""

    def __init__(self):
        self.calls: List[Tuple[str, ...]] = []

    def set_channel_power(self, channel: int, intensity: float) -> None:
        self.calls.append(("set_channel_power", channel, intensity))

    def turn_on_channel(self, channel: int) -> None:
        self.calls.append(("turn_on_channel", channel))

    def turn_off_channel(self, channel: int) -> None:
        self.calls.append(("turn_off_channel", channel))


class FakeFilterWheelService:
    """Fake filter wheel service for testing."""

    def __init__(self, available: bool = True):
        self.calls: List[Tuple[str, ...]] = []
        self._available = available

    def is_available(self) -> bool:
        return self._available

    def set_delay_offset_ms(self, delay: float) -> None:
        self.calls.append(("set_delay_offset_ms", delay))

    def set_filter_wheel_position(self, positions: Dict[int, int]) -> None:
        self.calls.append(("set_filter_wheel_position", positions))


class FakePeripheralService:
    """Fake peripheral service for testing."""

    def __init__(self):
        self.calls: List[Tuple[str, ...]] = []

    def wait_till_operation_is_completed(self) -> None:
        self.calls.append(("wait_till_operation_is_completed",))


class TestAcquisitionServiceInit:
    """Tests for AcquisitionService initialization."""

    def test_init_with_required_services_only(self):
        camera = FakeCameraService()
        peripheral = FakePeripheralService()

        svc = AcquisitionService(camera, peripheral)

        assert svc._camera is camera
        assert svc._peripheral is peripheral
        assert svc._illumination is None
        assert svc._filter_wheel is None

    def test_init_with_all_services(self):
        camera = FakeCameraService()
        peripheral = FakePeripheralService()
        illumination = FakeIlluminationService()
        filter_wheel = FakeFilterWheelService()

        svc = AcquisitionService(
            camera, peripheral, illumination, filter_wheel
        )

        assert svc._camera is camera
        assert svc._peripheral is peripheral
        assert svc._illumination is illumination
        assert svc._filter_wheel is filter_wheel

    def test_has_illumination_property(self):
        camera = FakeCameraService()
        peripheral = FakePeripheralService()

        svc_without = AcquisitionService(camera, peripheral)
        assert svc_without.has_illumination is False

        illumination = FakeIlluminationService()
        svc_with = AcquisitionService(camera, peripheral, illumination)
        assert svc_with.has_illumination is True

    def test_has_filter_wheel_property(self):
        camera = FakeCameraService()
        peripheral = FakePeripheralService()

        svc_without = AcquisitionService(camera, peripheral)
        assert svc_without.has_filter_wheel is False

        # Unavailable filter wheel
        filter_wheel_unavailable = FakeFilterWheelService(available=False)
        svc_unavailable = AcquisitionService(
            camera, peripheral, filter_wheel_service=filter_wheel_unavailable
        )
        assert svc_unavailable.has_filter_wheel is False

        # Available filter wheel
        filter_wheel_available = FakeFilterWheelService(available=True)
        svc_available = AcquisitionService(
            camera, peripheral, filter_wheel_service=filter_wheel_available
        )
        assert svc_available.has_filter_wheel is True


class TestApplyConfiguration:
    """Tests for apply_configuration method."""

    def test_sets_camera_exposure(self):
        camera = FakeCameraService()
        svc = AcquisitionService(camera, FakePeripheralService())
        config = FakeChannelMode(exposure_time=200.0)

        svc.apply_configuration(config, TriggerMode.SOFTWARE)

        assert ("set_exposure_time", 200.0) in camera.calls

    def test_sets_camera_gain(self):
        camera = FakeCameraService()
        svc = AcquisitionService(camera, FakePeripheralService())
        config = FakeChannelMode(analog_gain=2.5)

        svc.apply_configuration(config, TriggerMode.SOFTWARE)

        assert ("set_analog_gain", 2.5) in camera.calls

    def test_sets_illumination_power(self):
        camera = FakeCameraService()
        illumination = FakeIlluminationService()
        svc = AcquisitionService(
            camera, FakePeripheralService(), illumination
        )
        config = FakeChannelMode(illumination_source=3, illumination_intensity=75.0)

        svc.apply_configuration(config, TriggerMode.SOFTWARE)

        assert ("set_channel_power", 3, 75.0) in illumination.calls

    def test_does_not_turn_on_illumination(self):
        """apply_configuration sets power but does NOT turn on."""
        camera = FakeCameraService()
        illumination = FakeIlluminationService()
        svc = AcquisitionService(
            camera, FakePeripheralService(), illumination
        )
        config = FakeChannelMode()

        svc.apply_configuration(config, TriggerMode.SOFTWARE)

        turn_on_calls = [c for c in illumination.calls if c[0] == "turn_on_channel"]
        assert len(turn_on_calls) == 0

    def test_sets_filter_wheel_position_software_trigger(self):
        camera = FakeCameraService()
        filter_wheel = FakeFilterWheelService()
        svc = AcquisitionService(
            camera, FakePeripheralService(), filter_wheel_service=filter_wheel
        )
        config = FakeChannelMode(emission_filter_position=5)

        svc.apply_configuration(config, TriggerMode.SOFTWARE)

        # Should set delay to 0 for software trigger
        assert ("set_delay_offset_ms", 0) in filter_wheel.calls
        assert ("set_filter_wheel_position", {1: 5}) in filter_wheel.calls

    def test_sets_filter_wheel_position_hardware_trigger(self):
        camera = FakeCameraService()
        camera._strobe_time = 15.0
        filter_wheel = FakeFilterWheelService()
        svc = AcquisitionService(
            camera, FakePeripheralService(), filter_wheel_service=filter_wheel
        )
        config = FakeChannelMode(emission_filter_position=3)

        svc.apply_configuration(config, TriggerMode.HARDWARE)

        # Should set delay to negative strobe time for hardware trigger
        assert ("set_delay_offset_ms", -15) in filter_wheel.calls
        assert ("set_filter_wheel_position", {1: 3}) in filter_wheel.calls

    def test_skips_filter_when_disabled(self):
        camera = FakeCameraService()
        filter_wheel = FakeFilterWheelService()
        svc = AcquisitionService(
            camera, FakePeripheralService(), filter_wheel_service=filter_wheel
        )
        config = FakeChannelMode()

        svc.apply_configuration(config, TriggerMode.SOFTWARE, enable_filter_switching=False)

        assert len(filter_wheel.calls) == 0

    def test_handles_missing_optional_services(self):
        """Should not raise even without optional services."""
        camera = FakeCameraService()
        svc = AcquisitionService(camera, FakePeripheralService())
        config = FakeChannelMode()

        # Should not raise
        svc.apply_configuration(config, TriggerMode.SOFTWARE)


class TestTurnOnIllumination:
    """Tests for turn_on_illumination method."""

    def test_turns_on_channel(self):
        camera = FakeCameraService()
        illumination = FakeIlluminationService()
        svc = AcquisitionService(
            camera, FakePeripheralService(), illumination
        )
        config = FakeChannelMode(illumination_source=2, illumination_intensity=60.0)

        result = svc.turn_on_illumination(config)

        assert result is True
        assert ("set_channel_power", 2, 60.0) in illumination.calls
        assert ("turn_on_channel", 2) in illumination.calls

    def test_returns_false_without_illumination_service(self):
        svc = AcquisitionService(FakeCameraService(), FakePeripheralService())
        config = FakeChannelMode()

        result = svc.turn_on_illumination(config)

        assert result is False

    def test_returns_false_for_missing_source(self):
        illumination = FakeIlluminationService()
        svc = AcquisitionService(
            FakeCameraService(), FakePeripheralService(), illumination
        )
        config = FakeChannelMode()
        config.illumination_source = None  # type: ignore

        result = svc.turn_on_illumination(config)

        assert result is False


class TestTurnOffIllumination:
    """Tests for turn_off_illumination method."""

    def test_turns_off_channel(self):
        camera = FakeCameraService()
        illumination = FakeIlluminationService()
        svc = AcquisitionService(
            camera, FakePeripheralService(), illumination
        )
        config = FakeChannelMode(illumination_source=4)

        result = svc.turn_off_illumination(config)

        assert result is True
        assert ("turn_off_channel", 4) in illumination.calls

    def test_returns_false_without_illumination_service(self):
        svc = AcquisitionService(FakeCameraService(), FakePeripheralService())
        config = FakeChannelMode()

        result = svc.turn_off_illumination(config)

        assert result is False


class TestIlluminationContext:
    """Tests for illumination_context context manager."""

    def test_software_trigger_turns_on_then_off(self):
        illumination = FakeIlluminationService()
        svc = AcquisitionService(
            FakeCameraService(), FakePeripheralService(), illumination
        )
        config = FakeChannelMode(illumination_source=1)

        with svc.illumination_context(config, TriggerMode.SOFTWARE):
            # During context, should have turned on
            assert ("turn_on_channel", 1) in illumination.calls

        # After context, should have turned off
        assert ("turn_off_channel", 1) in illumination.calls

    def test_hardware_trigger_does_not_turn_on(self):
        illumination = FakeIlluminationService()
        svc = AcquisitionService(
            FakeCameraService(), FakePeripheralService(), illumination
        )
        config = FakeChannelMode(illumination_source=1)

        with svc.illumination_context(config, TriggerMode.HARDWARE):
            pass

        # Should not turn on/off for hardware trigger
        turn_on_calls = [c for c in illumination.calls if c[0] == "turn_on_channel"]
        turn_off_calls = [c for c in illumination.calls if c[0] == "turn_off_channel"]
        assert len(turn_on_calls) == 0
        assert len(turn_off_calls) == 0

    def test_turns_off_on_exception(self):
        illumination = FakeIlluminationService()
        svc = AcquisitionService(
            FakeCameraService(), FakePeripheralService(), illumination
        )
        config = FakeChannelMode(illumination_source=1)

        with pytest.raises(ValueError):
            with svc.illumination_context(config, TriggerMode.SOFTWARE):
                raise ValueError("test error")

        # Should still turn off even after exception
        assert ("turn_off_channel", 1) in illumination.calls


class TestWaitForReady:
    """Tests for wait_for_ready method."""

    def test_returns_true_when_ready(self):
        camera = FakeCameraService()
        camera._ready_for_trigger = True
        svc = AcquisitionService(camera, FakePeripheralService())

        result = svc.wait_for_ready(timeout_s=1.0)

        assert result is True

    def test_returns_false_on_timeout(self):
        camera = FakeCameraService()
        camera._ready_for_trigger = False
        svc = AcquisitionService(camera, FakePeripheralService())

        result = svc.wait_for_ready(timeout_s=0.01)  # Very short timeout

        assert result is False


class TestGetStrobeTime:
    """Tests for get_strobe_time method."""

    def test_returns_strobe_time(self):
        camera = FakeCameraService()
        camera._strobe_time = 25.0
        svc = AcquisitionService(camera, FakePeripheralService())

        result = svc.get_strobe_time()

        assert result == 25.0

    def test_returns_zero_on_exception(self):
        camera = MagicMock()
        camera.get_strobe_time.side_effect = Exception("error")
        svc = AcquisitionService(camera, FakePeripheralService())

        result = svc.get_strobe_time()

        assert result == 0.0
