"""
Unit tests for v1.1 configuration models.

Tests the new models introduced in schema v1.1:
- CameraRegistryConfig and CameraDefinition
- FilterWheelRegistryConfig and FilterWheelDefinition
- ChannelGroup, ChannelGroupEntry, SynchronizationMode
- Migration utilities
"""

import pytest
from pydantic import ValidationError

from control.models import (
    CameraDefinition,
    CameraRegistryConfig,
    FilterWheelDefinition,
    FilterWheelRegistryConfig,
    ChannelGroup,
    ChannelGroupEntry,
    SynchronizationMode,
    AcquisitionChannel,
    CameraSettings,
    IlluminationSettings,
    validate_channel_group,
)
from control.core.config.migration import (
    get_config_version,
    needs_migration,
    migrate_channel_config_v1_to_v1_1,
    migrate_illumination_config_v1_to_v1_1,
)


class TestCameraDefinition:
    """Tests for CameraDefinition model."""

    def test_camera_definition_creation(self):
        """Test creating a camera definition with required fields."""
        camera = CameraDefinition(
            name="Main Camera",
            serial_number="ABC12345",
        )
        assert camera.name == "Main Camera"
        assert camera.serial_number == "ABC12345"
        assert camera.model is None

    def test_camera_definition_with_model(self):
        """Test camera definition with optional model field."""
        camera = CameraDefinition(
            name="Main Camera",
            serial_number="ABC12345",
            model="Hamamatsu C15440",
        )
        assert camera.model == "Hamamatsu C15440"

    def test_camera_definition_empty_name_rejected(self):
        """Test that empty camera name is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CameraDefinition(name="", serial_number="ABC12345")
        assert "String should have at least 1 character" in str(exc_info.value)

    def test_camera_definition_empty_serial_rejected(self):
        """Test that empty serial number is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CameraDefinition(name="Main Camera", serial_number="")
        assert "String should have at least 1 character" in str(exc_info.value)

    def test_camera_definition_extra_fields_rejected(self):
        """Test that extra fields are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CameraDefinition(
                name="Main Camera",
                serial_number="ABC12345",
                unknown_field="value",
            )
        assert "Extra inputs are not permitted" in str(exc_info.value)


class TestCameraRegistryConfig:
    """Tests for CameraRegistryConfig model."""

    def test_empty_registry(self):
        """Test creating an empty camera registry."""
        registry = CameraRegistryConfig()
        assert registry.version == 1.1
        assert registry.cameras == []

    def test_registry_with_cameras(self):
        """Test registry with multiple cameras."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(name="Main Camera", serial_number="ABC12345"),
                CameraDefinition(name="Side Camera", serial_number="DEF67890"),
            ]
        )
        assert len(registry.cameras) == 2

    def test_get_camera_by_name_found(self):
        """Test finding camera by name."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(name="Main Camera", serial_number="ABC12345"),
                CameraDefinition(name="Side Camera", serial_number="DEF67890"),
            ]
        )
        camera = registry.get_camera_by_name("Main Camera")
        assert camera is not None
        assert camera.serial_number == "ABC12345"

    def test_get_camera_by_name_not_found(self):
        """Test returning None when camera name not found."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(name="Main Camera", serial_number="ABC12345"),
            ]
        )
        camera = registry.get_camera_by_name("Unknown Camera")
        assert camera is None

    def test_get_camera_by_sn_found(self):
        """Test finding camera by serial number."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(name="Main Camera", serial_number="ABC12345"),
            ]
        )
        camera = registry.get_camera_by_sn("ABC12345")
        assert camera is not None
        assert camera.name == "Main Camera"

    def test_get_serial_number_mapping(self):
        """Test name to serial number mapping."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(name="Main Camera", serial_number="ABC12345"),
            ]
        )
        sn = registry.get_serial_number("Main Camera")
        assert sn == "ABC12345"

    def test_get_camera_names(self):
        """Test getting list of all camera names."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(name="Main Camera", serial_number="ABC12345"),
                CameraDefinition(name="Side Camera", serial_number="DEF67890"),
            ]
        )
        names = registry.get_camera_names()
        assert names == ["Main Camera", "Side Camera"]

    def test_duplicate_camera_names_rejected(self):
        """Test that duplicate camera names are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CameraRegistryConfig(
                cameras=[
                    CameraDefinition(name="Main Camera", serial_number="ABC12345"),
                    CameraDefinition(name="Main Camera", serial_number="DEF67890"),
                ]
            )
        assert "Camera names must be unique" in str(exc_info.value)

    def test_duplicate_serial_numbers_rejected(self):
        """Test that duplicate serial numbers are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CameraRegistryConfig(
                cameras=[
                    CameraDefinition(name="Camera 1", serial_number="ABC12345"),
                    CameraDefinition(name="Camera 2", serial_number="ABC12345"),
                ]
            )
        assert "Camera serial numbers must be unique" in str(exc_info.value)


class TestFilterWheelDefinition:
    """Tests for FilterWheelDefinition model."""

    def test_filter_wheel_creation(self):
        """Test creating a filter wheel definition."""
        wheel = FilterWheelDefinition(
            name="Emission Filter Wheel",
            id=1,
            positions={1: "Empty", 2: "BP 525/50", 3: "BP 600/50"},
        )
        assert wheel.name == "Emission Filter Wheel"
        assert wheel.id == 1
        assert len(wheel.positions) == 3

    def test_get_filter_name_valid_position(self):
        """Test getting filter name at valid position."""
        wheel = FilterWheelDefinition(
            name="Test Wheel",
            id=1,
            positions={1: "Empty", 2: "BP 525/50"},
        )
        assert wheel.get_filter_name(1) == "Empty"
        assert wheel.get_filter_name(2) == "BP 525/50"

    def test_get_filter_name_invalid_position(self):
        """Test returning None for invalid position."""
        wheel = FilterWheelDefinition(
            name="Test Wheel",
            id=1,
            positions={1: "Empty"},
        )
        assert wheel.get_filter_name(99) is None

    def test_get_position_by_filter_found(self):
        """Test reverse lookup: filter name to position."""
        wheel = FilterWheelDefinition(
            name="Test Wheel",
            id=1,
            positions={1: "Empty", 2: "BP 525/50"},
        )
        assert wheel.get_position_by_filter("BP 525/50") == 2

    def test_get_position_by_filter_not_found(self):
        """Test returning None for unknown filter."""
        wheel = FilterWheelDefinition(
            name="Test Wheel",
            id=1,
            positions={1: "Empty"},
        )
        assert wheel.get_position_by_filter("Unknown") is None

    def test_get_filter_names(self):
        """Test getting list of all filter names."""
        wheel = FilterWheelDefinition(
            name="Test Wheel",
            id=1,
            positions={1: "Empty", 2: "BP 525/50", 3: "BP 600/50"},
        )
        names = wheel.get_filter_names()
        assert set(names) == {"Empty", "BP 525/50", "BP 600/50"}

    def test_get_positions_sorted(self):
        """Test that positions are returned sorted."""
        wheel = FilterWheelDefinition(
            name="Test Wheel",
            id=1,
            positions={3: "C", 1: "A", 2: "B"},
        )
        assert wheel.get_positions() == [1, 2, 3]

    def test_empty_name_rejected(self):
        """Test that empty wheel name is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            FilterWheelDefinition(name="", id=1, positions={1: "Empty"})
        assert "String should have at least 1 character" in str(exc_info.value)

    def test_negative_id_rejected(self):
        """Test that negative hardware ID is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            FilterWheelDefinition(name="Wheel", id=-1, positions={1: "Empty"})
        assert "greater than or equal to 0" in str(exc_info.value)

    def test_position_zero_rejected(self):
        """Test that position 0 is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            FilterWheelDefinition(name="Wheel", id=1, positions={0: "Empty"})
        assert "Position 0 must be >= 1" in str(exc_info.value)

    def test_empty_filter_name_rejected(self):
        """Test that empty filter name is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            FilterWheelDefinition(name="Wheel", id=1, positions={1: ""})
        assert "cannot be empty" in str(exc_info.value)


class TestFilterWheelRegistryConfig:
    """Tests for FilterWheelRegistryConfig model."""

    def test_empty_registry(self):
        """Test creating an empty filter wheel registry."""
        registry = FilterWheelRegistryConfig()
        assert registry.version == 1.1
        assert registry.filter_wheels == []

    def test_registry_with_wheels(self):
        """Test registry with multiple filter wheels."""
        registry = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(name="Wheel 1", id=1, positions={1: "Empty"}),
                FilterWheelDefinition(name="Wheel 2", id=2, positions={1: "Empty"}),
            ]
        )
        assert len(registry.filter_wheels) == 2

    def test_get_wheel_by_name(self):
        """Test finding wheel by name."""
        registry = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(name="Emission", id=1, positions={1: "Empty"}),
            ]
        )
        wheel = registry.get_wheel_by_name("Emission")
        assert wheel is not None
        assert wheel.id == 1

    def test_get_wheel_by_id(self):
        """Test finding wheel by hardware ID."""
        registry = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(name="Emission", id=1, positions={1: "Empty"}),
            ]
        )
        wheel = registry.get_wheel_by_id(1)
        assert wheel is not None
        assert wheel.name == "Emission"

    def test_get_hardware_id(self):
        """Test getting hardware ID for wheel name."""
        registry = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(name="Emission", id=5, positions={1: "Empty"}),
            ]
        )
        assert registry.get_hardware_id("Emission") == 5

    def test_compound_lookup(self):
        """Test get_filter_name(wheel_name, position)."""
        registry = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(
                    name="Emission",
                    id=1,
                    positions={1: "Empty", 2: "BP 525/50"},
                ),
            ]
        )
        assert registry.get_filter_name("Emission", 2) == "BP 525/50"

    def test_duplicate_wheel_names_rejected(self):
        """Test that duplicate wheel names are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            FilterWheelRegistryConfig(
                filter_wheels=[
                    FilterWheelDefinition(name="Wheel", id=1, positions={1: "Empty"}),
                    FilterWheelDefinition(name="Wheel", id=2, positions={1: "Empty"}),
                ]
            )
        assert "Filter wheel names must be unique" in str(exc_info.value)

    def test_duplicate_wheel_ids_rejected(self):
        """Test that duplicate wheel IDs are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            FilterWheelRegistryConfig(
                filter_wheels=[
                    FilterWheelDefinition(name="Wheel 1", id=1, positions={1: "Empty"}),
                    FilterWheelDefinition(name="Wheel 2", id=1, positions={1: "Empty"}),
                ]
            )
        assert "Filter wheel IDs must be unique" in str(exc_info.value)


class TestChannelGroupEntry:
    """Tests for ChannelGroupEntry model."""

    def test_entry_creation(self):
        """Test creating a channel group entry."""
        entry = ChannelGroupEntry(name="BF LED matrix full")
        assert entry.name == "BF LED matrix full"
        assert entry.offset_us == 0.0

    def test_entry_with_offset(self):
        """Test entry with custom offset."""
        entry = ChannelGroupEntry(name="Fluorescence 488nm", offset_us=100.0)
        assert entry.offset_us == 100.0

    def test_empty_name_rejected(self):
        """Test that empty channel name is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            ChannelGroupEntry(name="")
        assert "String should have at least 1 character" in str(exc_info.value)

    def test_negative_offset_rejected(self):
        """Test that negative offset is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            ChannelGroupEntry(name="Channel", offset_us=-10.0)
        assert "greater than or equal to 0" in str(exc_info.value)


class TestChannelGroup:
    """Tests for ChannelGroup model."""

    def test_sequential_group(self):
        """Test creating a sequential channel group."""
        group = ChannelGroup(
            name="Standard",
            synchronization=SynchronizationMode.SEQUENTIAL,
            channels=[
                ChannelGroupEntry(name="Channel A"),
                ChannelGroupEntry(name="Channel B"),
            ],
        )
        assert group.synchronization == SynchronizationMode.SEQUENTIAL
        assert len(group.channels) == 2

    def test_simultaneous_group(self):
        """Test creating a simultaneous channel group."""
        group = ChannelGroup(
            name="Dual Capture",
            synchronization=SynchronizationMode.SIMULTANEOUS,
            channels=[
                ChannelGroupEntry(name="Channel A", offset_us=0),
                ChannelGroupEntry(name="Channel B", offset_us=100),
            ],
        )
        assert group.synchronization == SynchronizationMode.SIMULTANEOUS

    def test_default_synchronization(self):
        """Test that default synchronization is sequential."""
        group = ChannelGroup(
            name="Default",
            channels=[ChannelGroupEntry(name="Channel A")],
        )
        assert group.synchronization == SynchronizationMode.SEQUENTIAL

    def test_get_channel_names(self):
        """Test extracting channel names from group."""
        group = ChannelGroup(
            name="Test",
            channels=[
                ChannelGroupEntry(name="A"),
                ChannelGroupEntry(name="B"),
            ],
        )
        assert group.get_channel_names() == ["A", "B"]

    def test_get_channel_offset_found(self):
        """Test getting offset for existing channel."""
        group = ChannelGroup(
            name="Test",
            channels=[
                ChannelGroupEntry(name="A", offset_us=50.0),
            ],
        )
        assert group.get_channel_offset("A") == 50.0

    def test_get_channel_offset_not_found(self):
        """Test default offset (0) for unknown channel."""
        group = ChannelGroup(
            name="Test",
            channels=[ChannelGroupEntry(name="A")],
        )
        assert group.get_channel_offset("Unknown") == 0.0

    def test_get_channels_sorted_by_offset(self):
        """Test sorting channels by trigger offset."""
        group = ChannelGroup(
            name="Test",
            synchronization=SynchronizationMode.SIMULTANEOUS,
            channels=[
                ChannelGroupEntry(name="C", offset_us=200),
                ChannelGroupEntry(name="A", offset_us=0),
                ChannelGroupEntry(name="B", offset_us=100),
            ],
        )
        sorted_channels = group.get_channels_sorted_by_offset()
        assert [c.name for c in sorted_channels] == ["A", "B", "C"]

    def test_empty_name_rejected(self):
        """Test that empty group name is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            ChannelGroup(name="", channels=[ChannelGroupEntry(name="A")])
        assert "String should have at least 1 character" in str(exc_info.value)

    def test_empty_channels_rejected(self):
        """Test that empty channels list is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            ChannelGroup(name="Test", channels=[])
        assert "at least 1" in str(exc_info.value).lower()


class TestValidateChannelGroup:
    """Tests for validate_channel_group function."""

    def _make_channel(self, name: str, camera: str = "Main Camera") -> AcquisitionChannel:
        """Helper to create a test channel (v1.1 schema)."""
        return AcquisitionChannel(
            name=name,
            display_color="#FFFFFF",
            camera=camera,
            illumination_settings=IlluminationSettings(
                intensity={"Test": 20.0},
            ),
            camera_settings=CameraSettings(
                exposure_time_ms=20.0,
                gain_mode=0.0,
            ),
        )

    def test_valid_sequential_group(self):
        """Test validation passes for valid sequential group."""
        channels = [
            self._make_channel("Channel A"),
            self._make_channel("Channel B"),
        ]
        group = ChannelGroup(
            name="Test",
            synchronization=SynchronizationMode.SEQUENTIAL,
            channels=[
                ChannelGroupEntry(name="Channel A"),
                ChannelGroupEntry(name="Channel B"),
            ],
        )
        errors = validate_channel_group(group, channels)
        assert errors == []

    def test_invalid_channel_reference(self):
        """Test error when channel name not in channels list."""
        channels = [self._make_channel("Channel A")]
        group = ChannelGroup(
            name="Test",
            channels=[ChannelGroupEntry(name="Unknown Channel")],
        )
        errors = validate_channel_group(group, channels)
        assert len(errors) == 1
        assert "not found" in errors[0]

    def test_offset_warning_in_sequential_mode(self):
        """Test warning when offset specified for sequential mode."""
        channels = [self._make_channel("Channel A")]
        group = ChannelGroup(
            name="Test",
            synchronization=SynchronizationMode.SEQUENTIAL,
            channels=[ChannelGroupEntry(name="Channel A", offset_us=100)],
        )
        errors = validate_channel_group(group, channels)
        assert len(errors) == 1
        assert "offset will be ignored" in errors[0]

    def test_duplicate_camera_in_simultaneous_mode(self):
        """Test error when same camera used twice in simultaneous mode."""
        channels = [
            self._make_channel("Channel A", camera="Main Camera"),
            self._make_channel("Channel B", camera="Main Camera"),
        ]
        group = ChannelGroup(
            name="Test",
            synchronization=SynchronizationMode.SIMULTANEOUS,
            channels=[
                ChannelGroupEntry(name="Channel A"),
                ChannelGroupEntry(name="Channel B"),
            ],
        )
        errors = validate_channel_group(group, channels)
        assert len(errors) == 1
        assert "same camera" in errors[0]


class TestMigrationUtilities:
    """Tests for migration utility functions."""

    def test_get_config_version_present(self):
        """Test extracting version when present."""
        config = {"version": 1.1, "channels": []}
        assert get_config_version(config) == 1.1

    def test_get_config_version_integer(self):
        """Test version as integer is converted to float."""
        config = {"version": 1, "channels": []}
        assert get_config_version(config) == 1.0

    def test_get_config_version_missing_defaults_to_1(self):
        """Test default version 1 when field missing."""
        config = {"channels": []}
        assert get_config_version(config) == 1.0

    def test_get_config_version_none_config(self):
        """Test handling of None config."""
        assert get_config_version(None) == 1.0

    def test_get_config_version_invalid_value(self):
        """Test handling of invalid version value."""
        config = {"version": "invalid"}
        assert get_config_version(config) == 1.0

    def test_needs_migration_below_target(self):
        """Test migration needed for v1.0 config."""
        config = {"version": 1, "channels": []}
        assert needs_migration(config, target_version=1.1) is True

    def test_needs_migration_at_target(self):
        """Test no migration for current version."""
        config = {"version": 1.1, "channels": []}
        assert needs_migration(config, target_version=1.1) is False

    def test_needs_migration_above_target(self):
        """Test no migration for higher version."""
        config = {"version": 1.2, "channels": []}
        assert needs_migration(config, target_version=1.1) is False

    def test_migrate_channel_config_adds_channel_groups(self):
        """Test migration adds empty channel_groups list."""
        config = {"version": 1, "channels": [{"name": "Test"}]}
        migrated = migrate_channel_config_v1_to_v1_1(config)
        assert migrated["channel_groups"] == []
        assert migrated["version"] == 1.1

    def test_migrate_channel_config_preserves_existing_data(self):
        """Test migration preserves existing channel data while adding v1.1 fields."""
        config = {"version": 1, "channels": [{"name": "Test", "value": 42}]}
        migrated = migrate_channel_config_v1_to_v1_1(config)
        # Original data is preserved
        assert migrated["channels"][0]["name"] == "Test"
        assert migrated["channels"][0]["value"] == 42
        # v1.1 fields are added
        assert "display_color" in migrated["channels"][0]
        assert "camera_settings" in migrated["channels"][0]

    def test_migrate_channel_config_idempotent(self):
        """Test migrating already-migrated config is no-op."""
        config = {"version": 1.1, "channels": [], "channel_groups": []}
        migrated = migrate_channel_config_v1_to_v1_1(config)
        assert migrated == config

    def test_migrate_channel_config_deep_copy(self):
        """Test that migration doesn't modify original config."""
        original_channels = [{"name": "Test"}]
        config = {"version": 1, "channels": original_channels}
        migrated = migrate_channel_config_v1_to_v1_1(config)
        # Modify migrated to verify original is unchanged
        migrated["channels"].append({"name": "New"})
        assert len(config["channels"]) == 1
        assert len(original_channels) == 1

    def test_migrate_illumination_config(self):
        """Test illumination config migration bumps version."""
        config = {"version": 1, "channels": []}
        migrated = migrate_illumination_config_v1_to_v1_1(config)
        assert migrated["version"] == 1.1

    def test_migrate_illumination_config_idempotent(self):
        """Test illumination migration is idempotent."""
        config = {"version": 1.1, "channels": []}
        migrated = migrate_illumination_config_v1_to_v1_1(config)
        assert migrated == config

    def test_migrate_extracts_display_color_from_camera_settings(self):
        """Test display_color is moved from camera_settings to channel level."""
        config = {
            "version": 1,
            "channels": [
                {
                    "name": "Test",
                    "camera_settings": {
                        "camera_1": {
                            "display_color": "#FF0000",
                            "exposure_time_ms": 20.0,
                            "gain_mode": 10.0,
                        }
                    },
                    "illumination_settings": {"intensity": {"Test": 20.0}},
                }
            ],
        }
        migrated = migrate_channel_config_v1_to_v1_1(config)
        assert migrated["channels"][0]["display_color"] == "#FF0000"
        # display_color should not be in camera_settings anymore
        assert "display_color" not in migrated["channels"][0]["camera_settings"]

    def test_migrate_flattens_camera_settings(self):
        """Test camera_settings is flattened from Dict to single object."""
        config = {
            "version": 1,
            "channels": [
                {
                    "name": "Test",
                    "camera_settings": {
                        "camera_1": {
                            "exposure_time_ms": 50.0,
                            "gain_mode": 5.0,
                            "pixel_format": "Mono12",
                        }
                    },
                    "illumination_settings": {"intensity": {"Test": 20.0}},
                }
            ],
        }
        migrated = migrate_channel_config_v1_to_v1_1(config)
        cam = migrated["channels"][0]["camera_settings"]
        # Should be a flat dict, not nested
        assert "camera_1" not in cam
        assert cam["exposure_time_ms"] == 50.0
        assert cam["gain_mode"] == 5.0
        assert cam["pixel_format"] == "Mono12"

    def test_migrate_emission_filter_wheel_position(self):
        """Test emission_filter_wheel_position converts to filter_wheel/filter_position."""
        config = {
            "version": 1,
            "channels": [
                {
                    "name": "Test",
                    "emission_filter_wheel_position": {1: 3},
                    "camera_settings": {"camera_1": {"exposure_time_ms": 20.0, "gain_mode": 0.0}},
                    "illumination_settings": {"intensity": {"Test": 20.0}},
                }
            ],
        }
        migrated = migrate_channel_config_v1_to_v1_1(config)
        ch = migrated["channels"][0]
        assert "emission_filter_wheel_position" not in ch
        assert ch["filter_position"] == 3
        # Without registry, filter_wheel is None (needs manual mapping)
        assert ch["filter_wheel"] is None

    def test_migrate_with_filter_wheel_registry(self):
        """Test migration maps wheel ID to name when registry provided."""
        registry = FilterWheelRegistryConfig(
            filter_wheels=[FilterWheelDefinition(name="Emission Wheel", id=1, positions={1: "DAPI", 2: "GFP"})]
        )
        config = {
            "version": 1,
            "channels": [
                {
                    "name": "Test",
                    "emission_filter_wheel_position": {1: 2},
                    "camera_settings": {"camera_1": {"exposure_time_ms": 20.0, "gain_mode": 0.0}},
                    "illumination_settings": {"intensity": {"Test": 20.0}},
                }
            ],
        }
        migrated = migrate_channel_config_v1_to_v1_1(config, filter_wheel_registry=registry)
        ch = migrated["channels"][0]
        assert ch["filter_wheel"] == "Emission Wheel"
        assert ch["filter_position"] == 2

    def test_migrate_with_invalid_wheel_id_graceful(self):
        """Test migration handles invalid wheel_id gracefully."""
        registry = FilterWheelRegistryConfig(
            filter_wheels=[FilterWheelDefinition(name="Emission Wheel", id=1, positions={1: "DAPI"})]
        )
        config = {
            "version": 1,
            "channels": [
                {
                    "name": "Test",
                    # Invalid wheel ID (string instead of int key)
                    "emission_filter_wheel_position": {"invalid": 2},
                    "camera_settings": {"camera_1": {"exposure_time_ms": 20.0, "gain_mode": 0.0}},
                    "illumination_settings": {"intensity": {"Test": 20.0}},
                }
            ],
        }
        # Should not raise, should log warning and set filter_wheel to None
        migrated = migrate_channel_config_v1_to_v1_1(config, filter_wheel_registry=registry)
        ch = migrated["channels"][0]
        assert ch["filter_wheel"] is None

    def test_migrate_confocal_override_camera_settings(self):
        """Test confocal_override.camera_settings is flattened."""
        config = {
            "version": 1,
            "channels": [
                {
                    "name": "Test",
                    "camera_settings": {"cam1": {"exposure_time_ms": 20.0, "gain_mode": 0.0}},
                    "illumination_settings": {"intensity": {"Test": 20.0}},
                    "confocal_override": {"camera_settings": {"cam1": {"exposure_time_ms": 100.0, "gain_mode": 20.0}}},
                }
            ],
        }
        migrated = migrate_channel_config_v1_to_v1_1(config)
        override_cam = migrated["channels"][0]["confocal_override"]["camera_settings"]
        assert override_cam["exposure_time_ms"] == 100.0
        assert override_cam["gain_mode"] == 20.0

    def test_migrate_default_display_color(self):
        """Test default display_color is used when not in camera_settings."""
        config = {
            "version": 1,
            "channels": [
                {
                    "name": "Test",
                    "camera_settings": {
                        "camera_1": {
                            # No display_color
                            "exposure_time_ms": 20.0,
                            "gain_mode": 10.0,
                        }
                    },
                    "illumination_settings": {"intensity": {"Test": 20.0}},
                }
            ],
        }
        migrated = migrate_channel_config_v1_to_v1_1(config)
        # Should have default display_color
        assert migrated["channels"][0]["display_color"] == "#FFFFFF"


