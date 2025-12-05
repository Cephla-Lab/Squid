# Phase 4: Configuration Objects

**Goal**: Replace scattered configuration with validated, immutable Pydantic models.

**Impact**: Cleaner code, validation at load time, serializable configurations.

**Estimated Effort**: 3 days

---

## Checklist

### Task 4.1: Create AcquisitionConfig Pydantic models
- [ ] Create `software/squid/config/acquisition.py`
- [ ] Create test file `software/tests/squid/config/test_acquisition.py`
- [ ] Run tests
- [ ] Commit: "Add AcquisitionConfig Pydantic models"

---

## Task 4.1: Create AcquisitionConfig Pydantic models

### Test File

**File**: `software/tests/squid/config/test_acquisition.py`

```python
"""Tests for acquisition configuration models."""
import pytest
from squid.config.acquisition import (
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
        json_str = config.json()

        restored = GridScanConfig.parse_raw(json_str)
        assert restored == config

    def test_serialization_dict(self):
        """Should convert to/from dict."""
        config = GridScanConfig(nx=3, ny=4)
        d = config.dict()

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
            ]
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
                channels=[]
            )

    def test_full_serialization(self):
        """Full config should serialize and deserialize."""
        config = AcquisitionConfig(
            experiment_id="test_001",
            output_path="/tmp/test",
            grid=GridScanConfig(nx=2),
            timelapse=TimelapseConfig(),
            channels=[ChannelConfig(name="DAPI", exposure_ms=100)]
        )

        json_str = config.json(indent=2)
        restored = AcquisitionConfig.parse_raw(json_str)

        assert restored == config
```

### Implementation File

**File**: `software/squid/config/acquisition.py`

```python
"""
Acquisition configuration models.

Provides validated, immutable configuration objects for acquisitions.
Replaces scattered setters and mutable state with clear, typed configs.

Usage:
    from squid.config.acquisition import AcquisitionConfig, GridScanConfig

    config = AcquisitionConfig(
        experiment_id="exp_001",
        output_path="/data/experiments",
        grid=GridScanConfig(nx=10, ny=10),
        channels=[
            ChannelConfig(name="DAPI", exposure_ms=100),
            ChannelConfig(name="GFP", exposure_ms=200),
        ]
    )

    # Save for reproducibility
    config_json = config.json(indent=2)

    # Restore from file
    config = AcquisitionConfig.parse_file("config.json")
"""
from typing import List, Optional
from pydantic import BaseModel, validator


class GridScanConfig(BaseModel):
    """
    Configuration for grid-based scanning.

    Attributes:
        nx: Number of positions in X
        ny: Number of positions in Y
        nz: Number of Z slices
        delta_x_mm: Step size in X (mm)
        delta_y_mm: Step size in Y (mm)
        delta_z_um: Step size in Z (um)
    """
    nx: int = 1
    ny: int = 1
    nz: int = 1
    delta_x_mm: float = 0.9
    delta_y_mm: float = 0.9
    delta_z_um: float = 1.5

    class Config:
        frozen = True  # Immutable

    @validator('nx', 'ny', 'nz')
    def must_be_positive(cls, v, field):
        if v < 1:
            raise ValueError(f'{field.name} must be at least 1')
        return v


class TimelapseConfig(BaseModel):
    """
    Configuration for timelapse acquisition.

    Attributes:
        n_timepoints: Number of timepoints
        interval_seconds: Time between timepoints
    """
    n_timepoints: int = 1
    interval_seconds: float = 0

    class Config:
        frozen = True

    @validator('n_timepoints')
    def must_be_positive(cls, v):
        if v < 1:
            raise ValueError('n_timepoints must be at least 1')
        return v

    @validator('interval_seconds')
    def must_be_non_negative(cls, v):
        if v < 0:
            raise ValueError('interval_seconds must be non-negative')
        return v


class ChannelConfig(BaseModel):
    """
    Configuration for a single acquisition channel.

    Attributes:
        name: Channel name (e.g., "DAPI", "GFP")
        exposure_ms: Exposure time in milliseconds
        analog_gain: Optional analog gain
        illumination_source: Optional illumination source name
        z_offset_um: Z offset for this channel (um)
    """
    name: str
    exposure_ms: float
    analog_gain: Optional[float] = None
    illumination_source: Optional[str] = None
    z_offset_um: float = 0

    class Config:
        frozen = True

    @validator('exposure_ms')
    def exposure_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('exposure_ms must be positive')
        return v


class AutofocusConfig(BaseModel):
    """
    Configuration for autofocus.

    Attributes:
        enabled: Whether autofocus is enabled
        algorithm: Autofocus algorithm name
        n_steps: Number of z steps to scan
        step_size_um: Step size in um
        every_n_fovs: Run autofocus every N FOVs (0 = only at start)
    """
    enabled: bool = False
    algorithm: str = "brenner_gradient"
    n_steps: int = 10
    step_size_um: float = 1.5
    every_n_fovs: int = 3

    class Config:
        frozen = True


class AcquisitionConfig(BaseModel):
    """
    Complete acquisition configuration.

    This replaces the scattered configuration across AcquisitionParameters,
    _def.py globals, and controller setters with a single, validated,
    immutable configuration object.

    Attributes:
        experiment_id: Unique identifier for this experiment
        output_path: Directory to save images
        grid: Grid scanning configuration
        timelapse: Timelapse configuration
        channels: List of channels to acquire
        autofocus: Optional autofocus configuration
    """
    experiment_id: str
    output_path: str
    grid: GridScanConfig
    timelapse: TimelapseConfig
    channels: List[ChannelConfig]
    autofocus: Optional[AutofocusConfig] = None

    class Config:
        frozen = True

    @validator('channels')
    def must_have_channels(cls, v):
        if not v:
            raise ValueError('Must have at least one channel')
        return v

    def total_images(self) -> int:
        """Calculate total number of images in acquisition."""
        return (
            self.grid.nx *
            self.grid.ny *
            self.grid.nz *
            self.timelapse.n_timepoints *
            len(self.channels)
        )
```

### Update tests/__init__.py

Create `software/tests/squid/config/__init__.py`:

```python
"""Tests for squid.config module."""
```

### Run Tests

```bash
cd /Users/wea/src/allenlab/Squid/software
pytest tests/squid/config/test_acquisition.py -v
```

### Commit

```bash
git add software/squid/config/acquisition.py software/tests/squid/config/
git commit -m "Add AcquisitionConfig Pydantic models

Provides validated, immutable configuration objects:
- GridScanConfig: nx, ny, nz, step sizes
- TimelapseConfig: timepoints, interval
- ChannelConfig: name, exposure, gain, illumination
- AutofocusConfig: algorithm, steps, frequency
- AcquisitionConfig: complete acquisition configuration

All configs are:
- Validated at creation time
- Immutable (frozen)
- Serializable to JSON
- Self-documenting with type hints

Part of extensibility improvements - see docs/EXTENSIBILITY.md Section 4.
"
```

---

## Phase 4 Complete

After completing all tasks:

1. Run full test suite:
```bash
pytest --tb=short -v
```

2. Test serialization:
```bash
python -c "
from squid.config.acquisition import *
config = AcquisitionConfig(
    experiment_id='test',
    output_path='/tmp',
    grid=GridScanConfig(nx=2, ny=2),
    timelapse=TimelapseConfig(),
    channels=[ChannelConfig(name='DAPI', exposure_ms=100)]
)
print(config.json(indent=2))
print(f'Total images: {config.total_images()}')
"
```
