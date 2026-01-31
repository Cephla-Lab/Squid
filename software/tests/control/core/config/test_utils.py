"""
Unit tests for config utility functions.
"""

import pytest
from pathlib import Path
import tempfile
import shutil

from control.core.config import ConfigRepository
from control.core.config.utils import (
    apply_confocal_override,
    copy_profile_configs,
    get_effective_channels,
)
from control.models import (
    AcquisitionChannel,
    AcquisitionChannelOverride,
    CameraSettings,
    ConfocalSettings,
    GeneralChannelConfig,
    IlluminationSettings,
    ObjectiveChannelConfig,
)


@pytest.fixture
def sample_channel():
    """Create a sample acquisition channel (v1.0 schema)."""
    return AcquisitionChannel(
        name="Test Channel",
        display_color="#00FF00",
        camera=1,  # v1.0: camera is int ID, null for single-camera
        illumination_settings=IlluminationSettings(
            illumination_channel="488nm",
            intensity=50.0,
        ),
        camera_settings=CameraSettings(
            exposure_time_ms=100.0,
            gain_mode=0.0,
        ),
    )


@pytest.fixture
def sample_channel_with_confocal_override():
    """Create a channel with confocal override settings (v1.0 schema)."""
    return AcquisitionChannel(
        name="Confocal Channel",
        display_color="#00FF00",
        camera=1,  # v1.0: camera is int ID
        illumination_settings=IlluminationSettings(
            illumination_channel="488nm",
            intensity=50.0,
        ),
        camera_settings=CameraSettings(
            exposure_time_ms=100.0,
            gain_mode=0.0,
        ),
        # v1.0: confocal_override contains iris settings only (no confocal_settings at channel level)
        confocal_override=AcquisitionChannelOverride(
            illumination_settings=IlluminationSettings(
                illumination_channel="488nm",
                intensity=75.0,  # Higher intensity for confocal
            ),
            camera_settings=CameraSettings(
                exposure_time_ms=200.0,  # Longer exposure for confocal
                gain_mode=1.0,
            ),
            confocal_settings=ConfocalSettings(
                illumination_iris=50.0,
                emission_iris=50.0,
            ),
        ),
    )


class TestApplyConfocalOverride:
    """Tests for apply_confocal_override function."""

    def test_returns_unchanged_when_confocal_mode_false(self, sample_channel):
        """Test that channels are unchanged when confocal_mode is False."""
        channels = [sample_channel]
        result = apply_confocal_override(channels, confocal_mode=False)

        assert result is channels  # Same list object
        assert result[0].illumination_settings.intensity == 50.0

    def test_returns_unchanged_when_no_override(self, sample_channel):
        """Test that channels without override are unchanged even in confocal mode."""
        channels = [sample_channel]
        result = apply_confocal_override(channels, confocal_mode=True)

        # Should be a new list with same channel (no override to apply)
        assert result[0].illumination_settings.intensity == 50.0

    def test_applies_override_when_confocal_mode_true(self, sample_channel_with_confocal_override):
        """Test that confocal override is applied when confocal_mode is True (v1.0 schema)."""
        channels = [sample_channel_with_confocal_override]
        result = apply_confocal_override(channels, confocal_mode=True)

        # Should have override values
        assert result[0].illumination_settings.intensity == 75.0
        # v1.0: camera_settings is a single object, not a Dict
        assert result[0].camera_settings.exposure_time_ms == 200.0
        assert result[0].camera_settings.gain_mode == 1.0

    def test_preserves_non_overridden_channels(self, sample_channel, sample_channel_with_confocal_override):
        """Test that channels without override are preserved alongside overridden ones."""
        channels = [sample_channel, sample_channel_with_confocal_override]
        result = apply_confocal_override(channels, confocal_mode=True)

        # First channel should be unchanged (no override)
        assert result[0].illumination_settings.intensity == 50.0

        # Second channel should have override applied
        assert result[1].illumination_settings.intensity == 75.0

    def test_empty_list(self):
        """Test with empty channel list."""
        result = apply_confocal_override([], confocal_mode=True)
        assert result == []


