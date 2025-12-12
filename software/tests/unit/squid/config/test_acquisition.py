"""Tests for acquisition configuration models."""

import pytest
from squid.core.config.acquisition import (
    GridScanConfig,
    TimelapseConfig,
    ChannelConfig,
    AcquisitionConfig,
)


class TestGridScanConfig:
    """Test suite for GridScanConfig."""

    def test_defaults(self):
        """Default values should be sensible."""
        config = GridScanConfig()
        assert config.nx == 1
        assert config.ny == 1
        assert config.nz == 1
        assert config.delta_x_mm == 0.9
        assert config.delta_y_mm == 0.9
        assert config.delta_z_um == 1.5

    def test_validation_rejects_zero(self):
        """Counts must be at least 1."""
        with pytest.raises(ValueError):
            GridScanConfig(nx=0)

        with pytest.raises(ValueError):
            GridScanConfig(ny=0)

        with pytest.raises(ValueError):
            GridScanConfig(nz=0)

    def test_immutable(self):
        """Config should be immutable (frozen)."""
        config = GridScanConfig()
        with pytest.raises(Exception):  # Pydantic ValidationError for frozen
            config.nx = 5

    def test_serialization_json(self):
        """Should serialize to/from JSON."""
        config = GridScanConfig(nx=3, ny=4, nz=2)
        json_str = config.model_dump_json()

        restored = GridScanConfig.model_validate_json(json_str)
        assert restored == config

    def test_serialization_dict(self):
        """Should convert to/from dict."""
        config = GridScanConfig(nx=3, ny=4)
        d = config.model_dump()

        restored = GridScanConfig(**d)
        assert restored == config


class TestTimelapseConfig:
    """Test suite for TimelapseConfig."""

    def test_defaults(self):
        """Default should be single timepoint."""
        config = TimelapseConfig()
        assert config.n_timepoints == 1
        assert config.interval_seconds == 0

    def test_validation_timepoints(self):
        """Must have at least 1 timepoint."""
        with pytest.raises(ValueError):
            TimelapseConfig(n_timepoints=0)

    def test_validation_interval(self):
        """Interval must be non-negative."""
        with pytest.raises(ValueError):
            TimelapseConfig(interval_seconds=-1)


class TestChannelConfig:
    """Test suite for ChannelConfig."""

    def test_required_fields(self):
        """name and exposure_ms are required."""
        config = ChannelConfig(name="DAPI", exposure_ms=100)
        assert config.name == "DAPI"
        assert config.exposure_ms == 100

    def test_optional_fields(self):
        """Optional fields have defaults."""
        config = ChannelConfig(name="DAPI", exposure_ms=100)
        assert config.analog_gain is None
        assert config.illumination_source is None
        assert config.z_offset_um == 0


class TestAcquisitionConfig:
    """Test suite for AcquisitionConfig."""

    def test_full_config(self):
        """Should create complete acquisition config."""
        config = AcquisitionConfig(
            experiment_id="test_001",
            output_path="/tmp/test",
            grid=GridScanConfig(nx=2, ny=2),
            timelapse=TimelapseConfig(n_timepoints=5),
            channels=[
                ChannelConfig(name="DAPI", exposure_ms=100),
                ChannelConfig(name="GFP", exposure_ms=200),
            ],
        )

        assert config.experiment_id == "test_001"
        assert config.grid.nx == 2
        assert config.timelapse.n_timepoints == 5
        assert len(config.channels) == 2

    def test_must_have_channels(self):
        """Must have at least one channel."""
        with pytest.raises(ValueError):
            AcquisitionConfig(
                experiment_id="test",
                output_path="/tmp",
                grid=GridScanConfig(),
                timelapse=TimelapseConfig(),
                channels=[],
            )

    def test_full_serialization(self):
        """Full config should serialize and deserialize."""
        config = AcquisitionConfig(
            experiment_id="test_001",
            output_path="/tmp/test",
            grid=GridScanConfig(nx=2),
            timelapse=TimelapseConfig(),
            channels=[ChannelConfig(name="DAPI", exposure_ms=100)],
        )

        json_str = config.model_dump_json(indent=2)
        restored = AcquisitionConfig.model_validate_json(json_str)

        assert restored == config

    def test_total_images(self):
        """total_images() should calculate correctly."""
        config = AcquisitionConfig(
            experiment_id="test",
            output_path="/tmp",
            grid=GridScanConfig(nx=2, ny=3, nz=4),
            timelapse=TimelapseConfig(n_timepoints=5),
            channels=[
                ChannelConfig(name="DAPI", exposure_ms=100),
                ChannelConfig(name="GFP", exposure_ms=200),
            ],
        )

        # 2 * 3 * 4 * 5 * 2 = 240
        assert config.total_images() == 240
