# Phase 2: Create Infrastructure

**Purpose:** Add new events to `squid/events.py` and new protocols to `squid/abc.py`. Create the controllers directory structure.

**Prerequisites:** Phase 1 complete (inventories verified)

**Estimated Effort:** 2-3 days

---

## Overview

This phase adds the foundational infrastructure needed for subsequent phases. No behavior changes - just adding new types and creating directory structure.

**Changes:**
1. Add ~20 new event types to `squid/events.py`
2. Add 3-4 new protocols to `squid/abc.py`
3. Create `squid/controllers/` directory
4. (Optional) Harden `IlluminationService` (locking + multi-source routing)

---

## Task Checklist

### 2.1 Add Peripheral Command Events ✅ COMPLETED

**File:** `/Users/wea/src/allenlab/Squid/software/squid/events.py`

Add these command events at the end of the file, after existing events:

- [x] Add `SetFilterPositionCommand`
- [x] Add `SetObjectiveCommand`
- [x] Add `SetSpinningDiskPositionCommand`
- [x] Add `SetSpinningDiskSpinningCommand`
- [x] Add `SetDiskDichroicCommand`
- [x] Add `SetDiskEmissionFilterCommand`
- [x] Add `SetPiezoPositionCommand`
- [x] Add `MovePiezoRelativeCommand`

**Code to add:**

```python
# ============================================================================
# Peripheral Commands
# ============================================================================

@dataclass(frozen=True)
class SetFilterPositionCommand(Event):
    """Set filter wheel position."""
    position: int
    wheel_index: int = 0


@dataclass(frozen=True)
class SetObjectiveCommand(Event):
    """Change objective lens."""
    position: int


@dataclass(frozen=True)
class SetSpinningDiskPositionCommand(Event):
    """Move disk in/out of beam path."""
    in_beam: bool


@dataclass(frozen=True)
class SetSpinningDiskSpinningCommand(Event):
    """Start/stop disk spinning."""
    spinning: bool


@dataclass(frozen=True)
class SetDiskDichroicCommand(Event):
    """Set spinning disk dichroic position."""
    position: int


@dataclass(frozen=True)
class SetDiskEmissionFilterCommand(Event):
    """Set spinning disk emission filter position."""
    position: int


@dataclass(frozen=True)
class SetPiezoPositionCommand(Event):
    """Set piezo Z position (absolute)."""
    position_um: float


@dataclass(frozen=True)
class MovePiezoRelativeCommand(Event):
    """Move piezo Z relative to current position."""
    delta_um: float
```

**Commit:** `feat(events): Add peripheral command events`

---

### 2.2 Add Peripheral State Events ✅ COMPLETED

**File:** `/Users/wea/src/allenlab/Squid/software/squid/events.py`

- [x] Add `FilterPositionChanged`
- [x] Add `ObjectiveChanged`
- [x] Add `PixelSizeChanged`
- [x] Add `SpinningDiskStateChanged`
- [x] Add `PiezoPositionChanged`

**Code to add:**

```python
# ============================================================================
# Peripheral State Events
# ============================================================================

@dataclass(frozen=True)
class FilterPositionChanged(Event):
    """Filter wheel position changed."""
    position: int
    wheel_index: int = 0


@dataclass(frozen=True)
class ObjectiveChanged(Event):
    """Objective lens changed."""
    position: int
    objective_name: str | None = None
    magnification: float | None = None
    pixel_size_um: float | None = None


@dataclass(frozen=True)
class PixelSizeChanged(Event):
    """Pixel size changed (due to objective or binning change)."""
    pixel_size_um: float


@dataclass(frozen=True)
class SpinningDiskStateChanged(Event):
    """Spinning disk state changed."""
    is_disk_in: bool
    is_spinning: bool
    motor_speed: int
    dichroic: int
    emission_filter: int


@dataclass(frozen=True)
class PiezoPositionChanged(Event):
    """Piezo Z position changed."""
    position_um: float
```

**Commit:** `feat(events): Add peripheral state events`

---

### 2.3 Add Acquisition Progress Events ✅ COMPLETED

**File:** `/Users/wea/src/allenlab/Squid/software/squid/events.py`

- [x] Add `AcquisitionProgress`
- [x] Add `AcquisitionPaused`
- [x] Add `AcquisitionResumed`
- [x] Add `StartAcquisitionCommand`
- [x] Add `StopAcquisitionCommand`
- [x] Add `PauseAcquisitionCommand`
- [x] Add `ResumeAcquisitionCommand`

**Code to add:**

