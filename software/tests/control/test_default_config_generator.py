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
    FilterWheelDefinition,
    FilterWheelType,
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
        """Test creating acquisition channel for general.yaml (v1.0 schema)."""
        ill_channel = IlluminationChannel(
            name="Fluorescence 488nm",
            type=IlluminationType.EPI_ILLUMINATION,
            wavelength_nm=488,
            controller_port="D1",
            source_code=11,
        )

        acq_channel = create_general_acquisition_channel(ill_channel, include_confocal=False)

        assert acq_channel.name == "Fluorescence 488nm"  # Preserves illumination channel name
        # v1.0: camera_settings is a single object, not a Dict
        assert acq_channel.camera_settings.exposure_time_ms == DEFAULT_EXPOSURE_TIME_MS
        assert acq_channel.camera_settings.gain_mode == DEFAULT_GAIN_MODE
        assert acq_channel.illumination_settings.intensity == DEFAULT_ILLUMINATION_INTENSITY
        # v1.0: no confocal_settings - iris settings only in confocal_override (objective files)
        assert acq_channel.confocal_override is None

    def test_create_objective_acquisition_channel_with_confocal(self):
        """Test creating objective acquisition channel with confocal_override (v1.0 schema)."""
        ill_channel = IlluminationChannel(
            name="Fluorescence 488nm",
            type=IlluminationType.EPI_ILLUMINATION,
            wavelength_nm=488,
            controller_port="D1",
            source_code=11,
        )

        acq_channel = create_objective_acquisition_channel(ill_channel, include_confocal=True)

        # v1.0: confocal_override contains iris settings only
        assert acq_channel.confocal_override is not None
        assert acq_channel.confocal_override.confocal_settings is not None
        # Iris settings are None by default (to be configured per-objective)
        assert acq_channel.confocal_override.confocal_settings.illumination_iris is None
        assert acq_channel.confocal_override.confocal_settings.emission_iris is None

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
        assert laser_acq.illumination_settings.intensity == DEFAULT_ILLUMINATION_INTENSITY

        # USB LED source should get lower intensity of 5
        led_channel = IlluminationChannel(
            name="BF LED matrix",
            type=IlluminationType.TRANSILLUMINATION,
            wavelength_nm=None,
            controller_port="USB1",
            source_code=0,
        )
        led_acq = create_objective_acquisition_channel(led_channel)
        assert led_acq.illumination_settings.intensity == DEFAULT_LED_ILLUMINATION_INTENSITY
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

        assert general_config.version == 1.0  # v1.0 schema
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

        assert general.version == 1.0  # v1.0 schema
        assert len(general.channels) == 1
        assert "10x" in objectives
        assert "20x" in objectives
        assert objectives["10x"].version == 1.0  # v1.0 schema

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
            filter_wheels=[
                FilterWheelDefinition(
                    type=FilterWheelType.EMISSION,
                    positions={1: "Filter"},
                ),
            ],
        )

        general, objectives = generate_default_configs(
            illumination_config,
            confocal_config=confocal_config,
            objectives=["20x"],
        )

        # v1.0: confocal_override only in objective files, general has no confocal_settings
        assert general.channels[0].confocal_override is None
        assert objectives["20x"].channels[0].confocal_override is not None
