"""Tests for ChannelConfigService."""

import time

import pytest

from squid.backend.managers.channel_config_service import ChannelConfigService
from squid.core.config.models.acquisition_config import (
    AcquisitionChannel,
    AcquisitionChannelOverride,
    CameraSettings,
    GeneralChannelConfig,
    IlluminationSettings,
    ObjectiveChannelConfig,
)
from squid.core.config.models.illumination_config import (
    IlluminationChannel,
    IlluminationChannelConfig,
    IlluminationType,
)
from squid.core.config.repository import ConfigRepository
from squid.core.events import (
    ChannelConfigurationsChanged,
    ConfocalModeChanged,
    EventBus,
    SetConfocalModeCommand,
    UpdateChannelConfigurationCommand,
)


def _make_channel(name="DAPI", exposure=20.0, gain=10.0, intensity=50.0, ill_channel=None):
    return AcquisitionChannel(
        name=name,
        camera_settings=CameraSettings(exposure_time_ms=exposure, gain_mode=gain),
        illumination_settings=IlluminationSettings(
            illumination_channel=ill_channel, intensity=intensity
        ),
    )


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Create a temporary config directory with required subdirectories."""
    machine_configs = tmp_path / "machine_configs"
    machine_configs.mkdir()
    user_profiles = tmp_path / "user_profiles"
    user_profiles.mkdir()
    return tmp_path


@pytest.fixture
def illumination_config(tmp_config_dir):
    """Save a sample illumination config and return it."""
    config = IlluminationChannelConfig(
        controller_port_mapping={"D1": 11, "D2": 12},
        channels=[
            IlluminationChannel(
                name="405nm",
                type=IlluminationType.EPI_ILLUMINATION,
                controller_port="D1",
                wavelength_nm=405,
            ),
            IlluminationChannel(
                name="488nm",
                type=IlluminationType.EPI_ILLUMINATION,
                controller_port="D2",
                wavelength_nm=488,
            ),
        ],
    )
    return config


@pytest.fixture
def config_repo(tmp_config_dir, illumination_config):
    """Create a ConfigRepository with test profile and channels."""
    repo = ConfigRepository(base_path=tmp_config_dir)
    repo.save_illumination_config(illumination_config)

    repo.create_profile("default")
    repo.set_profile("default")

    general = GeneralChannelConfig(
        channels=[
            _make_channel("DAPI", exposure=20.0, ill_channel="405nm"),
            _make_channel("GFP", exposure=30.0, ill_channel="488nm"),
        ]
    )
    repo.save_general_config("default", general)

    # Objective config with both channels so updates work for either
    objective = ObjectiveChannelConfig(
        channels=[
            _make_channel("DAPI", exposure=100.0),
            _make_channel("GFP", exposure=30.0, gain=10.0, intensity=50.0),
        ]
    )
    repo.save_objective_config("default", "10x", objective)

    return repo


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def service(config_repo, event_bus):
    return ChannelConfigService(config_repo, event_bus)


class TestGetConfigurations:
    def test_returns_merged_channels(self, service):
        channels = service.get_configurations("10x")
        assert len(channels) == 2
        dapi = next(ch for ch in channels if ch.name == "DAPI")
        gfp = next(ch for ch in channels if ch.name == "GFP")
        # DAPI has objective override
        assert dapi.exposure_time == 100.0
        # GFP uses general defaults
        assert gfp.exposure_time == 30.0

    def test_enabled_only_filter(self, config_repo, event_bus):
        # Set one channel to disabled
        general_config = config_repo.get_general_config()
        general_config.channels[1].enabled = False
        config_repo.save_general_config("default", general_config)
        config_repo.clear_profile_cache()

        service = ChannelConfigService(config_repo, event_bus)
        enabled = service.get_enabled_configurations("10x")
        assert len(enabled) == 1
        assert enabled[0].name == "DAPI"

    def test_returns_empty_for_unknown_objective(self, service):
        channels = service.get_configurations("100x")
        # Should still return general channels (no objective overrides)
        assert len(channels) == 2

    def test_get_by_name(self, service):
        ch = service.get_channel_configuration_by_name("10x", "DAPI")
        assert ch is not None
        assert ch.exposure_time == 100.0

    def test_get_by_name_not_found(self, service):
        ch = service.get_channel_configuration_by_name("10x", "NonExistent")
        assert ch is None


class TestIlluminationSourceResolution:
    def test_illumination_source_injected(self, service):
        channels = service.get_configurations("10x")
        dapi = next(ch for ch in channels if ch.name == "DAPI")
        # illumination_source should be injected via __dict__
        assert dapi.__dict__.get("illumination_source") == 11

    def test_illumination_source_for_488(self, service):
        channels = service.get_configurations("10x")
        gfp = next(ch for ch in channels if ch.name == "GFP")
        assert gfp.__dict__.get("illumination_source") == 12


class TestUpdateConfiguration:
    def test_update_exposure(self, service):
        service.update_configuration("10x", "DAPI", "ExposureTime", 200.0)
        ch = service.get_channel_configuration_by_name("10x", "DAPI")
        assert ch.exposure_time == 200.0

    def test_update_gain(self, service):
        service.update_configuration("10x", "DAPI", "AnalogGain", 5.0)
        ch = service.get_channel_configuration_by_name("10x", "DAPI")
        assert ch.analog_gain == 5.0


class TestConfocalMode:
    def test_default_is_widefield(self, service):
        assert service.is_confocal_mode() is False

    def test_toggle_confocal(self, service):
        service.toggle_confocal_widefield(True)
        assert service.is_confocal_mode() is True

    def test_toggle_accepts_int(self, service):
        service.toggle_confocal_widefield(1)
        assert service.is_confocal_mode() is True
        service.toggle_confocal_widefield(0)
        assert service.is_confocal_mode() is False

    def test_sync_from_hardware(self, service):
        service.sync_confocal_mode_from_hardware(True)
        assert service.is_confocal_mode() is True

    def test_confocal_mode_uses_overrides(self, config_repo, event_bus):
        # Add confocal override to DAPI
        obj_config = config_repo.get_objective_config("10x")
        dapi = obj_config.get_channel_by_name("DAPI")
        dapi.confocal_override = AcquisitionChannelOverride(
            camera_settings=CameraSettings(exposure_time_ms=500.0, gain_mode=2.0),
            illumination_settings=IlluminationSettings(intensity=90.0),
        )
        config_repo.save_objective_config("default", "10x", obj_config)
        config_repo.clear_profile_cache()

        service = ChannelConfigService(config_repo, event_bus)
        service.toggle_confocal_widefield(True)

        channels = service.get_configurations("10x")
        dapi = next(ch for ch in channels if ch.name == "DAPI")
        assert dapi.exposure_time == 500.0
        assert dapi.illumination_intensity == 90.0


class TestApplyChannelOverrides:
    def test_apply_overrides(self, service):
        service.apply_channel_overrides(
            "10x",
            [
                {"name": "DAPI", "exposure_time_ms": 50.0},
                {"name": "GFP", "analog_gain": 5.0, "illumination_intensity": 80.0},
            ],
        )
        dapi = service.get_channel_configuration_by_name("10x", "DAPI")
        assert dapi.exposure_time == 50.0

        gfp = service.get_channel_configuration_by_name("10x", "GFP")
        assert gfp.analog_gain == 5.0

    def test_skip_missing_name(self, service):
        # Should not raise
        service.apply_channel_overrides("10x", [{"exposure_time_ms": 50.0}])

    def test_skip_unknown_channel(self, service):
        # Should not raise
        service.apply_channel_overrides(
            "10x", [{"name": "NonExistent", "exposure_time_ms": 50.0}]
        )


class TestEventHandlers:
    def test_update_command_handler(self, service):
        """Test handler directly (EventBus dispatch is async)."""
        cmd = UpdateChannelConfigurationCommand(
            objective_name="10x",
            config_name="DAPI",
            exposure_time_ms=150.0,
        )
        service._on_update_configuration_command(cmd)

        ch = service.get_channel_configuration_by_name("10x", "DAPI")
        assert ch.exposure_time == 150.0

    def test_update_command_publishes_configs_changed(self, service, event_bus):
        """Test that update command handler publishes ChannelConfigurationsChanged."""
        received_events = []

        def on_configs_changed(evt):
            received_events.append(evt)

        event_bus.subscribe(ChannelConfigurationsChanged, on_configs_changed)

        cmd = UpdateChannelConfigurationCommand(
            objective_name="10x",
            config_name="DAPI",
            exposure_time_ms=200.0,
        )
        service._on_update_configuration_command(cmd)

        time.sleep(0.1)
        assert len(received_events) == 1
        assert received_events[0].objective_name == "10x"

    def test_confocal_mode_command_publishes_events(self, service, event_bus):
        """Test handler directly and verify events are published."""
        received_events = []

        def on_configs_changed(evt):
            received_events.append(("configs_changed", evt))

        def on_confocal_changed(evt):
            received_events.append(("confocal_changed", evt))

        event_bus.subscribe(ChannelConfigurationsChanged, on_configs_changed)
        event_bus.subscribe(ConfocalModeChanged, on_confocal_changed)

        cmd = SetConfocalModeCommand(objective_name="10x", is_confocal=True)
        # Call handler directly
        service._on_set_confocal_mode_command(cmd)

        assert service.is_confocal_mode() is True

        # The handler publishes events synchronously via event_bus.publish(),
        # but dispatch is async. Wait briefly for the background thread.
        time.sleep(0.1)
        assert any(t == "confocal_changed" for t, _ in received_events)
        assert any(t == "configs_changed" for t, _ in received_events)


class TestSaveAcquisitionOutput:
    def test_saves_yaml(self, service, tmp_path):
        output_dir = tmp_path / "experiment_001"
        output_dir.mkdir()

        channels = service.get_configurations("10x")
        service.save_acquisition_output(output_dir, "10x", channels)

        output_file = output_dir / "acquisition_channels.yaml"
        assert output_file.exists()
