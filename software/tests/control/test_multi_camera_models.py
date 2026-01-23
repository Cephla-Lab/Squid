"""
Unit tests for v1.0 configuration models.

Tests the new models introduced in schema v1.0:
- CameraRegistryConfig and CameraDefinition
- FilterWheelRegistryConfig and FilterWheelDefinition
- ChannelGroup, ChannelGroupEntry, SynchronizationMode
- HardwareBindingsConfig and FilterWheelReference
"""

from typing import Optional

import pytest
from pydantic import ValidationError

from control.models import (
    CameraDefinition,
    CameraRegistryConfig,
    FilterWheelDefinition,
    FilterWheelRegistryConfig,
    FilterWheelType,
    FilterWheelReference,
    HardwareBindingsConfig,
    FILTER_WHEEL_SOURCE_CONFOCAL,
    FILTER_WHEEL_SOURCE_STANDALONE,
    ChannelGroup,
    ChannelGroupEntry,
    SynchronizationMode,
    AcquisitionChannel,
    CameraSettings,
    IlluminationSettings,
    validate_channel_group,
)


class TestCameraDefinition:
    """Tests for CameraDefinition model."""

    def test_camera_definition_creation(self):
        """Test creating a camera definition with all fields."""
        camera = CameraDefinition(
            name="Main Camera",
            id=1,
            serial_number="ABC12345",
        )
        assert camera.name == "Main Camera"
        assert camera.id == 1
        assert camera.serial_number == "ABC12345"
        assert camera.model is None

    def test_camera_definition_minimal(self):
        """Test creating a camera with only required serial_number."""
        camera = CameraDefinition(serial_number="ABC12345")
        assert camera.serial_number == "ABC12345"
        assert camera.name is None
        assert camera.id is None

    def test_camera_definition_with_model(self):
        """Test camera definition with optional model field."""
        camera = CameraDefinition(
            name="Main Camera",
            id=1,
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
            CameraDefinition(serial_number="")
        assert "String should have at least 1 character" in str(exc_info.value)

    def test_camera_definition_extra_fields_rejected(self):
        """Test that extra fields are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CameraDefinition(
                name="Main Camera",
                id=1,
                serial_number="ABC12345",
                unknown_field="value",
            )
        assert "Extra inputs are not permitted" in str(exc_info.value)

    def test_camera_definition_invalid_id_rejected(self):
        """Test that non-positive ID is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CameraDefinition(id=0, serial_number="ABC12345")
        assert "greater than or equal to 1" in str(exc_info.value)


class TestCameraRegistryConfig:
    """Tests for CameraRegistryConfig model."""

    def test_empty_registry(self):
        """Test creating an empty camera registry."""
        registry = CameraRegistryConfig()
        assert registry.version == 1.0
        assert registry.cameras == []

    def test_registry_with_single_camera_defaults(self):
        """Test that single camera gets default id=1 and name='Camera'."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(serial_number="ABC12345"),
            ]
        )
        assert len(registry.cameras) == 1
        assert registry.cameras[0].id == 1
        assert registry.cameras[0].name == "Camera"

    def test_registry_with_cameras(self):
        """Test registry with multiple cameras (requires explicit id)."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(name="Main Camera", id=1, serial_number="ABC12345"),
                CameraDefinition(name="Side Camera", id=2, serial_number="DEF67890"),
            ]
        )
        assert len(registry.cameras) == 2

    def test_multi_camera_requires_id(self):
        """Test that multiple cameras require explicit id."""
        with pytest.raises(ValidationError) as exc_info:
            CameraRegistryConfig(
                cameras=[
                    CameraDefinition(name="Main Camera", serial_number="ABC12345"),
                    CameraDefinition(name="Side Camera", serial_number="DEF67890"),
                ]
            )
        assert "missing required 'id'" in str(exc_info.value)

    def test_get_camera_by_name_found(self):
        """Test finding camera by name."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(name="Main Camera", id=1, serial_number="ABC12345"),
                CameraDefinition(name="Side Camera", id=2, serial_number="DEF67890"),
            ]
        )
        camera = registry.get_camera_by_name("Main Camera")
        assert camera is not None
        assert camera.serial_number == "ABC12345"

    def test_get_camera_by_name_not_found(self):
        """Test returning None when camera name not found."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(name="Main Camera", id=1, serial_number="ABC12345"),
            ]
        )
        camera = registry.get_camera_by_name("Unknown Camera")
        assert camera is None

    def test_get_camera_by_sn_found(self):
        """Test finding camera by serial number."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(name="Main Camera", id=1, serial_number="ABC12345"),
            ]
        )
        camera = registry.get_camera_by_sn("ABC12345")
        assert camera is not None
        assert camera.name == "Main Camera"

    def test_get_camera_by_id_found(self):
        """Test finding camera by ID."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(name="Main Camera", id=1, serial_number="ABC12345"),
                CameraDefinition(name="Side Camera", id=2, serial_number="DEF67890"),
            ]
        )
        camera = registry.get_camera_by_id(2)
        assert camera is not None
        assert camera.name == "Side Camera"

    def test_get_serial_number_mapping(self):
        """Test name to serial number mapping."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(name="Main Camera", id=1, serial_number="ABC12345"),
            ]
        )
        sn = registry.get_serial_number("Main Camera")
        assert sn == "ABC12345"

    def test_get_camera_names(self):
        """Test getting list of all camera names."""
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(name="Main Camera", id=1, serial_number="ABC12345"),
                CameraDefinition(name="Side Camera", id=2, serial_number="DEF67890"),
            ]
        )
        names = registry.get_camera_names()
        assert names == ["Main Camera", "Side Camera"]

    def test_duplicate_camera_names_rejected(self):
        """Test that duplicate camera names are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CameraRegistryConfig(
                cameras=[
                    CameraDefinition(name="Main Camera", id=1, serial_number="ABC12345"),
                    CameraDefinition(name="Main Camera", id=2, serial_number="DEF67890"),
                ]
            )
        assert "Camera names must be unique" in str(exc_info.value)

    def test_duplicate_serial_numbers_rejected(self):
        """Test that duplicate serial numbers are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CameraRegistryConfig(
                cameras=[
                    CameraDefinition(name="Camera 1", id=1, serial_number="ABC12345"),
                    CameraDefinition(name="Camera 2", id=2, serial_number="ABC12345"),
                ]
            )
        assert "Camera serial numbers must be unique" in str(exc_info.value)

    def test_duplicate_camera_ids_rejected(self):
        """Test that duplicate camera IDs are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CameraRegistryConfig(
                cameras=[
                    CameraDefinition(name="Camera 1", id=1, serial_number="ABC12345"),
                    CameraDefinition(name="Camera 2", id=1, serial_number="DEF67890"),
                ]
            )
        assert "Camera IDs must be unique" in str(exc_info.value)