```python
# ============================================================================
# Acquisition Commands
# ============================================================================

@dataclass(frozen=True)
class StartAcquisitionCommand(Event):
    """Start multi-point acquisition."""
    # Note: Full config passed separately or via controller state
    experiment_id: str | None = None


@dataclass(frozen=True)
class StopAcquisitionCommand(Event):
    """Stop acquisition."""
    pass


@dataclass(frozen=True)
class PauseAcquisitionCommand(Event):
    """Pause acquisition."""
    pass


@dataclass(frozen=True)
class ResumeAcquisitionCommand(Event):
    """Resume paused acquisition."""
    pass


# ============================================================================
# Acquisition State Events
# ============================================================================

@dataclass(frozen=True)
class AcquisitionProgress(Event):
    """Progress update during acquisition."""
    current_fov: int
    total_fovs: int
    current_round: int
    total_rounds: int
    current_channel: str
    progress_percent: float
    eta_seconds: float | None = None


@dataclass(frozen=True)
class AcquisitionPaused(Event):
    """Acquisition was paused."""
    pass


@dataclass(frozen=True)
class AcquisitionResumed(Event):
    """Acquisition was resumed."""
    pass
```

**Commit:** `feat(events): Add acquisition progress events`

---

### 2.4 Add Autofocus Events ✅ COMPLETED

**File:** `/Users/wea/src/allenlab/Squid/software/squid/events.py`

- [x] Add `StartAutofocusCommand`
- [x] Add `StopAutofocusCommand`
- [x] Add `SetAutofocusParamsCommand`
- [x] Add `AutofocusProgress`
- [x] Add `AutofocusCompleted`
- [x] `FocusChanged` already existed (line ~170)

**Code to add:**

```python
# ============================================================================
# Autofocus Commands
# ============================================================================

@dataclass(frozen=True)
class StartAutofocusCommand(Event):
    """Start autofocus."""
    pass


@dataclass(frozen=True)
class StopAutofocusCommand(Event):
    """Stop autofocus."""
    pass


@dataclass(frozen=True)
class SetAutofocusParamsCommand(Event):
    """Configure autofocus parameters."""
    n_planes: int | None = None
    delta_z_um: float | None = None
    focus_metric: str | None = None


# ============================================================================
# Autofocus State Events
# ============================================================================

@dataclass(frozen=True)
class AutofocusProgress(Event):
    """Autofocus progress update."""
    current_step: int
    total_steps: int
    current_z: float
    best_z: float | None
    best_score: float | None


@dataclass(frozen=True)
class AutofocusCompleted(Event):
    """Autofocus completed."""
    success: bool
    z_position: float | None
    score: float | None
    error: str | None = None


@dataclass(frozen=True)
class FocusChanged(Event):
    """Focus position changed."""
    z_mm: float
    source: str  # "autofocus", "manual", "focus_map", "laser_af"
```

**Commit:** `feat(events): Add autofocus events`

---

### 2.5 Add New Hardware Protocols ✅ COMPLETED

**File:** `/Users/wea/src/allenlab/Squid/software/squid/abc.py`

Add these near the existing hardware protocols:

- [x] Add `ObjectiveInfo` dataclass (added `index` field to existing)
- [x] Verify/update `ObjectiveChanger` protocol (added @property decorators)
- [x] Verify/update `SpinningDiskController` protocol (added @property decorators)
- [x] Verify/update `PiezoStage` protocol (added @property decorators)

**Code to add/verify:**

```python
# ============================================================================
# Objective Changer
# ============================================================================

@dataclass(frozen=True)
class ObjectiveInfo:
    """Metadata about an objective lens."""
    name: str
    magnification: float
    na: float
    pixel_size_um: float
    parfocal_offset_um: float = 0.0


class ObjectiveChanger(Protocol):
    """Motorized objective turret."""

    @property
    def current_position(self) -> int:
        """Current objective position (0-indexed)."""
        ...

    @property
    def num_positions(self) -> int:
        """Number of objective positions."""
        ...

    def set_position(self, position: int) -> None:
        """Change to specified objective position."""
        ...

    def get_objective_info(self, position: int) -> ObjectiveInfo | None:
        """Get metadata for objective at position."""
        ...


# ============================================================================
# Spinning Disk Controller
# ============================================================================

class SpinningDiskController(Protocol):
    """Spinning disk confocal unit (xLight, Dragonfly, etc.)."""

    @property
    def is_disk_in(self) -> bool:
        """True if disk is in the beam path."""
        ...

    @property
    def is_spinning(self) -> bool:
        """True if disk is spinning."""
        ...

    @property
    def disk_motor_speed(self) -> int:
        """Current disk motor speed."""
        ...

    @property
    def current_dichroic(self) -> int:
        """Current dichroic position."""
        ...

    @property
    def current_emission_filter(self) -> int:
        """Current emission filter position."""
        ...

    def set_disk_position(self, in_beam: bool) -> None:
        """Move disk in/out of beam path."""
        ...

    def set_spinning(self, spinning: bool) -> None:
        """Start/stop disk spinning."""
        ...

    def set_disk_motor_speed(self, speed: int) -> None:
        """Set disk motor speed."""
        ...

    def set_dichroic(self, position: int) -> None:
        """Set dichroic position."""
        ...

    def set_emission_filter(self, position: int) -> None:
        """Set emission filter position."""
        ...


# ============================================================================
# Piezo Stage
# ============================================================================

class PiezoStage(Protocol):
    """Fast Z piezo for fine focus."""

    @property
    def position_um(self) -> float:
        """Current position in micrometers."""
        ...

    @property
    def range_um(self) -> tuple[float, float]:
        """Valid position range (min, max) in micrometers."""
        ...

    def move_to(self, position_um: float) -> None:
        """Move to absolute position."""
        ...

    def move_relative(self, delta_um: float) -> None:
        """Move relative to current position."""
        ...
```

