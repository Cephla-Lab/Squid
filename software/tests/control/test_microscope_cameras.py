"""Tests for multi-camera support in Microscope class."""

import pytest
from unittest.mock import MagicMock

from control.models import CameraRegistryConfig, CameraDefinition
from squid.camera.config_factory import create_camera_configs, get_primary_camera_id
from squid.config import CameraConfig, CameraVariant, CameraPixelFormat


@pytest.fixture
def base_camera_config():
    """Minimal camera config for testing."""
    return CameraConfig(
        camera_type=CameraVariant.TOUPCAM,
        default_pixel_format=CameraPixelFormat.MONO16,
    )


class TestCameraConfigFactory:
    """Tests for create_camera_configs()."""

    def test_no_registry_returns_single_camera(self, base_camera_config):
        """When no registry exists, return single camera with ID 1."""
        configs = create_camera_configs(None, base_camera_config)
        assert list(configs.keys()) == [1]
        assert configs[1] == base_camera_config

    def test_empty_registry_returns_single_camera(self, base_camera_config):
        """When registry has no cameras, return single camera with ID 1."""
        registry = CameraRegistryConfig(cameras=[])
        configs = create_camera_configs(registry, base_camera_config)
        assert list(configs.keys()) == [1]

    def test_single_camera_registry(self, base_camera_config):
        """Single camera in registry gets default ID 1 and default name."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(serial_number="SN001"),  # ID and name will default
            ]
        )
        configs = create_camera_configs(registry, base_camera_config)
        assert list(configs.keys()) == [1]
        assert configs[1].serial_number == "SN001"

    def test_multi_camera_registry(self, base_camera_config):
        """Multiple cameras in registry each get their own config."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(id=1, name="Main", serial_number="SN001"),
                CameraDefinition(id=2, name="Side", serial_number="SN002"),
            ]
        )
        configs = create_camera_configs(registry, base_camera_config)
        assert sorted(configs.keys()) == [1, 2]
        assert configs[1].serial_number == "SN001"
        assert configs[2].serial_number == "SN002"

    def test_camera_ids_not_sequential(self, base_camera_config):
        """Camera IDs don't have to be sequential."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(id=5, name="A", serial_number="SN005"),
                CameraDefinition(id=10, name="B", serial_number="SN010"),
            ]
        )
        configs = create_camera_configs(registry, base_camera_config)
        assert sorted(configs.keys()) == [5, 10]

    def test_base_config_is_copied(self, base_camera_config):
        """Each camera gets a deep copy of base config."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(id=1, name="A", serial_number="SN001"),
                CameraDefinition(id=2, name="B", serial_number="SN002"),
            ]
        )
        configs = create_camera_configs(registry, base_camera_config)

        # Verify they're different objects
        assert configs[1] is not configs[2]
        assert configs[1] is not base_camera_config

        # Verify serial numbers are different
        assert configs[1].serial_number == "SN001"
        assert configs[2].serial_number == "SN002"

        # Verify other properties are copied from base
        assert configs[1].camera_type == base_camera_config.camera_type
        assert configs[2].camera_type == base_camera_config.camera_type


class TestGetPrimaryCameraId:
    """Tests for get_primary_camera_id()."""

    def test_single_camera(self):
        """Single camera returns its ID."""
        assert get_primary_camera_id([1]) == 1
        assert get_primary_camera_id([5]) == 5

    def test_multiple_cameras_returns_lowest(self):
        """Multiple cameras returns lowest ID."""
        assert get_primary_camera_id([3, 1, 2]) == 1
        assert get_primary_camera_id([10, 5, 20]) == 5

    def test_empty_list_raises(self):
        """Empty list raises ValueError."""
        with pytest.raises(ValueError, match="No camera IDs provided"):
            get_primary_camera_id([])