class TestFilterWheelDefinition:
    """Tests for FilterWheelDefinition model."""

    def test_filter_wheel_creation(self):
        """Test creating a filter wheel definition with all fields."""
        wheel = FilterWheelDefinition(
            name="Emission Filter Wheel",
            id=1,
            type=FilterWheelType.EMISSION,
            positions={1: "Empty", 2: "BP 525/50", 3: "BP 600/50"},
        )
        assert wheel.name == "Emission Filter Wheel"
        assert wheel.id == 1
        assert wheel.type == FilterWheelType.EMISSION
        assert len(wheel.positions) == 3

    def test_filter_wheel_minimal(self):
        """Test creating a filter wheel with only required fields (type, positions)."""
        wheel = FilterWheelDefinition(
            type=FilterWheelType.EMISSION,
            positions={1: "Empty", 2: "BP 525/50"},
        )
        assert wheel.type == FilterWheelType.EMISSION
        assert wheel.name is None
        assert wheel.id is None
        assert len(wheel.positions) == 2

    def test_filter_wheel_type_required(self):
        """Test that type field is required."""
        with pytest.raises(ValidationError) as exc_info:
            FilterWheelDefinition(
                name="Filter Wheel",
                id=1,
                positions={1: "Empty"},
            )
        assert "Field required" in str(exc_info.value)

    def test_get_filter_name_valid_position(self):
        """Test getting filter name at valid position."""
        wheel = FilterWheelDefinition(
            name="Test Wheel",
            id=1,
            type=FilterWheelType.EMISSION,
            positions={1: "Empty", 2: "BP 525/50"},
        )
        assert wheel.get_filter_name(1) == "Empty"
        assert wheel.get_filter_name(2) == "BP 525/50"

    def test_get_filter_name_invalid_position(self):
        """Test returning None for invalid position."""
        wheel = FilterWheelDefinition(
            name="Test Wheel",
            id=1,
            type=FilterWheelType.EMISSION,
            positions={1: "Empty"},
        )
        assert wheel.get_filter_name(99) is None

    def test_get_position_by_filter_found(self):
        """Test reverse lookup: filter name to position."""
        wheel = FilterWheelDefinition(
            name="Test Wheel",
            id=1,
            type=FilterWheelType.EMISSION,
            positions={1: "Empty", 2: "BP 525/50"},
        )
        assert wheel.get_position_by_filter("BP 525/50") == 2

    def test_get_position_by_filter_not_found(self):
        """Test returning None for unknown filter."""
        wheel = FilterWheelDefinition(
            name="Test Wheel",
            id=1,
            type=FilterWheelType.EMISSION,
            positions={1: "Empty"},
        )
        assert wheel.get_position_by_filter("Unknown") is None

    def test_get_filter_names(self):
        """Test getting list of all filter names."""
        wheel = FilterWheelDefinition(
            name="Test Wheel",
            id=1,
            type=FilterWheelType.EMISSION,
            positions={1: "Empty", 2: "BP 525/50", 3: "BP 600/50"},
        )
        names = wheel.get_filter_names()
        assert set(names) == {"Empty", "BP 525/50", "BP 600/50"}

    def test_get_positions_sorted(self):
        """Test that positions are returned sorted."""
        wheel = FilterWheelDefinition(
            name="Test Wheel",
            id=1,
            type=FilterWheelType.EMISSION,
            positions={3: "C", 1: "A", 2: "B"},
        )
        assert wheel.get_positions() == [1, 2, 3]

    def test_empty_name_rejected(self):
        """Test that empty wheel name is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            FilterWheelDefinition(name="", id=1, type=FilterWheelType.EMISSION, positions={1: "Empty"})
        assert "String should have at least 1 character" in str(exc_info.value)

    def test_invalid_id_rejected(self):
        """Test that non-positive ID is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            FilterWheelDefinition(name="Wheel", id=0, type=FilterWheelType.EMISSION, positions={1: "Empty"})
        assert "greater than or equal to 1" in str(exc_info.value)

    def test_position_zero_rejected(self):
        """Test that position 0 is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            FilterWheelDefinition(name="Wheel", id=1, type=FilterWheelType.EMISSION, positions={0: "Empty"})
        assert "Position 0 must be >= 1" in str(exc_info.value)

    def test_empty_filter_name_rejected(self):
        """Test that empty filter name is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            FilterWheelDefinition(name="Wheel", id=1, type=FilterWheelType.EMISSION, positions={1: ""})
        assert "cannot be empty" in str(exc_info.value)

    def test_filter_wheel_with_type_emission(self):
        """Test creating a filter wheel with emission type."""
        wheel = FilterWheelDefinition(
            name="Emission Filter Wheel",
            id=1,
            type=FilterWheelType.EMISSION,
            positions={1: "Empty", 2: "BP 525/50"},
        )
        assert wheel.type == FilterWheelType.EMISSION

    def test_filter_wheel_with_type_excitation(self):
        """Test creating a filter wheel with excitation type."""
        wheel = FilterWheelDefinition(
            name="Excitation Filter Wheel",
            id=2,
            type=FilterWheelType.EXCITATION,
            positions={1: "Empty", 2: "BP 470/40"},
        )
        assert wheel.type == FilterWheelType.EXCITATION

    def test_filter_wheel_type_from_string(self):
        """Test that type can be set from string value."""
        wheel = FilterWheelDefinition(
            name="Emission Filter Wheel",
            id=1,
            type="emission",
            positions={1: "Empty"},
        )
        assert wheel.type == FilterWheelType.EMISSION