**Note:** Some of these may already exist in `abc.py`. Verify first, then add or update as needed.

**Commit:** `feat(abc): Add/update peripheral hardware protocols`

---

### 2.6 Harden IlluminationService (Optional but Recommended) ✅ COMPLETED

**File:** `/Users/wea/src/allenlab/Squid/software/squid/services/illumination_service.py`

- [x] Add `threading.RLock()` around hardware access
- [ ] Support multi-source routing (LED array, NL5, laser, etc.) via a channel->source map (deferred - not critical for Phase 2)
- [x] Ensure `SetIlluminationCommand` respects `on` flag (shutter open/close) and publishes `IlluminationStateChanged`
- [x] Keep API stable for controllers/widgets; no widget behavior changes

**Commit:** `feat(services): Harden illumination service`

---

### 2.7 Create Controllers Directory ✅ COMPLETED

- [x] Create directory structure
- [x] Create `__init__.py` with exports

**Commands:**
```bash
mkdir -p /Users/wea/src/allenlab/Squid/software/squid/controllers
```

**Create file:** `/Users/wea/src/allenlab/Squid/software/squid/controllers/__init__.py`

```python
"""
Controller layer for Squid microscopy software.

Controllers orchestrate workflows and manage state.
They subscribe to command events and publish state events.

Available controllers (to be implemented in Phase 3):
- MicroscopeModeController: Manages microscope channel/mode switching
- PeripheralsController: Manages objective, spinning disk, piezo
"""

# Will be populated as controllers are created in Phase 3
# from .microscope_mode_controller import MicroscopeModeController
# from .peripherals_controller import PeripheralsController

__all__ = [
    # "MicroscopeModeController",
    # "PeripheralsController",
]
```

**Commit:** `chore: Create squid/controllers directory structure`

---

### 2.8 Write Tests for New Events ✅ COMPLETED

**File:** `/Users/wea/src/allenlab/Squid/software/tests/unit/squid/test_events.py`

Tests added:
- `TestNewPeripheralEvents` (14 test methods)
- `TestNewAcquisitionEvents` (9 test methods)
- `TestNewAutofocusEvents` (8 test methods)

Add tests verifying new events can be instantiated:

```python
import pytest
from squid.events import (
    # Peripheral commands
    SetFilterPositionCommand,
    SetObjectiveCommand,
    SetSpinningDiskPositionCommand,
    SetSpinningDiskSpinningCommand,
    SetDiskDichroicCommand,
    SetDiskEmissionFilterCommand,
    SetPiezoPositionCommand,
    MovePiezoRelativeCommand,
    # Peripheral state
    FilterPositionChanged,
    ObjectiveChanged,
    PixelSizeChanged,
    SpinningDiskStateChanged,
    PiezoPositionChanged,
    # Acquisition
    StartAcquisitionCommand,
    StopAcquisitionCommand,
    PauseAcquisitionCommand,
    ResumeAcquisitionCommand,
    AcquisitionProgress,
    AcquisitionPaused,
    AcquisitionResumed,
    # Autofocus
    StartAutofocusCommand,
    StopAutofocusCommand,
    SetAutofocusParamsCommand,
    AutofocusProgress,
    AutofocusCompleted,
    FocusChanged,
)


class TestNewPeripheralEvents:
    """Test new peripheral events."""

    def test_set_filter_position_command(self):
        cmd = SetFilterPositionCommand(position=3, wheel_index=0)
        assert cmd.position == 3
        assert cmd.wheel_index == 0

    def test_set_objective_command(self):
        cmd = SetObjectiveCommand(position=1)
        assert cmd.position == 1

    def test_set_spinning_disk_position_command(self):
        cmd = SetSpinningDiskPositionCommand(in_beam=True)
        assert cmd.in_beam is True

    def test_set_piezo_position_command(self):
        cmd = SetPiezoPositionCommand(position_um=50.0)
        assert cmd.position_um == 50.0

    def test_filter_position_changed(self):
        event = FilterPositionChanged(position=2, wheel_index=1)
        assert event.position == 2
        assert event.wheel_index == 1

    def test_objective_changed(self):
        event = ObjectiveChanged(position=0, objective_name="20x", magnification=20.0)
        assert event.position == 0
        assert event.objective_name == "20x"

    def test_spinning_disk_state_changed(self):
        event = SpinningDiskStateChanged(
            is_disk_in=True,
            is_spinning=True,
            motor_speed=5000,
            dichroic=0,
            emission_filter=1,
        )
        assert event.is_disk_in is True
        assert event.motor_speed == 5000


class TestNewAcquisitionEvents:
    """Test new acquisition events."""

    def test_acquisition_progress(self):
        event = AcquisitionProgress(
            current_fov=5,
            total_fovs=100,
            current_round=1,
            total_rounds=3,
            current_channel="DAPI",
            progress_percent=5.0,
            eta_seconds=3600.0,
        )
        assert event.current_fov == 5
        assert event.progress_percent == 5.0

    def test_start_acquisition_command(self):
        cmd = StartAcquisitionCommand(experiment_id="exp_001")
        assert cmd.experiment_id == "exp_001"


class TestNewAutofocusEvents:
    """Test new autofocus events."""

    def test_autofocus_progress(self):
        event = AutofocusProgress(
            current_step=3,
            total_steps=10,
            current_z=1.5,
            best_z=1.2,
            best_score=0.95,
        )
        assert event.current_step == 3
        assert event.best_score == 0.95

    def test_autofocus_completed(self):
        event = AutofocusCompleted(
            success=True,
            z_position=1.25,
            score=0.98,
            error=None,
        )
        assert event.success is True
        assert event.z_position == 1.25

    def test_focus_changed(self):
        event = FocusChanged(z_mm=1.5, source="autofocus")
        assert event.z_mm == 1.5
        assert event.source == "autofocus"
```

**Run tests:**
```bash
NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/test_events.py -v
```

**Commit:** `test(events): Add tests for new event types`

---

## Verification Checklist

Before proceeding to Phase 3, verify:

- [x] All new events are defined in `squid/events.py`
- [x] All new protocols are defined in `squid/abc.py`
- [x] `squid/controllers/__init__.py` exists
- [x] No import errors: `python -c "from squid.events import *"`
- [X] No import errors: `python -c "from squid.abc import *"`
- [X] No import errors: `python -c "from squid.controllers import *"`
- [X] Tests pass: `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/test_events.py -v`
- [ ] Application still starts: `python main_hcs.py --simulation`

**Status:** All code tasks (2.1-2.8) complete. Manual verification required.

**Manual Verification Commands:**
```bash
# Quick syntax check
python3 -m py_compile squid/events.py
python3 -m py_compile squid/abc.py
python3 -m py_compile squid/controllers/__init__.py

# Import tests (may be slow due to numba)
NUMBA_DISABLE_JIT=1 python -c "from squid.events import *"
NUMBA_DISABLE_JIT=1 python -c "from squid.abc import *"
NUMBA_DISABLE_JIT=1 python -c "from squid.controllers import *"

# Run tests
NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/test_events.py -v

# Full app test
python main_hcs.py --simulation
```

---

## Commit Summary

| Order | Commit Message | Files |
|-------|----------------|-------|
| 1 | `feat(events): Add peripheral command events` | `squid/events.py` |
| 2 | `feat(events): Add peripheral state events` | `squid/events.py` |
| 3 | `feat(events): Add acquisition progress events` | `squid/events.py` |
| 4 | `feat(events): Add autofocus events` | `squid/events.py` |
| 5 | `feat(abc): Add/update peripheral hardware protocols` | `squid/abc.py` |
| 6 | `feat(services): Harden illumination service` | `squid/services/illumination_service.py` |
| 7 | `chore: Create squid/controllers directory structure` | `squid/controllers/__init__.py` |
| 8 | `test(events): Add tests for new event types` | `tests/unit/squid/test_events.py` |

---

## Next Steps

Once all checkmarks are complete, proceed to:
→ [PHASE_3_SERVICE_CONTROLLER_MERGE.md](./PHASE_3_SERVICE_CONTROLLER_MERGE.md)