class TestGetEffectiveChannels:
    """Tests for get_effective_channels function (v1.0 schema)."""

    def test_merges_general_and_objective(self):
        """Test that general and objective configs are merged (v1.0 schema)."""
        general = GeneralChannelConfig(
            version=1.0,
            channels=[
                AcquisitionChannel(
                    name="Channel 1",
                    display_color="#00FF00",
                    camera=1,  # v1.0: camera is int ID
                    illumination_settings=IlluminationSettings(
                        illumination_channel="488nm",
                        intensity=50.0,
                    ),
                    camera_settings=CameraSettings(
                        exposure_time_ms=100.0,
                        gain_mode=0.0,
                    ),
                    z_offset_um=5.0,  # v1.0: at channel level
                )
            ],
        )

        objective = ObjectiveChannelConfig(
            version=1.0,
            channels=[
                AcquisitionChannel(
                    name="Channel 1",
                    display_color="#FFFFFF",  # Ignored - from general
                    illumination_settings=IlluminationSettings(
                        intensity=75.0,  # Objective-specific intensity
                    ),
                    camera_settings=CameraSettings(
                        exposure_time_ms=50.0,  # Objective-specific exposure
                        gain_mode=1.0,
                    ),
                )
            ],
        )

        result = get_effective_channels(general, objective, confocal_mode=False)

        assert len(result) == 1
        ch = result[0]
        # From general: illumination_channel, z_offset_um, display_color
        assert ch.illumination_settings.illumination_channel == "488nm"
        assert ch.z_offset_um == 5.0  # v1.0: at channel level
        assert ch.display_color == "#00FF00"  # v1.0: at channel level
        # From objective: intensity, exposure_time_ms, gain_mode
        assert ch.illumination_settings.intensity == 75.0
        assert ch.camera_settings.exposure_time_ms == 50.0
        assert ch.camera_settings.gain_mode == 1.0

    def test_applies_confocal_override_when_mode_true(self):
        """Test that confocal override is applied when confocal_mode is True (v1.0 schema)."""
        general = GeneralChannelConfig(
            version=1.0,
            channels=[
                AcquisitionChannel(
                    name="Channel 1",
                    display_color="#00FF00",
                    camera=1,  # v1.0: camera is int ID
                    illumination_settings=IlluminationSettings(
                        illumination_channel="488nm",
                        intensity=50.0,
                    ),
                    camera_settings=CameraSettings(
                        exposure_time_ms=100.0,
                        gain_mode=0.0,
                    ),
                )
            ],
        )

        objective = ObjectiveChannelConfig(
            version=1.0,
            channels=[
                AcquisitionChannel(
                    name="Channel 1",
                    display_color="#FFFFFF",
                    illumination_settings=IlluminationSettings(
                        intensity=60.0,
                    ),
                    camera_settings=CameraSettings(
                        exposure_time_ms=80.0,
                        gain_mode=0.0,
                    ),
                    confocal_override=AcquisitionChannelOverride(
                        illumination_settings=IlluminationSettings(
                            illumination_channel="488nm",
                            intensity=90.0,
                        ),
                    ),
                )
            ],
        )

        # Without confocal mode
        result_widefield = get_effective_channels(general, objective, confocal_mode=False)
        assert result_widefield[0].illumination_settings.intensity == 60.0

        # With confocal mode
        result_confocal = get_effective_channels(general, objective, confocal_mode=True)
        assert result_confocal[0].illumination_settings.intensity == 90.0


class TestEnabledChannelFiltering:
    """Tests for enabled channel filtering behavior."""

    def test_enabled_field_default_is_true(self):
        """Test that enabled field defaults to True."""
        channel = AcquisitionChannel(
            name="Test Channel",
            display_color="#00FF00",
            illumination_settings=IlluminationSettings(
                illumination_channel="488nm",
                intensity=50.0,
            ),
            camera_settings=CameraSettings(
                exposure_time_ms=100.0,
                gain_mode=0.0,
            ),
        )
        assert channel.enabled is True

    def test_enabled_false_can_be_set(self):
        """Test that enabled can be set to False."""
        channel = AcquisitionChannel(
            name="Test Channel",
            display_color="#00FF00",
            enabled=False,
            illumination_settings=IlluminationSettings(
                illumination_channel="488nm",
                intensity=50.0,
            ),
            camera_settings=CameraSettings(
                exposure_time_ms=100.0,
                gain_mode=0.0,
            ),
        )
        assert channel.enabled is False

    def test_enabled_preserved_in_get_effective_channels(self):
        """Test that enabled=False is preserved through get_effective_channels."""
        general = GeneralChannelConfig(
            version=1.0,
            channels=[
                AcquisitionChannel(
                    name="Enabled Channel",
                    display_color="#00FF00",
                    enabled=True,
                    illumination_settings=IlluminationSettings(
                        illumination_channel="488nm",
                        intensity=50.0,
                    ),
                    camera_settings=CameraSettings(
                        exposure_time_ms=100.0,
                        gain_mode=0.0,
                    ),
                ),
                AcquisitionChannel(
                    name="Disabled Channel",
                    display_color="#FF0000",
                    enabled=False,
                    illumination_settings=IlluminationSettings(
                        illumination_channel="561nm",
                        intensity=50.0,
                    ),
                    camera_settings=CameraSettings(
                        exposure_time_ms=100.0,
                        gain_mode=0.0,
                    ),
                ),
            ],
        )

        # Use empty objective config (not None) since merge_channel_configs expects ObjectiveChannelConfig
        objective = ObjectiveChannelConfig(version=1.0, channels=[])

        result = get_effective_channels(general, objective, confocal_mode=False)

        assert len(result) == 2
        enabled_ch = next(ch for ch in result if ch.name == "Enabled Channel")
        disabled_ch = next(ch for ch in result if ch.name == "Disabled Channel")
        assert enabled_ch.enabled is True
        assert disabled_ch.enabled is False

    def test_filter_enabled_channels(self):
        """Test filtering to only enabled channels (simulates LiveController behavior)."""
        channels = [
            AcquisitionChannel(
                name="Channel A",
                display_color="#00FF00",
                enabled=True,
                illumination_settings=IlluminationSettings(intensity=50.0),
                camera_settings=CameraSettings(exposure_time_ms=100.0, gain_mode=0.0),
            ),
            AcquisitionChannel(
                name="Channel B",
                display_color="#FF0000",
                enabled=False,
                illumination_settings=IlluminationSettings(intensity=50.0),
                camera_settings=CameraSettings(exposure_time_ms=100.0, gain_mode=0.0),
            ),
            AcquisitionChannel(
                name="Channel C",
                display_color="#0000FF",
                enabled=True,
                illumination_settings=IlluminationSettings(intensity=50.0),
                camera_settings=CameraSettings(exposure_time_ms=100.0, gain_mode=0.0),
            ),
        ]

        # This is the filtering logic used in LiveController.get_channels()
        enabled_channels = [ch for ch in channels if ch.enabled]

        assert len(enabled_channels) == 2
        assert all(ch.enabled for ch in enabled_channels)
        assert "Channel A" in [ch.name for ch in enabled_channels]
        assert "Channel B" not in [ch.name for ch in enabled_channels]
        assert "Channel C" in [ch.name for ch in enabled_channels]

    def test_filter_all_disabled_returns_empty(self):
        """Test that filtering all-disabled channels returns empty list."""
        channels = [
            AcquisitionChannel(
                name="Channel A",
                display_color="#00FF00",
                enabled=False,
                illumination_settings=IlluminationSettings(intensity=50.0),
                camera_settings=CameraSettings(exposure_time_ms=100.0, gain_mode=0.0),
            ),
        ]

        enabled_channels = [ch for ch in channels if ch.enabled]
        assert enabled_channels == []


