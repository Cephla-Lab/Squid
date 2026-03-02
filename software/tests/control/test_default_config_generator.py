"""
Unit tests for default_config_generator.py.

Tests default configuration generation functions.
"""

import pytest

from control.default_config_generator import (
    ALL_IRIS_PROPERTIES,
    DEFAULT_EXPOSURE_TIME_MS,
    DEFAULT_GAIN_MODE,
    DEFAULT_ILLUMINATION_INTENSITY,
    DEFAULT_IRIS_VALUE,
    build_confocal_settings_from_config,
    create_general_acquisition_channel,
    create_objective_acquisition_channel,
    generate_default_configs,
    generate_general_config,
    get_display_color_for_channel,
)
from control.models import (
    IlluminationChannel,
    IlluminationChannelConfig,
)
from control.models.confocal_config import ConfocalConfig
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
        """Test creating objective acquisition channel with confocal (v1.0 schema)."""
        ill_channel = IlluminationChannel(
            name="Fluorescence 488nm",
            type=IlluminationType.EPI_ILLUMINATION,
            wavelength_nm=488,
            controller_port="D1",
            source_code=11,
        )

        acq_channel = create_objective_acquisition_channel(ill_channel, include_confocal=True)

        # Iris settings are now in confocal_hardware_settings (channel level)
        assert acq_channel.confocal_hardware_settings is not None
        # No confocal_config passed → fallback: both iris at DEFAULT_IRIS_VALUE (100.0)
        assert acq_channel.confocal_hardware_settings.illumination_iris == DEFAULT_IRIS_VALUE
        assert acq_channel.confocal_hardware_settings.emission_iris == DEFAULT_IRIS_VALUE
        # confocal_override still exists for camera/illumination diffs
        assert acq_channel.confocal_override is not None
        assert acq_channel.confocal_override.camera_settings is not None
        assert acq_channel.confocal_override.illumination_settings is not None

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
            objectives=["10x", "20x"],
        )

        assert general.version == 1.0  # v1.0 schema
        assert len(general.channels) == 1
        assert "10x" in objectives
        assert "20x" in objectives
        assert objectives["10x"].version == 1.0  # v1.0 schema

    def test_generate_default_configs_with_confocal(self):
        """Test generating default configs with include_confocal=True."""
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
            include_confocal=True,
            objectives=["20x"],
        )

        # v1.0: confocal_override only in objective files, general has no confocal_settings
        assert general.channels[0].confocal_override is None
        assert objectives["20x"].channels[0].confocal_override is not None


class TestBuildConfocalSettingsFromConfig:
    """Tests for build_confocal_settings_from_config()."""

    def test_no_config_returns_all_iris_defaults(self):
        """None config → both iris fields at DEFAULT_IRIS_VALUE."""
        settings = build_confocal_settings_from_config(None)
        assert settings.illumination_iris == DEFAULT_IRIS_VALUE
        assert settings.emission_iris == DEFAULT_IRIS_VALUE

    def test_model_xlight_v3_returns_both_iris(self):
        """Config with model=xlight_v3 → both iris at model default (100.0)."""
        config = ConfocalConfig(model="xlight_v3")
        settings = build_confocal_settings_from_config(config)
        assert settings.illumination_iris == 100.0
        assert settings.emission_iris == 100.0

    def test_model_cicero_returns_empty_settings(self):
        """Config with model=cicero → no iris fields (both None)."""
        config = ConfocalConfig(model="cicero")
        settings = build_confocal_settings_from_config(config)
        assert settings.illumination_iris is None
        assert settings.emission_iris is None

    def test_model_xlight_v2_returns_empty_settings(self):
        """Config with model=xlight_v2 → no iris fields (both None)."""
        config = ConfocalConfig(model="xlight_v2")
        settings = build_confocal_settings_from_config(config)
        assert settings.illumination_iris is None
        assert settings.emission_iris is None

    def test_unknown_model_falls_back_to_string_list(self):
        """Config with unknown model and string list → uses string list fallback."""
        config = ConfocalConfig(
            model="unknown_model",
            objective_specific_properties=["illumination_iris"],
        )
        settings = build_confocal_settings_from_config(config)
        assert settings.illumination_iris == DEFAULT_IRIS_VALUE
        assert settings.emission_iris is None

    def test_config_with_both_iris_properties(self):
        """Config listing both iris properties (no model) → both at DEFAULT_IRIS_VALUE."""
        config = ConfocalConfig(
            objective_specific_properties=["illumination_iris", "emission_iris"],
        )
        settings = build_confocal_settings_from_config(config)
        assert settings.illumination_iris == DEFAULT_IRIS_VALUE
        assert settings.emission_iris == DEFAULT_IRIS_VALUE

    def test_config_with_only_illumination_iris(self):
        """Config listing only illumination_iris → emission_iris is None."""
        config = ConfocalConfig(
            objective_specific_properties=["illumination_iris"],
        )
        settings = build_confocal_settings_from_config(config)
        assert settings.illumination_iris == DEFAULT_IRIS_VALUE
        assert settings.emission_iris is None

    def test_config_with_only_emission_iris(self):
        """Config listing only emission_iris → illumination_iris is None."""
        config = ConfocalConfig(
            objective_specific_properties=["emission_iris"],
        )
        settings = build_confocal_settings_from_config(config)
        assert settings.illumination_iris is None
        assert settings.emission_iris == DEFAULT_IRIS_VALUE

    def test_config_with_empty_properties_no_iris(self):
        """Config with empty objective_specific_properties → both None."""
        config = ConfocalConfig(
            objective_specific_properties=[],
        )
        settings = build_confocal_settings_from_config(config)
        assert settings.illumination_iris is None
        assert settings.emission_iris is None

    def test_config_ignores_non_iris_properties(self):
        """Non-iris property names in list are filtered out."""
        config = ConfocalConfig(
            objective_specific_properties=["emission_filter_wheel_position", "illumination_iris"],
        )
        settings = build_confocal_settings_from_config(config)
        assert settings.illumination_iris == DEFAULT_IRIS_VALUE
        assert settings.emission_iris is None


