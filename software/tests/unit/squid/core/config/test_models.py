"""Tests for upstream YAML config models."""

import pytest

from squid.core.config.models.acquisition_config import (
    AcquisitionChannel,
    AcquisitionChannelOverride,
    CameraSettings,
    ConfocalSettings,
    GeneralChannelConfig,
    IlluminationSettings,
    ObjectiveChannelConfig,
    AcquisitionOutputConfig,
    merge_channel_configs,
    SynchronizationMode,
    ChannelGroupEntry,
    ChannelGroup,
    validate_channel_group,
)
from squid.core.config.models.illumination_config import (
    IlluminationType,
    IlluminationChannel,
    IlluminationChannelConfig,
)
from squid.core.config.models.confocal_config import ConfocalConfig
from squid.core.config.models.camera_config import CameraMappingsConfig
from squid.core.config.models.camera_registry import CameraDefinition, CameraRegistryConfig
from squid.core.config.models.filter_wheel_config import (
    FilterWheelType,
    FilterWheelDefinition,
    FilterWheelRegistryConfig,
)
from squid.core.config.models.hardware_bindings import (
    FilterWheelSource,
    FilterWheelReference,
    HardwareBindingsConfig,
)


# ─────────────────────────────────────────────────────────────────────────────
# AcquisitionChannel
# ─────────────────────────────────────────────────────────────────────────────


class TestAcquisitionChannel:
    def _make_channel(self, **kwargs):
        defaults = dict(
            name="Test Channel",
            camera_settings=CameraSettings(exposure_time_ms=20.0, gain_mode=10.0),
            illumination_settings=IlluminationSettings(
                illumination_channel="405nm", intensity=50.0
            ),
        )
        defaults.update(kwargs)
        return AcquisitionChannel(**defaults)

    def test_basic_creation(self):
        ch = self._make_channel()
        assert ch.name == "Test Channel"
        assert ch.enabled is True
        assert ch.display_color == "#FFFFFF"

    def test_convenience_properties(self):
        ch = self._make_channel()
        assert ch.exposure_time == 20.0
        assert ch.analog_gain == 10.0
        assert ch.illumination_intensity == 50.0
        assert ch.z_offset == 0.0
        assert ch.primary_illumination_channel == "405nm"

    def test_id_derived_from_name(self):
        ch = self._make_channel()
        assert len(ch.id) == 16
        # Same name should produce same ID
        ch2 = self._make_channel()
        assert ch.id == ch2.id

    def test_setter_properties(self):
        ch = self._make_channel()
        ch.exposure_time = 100.0
        assert ch.camera_settings.exposure_time_ms == 100.0
        ch.analog_gain = 5.0
        assert ch.camera_settings.gain_mode == 5.0
        ch.illumination_intensity = 80.0
        assert ch.illumination_settings.intensity == 80.0

    def test_confocal_override(self):
        override = AcquisitionChannelOverride(
            camera_settings=CameraSettings(exposure_time_ms=50.0, gain_mode=5.0),
            illumination_settings=IlluminationSettings(
                illumination_channel=None, intensity=80.0
            ),
        )
        ch = self._make_channel(confocal_override=override)

        # Without confocal mode
        effective = ch.get_effective_settings(confocal_mode=False)
        assert effective.exposure_time == 20.0

        # With confocal mode
        effective = ch.get_effective_settings(confocal_mode=True)
        assert effective.exposure_time == 50.0
        assert effective.illumination_intensity == 80.0

    def test_extra_fields_forbidden(self):
        with pytest.raises(Exception):
            AcquisitionChannel(
                name="Bad",
                camera_settings=CameraSettings(exposure_time_ms=20.0, gain_mode=10.0),
                illumination_settings=IlluminationSettings(intensity=50.0),
                unknown_field="bad",
            )


# ─────────────────────────────────────────────────────────────────────────────
# Merge
# ─────────────────────────────────────────────────────────────────────────────


