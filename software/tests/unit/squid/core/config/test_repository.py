"""Tests for ConfigRepository."""

import pytest
from pathlib import Path

from squid.core.config.repository import ConfigRepository
from squid.core.config.models.acquisition_config import (
    AcquisitionChannel,
    CameraSettings,
    GeneralChannelConfig,
    IlluminationSettings,
    ObjectiveChannelConfig,
)
from squid.core.config.models.illumination_config import (
    IlluminationType,
    IlluminationChannel,
    IlluminationChannelConfig,
)
from squid.core.config.models.filter_wheel_config import (
    FilterWheelType,
    FilterWheelDefinition,
    FilterWheelRegistryConfig,
)


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Create a temporary config directory structure."""
    machine_configs = tmp_path / "machine_configs"
    machine_configs.mkdir()
    user_profiles = tmp_path / "user_profiles"
    user_profiles.mkdir()
    return tmp_path


@pytest.fixture
def repo(tmp_config_dir):
    """Create a ConfigRepository with temporary directory."""
    return ConfigRepository(base_path=tmp_config_dir)


@pytest.fixture
def illumination_config():
    """Create a sample illumination config."""
    return IlluminationChannelConfig(
        controller_port_mapping={"D1": 11, "USB1": 0},
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
    )


def _make_channel(name="Test", exposure=20.0, gain=10.0, intensity=50.0, ill_channel=None):
    return AcquisitionChannel(
        name=name,
        camera_settings=CameraSettings(exposure_time_ms=exposure, gain_mode=gain),
        illumination_settings=IlluminationSettings(
            illumination_channel=ill_channel, intensity=intensity
        ),
    )


class TestConfigRepositoryBasics:
    def test_init(self, repo, tmp_config_dir):
        assert repo.base_path == tmp_config_dir
        assert repo.current_profile is None

    def test_machine_configs_path(self, repo, tmp_config_dir):
        assert repo.machine_configs_path == tmp_config_dir / "machine_configs"

    def test_save_and_load_illumination(self, repo, illumination_config):
        repo.save_illumination_config(illumination_config)
        loaded = repo.get_illumination_config()
        assert loaded is not None
        assert len(loaded.channels) == 2
        assert loaded.channels[0].name == "405nm Laser"

    def test_illumination_not_found(self, repo):
        assert repo.get_illumination_config() is None


class TestProfileManagement:
    def test_create_profile(self, repo):
        repo.create_profile("test_profile")
        assert repo.profile_exists("test_profile")

    def test_create_duplicate_raises(self, repo):
        repo.create_profile("test_profile")
        with pytest.raises(ValueError, match="already exists"):
            repo.create_profile("test_profile")

    def test_set_profile(self, repo):
        repo.create_profile("test_profile")
        repo.set_profile("test_profile")
        assert repo.current_profile == "test_profile"

    def test_set_nonexistent_profile_raises(self, repo):
        with pytest.raises(ValueError, match="does not exist"):
            repo.set_profile("nonexistent")

    def test_get_available_profiles(self, repo):
        repo.create_profile("alpha")
        repo.create_profile("beta")
        profiles = repo.get_available_profiles()
        assert profiles == ["alpha", "beta"]

    def test_copy_profile(self, repo):
        repo.create_profile("source")
        repo.set_profile("source")

        # Save a general config
        config = GeneralChannelConfig(channels=[_make_channel("DAPI")])
        repo.save_general_config("source", config)

        # Copy
        repo.copy_profile("source", "dest")
        assert repo.profile_exists("dest")

        # Verify config was copied
        repo.set_profile("dest")
        loaded = repo.get_general_config()
        assert loaded is not None
        assert len(loaded.channels) == 1


class TestChannelConfigs:
    def test_save_and_load_general(self, repo):
        repo.create_profile("test")
        repo.set_profile("test")

        config = GeneralChannelConfig(
            channels=[_make_channel("DAPI", ill_channel="405nm")]
        )
        repo.save_general_config("test", config)

        loaded = repo.get_general_config()
        assert loaded is not None
        assert len(loaded.channels) == 1
        assert loaded.channels[0].name == "DAPI"

    def test_save_and_load_objective(self, repo):
        repo.create_profile("test")
        repo.set_profile("test")

        config = ObjectiveChannelConfig(
            channels=[_make_channel("DAPI", exposure=100.0)]
        )
        repo.save_objective_config("test", "10x", config)

        loaded = repo.get_objective_config("10x")
        assert loaded is not None
        assert loaded.channels[0].camera_settings.exposure_time_ms == 100.0

    def test_get_merged_channels(self, repo):
        repo.create_profile("test")
        repo.set_profile("test")

        general = GeneralChannelConfig(
            channels=[
                _make_channel("DAPI", exposure=20.0, ill_channel="405nm"),
                _make_channel("GFP", exposure=20.0, ill_channel="488nm"),
            ]
        )
        repo.save_general_config("test", general)

        objective = ObjectiveChannelConfig(
            channels=[_make_channel("DAPI", exposure=100.0)]
        )
        repo.save_objective_config("test", "10x", objective)

        merged = repo.get_merged_channels("10x")
        assert len(merged) == 2
        dapi = next(ch for ch in merged if ch.name == "DAPI")
        gfp = next(ch for ch in merged if ch.name == "GFP")
        # DAPI should have objective override
        assert dapi.exposure_time == 100.0
        # GFP should have general defaults
        assert gfp.exposure_time == 20.0
        # illumination_channel preserved from general
        assert dapi.illumination_settings.illumination_channel == "405nm"

    def test_update_channel_setting(self, repo):
        repo.create_profile("test")
        repo.set_profile("test")

        general = GeneralChannelConfig(
            channels=[_make_channel("DAPI", ill_channel="405nm")]
        )
        repo.save_general_config("test", general)

        # Update should create objective config
        success = repo.update_channel_setting("10x", "DAPI", "ExposureTime", 200.0)
        assert success

        obj_config = repo.get_objective_config("10x")
        assert obj_config is not None
        ch = obj_config.get_channel_by_name("DAPI")
        assert ch is not None
        assert ch.camera_settings.exposure_time_ms == 200.0

    def test_update_channel_setting_zoffset(self, repo):
        repo.create_profile("test")
        repo.set_profile("test")

        general = GeneralChannelConfig(
            channels=[_make_channel("DAPI", ill_channel="405nm")]
        )
        repo.save_general_config("test", general)

        success = repo.update_channel_setting("10x", "DAPI", "ZOffset", 5.0)
        assert success

        obj_config = repo.get_objective_config("10x")
        assert obj_config is not None
        ch = obj_config.get_channel_by_name("DAPI")
        assert ch is not None
        assert ch.z_offset_um == 5.0

    def test_get_available_objectives(self, repo):
        repo.create_profile("test")
        repo.set_profile("test")

        general = GeneralChannelConfig(channels=[_make_channel("DAPI")])
        repo.save_general_config("test", general)

        for obj in ["10x", "20x", "40x"]:
            repo.save_objective_config(
                "test", obj, ObjectiveChannelConfig(channels=[_make_channel("DAPI")])
            )

        objectives = repo.get_available_objectives()
        assert set(objectives) == {"10x", "20x", "40x"}


class TestFilterWheelRegistry:
    def test_save_and_load(self, repo):
        config = FilterWheelRegistryConfig(
            filter_wheels=[
                FilterWheelDefinition(
                    name="Emission",
                    id=1,
                    type=FilterWheelType.EMISSION,
                    positions={1: "Empty", 2: "DAPI"},
                )
            ]
        )
        repo.save_filter_wheel_registry(config)
        loaded = repo.get_filter_wheel_registry()
        assert loaded is not None
        assert len(loaded.filter_wheels) == 1
        assert loaded.filter_wheels[0].name == "Emission"


class TestAcquisitionOutput:
    def test_save_acquisition_output(self, repo, tmp_config_dir):
        output_dir = tmp_config_dir / "experiment_001"
        output_dir.mkdir()

        channels = [_make_channel("DAPI")]
        repo.save_acquisition_output(output_dir, "10x", channels)

        output_file = output_dir / "acquisition_channels.yaml"
        assert output_file.exists()


class TestCacheManagement:
    def test_clear_profile_cache(self, repo):
        repo.create_profile("test")
        repo.set_profile("test")

        config = GeneralChannelConfig(channels=[_make_channel("DAPI")])
        repo.save_general_config("test", config)

        # Load to cache
        repo.get_general_config()
        assert "general" in repo._profile_cache

        repo.clear_profile_cache()
        assert "general" not in repo._profile_cache

    def test_clear_all_cache(self, repo, illumination_config):
        repo.save_illumination_config(illumination_config)
        repo.get_illumination_config()  # Cache
        assert "illumination" in repo._machine_cache

        repo.clear_all_cache()
        assert len(repo._machine_cache) == 0