class TestFilterWheelRegistryConfig:
    """Tests for FilterWheelRegistryConfig model."""

    def test_empty_registry(self):
        """Test creating an empty filter wheel registry."""
        registry = FilterWheelRegistryConfig()
        assert registry.version == 1.0
        assert registry.filter_wheels == []

    def test_single_wheel_defaults(self):
        """Test that single wheel gets default id=1 and name from type."""
        registry = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(type=FilterWheelType.EMISSION, positions={1: "Empty", 2: "BP 525/50"}),
            ]
        )
        assert len(registry.filter_wheels) == 1
        assert registry.filter_wheels[0].id == 1
        assert registry.filter_wheels[0].name == "Emission Wheel"

    def test_registry_with_wheels(self):
        """Test registry with multiple filter wheels (requires explicit id/name)."""
        registry = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(name="Wheel 1", id=1, type=FilterWheelType.EMISSION, positions={1: "Empty"}),
                FilterWheelDefinition(name="Wheel 2", id=2, type=FilterWheelType.EXCITATION, positions={1: "Empty"}),
            ]
        )
        assert len(registry.filter_wheels) == 2

    def test_multi_wheel_requires_id(self):
        """Test that multiple wheels require explicit id."""
        with pytest.raises(ValidationError) as exc_info:
            FilterWheelRegistryConfig(
                filter_wheels=[
                    FilterWheelDefinition(name="Wheel 1", type=FilterWheelType.EMISSION, positions={1: "Empty"}),
                    FilterWheelDefinition(name="Wheel 2", type=FilterWheelType.EXCITATION, positions={1: "Empty"}),
                ]
            )
        assert "missing required 'id'" in str(exc_info.value)

    def test_get_wheel_by_name(self):
        """Test finding wheel by name."""
        registry = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(name="Emission", id=1, type=FilterWheelType.EMISSION, positions={1: "Empty"}),
            ]
        )
        wheel = registry.get_wheel_by_name("Emission")
        assert wheel is not None
        assert wheel.id == 1

    def test_get_wheel_by_id(self):
        """Test finding wheel by hardware ID."""
        registry = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(name="Emission", id=1, type=FilterWheelType.EMISSION, positions={1: "Empty"}),
            ]
        )
        wheel = registry.get_wheel_by_id(1)
        assert wheel is not None
        assert wheel.name == "Emission"

    def test_get_hardware_id(self):
        """Test getting hardware ID for wheel name."""
        registry = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(name="Emission", id=5, type=FilterWheelType.EMISSION, positions={1: "Empty"}),
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
                    type=FilterWheelType.EMISSION,
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
                    FilterWheelDefinition(name="Wheel", id=1, type=FilterWheelType.EMISSION, positions={1: "Empty"}),
                    FilterWheelDefinition(name="Wheel", id=2, type=FilterWheelType.EXCITATION, positions={1: "Empty"}),
                ]
            )
        assert "Filter wheel names must be unique" in str(exc_info.value)

    def test_duplicate_wheel_ids_rejected(self):
        """Test that duplicate wheel IDs are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            FilterWheelRegistryConfig(
                filter_wheels=[
                    FilterWheelDefinition(name="Wheel 1", id=1, type=FilterWheelType.EMISSION, positions={1: "Empty"}),
                    FilterWheelDefinition(
                        name="Wheel 2", id=1, type=FilterWheelType.EXCITATION, positions={1: "Empty"}
                    ),
                ]
            )
        assert "Filter wheel IDs must be unique" in str(exc_info.value)

    def test_get_first_wheel(self):
        """Test get_first_wheel() returns first wheel."""
        registry = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(type=FilterWheelType.EMISSION, positions={1: "Empty", 2: "BP 525/50"}),
            ]
        )
        wheel = registry.get_first_wheel()
        assert wheel is not None
        assert wheel.positions[1] == "Empty"

    def test_get_first_wheel_empty_registry(self):
        """Test get_first_wheel() returns None for empty registry."""
        registry = FilterWheelRegistryConfig()
        assert registry.get_first_wheel() is None

    def test_get_wheels_by_type(self):
        """Test filtering wheels by type."""
        registry = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(name="Emission", id=1, type=FilterWheelType.EMISSION, positions={1: "Empty"}),
                FilterWheelDefinition(name="Excitation", id=2, type=FilterWheelType.EXCITATION, positions={1: "Empty"}),
            ]
        )
        emission_wheels = registry.get_wheels_by_type(FilterWheelType.EMISSION)
        assert len(emission_wheels) == 1
        assert emission_wheels[0].name == "Emission"

    def test_get_excitation_wheels(self):
        """Test convenience method for excitation wheels."""
        registry = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(name="Ex1", id=1, type=FilterWheelType.EXCITATION, positions={1: "Empty"}),
                FilterWheelDefinition(name="Ex2", id=2, type=FilterWheelType.EXCITATION, positions={1: "Empty"}),
                FilterWheelDefinition(name="Em1", id=3, type=FilterWheelType.EMISSION, positions={1: "Empty"}),
            ]
        )
        excitation = registry.get_excitation_wheels()
        assert len(excitation) == 2
        assert {w.name for w in excitation} == {"Ex1", "Ex2"}

    def test_get_emission_wheels(self):
        """Test convenience method for emission wheels."""
        registry = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(name="Ex1", id=1, type=FilterWheelType.EXCITATION, positions={1: "Empty"}),
                FilterWheelDefinition(name="Em1", id=2, type=FilterWheelType.EMISSION, positions={1: "Empty"}),
            ]
        )
        emission = registry.get_emission_wheels()
        assert len(emission) == 1
        assert emission[0].name == "Em1"

    def test_get_wheels_by_type_empty_result(self):
        """Test getting wheels by type when none match."""
        registry = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(name="Emission", id=1, type=FilterWheelType.EMISSION, positions={1: "Empty"}),
            ]
        )
        excitation = registry.get_excitation_wheels()
        assert excitation == []


class TestFilterWheelReference:
    """Tests for FilterWheelReference model (source-qualified references)."""

    def test_reference_with_id(self):
        """Test creating a reference with ID."""
        ref = FilterWheelReference(source="confocal", id=1)
        assert ref.source == "confocal"
        assert ref.id == 1
        assert ref.name is None

    def test_reference_with_name(self):
        """Test creating a reference with name."""
        ref = FilterWheelReference(source="standalone", name="Emission Wheel")
        assert ref.source == "standalone"
        assert ref.id is None
        assert ref.name == "Emission Wheel"

    def test_parse_confocal_id(self):
        """Test parsing 'confocal.1' format."""
        ref = FilterWheelReference.parse("confocal.1")
        assert ref.source == "confocal"
        assert ref.id == 1
        assert ref.name is None

    def test_parse_standalone_id(self):
        """Test parsing 'standalone.2' format."""
        ref = FilterWheelReference.parse("standalone.2")
        assert ref.source == "standalone"
        assert ref.id == 2

    def test_parse_with_name(self):
        """Test parsing 'source.name' format."""
        ref = FilterWheelReference.parse("standalone.Emission Wheel")
        assert ref.source == "standalone"
        assert ref.name == "Emission Wheel"
        assert ref.id is None

    def test_to_string_with_id(self):
        """Test converting reference with ID to string."""
        ref = FilterWheelReference(source="confocal", id=1)
        assert ref.to_string() == "confocal.1"

    def test_to_string_with_name(self):
        """Test converting reference with name to string."""
        ref = FilterWheelReference(source="standalone", name="Emission")
        assert ref.to_string() == "standalone.Emission"

    def test_invalid_source_rejected(self):
        """Test that invalid source is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            FilterWheelReference(source="invalid", id=1)
        assert "Invalid source" in str(exc_info.value)

    def test_missing_identifier_rejected(self):
        """Test that missing both id and name is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            FilterWheelReference(source="confocal")
        assert "Either 'id' or 'name' must be specified" in str(exc_info.value)

    def test_parse_no_dot_rejected(self):
        """Test that reference without dot is rejected."""
        with pytest.raises(ValueError) as exc_info:
            FilterWheelReference.parse("confocal1")
        assert "Expected 'source.id'" in str(exc_info.value)

    def test_parse_empty_identifier_rejected(self):
        """Test that empty identifier is rejected."""
        with pytest.raises(ValueError) as exc_info:
            FilterWheelReference.parse("confocal.")
        assert "identifier is empty" in str(exc_info.value)

    def test_both_id_and_name_rejected(self):
        """Test that specifying both id and name is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            FilterWheelReference(source="confocal", id=1, name="Emission")
        assert "Cannot specify both" in str(exc_info.value)

    def test_parse_name_with_dots(self):
        """Test parsing name containing dots (split on first dot only)."""
        ref = FilterWheelReference.parse("standalone.BP 525/50.nm Filter")
        assert ref.source == "standalone"
        assert ref.name == "BP 525/50.nm Filter"
        assert ref.id is None

    def test_parse_empty_source_rejected(self):
        """Test that empty source is rejected."""
        with pytest.raises(ValueError) as exc_info:
            FilterWheelReference.parse(".1")
        assert "source is empty" in str(exc_info.value)

    def test_reference_is_frozen(self):
        """Test that FilterWheelReference is immutable (frozen)."""
        ref = FilterWheelReference(source="confocal", id=1)
        with pytest.raises(ValidationError):
            ref.id = 2  # Should fail because model is frozen

    def test_reference_hashable(self):
        """Test that FilterWheelReference can be used as dict key."""
        ref1 = FilterWheelReference(source="confocal", id=1)
        ref2 = FilterWheelReference(source="confocal", id=1)
        # Should be able to use as dict key
        d = {ref1: "value"}
        assert d[ref2] == "value"  # Same content should hash the same

    def test_source_enum_values(self):
        """Test that FilterWheelSource enum has expected values."""
        from control.models.hardware_bindings import FilterWheelSource

        assert FilterWheelSource.CONFOCAL.value == "confocal"
        assert FilterWheelSource.STANDALONE.value == "standalone"
        # String comparison should work (str inheritance)
        assert FilterWheelSource.CONFOCAL == "confocal"
        assert FilterWheelSource.STANDALONE == "standalone"