class TestMergeChannelConfigs:
    def test_merge_overrides_objective_settings(self):
        general = GeneralChannelConfig(
            channels=[
                AcquisitionChannel(
                    name="DAPI",
                    display_color="#0000FF",
                    camera_settings=CameraSettings(exposure_time_ms=20.0, gain_mode=10.0),
                    illumination_settings=IlluminationSettings(
                        illumination_channel="405nm", intensity=50.0
                    ),
                    filter_position=2,
                    z_offset_um=1.0,
                )
            ]
        )
        objective = ObjectiveChannelConfig(
            channels=[
                AcquisitionChannel(
                    name="DAPI",
                    camera_settings=CameraSettings(exposure_time_ms=100.0, gain_mode=5.0),
                    illumination_settings=IlluminationSettings(intensity=80.0),
                )
            ]
        )

        merged = merge_channel_configs(general, objective)
        assert len(merged) == 1
        ch = merged[0]
        # From objective:
        assert ch.exposure_time == 100.0
        assert ch.analog_gain == 5.0
        assert ch.illumination_intensity == 80.0
        # From general:
        assert ch.display_color == "#0000FF"
        assert ch.filter_position == 2
        assert ch.z_offset_um == 1.0
        assert ch.illumination_settings.illumination_channel == "405nm"

    def test_merge_no_objective_override(self):
        general = GeneralChannelConfig(
            channels=[
                AcquisitionChannel(
                    name="GFP",
                    camera_settings=CameraSettings(exposure_time_ms=20.0, gain_mode=10.0),
                    illumination_settings=IlluminationSettings(
                        illumination_channel="488nm", intensity=50.0
                    ),
                )
            ]
        )
        objective = ObjectiveChannelConfig(channels=[])

        merged = merge_channel_configs(general, objective)
        assert len(merged) == 1
        assert merged[0].exposure_time == 20.0


# ─────────────────────────────────────────────────────────────────────────────
# IlluminationChannelConfig
# ─────────────────────────────────────────────────────────────────────────────


class TestIlluminationChannelConfig:
    def test_basic_creation(self):
        config = IlluminationChannelConfig(
            channels=[
                IlluminationChannel(
                    name="405nm Laser",
                    type=IlluminationType.EPI_ILLUMINATION,
                    controller_port="D1",
                    wavelength_nm=405,
                ),
                IlluminationChannel(
                    name="BF Full",
                    type=IlluminationType.TRANSILLUMINATION,
                    controller_port="USB1",
                ),
            ],
            controller_port_mapping={"D1": 11, "USB1": 0},
        )
        assert len(config.channels) == 2
        assert config.get_source_code(config.channels[0]) == 11
        assert config.get_source_code(config.channels[1]) == 0

    def test_get_channel_by_name(self):
        config = IlluminationChannelConfig(
            channels=[
                IlluminationChannel(
                    name="405nm", type=IlluminationType.EPI_ILLUMINATION, controller_port="D1"
                ),
            ],
            controller_port_mapping={"D1": 11},
        )
        assert config.get_channel_by_name("405nm") is not None
        assert config.get_channel_by_name("nonexistent") is None


# ─────────────────────────────────────────────────────────────────────────────
# Camera Registry
# ─────────────────────────────────────────────────────────────────────────────


class TestCameraRegistry:
    def test_single_camera_defaults(self):
        config = CameraRegistryConfig(
            cameras=[CameraDefinition(serial_number="ABC123")]
        )
        assert config.cameras[0].id == 1
        assert config.cameras[0].name == "Camera"

    def test_multi_camera_requires_id_and_name(self):
        with pytest.raises(Exception):
            CameraRegistryConfig(
                cameras=[
                    CameraDefinition(serial_number="ABC123"),
                    CameraDefinition(serial_number="DEF456"),
                ]
            )

    def test_unique_serials(self):
        with pytest.raises(Exception):
            CameraRegistryConfig(
                cameras=[
                    CameraDefinition(id=1, name="Cam1", serial_number="SAME"),
                    CameraDefinition(id=2, name="Cam2", serial_number="SAME"),
                ]
            )


# ─────────────────────────────────────────────────────────────────────────────
# Filter Wheel Registry
# ─────────────────────────────────────────────────────────────────────────────


class TestFilterWheelRegistry:
    def test_single_wheel_defaults(self):
        config = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(
                    type=FilterWheelType.EMISSION,
                    positions={1: "Empty", 2: "DAPI"},
                )
            ]
        )
        assert config.filter_wheels[0].id == 1
        assert config.filter_wheels[0].name == "Emission Wheel"

    def test_get_wheel_by_name(self):
        config = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(
                    name="My Wheel",
                    id=1,
                    type=FilterWheelType.EMISSION,
                    positions={1: "Empty"},
                )
            ]
        )
        assert config.get_wheel_by_name("My Wheel") is not None


