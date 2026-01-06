"""
Unit tests for default_config_generator.py.

Tests default configuration generation functions.
"""

import pytest

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
    IlluminationChannel,
    IlluminationChannelConfig,
)
from control.models.illumination_config import (
    DEFAULT_LED_COLOR,
    DEFAULT_WAVELENGTH_COLORS,
    IlluminationType,
)


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

        assert acq_channel.name == "Fluorescence 488nm"  # Preserves illumination channel name
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
