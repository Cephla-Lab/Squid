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
    """Create a sample acquisition channel."""
    return AcquisitionChannel(
        name="Test Channel",
        illumination_settings=IlluminationSettings(
            illumination_channels=["488nm"],
            intensity={"488nm": 50.0},
        ),
        camera_settings={
            "camera_1": CameraSettings(
                display_color="#00FF00",
                exposure_time_ms=100.0,
                gain_mode=0.0,
            )
        },
    )


@pytest.fixture
def sample_channel_with_confocal_override():
    """Create a channel with confocal override settings."""
    return AcquisitionChannel(
        name="Confocal Channel",
        illumination_settings=IlluminationSettings(
            illumination_channels=["488nm"],
            intensity={"488nm": 50.0},
        ),
        camera_settings={
            "camera_1": CameraSettings(
                display_color="#00FF00",
                exposure_time_ms=100.0,
                gain_mode=0.0,
            )
        },
        confocal_settings=ConfocalSettings(
            filter_wheel_id=1,
            emission_filter_wheel_position=2,
        ),
        confocal_override=AcquisitionChannelOverride(
            illumination_settings=IlluminationSettings(
                illumination_channels=["488nm"],
                intensity={"488nm": 75.0},  # Higher intensity for confocal
            ),
            camera_settings={
                "camera_1": CameraSettings(
                    display_color="#00FF00",
                    exposure_time_ms=200.0,  # Longer exposure for confocal
                    gain_mode=1.0,
                )
            },
        ),
    )


class TestApplyConfocalOverride:
    """Tests for apply_confocal_override function."""

    def test_returns_unchanged_when_confocal_mode_false(self, sample_channel):
        """Test that channels are unchanged when confocal_mode is False."""
        channels = [sample_channel]
        result = apply_confocal_override(channels, confocal_mode=False)

        assert result is channels  # Same list object
        assert result[0].illumination_settings.intensity["488nm"] == 50.0

    def test_returns_unchanged_when_no_override(self, sample_channel):
        """Test that channels without override are unchanged even in confocal mode."""
        channels = [sample_channel]
        result = apply_confocal_override(channels, confocal_mode=True)

        # Should be a new list with same channel (no override to apply)
        assert result[0].illumination_settings.intensity["488nm"] == 50.0

    def test_applies_override_when_confocal_mode_true(self, sample_channel_with_confocal_override):
        """Test that confocal override is applied when confocal_mode is True."""
        channels = [sample_channel_with_confocal_override]
        result = apply_confocal_override(channels, confocal_mode=True)

        # Should have override values
        assert result[0].illumination_settings.intensity["488nm"] == 75.0
        assert result[0].camera_settings["camera_1"].exposure_time_ms == 200.0
        assert result[0].camera_settings["camera_1"].gain_mode == 1.0

    def test_preserves_non_overridden_channels(self, sample_channel, sample_channel_with_confocal_override):
        """Test that channels without override are preserved alongside overridden ones."""
        channels = [sample_channel, sample_channel_with_confocal_override]
        result = apply_confocal_override(channels, confocal_mode=True)

        # First channel should be unchanged (no override)
        assert result[0].illumination_settings.intensity["488nm"] == 50.0

        # Second channel should have override applied
        assert result[1].illumination_settings.intensity["488nm"] == 75.0

    def test_empty_list(self):
        """Test with empty channel list."""
        result = apply_confocal_override([], confocal_mode=True)
        assert result == []


class TestGetEffectiveChannels:
    """Tests for get_effective_channels function."""

    def test_merges_general_and_objective(self):
        """Test that general and objective configs are merged."""
        general = GeneralChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="Channel 1",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["488nm"],
                        intensity={"488nm": 50.0},
                        z_offset_um=5.0,
                    ),
                    camera_settings={
                        "camera_1": CameraSettings(
                            display_color="#00FF00",
                            exposure_time_ms=100.0,
                            gain_mode=0.0,
                        )
                    },
                )
            ],
        )

        objective = ObjectiveChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="Channel 1",
                    illumination_settings=IlluminationSettings(
                        intensity={"488nm": 75.0},  # Objective-specific intensity
                    ),
                    camera_settings={
                        "camera_1": CameraSettings(
                            display_color="#00FF00",
                            exposure_time_ms=50.0,  # Objective-specific exposure
                            gain_mode=1.0,
                        )
                    },
                )
            ],
        )

        result = get_effective_channels(general, objective, confocal_mode=False)

        assert len(result) == 1
        ch = result[0]
        # From general: illumination_channels, z_offset_um, display_color
        assert ch.illumination_settings.illumination_channels == ["488nm"]
        assert ch.illumination_settings.z_offset_um == 5.0
        assert ch.camera_settings["camera_1"].display_color == "#00FF00"
        # From objective: intensity, exposure_time_ms, gain_mode
        assert ch.illumination_settings.intensity["488nm"] == 75.0
        assert ch.camera_settings["camera_1"].exposure_time_ms == 50.0
        assert ch.camera_settings["camera_1"].gain_mode == 1.0

    def test_applies_confocal_override_when_mode_true(self):
        """Test that confocal override is applied when confocal_mode is True."""
        general = GeneralChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="Channel 1",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["488nm"],
                        intensity={"488nm": 50.0},
                    ),
                    camera_settings={
                        "camera_1": CameraSettings(
                            display_color="#00FF00",
                            exposure_time_ms=100.0,
                            gain_mode=0.0,
                        )
                    },
                )
            ],
        )

        objective = ObjectiveChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="Channel 1",
                    illumination_settings=IlluminationSettings(
                        intensity={"488nm": 60.0},
                    ),
                    camera_settings={
                        "camera_1": CameraSettings(
                            display_color="#00FF00",
                            exposure_time_ms=80.0,
                            gain_mode=0.0,
                        )
                    },
                    confocal_override=AcquisitionChannelOverride(
                        illumination_settings=IlluminationSettings(
                            illumination_channels=["488nm"],
                            intensity={"488nm": 90.0},
                        ),
                    ),
                )
            ],
        )

        # Without confocal mode
        result_widefield = get_effective_channels(general, objective, confocal_mode=False)
        assert result_widefield[0].illumination_settings.intensity["488nm"] == 60.0

        # With confocal mode
        result_confocal = get_effective_channels(general, objective, confocal_mode=True)
        assert result_confocal[0].illumination_settings.intensity["488nm"] == 90.0


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

        # Write some config files
        (source / "channel_configs" / "general.yaml").write_text(
            """
version: 1
channels:
  - name: "Test"
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
        (source / "channel_configs" / "20x.yaml").write_text(
            """
version: 1
channels:
  - name: "Test"
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