# ─────────────────────────────────────────────────────────────────────────────
# Hardware Bindings
# ─────────────────────────────────────────────────────────────────────────────


class TestHardwareBindings:
    def test_parse_string_references(self):
        config = HardwareBindingsConfig(
            emission_filter_wheels={1: "confocal.1", 2: "standalone.Emission Wheel"}
        )
        ref1 = config.get_emission_wheel_ref(1)
        assert ref1 is not None
        assert ref1.source == FilterWheelSource.CONFOCAL
        assert ref1.id == 1

        ref2 = config.get_emission_wheel_ref(2)
        assert ref2 is not None
        assert ref2.source == FilterWheelSource.STANDALONE
        assert ref2.name == "Emission Wheel"

    def test_serialization_round_trip(self):
        config = HardwareBindingsConfig(
            emission_filter_wheels={1: "confocal.1"}
        )
        data = config.model_dump(mode="json")
        # JSON mode converts int keys to strings
        assert data["emission_filter_wheels"]["1"] == "confocal.1"


# ─────────────────────────────────────────────────────────────────────────────
# ConfocalConfig
# ─────────────────────────────────────────────────────────────────────────────


class TestConfocalConfig:
    def test_basic(self):
        config = ConfocalConfig(version=1.0)
        assert config.version == 1.0

    def test_filter_wheels(self):
        config = ConfocalConfig(
            filter_wheels=[
                FilterWheelDefinition(
                    id=1, name="Emission Wheel", type=FilterWheelType.EMISSION,
                    positions={1: "DAPI", 2: "GFP"},
                ),
            ]
        )
        assert config.get_filter_name(1, 1) == "DAPI"
        assert config.get_filter_name(1, 3) is None
        assert config.get_filter_name(2, 1) is None

    def test_wheel_accessors(self):
        wheel = FilterWheelDefinition(
            id=1, name="Emission", type=FilterWheelType.EMISSION,
            positions={1: "DAPI"},
        )
        config = ConfocalConfig(filter_wheels=[wheel])
        assert config.get_wheel_by_id(1) == wheel
        assert config.get_wheel_by_name("Emission") == wheel
        assert config.get_wheel_names() == ["Emission"]
        assert config.get_wheel_ids() == [1]
        assert config.get_first_wheel() == wheel
        assert config.get_emission_wheels() == [wheel]
        assert config.get_excitation_wheels() == []


# ─────────────────────────────────────────────────────────────────────────────
# Channel Groups
# ─────────────────────────────────────────────────────────────────────────────


class TestChannelGroups:
    def test_sequential_group(self):
        group = ChannelGroup(
            name="Sequential",
            synchronization=SynchronizationMode.SEQUENTIAL,
            channels=[
                ChannelGroupEntry(name="DAPI"),
                ChannelGroupEntry(name="GFP"),
            ],
        )
        assert group.get_channel_names() == ["DAPI", "GFP"]

    def test_validate_channel_group(self):
        channels = [
            AcquisitionChannel(
                name="DAPI",
                camera=1,
                camera_settings=CameraSettings(exposure_time_ms=20.0, gain_mode=10.0),
                illumination_settings=IlluminationSettings(intensity=50.0),
            ),
        ]
        group = ChannelGroup(
            name="Test",
            channels=[ChannelGroupEntry(name="DAPI")],
        )
        errors = validate_channel_group(group, channels)
        assert len(errors) == 0

    def test_validate_missing_channel(self):
        group = ChannelGroup(
            name="Test",
            channels=[ChannelGroupEntry(name="NonExistent")],
        )
        errors = validate_channel_group(group, [])
        assert len(errors) == 1
        assert "not found" in errors[0]


# ─────────────────────────────────────────────────────────────────────────────
# AcquisitionOutputConfig
# ─────────────────────────────────────────────────────────────────────────────


class TestAcquisitionOutputConfig:
    def test_creation(self):
        config = AcquisitionOutputConfig(
            objective="10x",
            channels=[
                AcquisitionChannel(
                    name="DAPI",
                    camera_settings=CameraSettings(exposure_time_ms=20.0, gain_mode=10.0),
                    illumination_settings=IlluminationSettings(intensity=50.0),
                )
            ],
        )
        assert config.objective == "10x"
        assert len(config.channels) == 1
