# Chunk 1: Events and Configuration

## Goal

Add all focus lock events and configuration without any behavior changes. This provides the foundation for both the simulator and real implementation.

## Files to Create

| File | Purpose |
|------|---------|
| `software/src/squid/core/config/focus_lock.py` | Pydantic config model |

## Files to Modify

| File | Changes |
|------|---------|
| `software/src/_def.py` | Add default constants |
| `software/src/squid/core/events.py` | Add focus lock events |

## Deliverables

### Events (in `events.py`)

**NOTE**: NO `FocusLockFrameUpdated` - frames go through QtStreamHandler, not EventBus.

**Single Source of Truth**: `FocusLockMode` is defined in `config/focus_lock.py`. Import it in events.py rather than duplicating.

```python
from typing import Literal
from squid.core.config.focus_lock import FocusLockMode  # Import, don't duplicate

FocusLockStatus = Literal["disabled", "searching", "locked", "lost", "paused"]

# Status events
@dataclass(frozen=True)
class FocusLockModeChanged(Event):
    mode: FocusLockMode

@dataclass(frozen=True)
class FocusLockStatusChanged(Event):
    is_locked: bool
    status: FocusLockStatus
    lock_buffer_fill: int
    lock_buffer_length: int

@dataclass(frozen=True)
class FocusLockMetricsUpdated(Event):
    z_error_um: float
    z_position_um: float
    spot_snr: float
    spot_intensity: float
    z_error_rms_um: float       # RMS error over recent history
    drift_rate_um_per_s: float  # Estimated drift rate
    is_good_reading: bool       # Whether current measurement is valid for lock
    correlation: float          # Cross-correlation quality (0-1), NaN if unavailable

@dataclass(frozen=True)
class FocusLockWarning(Event):
    warning_type: str  # "piezo_low" | "piezo_high" | "signal_lost" | "snr_low" | "reference_invalid" | "action_blocked"
    message: str

# Commands
@dataclass(frozen=True)
class SetFocusLockModeCommand(Event):
    mode: FocusLockMode

@dataclass(frozen=True)
class StartFocusLockCommand(Event):
    target_um: float = 0.0

@dataclass(frozen=True)
class StopFocusLockCommand(Event):
    pass

@dataclass(frozen=True)
class PauseFocusLockCommand(Event):
    pass

@dataclass(frozen=True)
class ResumeFocusLockCommand(Event):
    pass

@dataclass(frozen=True)
class AdjustFocusLockTargetCommand(Event):
    delta_um: float
```

### Constants (in `_def.py`)

Use SHORT names (not `FOCUS_LOCK_LOCK_GAIN`):

```python
# Focus Lock Configuration
FOCUS_LOCK_GAIN = 0.5
FOCUS_LOCK_GAIN_MAX = 0.7
FOCUS_LOCK_BUFFER_LENGTH = 5
FOCUS_LOCK_OFFSET_THRESHOLD_UM = 0.5
FOCUS_LOCK_MIN_SPOT_SNR = 5.0
FOCUS_LOCK_LOOP_RATE_HZ = 30
FOCUS_LOCK_METRICS_RATE_HZ = 10
FOCUS_LOCK_PIEZO_WARNING_MARGIN_UM = 20.0
FOCUS_LOCK_DEFAULT_MODE = "off"
```

### Config Model (in `config/focus_lock.py`)

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, field_validator
import _def

FocusLockMode = Literal["off", "always_on", "auto_lock"]

class FocusLockConfig(BaseModel):
    """Configuration for continuous focus lock system."""
    model_config = ConfigDict(frozen=True)

    gain: float = _def.FOCUS_LOCK_GAIN
    gain_max: float = _def.FOCUS_LOCK_GAIN_MAX
    buffer_length: int = _def.FOCUS_LOCK_BUFFER_LENGTH
    offset_threshold_um: float = _def.FOCUS_LOCK_OFFSET_THRESHOLD_UM
    min_spot_snr: float = _def.FOCUS_LOCK_MIN_SPOT_SNR
    loop_rate_hz: float = _def.FOCUS_LOCK_LOOP_RATE_HZ
    metrics_rate_hz: float = _def.FOCUS_LOCK_METRICS_RATE_HZ
    piezo_warning_margin_um: float = _def.FOCUS_LOCK_PIEZO_WARNING_MARGIN_UM
    default_mode: FocusLockMode = _def.FOCUS_LOCK_DEFAULT_MODE

    @field_validator("default_mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("off", "always_on", "auto_lock"):
            raise ValueError(f"Invalid mode: {v}")
        return v

    @field_validator("loop_rate_hz", "metrics_rate_hz")
    @classmethod
    def validate_positive_rate(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"Rate must be positive, got {v}")
        return v

    @field_validator("gain", "gain_max")
    @classmethod
    def validate_positive_gain(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"Gain must be positive, got {v}")
        return v

    @field_validator("buffer_length")
    @classmethod
    def validate_buffer_length(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"Buffer length must be >= 1, got {v}")
        return v
```

## Testing

```bash
cd software
pytest tests/unit/squid/core/config/ -v
pytest tests/unit/squid/core/test_events.py -v

# Verify imports work
python -c "from squid.core.config.focus_lock import FocusLockConfig; print(FocusLockConfig())"
python -c "from squid.core.events import FocusLockStatusChanged; print('OK')"
```

## Completion Checklist

### Events
- [ ] Import `FocusLockMode` from `config/focus_lock.py` (single source of truth)
- [ ] Add `FocusLockStatus` type alias
- [ ] Add `FocusLockModeChanged` event
- [ ] Add `FocusLockStatusChanged` event with `lock_buffer_fill/length`
- [ ] Add `FocusLockMetricsUpdated` event with:
  - `z_error_rms_um`, `drift_rate_um_per_s`
  - `is_good_reading: bool` (validity flag for UI)
  - `correlation: float` (cross-correlation quality)
- [ ] Add `FocusLockWarning` event
- [ ] Add `SetFocusLockModeCommand` command
- [ ] Add `StartFocusLockCommand` command
- [ ] Add `StopFocusLockCommand` command
- [ ] Add `PauseFocusLockCommand` command
- [ ] Add `ResumeFocusLockCommand` command
- [ ] Add `AdjustFocusLockTargetCommand` command
- [ ] **NO** `FocusLockFrameUpdated` (use QtStreamHandler for preview)

### Configuration
- [ ] Add constants to `_def.py` with short names
- [ ] Create `FocusLockConfig` Pydantic model
- [ ] Use `Literal` type for mode validation
- [ ] Add validators for positive rates (`loop_rate_hz`, `metrics_rate_hz`)
- [ ] Add validators for positive gains (`gain`, `gain_max`)
- [ ] Add validator for buffer_length >= 1
- [ ] Add `__init__.py` export if needed

### Testing
- [ ] Unit test: Config instantiation with defaults
- [ ] Unit test: Config validation (invalid mode rejected)
- [ ] Unit test: Event frozen behavior
- [ ] All existing tests still pass

### Verification
- [ ] `cd software && pytest tests/unit/squid/core/` passes
- [ ] Events can be imported from `squid.core.events`
- [ ] Config can be imported from `squid.core.config.focus_lock`