class TestMicroscopeCameraAPI:
    """Tests for Microscope multi-camera API."""

    def test_microscope_camera_property(self):
        """microscope.camera returns primary camera for backward compatibility."""
        from control.microscope import Microscope

        # Create mock cameras
        camera1 = MagicMock()
        camera2 = MagicMock()
        cameras = {1: camera1, 2: camera2}

        # Create minimal microscope (skip normal init)
        microscope = object.__new__(Microscope)
        microscope._cameras = cameras
        microscope._primary_camera_id = 1

        assert microscope.camera is camera1

    def test_microscope_get_camera(self):
        """microscope.get_camera() returns camera by ID."""
        from control.microscope import Microscope

        camera1 = MagicMock()
        camera2 = MagicMock()
        cameras = {1: camera1, 2: camera2}

        microscope = object.__new__(Microscope)
        microscope._cameras = cameras
        microscope._primary_camera_id = 1

        assert microscope.get_camera(1) is camera1
        assert microscope.get_camera(2) is camera2

    def test_microscope_get_camera_invalid_id(self):
        """microscope.get_camera() raises for invalid ID."""
        from control.microscope import Microscope

        microscope = object.__new__(Microscope)
        microscope._cameras = {1: MagicMock()}
        microscope._primary_camera_id = 1

        with pytest.raises(ValueError, match="Camera ID 99 not found"):
            microscope.get_camera(99)

    def test_microscope_get_camera_ids(self):
        """microscope.get_camera_ids() returns sorted IDs."""
        from control.microscope import Microscope

        microscope = object.__new__(Microscope)
        microscope._cameras = {5: MagicMock(), 1: MagicMock(), 3: MagicMock()}
        microscope._primary_camera_id = 1

        assert microscope.get_camera_ids() == [1, 3, 5]

    def test_microscope_get_camera_count(self):
        """microscope.get_camera_count() returns number of cameras."""
        from control.microscope import Microscope

        microscope = object.__new__(Microscope)
        microscope._cameras = {1: MagicMock(), 2: MagicMock()}
        microscope._primary_camera_id = 1

        assert microscope.get_camera_count() == 2

    def test_microscope_backward_compat_single_camera(self):
        """Passing single camera wraps it in dict with ID 1."""
        from control.microscope import Microscope

        single_camera = MagicMock()

        microscope = object.__new__(Microscope)
        microscope._log = MagicMock()

        # Simulate __init__ logic for camera handling
        cameras = single_camera  # Not a dict
        if isinstance(cameras, dict):
            microscope._cameras = cameras
        else:
            microscope._cameras = {1: cameras}
        microscope._primary_camera_id = get_primary_camera_id(list(microscope._cameras.keys()))

        assert microscope._cameras == {1: single_camera}
        assert microscope._primary_camera_id == 1
        assert microscope.camera is single_camera