class TestCopyProfileConfigs:
    """Tests for copy_profile_configs function."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test configs."""
        d = tempfile.mkdtemp()
        yield Path(d)
        shutil.rmtree(d)

    @pytest.fixture
    def repo_with_profiles(self, temp_dir):
        """Create a ConfigRepository with source and destination profiles."""
        user_profiles = temp_dir / "user_profiles"
        (temp_dir / "machine_configs").mkdir()

        # Create source profile with configs
        source = user_profiles / "source"
        (source / "channel_configs").mkdir(parents=True)
        (source / "laser_af_configs").mkdir(parents=True)

        # Write some config files (v1.0 schema)
        (source / "channel_configs" / "general.yaml").write_text(
            """
version: 1.0
channel_groups: []
channels:
  - name: "Test"
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
        (source / "channel_configs" / "20x.yaml").write_text(
            """
version: 1.0
channels:
  - name: "Test"
    display_color: "#00FF00"
    camera: null
    z_offset_um: 0.0
    illumination_settings:
      intensity: 75.0
    camera_settings:
      exposure_time_ms: 50.0
      gain_mode: 1.0
"""
        )
        (source / "laser_af_configs" / "20x.yaml").write_text(
            """
version: 1
reference_offset_um: 5.0
"""
        )

        # Create empty destination profile
        dest = user_profiles / "dest"
        (dest / "channel_configs").mkdir(parents=True)
        (dest / "laser_af_configs").mkdir(parents=True)

        return ConfigRepository(base_path=temp_dir)

    def test_copies_channel_configs(self, repo_with_profiles, temp_dir):
        """Test that channel configs are copied."""
        copy_profile_configs(repo_with_profiles, "source", "dest")

        dest_general = temp_dir / "user_profiles" / "dest" / "channel_configs" / "general.yaml"
        dest_20x = temp_dir / "user_profiles" / "dest" / "channel_configs" / "20x.yaml"

        assert dest_general.exists()
        assert dest_20x.exists()
        assert "Test" in dest_general.read_text()

    def test_copies_laser_af_configs(self, repo_with_profiles, temp_dir):
        """Test that laser AF configs are copied."""
        copy_profile_configs(repo_with_profiles, "source", "dest")

        dest_laser_af = temp_dir / "user_profiles" / "dest" / "laser_af_configs" / "20x.yaml"
        assert dest_laser_af.exists()
        assert "reference_offset_um" in dest_laser_af.read_text()

    def test_raises_if_source_missing(self, temp_dir):
        """Test that ValueError is raised if source profile doesn't exist."""
        (temp_dir / "machine_configs").mkdir()
        (temp_dir / "user_profiles" / "dest" / "channel_configs").mkdir(parents=True)

        repo = ConfigRepository(base_path=temp_dir)

        with pytest.raises(ValueError, match="Source profile"):
            copy_profile_configs(repo, "nonexistent", "dest")

    def test_raises_if_dest_missing(self, temp_dir):
        """Test that ValueError is raised if destination profile doesn't exist."""
        (temp_dir / "machine_configs").mkdir()
        (temp_dir / "user_profiles" / "source" / "channel_configs").mkdir(parents=True)

        repo = ConfigRepository(base_path=temp_dir)

        with pytest.raises(ValueError, match="Destination profile"):
            copy_profile_configs(repo, "source", "nonexistent")