import logging
import shutil
import tempfile
from pathlib import Path

from control.core.config import ConfigRepository
from control.default_config_generator import ensure_default_configs


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d)


class TestEnsureDefaultConfigsConfocal:
    """Tests for ensure_default_configs with include_confocal parameter."""

    def _make_repo(self, temp_dir, include_confocal_yaml=False):
        """Helper: create a ConfigRepository with illumination config and empty profile."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "illumination_channel_config.yaml").write_text(
            "version: 1\n"
            "channels:\n"
            '  - name: "488nm"\n'
            "    type: epi_illumination\n"
            "    controller_port: D2\n"
            "    wavelength_nm: 488\n"
        )

        if include_confocal_yaml:
            (machine_configs / "confocal_config.yaml").write_text(
                "version: 1\n"
                "filter_wheels:\n"
                '  - name: "Emission Wheel"\n'
                "    id: 1\n"
                "    type: emission\n"
                "    positions:\n"
                '      1: "Empty"\n'
            )

        profile_path = temp_dir / "user_profiles" / "default"
        (profile_path / "channel_configs").mkdir(parents=True)
        (profile_path / "laser_af_configs").mkdir()

        return ConfigRepository(base_path=temp_dir)

    def test_include_confocal_true_generates_overrides(self, temp_dir):
        """When include_confocal=True, objective configs get confocal_override even without confocal_config.yaml."""
        repo = self._make_repo(temp_dir, include_confocal_yaml=False)
        result = ensure_default_configs(repo, "default", objectives=["20x"], include_confocal=True)
        assert result is True

        repo.set_profile("default")
        obj = repo.get_objective_config("20x")
        assert obj.channels[0].confocal_override is not None

    def test_include_confocal_false_no_overrides(self, temp_dir):
        """When include_confocal=False, no confocal_override even if confocal_config.yaml exists."""
        repo = self._make_repo(temp_dir, include_confocal_yaml=True)
        result = ensure_default_configs(repo, "default", objectives=["20x"], include_confocal=False)
        assert result is True

        repo.set_profile("default")
        obj = repo.get_objective_config("20x")
        assert obj.channels[0].confocal_override is None
        assert obj.channels[0].confocal_hardware_settings is None

    def test_include_confocal_warns_on_missing_yaml(self, temp_dir, caplog):
        """When include_confocal=True but confocal_config.yaml doesn't exist, log a warning."""
        repo = self._make_repo(temp_dir, include_confocal_yaml=False)

        with caplog.at_level(logging.WARNING):
            result = ensure_default_configs(repo, "default", objectives=["20x"], include_confocal=True)

        assert result is True
        assert any("confocal_config.yaml not found" in msg for msg in caplog.messages)

        # Overrides should still be generated
        repo.set_profile("default")
        obj = repo.get_objective_config("20x")
        assert obj.channels[0].confocal_override is not None

    def test_include_confocal_warns_on_bad_yaml(self, temp_dir, caplog):
        """When include_confocal=True but confocal_config.yaml is invalid, log a warning."""
        repo = self._make_repo(temp_dir, include_confocal_yaml=False)

        # Write an invalid confocal config (old format with wrong field names)
        (temp_dir / "machine_configs" / "confocal_config.yaml").write_text(
            "version: 1\n" "filter_slots:\n" '  1: "Empty"\n'
        )

        with caplog.at_level(logging.WARNING):
            result = ensure_default_configs(repo, "default", objectives=["20x"], include_confocal=True)

        assert result is True
        assert any("confocal_config.yaml" in msg for msg in caplog.messages)

        # Overrides should still be generated
        repo.set_profile("default")
        obj = repo.get_objective_config("20x")
        assert obj.channels[0].confocal_override is not None

    def test_model_xlight_v3_generates_iris_in_hardware_settings(self, temp_dir):
        """Config with model=xlight_v3 → iris values in confocal_hardware_settings."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "illumination_channel_config.yaml").write_text(
            "version: 1\n"
            "channels:\n"
            '  - name: "488nm"\n'
            "    type: epi_illumination\n"
            "    controller_port: D2\n"
            "    wavelength_nm: 488\n"
        )

        (machine_configs / "confocal_config.yaml").write_text(
            "version: 1\n"
            "model: xlight_v3\n"
            "filter_wheels:\n"
            '  - name: "Emission Wheel"\n'
            "    id: 1\n"
            "    type: emission\n"
            "    positions:\n"
            '      1: "Empty"\n'
        )

        profile_path = temp_dir / "user_profiles" / "default"
        (profile_path / "channel_configs").mkdir(parents=True)
        (profile_path / "laser_af_configs").mkdir()

        repo = ConfigRepository(base_path=temp_dir)
        result = ensure_default_configs(repo, "default", objectives=["20x"], include_confocal=True)
        assert result is True

        repo.set_profile("default")
        obj = repo.get_objective_config("20x")
        ch = obj.channels[0]

        # Iris in confocal_hardware_settings (channel level)
        assert ch.confocal_hardware_settings is not None
        assert ch.confocal_hardware_settings.illumination_iris == 100.0
        assert ch.confocal_hardware_settings.emission_iris == 100.0
        # confocal_override has only camera/illumination (no confocal_settings)
        assert ch.confocal_override is not None
        assert ch.confocal_override.camera_settings is not None
        assert ch.confocal_override.illumination_settings is not None

    def test_model_cicero_generates_no_iris(self, temp_dir):
        """Config with model=cicero → confocal_hardware_settings has no iris."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "illumination_channel_config.yaml").write_text(
            "version: 1\n"
            "channels:\n"
            '  - name: "488nm"\n'
            "    type: epi_illumination\n"
            "    controller_port: D2\n"
            "    wavelength_nm: 488\n"
        )

        (machine_configs / "confocal_config.yaml").write_text(
            "version: 1\n" "model: cicero\n"
        )

        profile_path = temp_dir / "user_profiles" / "default"
        (profile_path / "channel_configs").mkdir(parents=True)
        (profile_path / "laser_af_configs").mkdir()

        repo = ConfigRepository(base_path=temp_dir)
        result = ensure_default_configs(repo, "default", objectives=["20x"], include_confocal=True)
        assert result is True

        repo.set_profile("default")
        obj = repo.get_objective_config("20x")
        ch = obj.channels[0]

        # Cicero has no iris — confocal_hardware_settings should be None (all fields None → excluded)
        # ConfocalSettings() with both iris=None is still created, but serialized as empty
        # The key check: no iris values
        if ch.confocal_hardware_settings is not None:
            assert ch.confocal_hardware_settings.illumination_iris is None
            assert ch.confocal_hardware_settings.emission_iris is None