class TestHardwareBindingsConfig:
    """Tests for HardwareBindingsConfig model."""

    def test_empty_bindings(self):
        """Test creating empty hardware bindings."""
        bindings = HardwareBindingsConfig()
        assert bindings.version == 1.0
        assert bindings.emission_filter_wheels == {}

    def test_single_camera_binding(self):
        """Test binding a single camera to a wheel."""
        bindings = HardwareBindingsConfig(emission_filter_wheels={1: "confocal.1"})
        ref = bindings.get_emission_wheel_ref(1)
        assert ref is not None
        assert ref.source == "confocal"
        assert ref.id == 1

    def test_multi_camera_bindings(self):
        """Test binding multiple cameras to different wheels."""
        bindings = HardwareBindingsConfig(
            emission_filter_wheels={
                1: "confocal.1",
                2: "standalone.Emission Wheel",
            }
        )
        ref1 = bindings.get_emission_wheel_ref(1)
        ref2 = bindings.get_emission_wheel_ref(2)
        assert ref1.source == "confocal"
        assert ref1.id == 1
        assert ref2.source == "standalone"
        assert ref2.name == "Emission Wheel"

    def test_get_nonexistent_binding(self):
        """Test that nonexistent binding returns None."""
        bindings = HardwareBindingsConfig()
        assert bindings.get_emission_wheel_ref(99) is None

    def test_set_binding_by_id(self):
        """Test setting a binding using wheel ID."""
        bindings = HardwareBindingsConfig()
        bindings.set_emission_wheel_binding(1, "confocal", wheel_id=2)
        ref = bindings.get_emission_wheel_ref(1)
        assert ref.source == "confocal"
        assert ref.id == 2

    def test_set_binding_by_name(self):
        """Test setting a binding using wheel name."""
        bindings = HardwareBindingsConfig()
        bindings.set_emission_wheel_binding(1, "standalone", wheel_name="Main Emission")
        ref = bindings.get_emission_wheel_ref(1)
        assert ref.source == "standalone"
        assert ref.name == "Main Emission"

    def test_get_all_emission_wheel_refs(self):
        """Test getting all emission wheel references."""
        bindings = HardwareBindingsConfig(
            emission_filter_wheels={
                1: "confocal.1",
                2: "standalone.2",
            }
        )
        all_refs = bindings.get_all_emission_wheel_refs()
        assert len(all_refs) == 2
        assert 1 in all_refs
        assert 2 in all_refs

    def test_source_constants(self):
        """Test that source constants are correct."""
        assert FILTER_WHEEL_SOURCE_CONFOCAL == "confocal"
        assert FILTER_WHEEL_SOURCE_STANDALONE == "standalone"

    def test_invalid_reference_rejected_at_load(self):
        """Test that invalid reference strings are rejected at load time."""
        with pytest.raises(ValidationError) as exc_info:
            HardwareBindingsConfig(emission_filter_wheels={1: "invalid_source.1"})
        assert "Invalid emission wheel references" in str(exc_info.value)
        assert "Invalid source" in str(exc_info.value)

    def test_malformed_reference_rejected_at_load(self):
        """Test that malformed reference strings are rejected at load time."""
        with pytest.raises(ValidationError) as exc_info:
            HardwareBindingsConfig(emission_filter_wheels={1: "no_dot_here"})
        assert "Invalid emission wheel references" in str(exc_info.value)
        assert "Expected 'source.id'" in str(exc_info.value)

    def test_multiple_invalid_references_all_reported(self):
        """Test that all invalid references are reported together."""
        with pytest.raises(ValidationError) as exc_info:
            HardwareBindingsConfig(
                emission_filter_wheels={
                    1: "invalid.1",
                    2: "also_invalid",
                }
            )
        error_str = str(exc_info.value)
        assert "Camera 1" in error_str
        assert "Camera 2" in error_str

    def test_serialization_to_strings(self):
        """Test that bindings serialize back to strings for YAML output."""
        bindings = HardwareBindingsConfig(
            emission_filter_wheels={
                1: "confocal.1",
                2: "standalone.Emission Wheel",
            }
        )
        # model_dump should serialize references to strings
        dumped = bindings.model_dump()
        assert dumped["emission_filter_wheels"][1] == "confocal.1"
        assert dumped["emission_filter_wheels"][2] == "standalone.Emission Wheel"

    def test_remove_binding(self):
        """Test removing a binding."""
        bindings = HardwareBindingsConfig(emission_filter_wheels={1: "confocal.1", 2: "standalone.1"})
        assert bindings.remove_emission_wheel_binding(1) is True
        assert len(bindings.emission_filter_wheels) == 1
        assert bindings.get_emission_wheel_ref(1) is None
        # Removing non-existent binding returns False
        assert bindings.remove_emission_wheel_binding(99) is False


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

    def _make_channel(self, name: str, camera: Optional[int] = 1) -> AcquisitionChannel:
        """Helper to create a test channel (v1.0 schema).

        Note: camera is now int ID (null for single-camera systems).
        """
        return AcquisitionChannel(
            name=name,
            display_color="#FFFFFF",
            camera=camera,
            illumination_settings=IlluminationSettings(
                intensity=20.0,
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
        """Test error when same camera ID used twice in simultaneous mode."""
        channels = [
            self._make_channel("Channel A", camera=1),
            self._make_channel("Channel B", camera=1),  # Same camera ID
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


class TestAcquisitionChannelConstraints:
    """Tests for AcquisitionChannel validation constraints."""

    def test_empty_channel_name_rejected(self):
        """Test that empty channel name is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            AcquisitionChannel(
                name="",
                illumination_settings=IlluminationSettings(intensity=20.0),
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
            IlluminationSettings(intensity=-10.0)
        assert "greater than or equal to 0" in str(exc_info.value)

    def test_intensity_above_100_rejected(self):
        """Test that intensity above 100 is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            IlluminationSettings(intensity=150.0)
        assert "less than or equal to 100" in str(exc_info.value)

    def test_valid_intensity_range(self):
        """Test that valid intensity values are accepted."""
        settings = IlluminationSettings(intensity=50.0)
        assert settings.intensity == 50.0
        # Also test boundary values
        settings_zero = IlluminationSettings(intensity=0.0)
        assert settings_zero.intensity == 0.0
        settings_max = IlluminationSettings(intensity=100.0)
        assert settings_max.intensity == 100.0