class TestLiveControllerMultiCamera:
    """Tests for LiveController multi-camera support."""

    def _create_mock_microscope(self, cameras_dict):
        """Create a mock microscope with given cameras."""
        from control.microscope import Microscope

        microscope = object.__new__(Microscope)
        microscope._cameras = cameras_dict
        microscope._primary_camera_id = min(cameras_dict.keys())
        microscope._log = MagicMock()
        microscope.config_repo = MagicMock()
        return microscope

    def _create_mock_channel(self, name="Channel", camera_id=None):
        """Create a mock acquisition channel."""
        channel = MagicMock()
        channel.name = name
        channel.camera = camera_id
        channel.exposure_time = 100
        channel.analog_gain = 1.0
        return channel

    def test_live_controller_tracks_active_camera(self):
        """LiveController tracks active camera ID."""
        from control.core.live_controller import LiveController

        camera1 = MagicMock()
        camera2 = MagicMock()
        microscope = self._create_mock_microscope({1: camera1, 2: camera2})

        controller = LiveController(microscope, camera1, control_illumination=False)

        assert controller._active_camera_id == 1
        assert controller.get_active_camera_id() == 1
        assert controller.camera is camera1

    def test_get_target_camera_id_returns_channel_camera(self):
        """_get_target_camera_id returns channel's camera ID when specified."""
        from control.core.live_controller import LiveController

        camera1 = MagicMock()
        camera2 = MagicMock()
        microscope = self._create_mock_microscope({1: camera1, 2: camera2})

        controller = LiveController(microscope, camera1, control_illumination=False)

        channel = self._create_mock_channel(camera_id=2)
        assert controller._get_target_camera_id(channel) == 2

    def test_get_target_camera_id_returns_primary_for_none(self):
        """_get_target_camera_id returns primary camera ID when channel.camera is None."""
        from control.core.live_controller import LiveController

        camera1 = MagicMock()
        camera2 = MagicMock()
        microscope = self._create_mock_microscope({1: camera1, 2: camera2})

        controller = LiveController(microscope, camera1, control_illumination=False)

        channel = self._create_mock_channel(camera_id=None)
        assert controller._get_target_camera_id(channel) == 1

    def test_switch_camera_updates_camera_reference(self):
        """_switch_camera updates the camera reference."""
        from control.core.live_controller import LiveController

        camera1 = MagicMock()
        camera2 = MagicMock()
        microscope = self._create_mock_microscope({1: camera1, 2: camera2})

        controller = LiveController(microscope, camera1, control_illumination=False)
        assert controller.camera is camera1

        controller._switch_camera(2)

        assert controller.camera is camera2
        assert controller._active_camera_id == 2

    def test_switch_camera_noop_when_same_camera(self):
        """_switch_camera does nothing when switching to same camera."""
        from control.core.live_controller import LiveController

        camera1 = MagicMock()
        microscope = self._create_mock_microscope({1: camera1})

        controller = LiveController(microscope, camera1, control_illumination=False)

        # This should be a no-op
        controller._switch_camera(1)

        assert controller.camera is camera1
        assert controller._active_camera_id == 1

    def test_switch_camera_raises_for_invalid_id(self):
        """_switch_camera raises ValueError for invalid camera ID."""
        from control.core.live_controller import LiveController

        camera1 = MagicMock()
        microscope = self._create_mock_microscope({1: camera1})

        controller = LiveController(microscope, camera1, control_illumination=False)

        with pytest.raises(ValueError, match="Camera ID 99 not found"):
            controller._switch_camera(99)

    def test_set_microscope_mode_switches_camera(self):
        """set_microscope_mode switches to channel's camera."""
        from control.core.live_controller import LiveController

        camera1 = MagicMock()
        camera2 = MagicMock()
        microscope = self._create_mock_microscope({1: camera1, 2: camera2})

        controller = LiveController(microscope, camera1, control_illumination=False)
        controller.is_live = False  # Not live, so no streaming operations

        channel = self._create_mock_channel(name="Camera2 Channel", camera_id=2)
        controller.set_microscope_mode(channel)

        assert controller.camera is camera2
        assert controller._active_camera_id == 2
        camera2.set_exposure_time.assert_called_once_with(100)

    def test_set_microscope_mode_stays_on_same_camera(self):
        """set_microscope_mode doesn't switch when channel uses same camera."""
        from control.core.live_controller import LiveController

        camera1 = MagicMock()
        camera2 = MagicMock()
        microscope = self._create_mock_microscope({1: camera1, 2: camera2})

        controller = LiveController(microscope, camera1, control_illumination=False)
        controller.is_live = False

        # Channel uses camera 1 (same as current)
        channel = self._create_mock_channel(name="Camera1 Channel", camera_id=1)
        controller.set_microscope_mode(channel)

        assert controller.camera is camera1
        assert controller._active_camera_id == 1
        camera1.set_exposure_time.assert_called_once_with(100)

    def test_set_microscope_mode_uses_primary_for_none_camera(self):
        """set_microscope_mode uses primary camera when channel.camera is None."""
        from control.core.live_controller import LiveController

        camera1 = MagicMock()
        camera2 = MagicMock()
        microscope = self._create_mock_microscope({1: camera1, 2: camera2})

        controller = LiveController(microscope, camera1, control_illumination=False)
        controller.is_live = False

        # Channel has no camera specified
        channel = self._create_mock_channel(camera_id=None)
        controller.set_microscope_mode(channel)

        assert controller.camera is camera1
        assert controller._active_camera_id == 1

    def test_set_microscope_mode_handles_streaming_on_camera_switch(self):
        """set_microscope_mode stops/starts streaming when switching cameras while live."""
        from control.core.live_controller import LiveController

        camera1 = MagicMock()
        camera2 = MagicMock()
        microscope = self._create_mock_microscope({1: camera1, 2: camera2})

        controller = LiveController(microscope, camera1, control_illumination=False)
        controller.is_live = True
        controller.timer_trigger = None  # No active timer

        channel = self._create_mock_channel(name="Camera2 Channel", camera_id=2)
        controller.set_microscope_mode(channel)

        # Old camera streaming stopped
        camera1.stop_streaming.assert_called_once()
        # New camera streaming started
        camera2.start_streaming.assert_called_once()
        # New camera has exposure set
        camera2.set_exposure_time.assert_called_once_with(100)

    def test_set_microscope_mode_invalid_camera_no_state_change(self):
        """set_microscope_mode with invalid camera ID changes nothing."""
        from control.core.live_controller import LiveController

        camera1 = MagicMock()
        microscope = self._create_mock_microscope({1: camera1})

        controller = LiveController(microscope, camera1, control_illumination=False)
        controller.is_live = False

        # Set an initial configuration
        initial_channel = self._create_mock_channel(name="Initial", camera_id=1)
        controller.set_microscope_mode(initial_channel)
        camera1.reset_mock()

        # Try to switch to non-existent camera 99
        invalid_channel = self._create_mock_channel(name="Invalid Camera", camera_id=99)
        controller.set_microscope_mode(invalid_channel)

        # State should be unchanged
        assert controller.camera is camera1
        assert controller._active_camera_id == 1
        assert controller.currentConfiguration is initial_channel  # Not changed
        # Camera should not have been touched
        camera1.set_exposure_time.assert_not_called()
        camera1.stop_streaming.assert_not_called()