class TestAcquisitionChannelConstraints:
    """Tests for AcquisitionChannel validation constraints."""

    def test_empty_channel_name_rejected(self):
        """Test that empty channel name is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            AcquisitionChannel(
                name="",
                illumination_settings=IlluminationSettings(intensity={"Test": 20.0}),
                camera_settings=CameraSettings(exposure_time_ms=20.0, gain_mode=0.0),
            )
        assert "String should have at least 1 character" in str(exc_info.value)

    def test_negative_exposure_rejected(self):
        """Test that negative exposure time is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CameraSettings(exposure_time_ms=-1.0, gain_mode=0.0)
        assert "greater than 0" in str(exc_info.value)

    def test_zero_exposure_rejected(self):
        """Test that zero exposure time is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CameraSettings(exposure_time_ms=0.0, gain_mode=0.0)
        assert "greater than 0" in str(exc_info.value)

    def test_negative_gain_rejected(self):
        """Test that negative gain is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CameraSettings(exposure_time_ms=20.0, gain_mode=-1.0)
        assert "greater than or equal to 0" in str(exc_info.value)

    def test_intensity_below_zero_rejected(self):
        """Test that intensity below 0 is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            IlluminationSettings(intensity={"Test": -10.0})
        assert "must be 0-100" in str(exc_info.value)

    def test_intensity_above_100_rejected(self):
        """Test that intensity above 100 is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            IlluminationSettings(intensity={"Test": 150.0})
        assert "must be 0-100" in str(exc_info.value)

    def test_valid_intensity_range(self):
        """Test that valid intensity values are accepted."""
        settings = IlluminationSettings(intensity={"Test": 0.0, "Test2": 100.0, "Test3": 50.0})
        assert settings.intensity["Test"] == 0.0
        assert settings.intensity["Test2"] == 100.0
