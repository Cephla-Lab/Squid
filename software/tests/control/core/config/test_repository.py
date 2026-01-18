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
from control.models.camera_registry import CameraRegistryConfig, CameraDefinition
from control.models.filter_wheel_config import FilterWheelRegistryConfig, FilterWheelDefinition, FilterWheelType
from control.models.hardware_bindings import (
    HardwareBindingsConfig,
    FilterWheelReference,
    FILTER_WHEEL_SOURCE_CONFOCAL,
    FILTER_WHEEL_SOURCE_STANDALONE,
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

    # Create a general config (schema v1.0)
    # Note: z_offset_um is at channel level, not in illumination_settings
    general_yaml = default_profile / "channel_configs" / "general.yaml"
    general_yaml.write_text(
        """
version: 1.0
channel_groups: []
channels:
  - name: "Fluorescence 488nm"
    display_color: "#00FF00"
    camera: null
    z_offset_um: 0.0
    illumination_settings:
      illumination_channel: "488nm"
      intensity: 50.0
    camera_settings:
      exposure_time_ms: 100.0
      gain_mode: 0.0
"""
    )

    # Create an objective config (schema v1.0)
    objective_yaml = default_profile / "channel_configs" / "20x.yaml"
    objective_yaml.write_text(
        """
version: 1.0
channels:
  - name: "Fluorescence 488nm"
    display_color: "#00FF00"
    camera: null
    illumination_settings:
      intensity: 75.0
    camera_settings:
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
        """Test loading general config (schema v1.0)."""
        config = repo_with_profile.get_general_config()

        assert config is not None
        assert config.version == 1.0  # schema v1.0
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
        assert config.channels[0].illumination_settings.intensity == 75.0

    def test_get_objective_config_returns_none_when_missing(self, repo_with_profile):
        """Test that missing objective config returns None."""
        config = repo_with_profile.get_objective_config("nonexistent")

        assert config is None

    def test_save_general_config(self, repo_with_profile, temp_dir):
        """Test saving general config updates cache (schema v1.0).

        Note: camera is now int ID (null for single-camera systems).
        """
        new_config = GeneralChannelConfig(
            version=1.0,
            channels=[
                AcquisitionChannel(
                    name="Test Channel",
                    display_color="#FF0000",
                    camera=1,  # Camera ID (int), not name
                    illumination_settings=IlluminationSettings(
                        illumination_channel="488nm",
                        intensity=100.0,
                    ),
                    camera_settings=CameraSettings(
                        exposure_time_ms=200.0,
                        gain_mode=0.0,
                    ),
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
        assert cached.version == 1.0

    def test_save_objective_config(self, repo_with_profile, temp_dir):
        """Test saving objective config (schema v1.0).

        Note: camera is now int ID (null for single-camera systems).
        """
        new_config = ObjectiveChannelConfig(
            version=1.0,
            channels=[
                AcquisitionChannel(
                    name="Test",
                    display_color="#0000FF",
                    camera=1,  # Camera ID (int), not name
                    illumination_settings=IlluminationSettings(
                        intensity=30.0,
                    ),
                    camera_settings=CameraSettings(
                        exposure_time_ms=25.0,
                        gain_mode=2.0,
                    ),
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
        """Test that switching profiles clears the profile cache (schema v1.0)."""
        user_profiles = temp_dir / "user_profiles"

        # Create two profiles with different configs (schema v1.0)
        for profile in ["profile1", "profile2"]:
            profile_path = user_profiles / profile / "channel_configs"
            profile_path.mkdir(parents=True)
            (user_profiles / profile / "laser_af_configs").mkdir()
            (profile_path / "general.yaml").write_text(
                f"""
version: 1.0
channel_groups: []
channels:
  - name: "Channel from {profile}"
    display_color: "#00FF00"
    camera: null
    illumination_settings:
      illumination_channel: "488nm"
      intensity: 50.0
    camera_settings:
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


class TestConfigRepositoryCameraRegistry:
    """Tests for camera registry methods."""

    def test_get_camera_registry(self, temp_dir):
        """Test loading camera registry."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "cameras.yaml").write_text(
            """
version: 1.0
cameras:
  - name: "Main Camera"
    id: 1
    serial_number: "ABC123"
    model: "Hamamatsu C15440"
  - name: "Side Camera"
    id: 2
    serial_number: "DEF456"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        registry = repo.get_camera_registry()

        assert registry is not None
        assert len(registry.cameras) == 2
        assert registry.cameras[0].name == "Main Camera"
        assert registry.cameras[0].serial_number == "ABC123"

    def test_get_camera_registry_returns_none_when_missing(self, temp_dir):
        """Test that missing cameras.yaml returns None."""
        (temp_dir / "machine_configs").mkdir()

        repo = ConfigRepository(base_path=temp_dir)
        registry = repo.get_camera_registry()

        assert registry is None

    def test_get_camera_registry_cached(self, temp_dir):
        """Test that camera registry is cached."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "cameras.yaml").write_text(
            """
version: 1.0
cameras:
  - name: "Test"
    serial_number: "XYZ"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        registry1 = repo.get_camera_registry()
        registry2 = repo.get_camera_registry()

        assert registry1 is registry2

    def test_get_camera_names(self, temp_dir):
        """Test get_camera_names returns camera names from registry."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "cameras.yaml").write_text(
            """
version: 1.0
cameras:
  - name: "Main Camera"
    id: 1
    serial_number: "ABC"
  - name: "Secondary Camera"
    id: 2
    serial_number: "DEF"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        names = repo.get_camera_names()

        assert names == ["Main Camera", "Secondary Camera"]

    def test_get_camera_names_returns_empty_when_no_registry(self, temp_dir):
        """Test get_camera_names returns empty list when no registry."""
        (temp_dir / "machine_configs").mkdir()

        repo = ConfigRepository(base_path=temp_dir)
        names = repo.get_camera_names()

        assert names == []

    def test_save_camera_registry(self, temp_dir):
        """Test saving camera registry."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        repo = ConfigRepository(base_path=temp_dir)
        new_registry = CameraRegistryConfig(
            version=1.0,
            cameras=[
                CameraDefinition(name="New Camera", serial_number="NEW123"),
            ],
        )

        repo.save_camera_registry(new_registry)

        # Verify file was written
        path = machine_configs / "cameras.yaml"
        assert path.exists()

        # Verify cache was updated
        cached = repo.get_camera_registry()
        assert cached is new_registry


class TestConfigRepositoryFilterWheelRegistry:
    """Tests for filter wheel registry methods."""

    def test_get_filter_wheel_registry(self, temp_dir):
        """Test loading filter wheel registry."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "filter_wheels.yaml").write_text(
            """
version: 1.0
filter_wheels:
  - name: "Emission Wheel"
    id: 1
    type: emission
    positions:
      1: "Empty"
      2: "BP 525/50"
      3: "BP 600/50"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        registry = repo.get_filter_wheel_registry()

        assert registry is not None
        assert len(registry.filter_wheels) == 1
        assert registry.filter_wheels[0].name == "Emission Wheel"
        assert registry.filter_wheels[0].positions[2] == "BP 525/50"

    def test_get_filter_wheel_registry_returns_none_when_missing(self, temp_dir):
        """Test that missing filter_wheels.yaml returns None."""
        (temp_dir / "machine_configs").mkdir()

        repo = ConfigRepository(base_path=temp_dir)
        registry = repo.get_filter_wheel_registry()

        assert registry is None

    def test_get_filter_wheel_registry_cached(self, temp_dir):
        """Test that filter wheel registry is cached."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "filter_wheels.yaml").write_text(
            """
version: 1.0
filter_wheels:
  - name: "Test Wheel"
    id: 1
    type: emission
    positions:
      1: "Empty"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        registry1 = repo.get_filter_wheel_registry()
        registry2 = repo.get_filter_wheel_registry()

        assert registry1 is registry2

    def test_get_filter_wheel_names(self, temp_dir):
        """Test get_filter_wheel_names returns wheel names from registry."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "filter_wheels.yaml").write_text(
            """
version: 1.0
filter_wheels:
  - name: "Emission Wheel"
    id: 1
    type: emission
    positions:
      1: "Empty"
  - name: "Excitation Wheel"
    id: 2
    type: excitation
    positions:
      1: "Empty"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        names = repo.get_filter_wheel_names()

        assert names == ["Emission Wheel", "Excitation Wheel"]

    def test_get_filter_wheel_names_returns_empty_when_no_registry(self, temp_dir):
        """Test get_filter_wheel_names returns empty list when no registry."""
        (temp_dir / "machine_configs").mkdir()

        repo = ConfigRepository(base_path=temp_dir)
        names = repo.get_filter_wheel_names()

        assert names == []

    def test_save_filter_wheel_registry(self, temp_dir):
        """Test saving filter wheel registry."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        repo = ConfigRepository(base_path=temp_dir)
        new_registry = FilterWheelRegistryConfig(
            version=1.0,
            filter_wheels=[
                FilterWheelDefinition(
                    name="New Wheel",
                    id=1,
                    type=FilterWheelType.EMISSION,
                    positions={1: "Empty", 2: "Filter A"},
                ),
            ],
        )

        repo.save_filter_wheel_registry(new_registry)

        # Verify file was written
        path = machine_configs / "filter_wheels.yaml"
        assert path.exists()

        # Verify cache was updated
        cached = repo.get_filter_wheel_registry()
        assert cached is new_registry


class TestConfigRepositoryHardwareBindings:
    """Tests for hardware bindings methods."""

    def test_get_hardware_bindings(self, temp_dir):
        """Test loading hardware bindings."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "hardware_bindings.yaml").write_text(
            """
version: 1.0
emission_filter_wheels:
  1: "confocal.1"
  2: "standalone.1"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        bindings = repo.get_hardware_bindings()

        assert bindings is not None
        assert len(bindings.emission_filter_wheels) == 2
        # Now stores FilterWheelReference objects instead of strings
        ref1 = bindings.emission_filter_wheels[1]
        ref2 = bindings.emission_filter_wheels[2]
        assert ref1.source.value == "confocal"
        assert ref1.id == 1
        assert ref2.source.value == "standalone"
        assert ref2.id == 1

    def test_get_hardware_bindings_returns_none_when_missing(self, temp_dir):
        """Test that missing hardware_bindings.yaml returns None."""
        (temp_dir / "machine_configs").mkdir()

        repo = ConfigRepository(base_path=temp_dir)
        bindings = repo.get_hardware_bindings()

        assert bindings is None

    def test_get_hardware_bindings_cached(self, temp_dir):
        """Test that hardware bindings are cached."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "hardware_bindings.yaml").write_text(
            """
version: 1.0
emission_filter_wheels:
  1: "confocal.1"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        bindings1 = repo.get_hardware_bindings()
        bindings2 = repo.get_hardware_bindings()

        assert bindings1 is bindings2

    def test_save_hardware_bindings(self, temp_dir):
        """Test saving hardware bindings."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        repo = ConfigRepository(base_path=temp_dir)
        new_bindings = HardwareBindingsConfig(
            version=1.0,
            emission_filter_wheels={1: "standalone.1"},
        )

        repo.save_hardware_bindings(new_bindings)

        # Verify file was written
        path = machine_configs / "hardware_bindings.yaml"
        assert path.exists()

        # Verify cache was updated
        cached = repo.get_hardware_bindings()
        assert cached is new_bindings


class TestConfigRepositoryFilterWheelAggregation:
    """Tests for filter wheel aggregation methods."""

    def test_get_all_filter_wheels_standalone_only(self, temp_dir):
        """Test aggregating filter wheels with standalone wheels only."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "filter_wheels.yaml").write_text(
            """
version: 1.0
filter_wheels:
  - name: "Emission Wheel"
    id: 1
    type: emission
    positions:
      1: "Empty"
      2: "BP 525/50"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        all_wheels = repo.get_all_filter_wheels()

        assert FILTER_WHEEL_SOURCE_STANDALONE in all_wheels
        assert FILTER_WHEEL_SOURCE_CONFOCAL not in all_wheels
        assert len(all_wheels[FILTER_WHEEL_SOURCE_STANDALONE]) == 1
        assert all_wheels[FILTER_WHEEL_SOURCE_STANDALONE][0].name == "Emission Wheel"

    def test_get_all_filter_wheels_confocal_only(self, temp_dir):
        """Test aggregating filter wheels with confocal wheels only."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "confocal_config.yaml").write_text(
            """
version: 1
filter_wheels:
  - name: "Confocal Emission"
    id: 1
    type: emission
    positions:
      1: "Empty"
      2: "LP 500"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        all_wheels = repo.get_all_filter_wheels()

        assert FILTER_WHEEL_SOURCE_CONFOCAL in all_wheels
        assert FILTER_WHEEL_SOURCE_STANDALONE not in all_wheels
        assert len(all_wheels[FILTER_WHEEL_SOURCE_CONFOCAL]) == 1
        assert all_wheels[FILTER_WHEEL_SOURCE_CONFOCAL][0].name == "Confocal Emission"

    def test_get_all_filter_wheels_both_sources(self, temp_dir):
        """Test aggregating filter wheels from both standalone and confocal."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "filter_wheels.yaml").write_text(
            """
version: 1.0
filter_wheels:
  - name: "Standalone Emission"
    id: 1
    type: emission
    positions:
      1: "Empty"
"""
        )

        (machine_configs / "confocal_config.yaml").write_text(
            """
version: 1
filter_wheels:
  - name: "Confocal Emission"
    id: 1
    type: emission
    positions:
      1: "Empty"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        all_wheels = repo.get_all_filter_wheels()

        assert FILTER_WHEEL_SOURCE_STANDALONE in all_wheels
        assert FILTER_WHEEL_SOURCE_CONFOCAL in all_wheels
        assert len(all_wheels[FILTER_WHEEL_SOURCE_STANDALONE]) == 1
        assert len(all_wheels[FILTER_WHEEL_SOURCE_CONFOCAL]) == 1

    def test_get_all_filter_wheels_empty(self, temp_dir):
        """Test aggregating filter wheels when none exist."""
        (temp_dir / "machine_configs").mkdir()

        repo = ConfigRepository(base_path=temp_dir)
        all_wheels = repo.get_all_filter_wheels()

        assert all_wheels == {}

    def test_get_emission_wheels(self, temp_dir):
        """Test getting emission wheels only."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "filter_wheels.yaml").write_text(
            """
version: 1.0
filter_wheels:
  - name: "Emission Wheel"
    id: 1
    type: emission
    positions:
      1: "Empty"
  - name: "Excitation Wheel"
    id: 2
    type: excitation
    positions:
      1: "Empty"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        emission_wheels = repo.get_emission_wheels()

        assert FILTER_WHEEL_SOURCE_STANDALONE in emission_wheels
        assert len(emission_wheels[FILTER_WHEEL_SOURCE_STANDALONE]) == 1
        assert emission_wheels[FILTER_WHEEL_SOURCE_STANDALONE][0].name == "Emission Wheel"

    def test_get_excitation_wheels(self, temp_dir):
        """Test getting excitation wheels only."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "filter_wheels.yaml").write_text(
            """
version: 1.0
filter_wheels:
  - name: "Emission Wheel"
    id: 1
    type: emission
    positions:
      1: "Empty"
  - name: "Excitation Wheel"
    id: 2
    type: excitation
    positions:
      1: "Empty"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        excitation_wheels = repo.get_excitation_wheels()

        assert FILTER_WHEEL_SOURCE_STANDALONE in excitation_wheels
        assert len(excitation_wheels[FILTER_WHEEL_SOURCE_STANDALONE]) == 1
        assert excitation_wheels[FILTER_WHEEL_SOURCE_STANDALONE][0].name == "Excitation Wheel"


class TestConfigRepositoryResolveWheelReference:
    """Tests for resolve_wheel_reference method."""

    def test_resolve_wheel_reference_by_id(self, temp_dir):
        """Test resolving wheel reference by ID."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "filter_wheels.yaml").write_text(
            """
version: 1.0
filter_wheels:
  - name: "Emission Wheel"
    id: 1
    type: emission
    positions:
      1: "Empty"
  - name: "Secondary Wheel"
    id: 2
    type: emission
    positions:
      1: "Empty"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        ref = FilterWheelReference(source=FILTER_WHEEL_SOURCE_STANDALONE, id=2)
        wheel = repo.resolve_wheel_reference(ref)

        assert wheel is not None
        assert wheel.name == "Secondary Wheel"
        assert wheel.id == 2

    def test_resolve_wheel_reference_by_name(self, temp_dir):
        """Test resolving wheel reference by name."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "confocal_config.yaml").write_text(
            """
version: 1
filter_wheels:
  - name: "Confocal Emission"
    id: 1
    type: emission
    positions:
      1: "Empty"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        ref = FilterWheelReference(source=FILTER_WHEEL_SOURCE_CONFOCAL, name="Confocal Emission")
        wheel = repo.resolve_wheel_reference(ref)

        assert wheel is not None
        assert wheel.name == "Confocal Emission"

    def test_resolve_wheel_reference_not_found(self, temp_dir):
        """Test resolving wheel reference that doesn't exist."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "filter_wheels.yaml").write_text(
            """
version: 1.0
filter_wheels:
  - name: "Emission Wheel"
    id: 1
    type: emission
    positions:
      1: "Empty"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        ref = FilterWheelReference(source=FILTER_WHEEL_SOURCE_STANDALONE, id=99)
        wheel = repo.resolve_wheel_reference(ref)

        assert wheel is None

    def test_resolve_wheel_reference_wrong_source(self, temp_dir):
        """Test resolving wheel reference with wrong source."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "filter_wheels.yaml").write_text(
            """
version: 1.0
filter_wheels:
  - name: "Emission Wheel"
    id: 1
    type: emission
    positions:
      1: "Empty"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        # Wheel exists in standalone but we're looking in confocal
        ref = FilterWheelReference(source=FILTER_WHEEL_SOURCE_CONFOCAL, id=1)
        wheel = repo.resolve_wheel_reference(ref)

        assert wheel is None


class TestConfigRepositoryEffectiveEmissionWheel:
    """Tests for get_effective_emission_wheel method."""

    def test_effective_emission_wheel_explicit_binding(self, temp_dir):
        """Test getting emission wheel from explicit binding."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "filter_wheels.yaml").write_text(
            """
version: 1.0
filter_wheels:
  - name: "Emission Wheel"
    id: 1
    type: emission
    positions:
      1: "Empty"
"""
        )

        (machine_configs / "hardware_bindings.yaml").write_text(
            """
version: 1.0
emission_filter_wheels:
  1: "standalone.1"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        wheel = repo.get_effective_emission_wheel(camera_id=1)

        assert wheel is not None
        assert wheel.name == "Emission Wheel"

    def test_effective_emission_wheel_explicit_binding_not_found(self, temp_dir):
        """Test getting emission wheel when explicit binding doesn't match."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "filter_wheels.yaml").write_text(
            """
version: 1.0
filter_wheels:
  - name: "Emission Wheel"
    id: 1
    type: emission
    positions:
      1: "Empty"
"""
        )

        (machine_configs / "hardware_bindings.yaml").write_text(
            """
version: 1.0
emission_filter_wheels:
  1: "standalone.1"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        # Camera 2 has no binding
        wheel = repo.get_effective_emission_wheel(camera_id=2)

        assert wheel is None

    def test_effective_emission_wheel_implicit_single_camera_single_wheel(self, temp_dir):
        """Test implicit binding with single camera and single emission wheel."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "cameras.yaml").write_text(
            """
version: 1.0
cameras:
  - name: "Main Camera"
    serial_number: "ABC123"
"""
        )

        (machine_configs / "filter_wheels.yaml").write_text(
            """
version: 1.0
filter_wheels:
  - name: "Emission Wheel"
    type: emission
    positions:
      1: "Empty"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        wheel = repo.get_effective_emission_wheel(camera_id=1)

        assert wheel is not None
        assert wheel.name == "Emission Wheel"

    def test_effective_emission_wheel_no_implicit_with_multiple_cameras(self, temp_dir):
        """Test no implicit binding when multiple cameras exist."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "cameras.yaml").write_text(
            """
version: 1.0
cameras:
  - name: "Main Camera"
    id: 1
    serial_number: "ABC123"
  - name: "Side Camera"
    id: 2
    serial_number: "DEF456"
"""
        )

        (machine_configs / "filter_wheels.yaml").write_text(
            """
version: 1.0
filter_wheels:
  - name: "Emission Wheel"
    type: emission
    positions:
      1: "Empty"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        wheel = repo.get_effective_emission_wheel(camera_id=1)

        # No implicit binding because multiple cameras exist
        assert wheel is None

    def test_effective_emission_wheel_no_implicit_with_multiple_wheels(self, temp_dir):
        """Test no implicit binding when multiple emission wheels exist."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "cameras.yaml").write_text(
            """
version: 1.0
cameras:
  - name: "Main Camera"
    serial_number: "ABC123"
"""
        )

        (machine_configs / "filter_wheels.yaml").write_text(
            """
version: 1.0
filter_wheels:
  - name: "Emission Wheel 1"
    id: 1
    type: emission
    positions:
      1: "Empty"
  - name: "Emission Wheel 2"
    id: 2
    type: emission
    positions:
      1: "Empty"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        wheel = repo.get_effective_emission_wheel(camera_id=1)

        # No implicit binding because multiple emission wheels exist
        assert wheel is None

    def test_effective_emission_wheel_confocal_binding(self, temp_dir):
        """Test emission wheel from confocal via explicit binding."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "confocal_config.yaml").write_text(
            """
version: 1
filter_wheels:
  - name: "Confocal Emission"
    id: 1
    type: emission
    positions:
      1: "Empty"
"""
        )

        (machine_configs / "hardware_bindings.yaml").write_text(
            """
version: 1.0
emission_filter_wheels:
  1: "confocal.1"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        wheel = repo.get_effective_emission_wheel(camera_id=1)

        assert wheel is not None
        assert wheel.name == "Confocal Emission"

    def test_effective_emission_wheel_implicit_no_cameras_yaml(self, temp_dir):
        """Test implicit binding works when cameras.yaml is missing (legacy/default mode)."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        # Only filter_wheels.yaml exists - no cameras.yaml
        (machine_configs / "filter_wheels.yaml").write_text(
            """
version: 1.0
filter_wheels:
  - name: "Emission Wheel"
    type: emission
    positions:
      1: "Empty"
"""
        )

        repo = ConfigRepository(base_path=temp_dir)
        wheel = repo.get_effective_emission_wheel(camera_id=1)

        # Should work because missing cameras.yaml is treated as single-camera system
        assert wheel is not None
        assert wheel.name == "Emission Wheel"