class TestChannelGroupValidation:
    """Tests for validate_channel_group function."""

    def test_duplicate_channel_names_detected(self):
        """Duplicate channel names in a group are detected."""
        from control.models import (
            AcquisitionChannel,
            CameraSettings,
            ChannelGroup,
            ChannelGroupEntry,
            IlluminationSettings,
            SynchronizationMode,
            validate_channel_group,
        )

        # Create channels
        channels = [
            AcquisitionChannel(
                name="Channel A",
                camera_settings=CameraSettings(exposure_time_ms=100, gain_mode=1.0),
                illumination_settings=IlluminationSettings(illumination_channel="BF", intensity=50.0),
            ),
        ]

        # Create group with duplicate channel
        group = ChannelGroup(
            name="Test Group",
            synchronization=SynchronizationMode.SEQUENTIAL,
            channels=[
                ChannelGroupEntry(name="Channel A"),
                ChannelGroupEntry(name="Channel A"),  # Duplicate!
            ],
        )

        errors = validate_channel_group(group, channels)
        assert any("duplicate channels" in e.lower() for e in errors)

    def test_no_duplicate_channels_passes(self):
        """Group without duplicate channels passes validation."""
        from control.models import (
            AcquisitionChannel,
            CameraSettings,
            ChannelGroup,
            ChannelGroupEntry,
            IlluminationSettings,
            SynchronizationMode,
            validate_channel_group,
        )

        # Create channels
        channels = [
            AcquisitionChannel(
                name="Channel A",
                camera_settings=CameraSettings(exposure_time_ms=100, gain_mode=1.0),
                illumination_settings=IlluminationSettings(illumination_channel="BF", intensity=50.0),
            ),
            AcquisitionChannel(
                name="Channel B",
                camera_settings=CameraSettings(exposure_time_ms=100, gain_mode=1.0),
                illumination_settings=IlluminationSettings(illumination_channel="GFP", intensity=50.0),
            ),
        ]

        # Create group with unique channels
        group = ChannelGroup(
            name="Test Group",
            synchronization=SynchronizationMode.SEQUENTIAL,
            channels=[
                ChannelGroupEntry(name="Channel A"),
                ChannelGroupEntry(name="Channel B"),
            ],
        )

        errors = validate_channel_group(group, channels)
        # Should not have duplicate channel error
        assert not any("duplicate channels" in e.lower() for e in errors)
