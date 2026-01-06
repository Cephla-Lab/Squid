"""
Unit tests for ConfigRepository.
"""

import pytest
from pathlib import Path
import tempfile
import shutil

from control.core.config import ConfigRepository
from control.models import (
    GeneralChannelConfig,
    ObjectiveChannelConfig,
    AcquisitionChannel,
    IlluminationSettings,
    CameraSettings,
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test configs."""
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d)


@pytest.fixture
def repo_with_profile(temp_dir):
    """Create a ConfigRepository with a test profile."""
    # Create directory structure
    machine_configs = temp_dir / "machine_configs"
    machine_configs.mkdir()

    user_profiles = temp_dir / "user_profiles"
    default_profile = user_profiles / "default"
    (default_profile / "channel_configs").mkdir(parents=True)
    (default_profile / "laser_af_configs").mkdir(parents=True)

    # Create a minimal illumination config
    illumination_yaml = machine_configs / "illumination_channel_config.yaml"
    illumination_yaml.write_text(
        """
version: 1
channels:
  - name: "488nm"
    type: epi_illumination
    controller_port: D2
    wavelength_nm: 488
"""
    )

    # Create a general config
    general_yaml = default_profile / "channel_configs" / "general.yaml"
    general_yaml.write_text(
        """
version: 1
channels:
  - name: "Fluorescence 488nm"
    illumination_settings:
      illumination_channels: ["488nm"]
      intensity:
        "488nm": 50.0
      z_offset_um: 0.0
    camera_settings:
      camera_1:
        display_color: "#00FF00"
        exposure_time_ms: 100.0
        gain_mode: 0.0
"""
    )

    # Create an objective config
    objective_yaml = default_profile / "channel_configs" / "20x.yaml"
    objective_yaml.write_text(
        """
version: 1
channels:
  - name: "Fluorescence 488nm"
    illumination_settings:
      intensity:
        "488nm": 75.0
    camera_settings:
      camera_1:
        display_color: "#00FF00"
        exposure_time_ms: 50.0
        gain_mode: 1.0
"""
    )

    repo = ConfigRepository(base_path=temp_dir)
    repo.set_profile("default")
    return repo


class TestConfigRepositoryProfileManagement:
    """Tests for profile management."""

    def test_get_available_profiles(self, temp_dir):
        """Test listing available profiles."""
        user_profiles = temp_dir / "user_profiles"
        (user_profiles / "profile1" / "channel_configs").mkdir(parents=True)
        (user_profiles / "profile2" / "channel_configs").mkdir(parents=True)
        (user_profiles / ".hidden" / "channel_configs").mkdir(parents=True)

        repo = ConfigRepository(base_path=temp_dir)
        profiles = repo.get_available_profiles()

        assert "profile1" in profiles
        assert "profile2" in profiles
        assert ".hidden" not in profiles

    def test_set_profile(self, temp_dir):
        """Test setting current profile."""
        user_profiles = temp_dir / "user_profiles"
        (user_profiles / "test_profile" / "channel_configs").mkdir(parents=True)

        repo = ConfigRepository(base_path=temp_dir)
        assert repo.current_profile is None

        repo.set_profile("test_profile")
        assert repo.current_profile == "test_profile"

    def test_set_profile_nonexistent_raises(self, temp_dir):
        """Test that setting nonexistent profile raises ValueError."""
        repo = ConfigRepository(base_path=temp_dir)

        with pytest.raises(ValueError, match="does not exist"):
            repo.set_profile("nonexistent")

    def test_create_profile(self, temp_dir):
        """Test creating a new profile."""
        (temp_dir / "user_profiles").mkdir()
        repo = ConfigRepository(base_path=temp_dir)

        repo.create_profile("new_profile")

        assert repo.profile_exists("new_profile")
        assert (temp_dir / "user_profiles" / "new_profile" / "channel_configs").exists()
        assert (temp_dir / "user_profiles" / "new_profile" / "laser_af_configs").exists()

    def test_create_profile_already_exists_raises(self, temp_dir):
        """Test that creating existing profile raises ValueError."""
        user_profiles = temp_dir / "user_profiles"
        (user_profiles / "existing" / "channel_configs").mkdir(parents=True)

        repo = ConfigRepository(base_path=temp_dir)

        with pytest.raises(ValueError, match="already exists"):
            repo.create_profile("existing")

    def test_profile_exists(self, temp_dir):
        """Test profile_exists method."""
        user_profiles = temp_dir / "user_profiles"
        (user_profiles / "exists" / "channel_configs").mkdir(parents=True)

        repo = ConfigRepository(base_path=temp_dir)

        assert repo.profile_exists("exists")
        assert not repo.profile_exists("not_exists")


class TestConfigRepositoryMachineConfigs:
    """Tests for machine config loading."""

    def test_get_illumination_config(self, repo_with_profile):
        """Test loading illumination config."""
        config = repo_with_profile.get_illumination_config()

        assert config is not None
        assert len(config.channels) == 1
        assert config.channels[0].name == "488nm"

    def test_get_illumination_config_cached(self, repo_with_profile):
        """Test that illumination config is cached."""
        config1 = repo_with_profile.get_illumination_config()
        config2 = repo_with_profile.get_illumination_config()

        assert config1 is config2  # Same object, from cache

    def test_get_confocal_config_returns_none_when_missing(self, temp_dir):
        """Test that missing confocal config returns None."""
        (temp_dir / "machine_configs").mkdir()
        (temp_dir / "user_profiles" / "default" / "channel_configs").mkdir(parents=True)

        repo = ConfigRepository(base_path=temp_dir)

        assert repo.get_confocal_config() is None
        assert repo.has_confocal() is False


class TestConfigRepositoryProfileConfigs:
    """Tests for profile config loading and saving."""

    def test_get_general_config(self, repo_with_profile):
        """Test loading general config."""
        config = repo_with_profile.get_general_config()

        assert config is not None
        assert config.version == 1
        assert len(config.channels) == 1
        assert config.channels[0].name == "Fluorescence 488nm"

    def test_get_general_config_cached(self, repo_with_profile):
        """Test that general config is cached."""
        config1 = repo_with_profile.get_general_config()
        config2 = repo_with_profile.get_general_config()

        assert config1 is config2

    def test_get_objective_config(self, repo_with_profile):
        """Test loading objective config."""
        config = repo_with_profile.get_objective_config("20x")

        assert config is not None
        assert config.channels[0].illumination_settings.intensity["488nm"] == 75.0

    def test_get_objective_config_returns_none_when_missing(self, repo_with_profile):
        """Test that missing objective config returns None."""
        config = repo_with_profile.get_objective_config("nonexistent")

        assert config is None

    def test_save_general_config(self, repo_with_profile, temp_dir):
        """Test saving general config updates cache."""
        new_config = GeneralChannelConfig(
            version=2,
            channels=[
                AcquisitionChannel(
                    name="Test Channel",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["488nm"],
                        intensity={"488nm": 100.0},
                    ),
                    camera_settings={
                        "camera_1": CameraSettings(
                            display_color="#FF0000",
                            exposure_time_ms=200.0,
                            gain_mode=0.0,
                        )
                    },
                )
            ],
        )

        repo_with_profile.save_general_config("default", new_config)

        # Check file was written
        path = temp_dir / "user_profiles" / "default" / "channel_configs" / "general.yaml"
        assert path.exists()

        # Check cache was updated
        cached = repo_with_profile.get_general_config()
        assert cached is new_config
        assert cached.version == 2

    def test_save_objective_config(self, repo_with_profile, temp_dir):
        """Test saving objective config."""
        new_config = ObjectiveChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="Test",
                    illumination_settings=IlluminationSettings(
                        intensity={"488nm": 30.0},
                    ),
                    camera_settings={
                        "camera_1": CameraSettings(
                            display_color="#0000FF",
                            exposure_time_ms=25.0,
                            gain_mode=2.0,
                        )
                    },
                )
            ],
        )

        repo_with_profile.save_objective_config("default", "40x", new_config)

        # Check file was written
        path = temp_dir / "user_profiles" / "default" / "channel_configs" / "40x.yaml"
        assert path.exists()

        # Check cache was updated
        cached = repo_with_profile.get_objective_config("40x")
        assert cached is new_config

    def test_get_available_objectives(self, repo_with_profile):
        """Test listing available objectives."""
        objectives = repo_with_profile.get_available_objectives()

        assert "20x" in objectives


class TestConfigRepositoryCacheManagement:
    """Tests for cache management."""

    def test_set_profile_clears_profile_cache(self, temp_dir):
        """Test that switching profiles clears the profile cache."""
        user_profiles = temp_dir / "user_profiles"

        # Create two profiles with different configs
        for profile in ["profile1", "profile2"]:
            profile_path = user_profiles / profile / "channel_configs"
            profile_path.mkdir(parents=True)
            (user_profiles / profile / "laser_af_configs").mkdir()
            (profile_path / "general.yaml").write_text(
                f"""
version: 1
channels:
  - name: "Channel from {profile}"
    illumination_settings:
      illumination_channels: ["488nm"]
      intensity:
        "488nm": 50.0
    camera_settings:
      camera_1:
        display_color: "#00FF00"
        exposure_time_ms: 100.0
        gain_mode: 0.0
"""
            )

        repo = ConfigRepository(base_path=temp_dir)

        repo.set_profile("profile1")
        config1 = repo.get_general_config()
        assert "profile1" in config1.channels[0].name

        repo.set_profile("profile2")
        config2 = repo.get_general_config()
        assert "profile2" in config2.channels[0].name

    def test_clear_profile_cache(self, repo_with_profile):
        """Test clearing profile cache."""
        # Load to populate cache
        repo_with_profile.get_general_config()
        assert "general" in repo_with_profile._profile_cache

        repo_with_profile.clear_profile_cache()

        assert len(repo_with_profile._profile_cache) == 0

    def test_clear_all_cache(self, repo_with_profile):
        """Test clearing all caches."""
        # Load to populate caches
        repo_with_profile.get_illumination_config()
        repo_with_profile.get_general_config()

        assert len(repo_with_profile._machine_cache) > 0
        assert len(repo_with_profile._profile_cache) > 0

        repo_with_profile.clear_all_cache()

        assert len(repo_with_profile._machine_cache) == 0
        assert len(repo_with_profile._profile_cache) == 0


class TestConfigRepositoryErrorHandling:
    """Tests for error handling."""

    def test_load_invalid_yaml_returns_none(self, temp_dir):
        """Test that invalid YAML returns None with warning."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()
        (temp_dir / "user_profiles" / "default" / "channel_configs").mkdir(parents=True)

        # Write invalid YAML
        (machine_configs / "illumination_channel_config.yaml").write_text(
            """
version: 1
channels:
  - name: [invalid yaml
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        config = repo.get_illumination_config()

        assert config is None

    def test_load_invalid_schema_returns_none(self, temp_dir):
        """Test that invalid schema returns None with warning."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()
        (temp_dir / "user_profiles" / "default" / "channel_configs").mkdir(parents=True)

        # Write valid YAML but invalid schema (missing required fields)
        (machine_configs / "illumination_channel_config.yaml").write_text(
            """
version: 1
channels:
  - name: "Test"
    # missing required 'type' field
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        config = repo.get_illumination_config()

        assert config is None

    def test_operations_without_profile_raise(self, temp_dir):
        """Test that profile operations without set_profile raise."""
        (temp_dir / "machine_configs").mkdir()
        repo = ConfigRepository(base_path=temp_dir)

        with pytest.raises(ValueError, match="No profile set"):
            repo.get_general_config()