class TestConfocalToggleConfigSwitching:
    """Integration test: verify get_merged_channels returns different settings when toggling confocal mode."""

    def _make_repo_with_confocal_configs(self, temp_dir):
        """Create a repo with configs that have different confocal override values."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "illumination_channel_config.yaml").write_text(
            "version: 1\n"
            "channels:\n"
            '  - name: "488nm"\n'
            "    type: epi_illumination\n"
            "    controller_port: D2\n"
            "    wavelength_nm: 488\n"
        )

        profile_path = temp_dir / "user_profiles" / "default"
        (profile_path / "channel_configs").mkdir(parents=True)
        (profile_path / "laser_af_configs").mkdir()

        # Write general.yaml with channel identity
        (profile_path / "channel_configs" / "general.yaml").write_text(
            "version: 1.0\n"
            "channels:\n"
            '  - name: "488nm"\n'
            '    display_color: "#1FFF00"\n'
            "    camera_settings:\n"
            "      exposure_time_ms: 20.0\n"
            "      gain_mode: 10.0\n"
            "    illumination_settings:\n"
            '      illumination_channel: "488nm"\n'
            "      intensity: 20.0\n"
            "    z_offset_um: 0.0\n"
        )

        # Write 20x.yaml with confocal_hardware_settings + confocal_override
        (profile_path / "channel_configs" / "20x.yaml").write_text(
            "version: 1.0\n"
            "channels:\n"
            '  - name: "488nm"\n'
            '    display_color: "#1FFF00"\n'
            "    camera_settings:\n"
            "      exposure_time_ms: 50.0\n"
            "      gain_mode: 5.0\n"
            "    illumination_settings:\n"
            "      intensity: 30.0\n"
            "    z_offset_um: 0.0\n"
            "    confocal_hardware_settings:\n"
            "      illumination_iris: 50.0\n"
            "      emission_iris: 60.0\n"
            "    confocal_override:\n"
            "      illumination_settings:\n"
            "        intensity: 80.0\n"
            "      camera_settings:\n"
            "        exposure_time_ms: 100.0\n"
            "        gain_mode: 2.0\n"
        )

        repo = ConfigRepository(base_path=temp_dir)
        repo.set_profile("default")
        return repo

    def test_widefield_mode_returns_base_settings(self, temp_dir):
        """In widefield mode, get_merged_channels returns base objective settings."""
        repo = self._make_repo_with_confocal_configs(temp_dir)

        channels = repo.get_merged_channels("20x", confocal_mode=False)

        assert len(channels) == 1
        ch = channels[0]
        assert ch.camera_settings.exposure_time_ms == 50.0
        assert ch.camera_settings.gain_mode == 5.0
        assert ch.illumination_settings.intensity == 30.0

    def test_confocal_mode_returns_override_settings(self, temp_dir):
        """In confocal mode, get_merged_channels returns confocal override settings."""
        repo = self._make_repo_with_confocal_configs(temp_dir)

        channels = repo.get_merged_channels("20x", confocal_mode=True)

        assert len(channels) == 1
        ch = channels[0]
        assert ch.camera_settings.exposure_time_ms == 100.0
        assert ch.camera_settings.gain_mode == 2.0
        assert ch.illumination_settings.intensity == 80.0

    def test_toggle_between_modes_returns_different_settings(self, temp_dir):
        """Toggling confocal_mode flag switches between widefield and confocal settings."""
        repo = self._make_repo_with_confocal_configs(temp_dir)

        widefield = repo.get_merged_channels("20x", confocal_mode=False)
        confocal = repo.get_merged_channels("20x", confocal_mode=True)

        # Exposure should differ
        assert widefield[0].camera_settings.exposure_time_ms == 50.0
        assert confocal[0].camera_settings.exposure_time_ms == 100.0

        # Intensity should differ
        assert widefield[0].illumination_settings.intensity == 30.0
        assert confocal[0].illumination_settings.intensity == 80.0

        # Iris settings should be accessible via confocal_hardware_settings (applies in both modes)
        assert confocal[0].confocal_hardware_settings is not None
        assert confocal[0].confocal_hardware_settings.illumination_iris == 50.0
        assert confocal[0].confocal_hardware_settings.emission_iris == 60.0

    def test_channel_without_override_unchanged_in_confocal_mode(self, temp_dir):
        """A channel with no confocal_override returns the same settings in both modes."""
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()

        (machine_configs / "illumination_channel_config.yaml").write_text(
            "version: 1\nchannels:\n"
            '  - name: "488nm"\n'
            "    type: epi_illumination\n"
            "    controller_port: D2\n"
            "    wavelength_nm: 488\n"
        )

        profile_path = temp_dir / "user_profiles" / "default"
        (profile_path / "channel_configs").mkdir(parents=True)
        (profile_path / "laser_af_configs").mkdir()

        # general.yaml
        (profile_path / "channel_configs" / "general.yaml").write_text(
            "version: 1.0\nchannels:\n"
            '  - name: "488nm"\n'
            '    display_color: "#1FFF00"\n'
            "    camera_settings:\n"
            "      exposure_time_ms: 20.0\n"
            "      gain_mode: 10.0\n"
            "    illumination_settings:\n"
            '      illumination_channel: "488nm"\n'
            "      intensity: 20.0\n"
            "    z_offset_um: 0.0\n"
        )

        # 20x.yaml WITHOUT confocal_override
        (profile_path / "channel_configs" / "20x.yaml").write_text(
            "version: 1.0\nchannels:\n"
            '  - name: "488nm"\n'
            '    display_color: "#1FFF00"\n'
            "    camera_settings:\n"
            "      exposure_time_ms: 50.0\n"
            "      gain_mode: 5.0\n"
            "    illumination_settings:\n"
            "      intensity: 30.0\n"
            "    z_offset_um: 0.0\n"
        )

        repo = ConfigRepository(base_path=temp_dir)
        repo.set_profile("default")

        widefield = repo.get_merged_channels("20x", confocal_mode=False)
        confocal = repo.get_merged_channels("20x", confocal_mode=True)

        # Both should return the same settings since no override exists
        assert widefield[0].camera_settings.exposure_time_ms == confocal[0].camera_settings.exposure_time_ms
        assert widefield[0].illumination_settings.intensity == confocal[0].illumination_settings.intensity

    def test_edit_in_confocal_mode_persists_to_override(self, temp_dir):
        """Simulate: user in confocal mode edits exposure -> toggle -> values diverge."""
        repo = self._make_repo_with_confocal_configs(temp_dir)

        # Verify initial state: both modes have different settings
        wf = repo.get_merged_channels("20x", confocal_mode=False)
        cf = repo.get_merged_channels("20x", confocal_mode=True)
        assert wf[0].camera_settings.exposure_time_ms == 50.0  # base
        assert cf[0].camera_settings.exposure_time_ms == 100.0  # override

        # User edits exposure to 200 while in confocal mode
        result = repo.update_channel_setting("20x", "488nm", "ExposureTime", 200.0, confocal_mode=True)
        assert result is True

        # Clear cache to force re-read from saved YAML
        repo.clear_profile_cache()

        # Widefield should be unchanged, confocal should reflect the edit
        wf2 = repo.get_merged_channels("20x", confocal_mode=False)
        cf2 = repo.get_merged_channels("20x", confocal_mode=True)
        assert wf2[0].camera_settings.exposure_time_ms == 50.0  # unchanged
        assert cf2[0].camera_settings.exposure_time_ms == 200.0  # updated

    def test_edit_in_widefield_mode_does_not_affect_override(self, temp_dir):
        """Simulate: user in widefield mode edits exposure -> confocal unchanged."""
        repo = self._make_repo_with_confocal_configs(temp_dir)

        # User edits exposure to 200 while in widefield mode
        result = repo.update_channel_setting("20x", "488nm", "ExposureTime", 200.0, confocal_mode=False)
        assert result is True

        repo.clear_profile_cache()

        wf = repo.get_merged_channels("20x", confocal_mode=False)
        cf = repo.get_merged_channels("20x", confocal_mode=True)
        assert wf[0].camera_settings.exposure_time_ms == 200.0  # updated
        assert cf[0].camera_settings.exposure_time_ms == 100.0  # unchanged

    def test_iris_edit_persists_in_both_modes(self, temp_dir):
        """Iris writes to confocal_hardware_settings, visible in both widefield and confocal."""
        repo = self._make_repo_with_confocal_configs(temp_dir)

        # Edit illumination iris (should work regardless of confocal_mode flag)
        result = repo.update_channel_setting("20x", "488nm", "IlluminationIris", 75.0, confocal_mode=False)
        assert result is True

        repo.clear_profile_cache()

        wf = repo.get_merged_channels("20x", confocal_mode=False)
        cf = repo.get_merged_channels("20x", confocal_mode=True)
        # Iris applies in both modes
        assert wf[0].confocal_hardware_settings.illumination_iris == 75.0
        assert cf[0].confocal_hardware_settings.illumination_iris == 75.0
        # Original emission_iris unchanged
        assert wf[0].confocal_hardware_settings.emission_iris == 60.0

    def test_iris_edit_from_confocal_mode_also_persists(self, temp_dir):
        """Iris edit while in confocal mode also saves to confocal_hardware_settings."""
        repo = self._make_repo_with_confocal_configs(temp_dir)

        result = repo.update_channel_setting("20x", "488nm", "EmissionIris", 40.0, confocal_mode=True)
        assert result is True

        repo.clear_profile_cache()

        wf = repo.get_merged_channels("20x", confocal_mode=False)
        cf = repo.get_merged_channels("20x", confocal_mode=True)
        assert wf[0].confocal_hardware_settings.emission_iris == 40.0
        assert cf[0].confocal_hardware_settings.emission_iris == 40.0
