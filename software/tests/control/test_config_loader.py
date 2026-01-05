"""
Unit tests for config_loader.py and default_config_generator.py.

Tests YAML I/O and default configuration generation.
"""

import tempfile
from pathlib import Path

import pytest

from control.config_loader import ConfigLoader
from control.default_config_generator import (
    DEFAULT_EXPOSURE_TIME_MS,
    DEFAULT_GAIN_MODE,
    DEFAULT_ILLUMINATION_INTENSITY,
    create_general_acquisition_channel,
    create_objective_acquisition_channel,
    generate_default_configs,
    generate_general_config,
    get_display_color_for_channel,
)
from control.models import (
    ConfocalConfig,
    GeneralChannelConfig,
    IlluminationChannel,
    IlluminationChannelConfig,
    LaserAFConfig,
    ObjectiveChannelConfig,
)
from control.models.illumination_config import (
    DEFAULT_LED_COLOR,
    DEFAULT_WAVELENGTH_COLORS,
    IlluminationType,
)


class TestConfigLoader:
    """Tests for ConfigLoader class."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def config_loader(self, temp_dir):
        """Create a ConfigLoader with temporary directory."""
        return ConfigLoader(base_path=temp_dir)

    def test_config_loader_init(self, config_loader, temp_dir):
        """Test ConfigLoader initialization."""
        assert config_loader.base_path == temp_dir
        assert config_loader.machine_configs_path == temp_dir / "machine_configs"
        assert config_loader.user_profiles_path == temp_dir / "user_profiles"

    def test_save_and_load_illumination_config(self, config_loader):
        """Test saving and loading illumination config."""
        config = IlluminationChannelConfig(
            version=1,
            channels=[
                IlluminationChannel(
                    name="Test Channel",
                    type=IlluminationType.EPI_ILLUMINATION,
                    wavelength_nm=488,
                    controller_port="D1",
                    source_code=11,
                ),
            ],
        )

        config_loader.save_illumination_config(config)
        loaded = config_loader.load_illumination_config()

        assert loaded is not None
        assert loaded.version == 1
        assert len(loaded.channels) == 1
        assert loaded.channels[0].name == "Test Channel"

    def test_load_nonexistent_illumination_config(self, config_loader):
        """Test loading nonexistent config returns None."""
        loaded = config_loader.load_illumination_config()
        assert loaded is None

    def test_save_and_load_confocal_config(self, config_loader):
        """Test saving and loading confocal config."""
        config = ConfocalConfig(
            version=1,
            filter_wheel_mappings={1: {1: "ET520/40"}},
            public_properties=["emission_filter_wheel_position"],
        )

        config_loader.save_confocal_config(config)
        loaded = config_loader.load_confocal_config()

        assert loaded is not None
        assert loaded.filter_wheel_mappings[1][1] == "ET520/40"

    def test_has_confocal(self, config_loader):
        """Test checking if confocal config exists."""
        assert config_loader.has_confocal() is False

        config_loader.save_confocal_config(ConfocalConfig())
        assert config_loader.has_confocal() is True

    def test_save_and_load_general_config(self, config_loader):
        """Test saving and loading general channel config."""
        from control.models import (
            AcquisitionChannel,
            CameraSettings,
            IlluminationSettings,
        )

        config = GeneralChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="Test",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["A"],
                        intensity={"A": 20.0},
                    ),
                    camera_settings={
                        "1": CameraSettings(exposure_time_ms=20.0, gain_mode=10.0),
                    },
                ),
            ],
        )

        config_loader.save_general_config("test_profile", config)
        loaded = config_loader.load_general_config("test_profile")

        assert loaded is not None
        assert len(loaded.channels) == 1
        assert loaded.channels[0].name == "Test"

    def test_save_and_load_objective_config(self, config_loader):
        """Test saving and loading objective-specific config."""
        from control.models import (
            AcquisitionChannel,
            CameraSettings,
            IlluminationSettings,
        )

        config = ObjectiveChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="Test",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["A"],
                        intensity={"A": 25.0},
                    ),
                    camera_settings={
                        "1": CameraSettings(exposure_time_ms=30.0, gain_mode=10.0),
                    },
                ),
            ],
        )

        config_loader.save_objective_config("test_profile", "20x", config)
        loaded = config_loader.load_objective_config("test_profile", "20x")

        assert loaded is not None
        assert loaded.channels[0].camera_settings["1"].exposure_time_ms == 30.0

    def test_save_and_load_laser_af_config(self, config_loader):
        """Test saving and loading laser AF config."""
        config = LaserAFConfig(
            x_offset=100,
            y_offset=200,
            pixel_to_um=0.75,
        )

        config_loader.save_laser_af_config("test_profile", "20x", config)
        loaded = config_loader.load_laser_af_config("test_profile", "20x")

        assert loaded is not None
        assert loaded.x_offset == 100
        assert loaded.y_offset == 200
        assert loaded.pixel_to_um == 0.75

    def test_get_available_profiles(self, config_loader):
        """Test getting list of available profiles."""
        # No profiles initially
        assert config_loader.get_available_profiles() == []

        # Create some profiles
        config_loader.ensure_profile_directories("profile1")
        config_loader.ensure_profile_directories("profile2")

        profiles = config_loader.get_available_profiles()
        assert "profile1" in profiles
        assert "profile2" in profiles

    def test_get_available_objectives(self, config_loader):
        """Test getting list of available objectives."""
        from control.models import (
            AcquisitionChannel,
            CameraSettings,
            IlluminationSettings,
        )

        # No objectives initially
        assert config_loader.get_available_objectives("test") == []

        # Create some objective configs
        config = ObjectiveChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="Test",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["A"],
                        intensity={"A": 20.0},
                    ),
                    camera_settings={
                        "1": CameraSettings(exposure_time_ms=20.0, gain_mode=10.0),
                    },
                ),
            ],
        )

        config_loader.save_objective_config("test", "10x", config)
        config_loader.save_objective_config("test", "20x", config)

        objectives = config_loader.get_available_objectives("test")
        assert "10x" in objectives
        assert "20x" in objectives

    def test_profile_has_configs(self, config_loader):
        """Test checking if profile has configs."""
        assert config_loader.profile_has_configs("test") is False

        from control.models import (
            AcquisitionChannel,
            CameraSettings,
            IlluminationSettings,
        )

        config = GeneralChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="Test",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["A"],
                        intensity={"A": 20.0},
                    ),
                    camera_settings={
                        "1": CameraSettings(exposure_time_ms=20.0, gain_mode=10.0),
                    },
                ),
            ],
        )

        config_loader.save_general_config("test", config)
        assert config_loader.profile_has_configs("test") is True


class TestDefaultConfigGenerator:
    """Tests for default_config_generator.py functions."""

    def test_get_display_color_for_fluorescence(self):
        """Test display color for fluorescence channels."""
        channel = IlluminationChannel(
            name="Fluorescence 488nm",
            type=IlluminationType.EPI_ILLUMINATION,
            wavelength_nm=488,
            controller_port="D1",
            source_code=11,
        )
        color = get_display_color_for_channel(channel)
        assert color == DEFAULT_WAVELENGTH_COLORS[488]

    def test_get_display_color_for_led(self):
        """Test display color for LED matrix channels."""
        channel = IlluminationChannel(
            name="BF LED matrix",
            type=IlluminationType.TRANSILLUMINATION,
            wavelength_nm=None,
            controller_port="USB1",
            source_code=0,
        )
        color = get_display_color_for_channel(channel)
        assert color == DEFAULT_LED_COLOR

    def test_create_general_acquisition_channel(self):
        """Test creating acquisition channel for general.yaml."""
        ill_channel = IlluminationChannel(
            name="Fluorescence 488nm",
            type=IlluminationType.EPI_ILLUMINATION,
            wavelength_nm=488,
            controller_port="D1",
            source_code=11,
        )

        acq_channel = create_general_acquisition_channel(ill_channel, include_confocal=False)

        assert acq_channel.name == "488 nm"  # Simplified name
        assert "1" in acq_channel.camera_settings
        assert acq_channel.camera_settings["1"].exposure_time_ms == DEFAULT_EXPOSURE_TIME_MS
        assert acq_channel.camera_settings["1"].gain_mode == DEFAULT_GAIN_MODE
        assert acq_channel.illumination_settings.intensity["Fluorescence 488nm"] == DEFAULT_ILLUMINATION_INTENSITY
        assert acq_channel.confocal_settings is None

    def test_create_objective_acquisition_channel_with_confocal(self):
        """Test creating objective acquisition channel with confocal settings."""
        ill_channel = IlluminationChannel(
            name="Fluorescence 488nm",
            type=IlluminationType.EPI_ILLUMINATION,
            wavelength_nm=488,
            controller_port="D1",
            source_code=11,
        )

        acq_channel = create_objective_acquisition_channel(ill_channel, include_confocal=True)

        assert acq_channel.confocal_settings is not None
        assert acq_channel.confocal_settings.filter_wheel_id == 1
        assert acq_channel.confocal_settings.emission_filter_wheel_position == 1
        assert acq_channel.confocal_override is not None

    def test_create_objective_acquisition_channel_led_intensity(self):
        """Test that USB LED sources get lower default intensity (5) vs lasers (20)."""
        from control.default_config_generator import (
            DEFAULT_LED_ILLUMINATION_INTENSITY,
        )

        # Laser source (D1 port) should get default intensity of 20
        laser_channel = IlluminationChannel(
            name="Fluorescence 488nm",
            type=IlluminationType.EPI_ILLUMINATION,
            wavelength_nm=488,
            controller_port="D1",
            source_code=11,
        )
        laser_acq = create_objective_acquisition_channel(laser_channel)
        assert laser_acq.illumination_settings.intensity["Fluorescence 488nm"] == DEFAULT_ILLUMINATION_INTENSITY

        # USB LED source should get lower intensity of 5
        led_channel = IlluminationChannel(
            name="BF LED matrix",
            type=IlluminationType.TRANSILLUMINATION,
            wavelength_nm=None,
            controller_port="USB1",
            source_code=0,
        )
        led_acq = create_objective_acquisition_channel(led_channel)
        assert led_acq.illumination_settings.intensity["BF LED matrix"] == DEFAULT_LED_ILLUMINATION_INTENSITY
        assert DEFAULT_LED_ILLUMINATION_INTENSITY == 5.0

    def test_generate_general_config(self):
        """Test generating general config from illumination config."""
        illumination_config = IlluminationChannelConfig(
            version=1,
            channels=[
                IlluminationChannel(
                    name="Channel A",
                    type=IlluminationType.EPI_ILLUMINATION,
                    wavelength_nm=488,
                    controller_port="D1",
                    source_code=11,
                ),
                IlluminationChannel(
                    name="Channel B",
                    type=IlluminationType.TRANSILLUMINATION,
                    controller_port="USB1",
                    source_code=0,
                ),
            ],
        )

        general_config = generate_general_config(illumination_config)

        assert general_config.version == 1
        assert len(general_config.channels) == 2

    def test_generate_default_configs(self):
        """Test generating default configs for objectives."""
        illumination_config = IlluminationChannelConfig(
            version=1,
            channels=[
                IlluminationChannel(
                    name="Channel A",
                    type=IlluminationType.EPI_ILLUMINATION,
                    wavelength_nm=488,
                    controller_port="D1",
                    source_code=11,
                ),
            ],
        )

        general, objectives = generate_default_configs(
            illumination_config,
            confocal_config=None,
            objectives=["10x", "20x"],
        )

        assert general.version == 1
        assert len(general.channels) == 1
        assert "10x" in objectives
        assert "20x" in objectives
        assert objectives["10x"].version == 1

    def test_generate_default_configs_with_confocal(self):
        """Test generating default configs with confocal."""
        illumination_config = IlluminationChannelConfig(
            version=1,
            channels=[
                IlluminationChannel(
                    name="Channel A",
                    type=IlluminationType.EPI_ILLUMINATION,
                    wavelength_nm=488,
                    controller_port="D1",
                    source_code=11,
                ),
            ],
        )

        confocal_config = ConfocalConfig(
            filter_wheel_mappings={1: {1: "Filter"}},
        )

        general, objectives = generate_default_configs(
            illumination_config,
            confocal_config=confocal_config,
            objectives=["20x"],
        )

        # Should have confocal settings
        assert general.channels[0].confocal_settings is not None
        assert objectives["20x"].channels[0].confocal_override is not None
