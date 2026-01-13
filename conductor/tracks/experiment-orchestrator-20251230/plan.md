# Experiment Orchestrator Implementation Plan

## Overview

This plan assumes the multipoint refactoring from `conductor/tracks/multipoint-refactor-20251230/` is complete, providing clean building blocks for the orchestrator.

---

## Updates (2025-01-12)

### BaseController Integration

The recent `BaseController`/`BaseManager` refactoring changes how the OrchestratorController should handle event subscriptions. Instead of manual subscription:

```python
# OLD - Don't use
def _subscribe_to_commands(self):
    self._event_bus.subscribe(LoadProtocolCommand, self._on_load_protocol)
```

Use `@handles` decorators with automatic subscription:

```python
# NEW - Use this pattern
from squid.core.events import handles

class OrchestratorController(StateMachine[OrchestratorState]):
    @handles(LoadProtocolCommand)
    def _on_load_protocol(self, cmd: LoadProtocolCommand) -> None:
        ...
```

StateMachine now calls `auto_subscribe()` in `__init__`, so handlers are automatically registered.

### Pre-flight Validation (NEW)

Add two-stage validation:

1. **Schema validation** (in `loader.py`) - Syntax, required fields, data types
2. **Pre-flight validation** (before RUNNING state):
   - All referenced channels exist in ChannelConfigurationManager
   - Positions file exists and is readable
   - Fluidics service available if fluidics steps defined
   - Sufficient disk space for estimated acquisition
   - Hardware connected (camera, stage, etc.)
   - Objective matches protocol specification

### Phase 0: Missing Dependencies (NEW)

Before orchestrator implementation, extract these from MultiPointController:

- **ExperimentManager** (`multipoint/experiment_manager.py`) - Folder creation, metadata
- **AcquisitionPlanner** (`multipoint/acquisition_planner.py`) - Estimation, validation
- **ImageCaptureExecutor** - Enhance `multipoint/image_capture.py`

### UI Updates

Add **InterventionDialog** for user decisions during errors:
- Triggered by `UserInterventionRequired` event
- Shows reason and options (Retry, Skip, Abort)
- Blocks orchestrator until user responds

### Revised Timeline

| Phase | Description | Effort |
|-------|-------------|--------|
| 0 | ExperimentManager + AcquisitionPlanner extraction | 2-3 days |
| 1 | Protocol schema + loader + pre-flight | 2 days |
| 2 | CancelToken + State management | 1.5 days |
| 3 | OrchestratorController + Executors | 3 days |
| 4 | Performance Mode UI + Intervention dialog | 2-3 days |
| 5 | Integration + Tests | 2 days |
| **Total** | | **~13 days** |

---

## Phase 1: Protocol Definition System

### 1.1 Protocol Schema (YAML)

**File:** `software/src/squid/core/protocol/schema.py`

```python
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum

class FluidicsAction(Enum):
    ADD_PROBE = "add_probe"
    WASH = "wash"
    INCUBATE = "incubate"
    CLEAVE = "cleave"
    CUSTOM = "custom"

@dataclass
class FluidicsStep:
    action: FluidicsAction
    probe: Optional[str] = None
    volume_ul: Optional[float] = None
    time_min: Optional[float] = None
    cycles: Optional[int] = None
    custom_command: Optional[str] = None

@dataclass
class FluidicsSequence:
    steps: List[FluidicsStep] = field(default_factory=list)

@dataclass
class ZStackConfig:
    range_um: float = 0.0
    step_um: float = 0.5
    center_on_autofocus: bool = True

@dataclass
class AutofocusConfig:
    enabled: bool = True
    use_laser_af: bool = True
    interval_fovs: int = 5

@dataclass
class ImagingConfig:
    channels: List[str]
    z_stack: Optional[ZStackConfig] = None
    autofocus: Optional[AutofocusConfig] = None
    exposure_overrides: Dict[str, float] = field(default_factory=dict)

@dataclass
class Round:
    name: str
    fluidics: Optional[FluidicsSequence] = None
    imaging: Optional[ImagingConfig] = None
    skip_imaging: bool = False  # For fluidics-only rounds

@dataclass
class PositionSource:
    file: Optional[str] = None  # Path to positions.csv
    inline: Optional[List[Dict[str, float]]] = None  # [{x: 1.0, y: 2.0, z: 0.5}, ...]

@dataclass
class MicroscopeConfig:
    objective: str
    binning: int = 1

@dataclass
class Protocol:
    name: str
    version: str = "1.0"
    microscope: MicroscopeConfig
    positions: PositionSource
    rounds: List[Round]

    # Metadata
    author: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[str] = None
```

### 1.2 Protocol Loader & Validator

**File:** `software/src/squid/core/protocol/loader.py`

```python
import yaml
from pathlib import Path
from typing import List, Tuple
from .schema import Protocol, Round, FluidicsSequence

class ProtocolValidationError(Exception):
    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__(f"Protocol validation failed: {errors}")

class ProtocolLoader:
    def load(self, path: Path) -> Protocol:
        """Load and validate a protocol from YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)

        protocol = self._parse_protocol(data)
        errors = self.validate(protocol)
        if errors:
            raise ProtocolValidationError(errors)
        return protocol

    def save(self, protocol: Protocol, path: Path) -> None:
        """Save protocol to YAML file."""
        data = self._serialize_protocol(protocol)
        with open(path, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def validate(self, protocol: Protocol) -> List[str]:
        """Validate protocol, return list of error messages."""
        errors = []

        # Must have at least one round
        if not protocol.rounds:
            errors.append("Protocol must have at least one round")

        # Each round with imaging must have channels
        for i, round in enumerate(protocol.rounds):
            if not round.skip_imaging and round.imaging:
                if not round.imaging.channels:
                    errors.append(f"Round {i+1} ({round.name}): imaging requires at least one channel")

        # Validate positions source
        if not protocol.positions.file and not protocol.positions.inline:
            errors.append("Protocol must specify positions (file or inline)")

        # Validate channel names exist (requires channel config manager)
        # This is done at runtime when loading

        return errors

    def _parse_protocol(self, data: dict) -> Protocol:
        """Parse YAML dict into Protocol dataclass."""
        # Implementation: recursive dataclass construction
        ...

    def _serialize_protocol(self, protocol: Protocol) -> dict:
        """Serialize Protocol to YAML-compatible dict."""
        ...
```

### 1.3 Example Protocol Files

**File:** `software/configurations/protocols/example_sequential_fish.yaml`

```yaml
protocol:
  name: "10-Round Sequential FISH"
  version: "1.0"
  author: "Lab User"
  description: "Sequential FISH protocol for 10 probe rounds"

microscope:
  objective: "20x"
  binning: 1

positions:
  file: "positions.csv"  # Relative to protocol file

rounds:
  - name: "Round 1 - Hybridization"
    fluidics:
      steps:
        - action: add_probe
          probe: "ProbeSet_R1"
          volume_ul: 200
        - action: incubate
          time_min: 30
        - action: wash
          cycles: 3
    imaging:
      channels: ["DAPI", "FITC", "Cy3", "Cy5"]
      z_stack:
        range_um: 10
        step_um: 0.5
      autofocus:
        enabled: true
        use_laser_af: true
        interval_fovs: 5

  - name: "Round 1 - Strip"
    fluidics:
      steps:
        - action: cleave
          time_min: 15
        - action: wash
          cycles: 5
    skip_imaging: true

  - name: "Round 2 - Hybridization"
    fluidics:
      steps:
        - action: add_probe
          probe: "ProbeSet_R2"
          volume_ul: 200
        - action: incubate
          time_min: 30
        - action: wash
          cycles: 3
    imaging:
      channels: ["DAPI", "FITC", "Cy3", "Cy5"]
      z_stack:
        range_um: 10
        step_um: 0.5
      autofocus:
        enabled: true
        interval_fovs: 5
```

---

## Phase 2: Experiment State Management

### 2.1 CancelToken - Cooperative Cancellation

**File:** `software/src/squid/core/utils/cancel_token.py`

The CancelToken provides clean pause/abort semantics with checkpoints only at operation boundaries.

**Key Design Principle:** Pause and abort only happen between atomic operations:
- Between FOVs (not mid-z-stack or mid-exposure)
- Between fluidics steps (not mid-wash-cycle)
- Between rounds

```python
import threading
import time
from typing import Optional, Callable
from dataclasses import dataclass
from enum import Enum, auto


class AbortRequested(Exception):
    """Raised at checkpoint when abort has been requested."""
    pass


class AbortMode(Enum):
    """How abort should behave."""
    SOFT = auto()   # Wait for current atomic operation to complete
    HARD = auto()   # Request immediate stop (may leave hardware in bad state)


@dataclass
class CheckpointContext:
    """Information about where we are when paused."""
    operation: str          # e.g., "imaging", "fluidics"
    round_index: int
    detail: str             # e.g., "FOV 47/100", "Wash step 2/3"


class CancelToken:
    """
    Cooperative cancellation token for long-running operations.

    Provides clean pause/abort at operation boundaries only.

    Usage:
        token = CancelToken()

        for fov in fovs:
            token.checkpoint(CheckpointContext("imaging", 0, f"FOV {fov}"))
            acquire_fov(fov)  # Runs to completion - atomic

    From another thread:
        token.request_pause()   # Will pause at next checkpoint
        token.request_resume()  # Resume execution
        token.request_abort()   # Will raise AbortRequested at next checkpoint
    """

    def __init__(self):
        self._abort = threading.Event()
        self._abort_mode = AbortMode.SOFT
        self._pause = threading.Event()

        # Callbacks
        self._on_paused: Optional[Callable[[CheckpointContext], None]] = None
        self._on_resumed: Optional[Callable[[], None]] = None
        self._on_checkpoint: Optional[Callable[[CheckpointContext], None]] = None

        # State tracking
        self._current_context: Optional[CheckpointContext] = None
        self._is_paused = False

    # === Control Methods (called from UI/main thread) ===

    def request_pause(self) -> None:
        """Request pause at next checkpoint."""
        self._pause.set()

    def request_resume(self) -> None:
        """Resume from paused state."""
        self._pause.clear()

    def request_abort(self, mode: AbortMode = AbortMode.SOFT) -> None:
        """
        Request abort at next checkpoint.

        Args:
            mode: SOFT waits for current operation, HARD requests immediate stop
        """
        self._abort_mode = mode
        self._abort.set()
        self._pause.clear()  # Unblock any pause wait

    # === Query Methods ===

    @property
    def is_abort_requested(self) -> bool:
        return self._abort.is_set()

    @property
    def is_pause_requested(self) -> bool:
        return self._pause.is_set()

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @property
    def abort_mode(self) -> AbortMode:
        return self._abort_mode

    @property
    def current_context(self) -> Optional[CheckpointContext]:
        """Where we are currently (or where we paused)."""
        return self._current_context

    # === Checkpoint Method (called from worker thread) ===

    def checkpoint(self, context: CheckpointContext) -> None:
        """
        Call at operation boundaries (between FOVs, between fluidics steps).

        - If abort requested: raises AbortRequested
        - If pause requested: blocks until resumed or aborted

        Args:
            context: Information about current position for UI/logging

        Raises:
            AbortRequested: When abort has been requested
        """
        self._current_context = context

        # Notify checkpoint callback (for progress tracking)
        if self._on_checkpoint:
            self._on_checkpoint(context)

        # Check abort first
        if self._abort.is_set():
            raise AbortRequested()

        # Handle pause
        if self._pause.is_set():
            self._is_paused = True

            if self._on_paused:
                self._on_paused(context)

            # Wait for resume or abort
            while self._pause.is_set() and not self._abort.is_set():
                time.sleep(0.05)

            self._is_paused = False

            # Check if we were aborted while paused
            if self._abort.is_set():
                raise AbortRequested()

            if self._on_resumed:
                self._on_resumed()

    # === Callback Registration ===

    def on_paused(self, callback: Callable[[CheckpointContext], None]) -> 'CancelToken':
        """Register callback when pause starts. Receives context of where we paused."""
        self._on_paused = callback
        return self

    def on_resumed(self, callback: Callable[[], None]) -> 'CancelToken':
        """Register callback when resumed from pause."""
        self._on_resumed = callback
        return self

    def on_checkpoint(self, callback: Callable[[CheckpointContext], None]) -> 'CancelToken':
        """Register callback at every checkpoint (for progress tracking)."""
        self._on_checkpoint = callback
        return self

    # === Context Manager for Scoped Operations ===

    def atomic_operation(self, description: str):
        """
        Context manager marking an atomic (non-interruptible) operation.

        Usage:
            with token.atomic_operation("Z-stack acquisition"):
                for z in z_levels:
                    capture(z)  # Cannot be paused/aborted mid-stack

        Note: This is documentation/logging only - we don't actually
        block pause requests, we just don't check them inside.
        """
        return _AtomicOperationContext(self, description)


class _AtomicOperationContext:
    """Context manager for atomic operations (documentation only)."""

    def __init__(self, token: CancelToken, description: str):
        self._token = token
        self._description = description

    def __enter__(self):
        # Could log that we're starting an atomic operation
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Could log completion
        return False
```

**Usage Pattern:**

```python
class ImagingExecutor:
    def execute(self, positions, cancel_token: CancelToken):
        for fov_idx, fov in enumerate(positions):
            # Checkpoint BETWEEN FOVs - can pause/abort here
            cancel_token.checkpoint(CheckpointContext(
                operation="imaging",
                round_index=self._round_index,
                detail=f"FOV {fov_idx + 1}/{len(positions)}"
            ))

            # FOV acquisition is ATOMIC - no pause/abort during this
            self._acquire_fov(fov)  # z-stack + all channels
```

### 2.2 State Machine

**File:** `software/src/squid/backend/controllers/orchestrator/state.py`

```python
from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum, auto
from datetime import datetime

class OrchestratorState(Enum):
    IDLE = auto()
    LOADING = auto()
    VALIDATING = auto()
    READY = auto()
    RUNNING_FLUIDICS = auto()
    RUNNING_IMAGING = auto()
    PAUSED = auto()
    WAITING_FOR_USER = auto()  # For manual intervention prompts
    COMPLETING_ROUND = auto()
    ABORTING = auto()
    COMPLETED = auto()
    FAILED = auto()

@dataclass
class RoundProgress:
    round_index: int
    round_name: str
    fluidics_complete: bool = False
    imaging_started: bool = False
    imaging_complete: bool = False
    current_fov: int = 0
    total_fovs: int = 0
    error: Optional[str] = None

@dataclass
class ExperimentProgress:
    """Complete state of an orchestrated experiment."""
    # Identity
    experiment_id: str
    protocol_name: str
    protocol_path: str

    # Round progress
    current_round: int = 0
    total_rounds: int = 0
    rounds: List[RoundProgress] = field(default_factory=list)

    # Overall progress
    total_fovs_all_rounds: int = 0
    completed_fovs_all_rounds: int = 0

    # Timing
    started_at: Optional[datetime] = None
    paused_at: Optional[datetime] = None
    total_pause_duration_s: float = 0.0
    estimated_completion: Optional[datetime] = None

    # Position state (for resume)
    last_completed_round: int = -1
    last_completed_fov: int = -1
    last_position: Optional[tuple] = None  # (x, y, z) in mm

    @property
    def progress_percent(self) -> float:
        if self.total_fovs_all_rounds == 0:
            return 0.0
        return (self.completed_fovs_all_rounds / self.total_fovs_all_rounds) * 100

@dataclass
class ExperimentCheckpoint:
    """Serializable checkpoint for crash recovery."""
    progress: ExperimentProgress
    state: OrchestratorState
    checkpoint_time: datetime

    # For exact resume
    pending_fluidics_steps: List[int] = field(default_factory=list)
    pending_positions: List[int] = field(default_factory=list)
```

### 2.2 Checkpoint Manager

**File:** `software/src/squid/backend/controllers/orchestrator/checkpoint.py`

```python
import json
from pathlib import Path
from datetime import datetime
from typing import Optional
from .state import ExperimentCheckpoint, ExperimentProgress, OrchestratorState

class CheckpointManager:
    """Manages experiment state persistence for crash recovery."""

    CHECKPOINT_FILENAME = "experiment_checkpoint.json"

    def __init__(self, experiment_path: Path):
        self._experiment_path = experiment_path
        self._checkpoint_path = experiment_path / self.CHECKPOINT_FILENAME

    def save(self, checkpoint: ExperimentCheckpoint) -> None:
        """Save checkpoint to disk. Called after each FOV completion."""
        data = self._serialize(checkpoint)
        # Atomic write: write to temp file, then rename
        temp_path = self._checkpoint_path.with_suffix('.tmp')
        with open(temp_path, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        temp_path.rename(self._checkpoint_path)

    def load(self) -> Optional[ExperimentCheckpoint]:
        """Load checkpoint from disk if exists."""
        if not self._checkpoint_path.exists():
            return None
        with open(self._checkpoint_path) as f:
            data = json.load(f)
        return self._deserialize(data)

    def clear(self) -> None:
        """Remove checkpoint file (on successful completion)."""
        if self._checkpoint_path.exists():
            self._checkpoint_path.unlink()

    def _serialize(self, checkpoint: ExperimentCheckpoint) -> dict:
        """Convert checkpoint to JSON-serializable dict."""
        ...

    def _deserialize(self, data: dict) -> ExperimentCheckpoint:
        """Reconstruct checkpoint from JSON dict."""
        ...
```

---

## Phase 3: Orchestrator Controller

### 3.1 Events

**File:** `software/src/squid/core/events.py` (additions)

```python
# === Orchestrator Commands ===

@dataclass(frozen=True)
class LoadProtocolCommand(Event):
    """Load a protocol file."""
    protocol_path: str

@dataclass(frozen=True)
class StartExperimentCommand(Event):
    """Start the loaded experiment."""
    resume_from_checkpoint: bool = False

@dataclass(frozen=True)
class PauseExperimentCommand(Event):
    """Pause the running experiment after current operation."""
    pass

@dataclass(frozen=True)
class ResumeExperimentCommand(Event):
    """Resume a paused experiment."""
    pass

@dataclass(frozen=True)
class SkipToRoundCommand(Event):
    """Skip to a specific round."""
    round_index: int
    skip_fluidics: bool = False

@dataclass(frozen=True)
class SkipCurrentFOVCommand(Event):
    """Skip the current FOV and move to next."""
    pass

@dataclass(frozen=True)
class AbortExperimentCommand(Event):
    """Abort the experiment entirely."""
    pass

@dataclass(frozen=True)
class RetryCurrentOperationCommand(Event):
    """Retry the current failed operation."""
    pass

# === Orchestrator State Events ===

@dataclass(frozen=True)
class ProtocolLoaded(Event):
    """Protocol successfully loaded and validated."""
    protocol_name: str
    total_rounds: int
    total_fovs: int
    estimated_duration_hours: float

@dataclass(frozen=True)
class ProtocolLoadFailed(Event):
    """Protocol loading/validation failed."""
    path: str
    errors: List[str]

@dataclass(frozen=True)
class ExperimentStateChanged(Event):
    """Orchestrator state changed."""
    state: OrchestratorState
    previous_state: OrchestratorState

@dataclass(frozen=True)
class ExperimentProgressUpdate(Event):
    """Progress update for UI."""
    progress: ExperimentProgress

@dataclass(frozen=True)
class RoundStarted(Event):
    """A new round has started."""
    round_index: int
    round_name: str
    has_fluidics: bool
    has_imaging: bool

@dataclass(frozen=True)
class RoundCompleted(Event):
    """A round has completed."""
    round_index: int
    round_name: str
    success: bool
    error: Optional[str] = None

@dataclass(frozen=True)
class FluidicsStepStarted(Event):
    """A fluidics step has started."""
    round_index: int
    step_index: int
    action: str
    description: str

@dataclass(frozen=True)
class FluidicsStepCompleted(Event):
    """A fluidics step has completed."""
    round_index: int
    step_index: int
    success: bool

@dataclass(frozen=True)
class UserInterventionRequired(Event):
    """Manual intervention needed."""
    reason: str
    options: List[str]  # e.g., ["Retry", "Skip", "Abort"]

@dataclass(frozen=True)
class ExperimentCompleted(Event):
    """Entire experiment completed."""
    experiment_id: str
    success: bool
    total_duration_hours: float
    error: Optional[str] = None
```

### 3.2 Orchestrator Controller

**File:** `software/src/squid/backend/controllers/orchestrator/orchestrator_controller.py`

```python
from typing import Optional, List
from pathlib import Path
import threading
import time

from squid.core.events import EventBus
from squid.core.state_machine import StateMachine
from squid.core.protocol.schema import Protocol, Round
from squid.core.protocol.loader import ProtocolLoader

from squid.backend.controllers.multipoint.experiment_manager import ExperimentManager
from squid.backend.controllers.multipoint.acquisition_planner import AcquisitionPlanner
from squid.backend.controllers.multipoint.progress_tracking import ProgressTracker
from squid.backend.controllers.multipoint.position_zstack import PositionController
from squid.backend.controllers.multipoint.image_capture import ImageCaptureExecutor
from squid.backend.services.acquisition_service import AcquisitionService
from squid.backend.services.fluidics_service import FluidicsService
from squid.backend.managers.scan_coordinates import ScanCoordinates

from .state import OrchestratorState, ExperimentProgress, ExperimentCheckpoint
from .checkpoint import CheckpointManager
from .fluidics_executor import FluidicsExecutor
from .imaging_executor import ImagingExecutor

import squid.logging
_log = squid.logging.get_logger(__name__)


class OrchestratorController(StateMachine[OrchestratorState]):
    """
    High-level experiment orchestrator.

    Sequences multiple rounds of fluidics + imaging operations,
    with support for pause/resume/skip.
    """

    VALID_TRANSITIONS = {
        OrchestratorState.IDLE: {OrchestratorState.LOADING},
        OrchestratorState.LOADING: {OrchestratorState.VALIDATING, OrchestratorState.FAILED},
        OrchestratorState.VALIDATING: {OrchestratorState.READY, OrchestratorState.FAILED},
        OrchestratorState.READY: {OrchestratorState.RUNNING_FLUIDICS, OrchestratorState.RUNNING_IMAGING, OrchestratorState.IDLE},
        OrchestratorState.RUNNING_FLUIDICS: {OrchestratorState.RUNNING_IMAGING, OrchestratorState.PAUSED, OrchestratorState.COMPLETING_ROUND, OrchestratorState.FAILED, OrchestratorState.ABORTING},
        OrchestratorState.RUNNING_IMAGING: {OrchestratorState.PAUSED, OrchestratorState.COMPLETING_ROUND, OrchestratorState.FAILED, OrchestratorState.ABORTING},
        OrchestratorState.PAUSED: {OrchestratorState.RUNNING_FLUIDICS, OrchestratorState.RUNNING_IMAGING, OrchestratorState.ABORTING},
        OrchestratorState.WAITING_FOR_USER: {OrchestratorState.RUNNING_FLUIDICS, OrchestratorState.RUNNING_IMAGING, OrchestratorState.ABORTING},
        OrchestratorState.COMPLETING_ROUND: {OrchestratorState.RUNNING_FLUIDICS, OrchestratorState.RUNNING_IMAGING, OrchestratorState.COMPLETED},
        OrchestratorState.ABORTING: {OrchestratorState.COMPLETED, OrchestratorState.FAILED},
        OrchestratorState.COMPLETED: {OrchestratorState.IDLE},
        OrchestratorState.FAILED: {OrchestratorState.IDLE},
    }

    def __init__(
        self,
        event_bus: EventBus,
        # Refactored components (from multipoint refactor)
        experiment_manager: ExperimentManager,
        acquisition_planner: AcquisitionPlanner,
        acquisition_service: AcquisitionService,
        position_controller: PositionController,
        # Services
        fluidics_service: Optional[FluidicsService],
        scan_coordinates: ScanCoordinates,
        # Other dependencies
        channel_config_manager,
        objective_store,
    ):
        super().__init__(OrchestratorState.IDLE, self.VALID_TRANSITIONS)

        self._event_bus = event_bus
        self._experiment_manager = experiment_manager
        self._acquisition_planner = acquisition_planner
        self._acquisition_service = acquisition_service
        self._position_controller = position_controller
        self._fluidics_service = fluidics_service
        self._scan_coordinates = scan_coordinates
        self._channel_config_manager = channel_config_manager
        self._objective_store = objective_store

        # Protocol state
        self._protocol: Optional[Protocol] = None
        self._protocol_path: Optional[Path] = None
        self._progress: Optional[ExperimentProgress] = None
        self._checkpoint_manager: Optional[CheckpointManager] = None

        # Executors (created per-experiment)
        self._fluidics_executor: Optional[FluidicsExecutor] = None
        self._imaging_executor: Optional[ImagingExecutor] = None

        # CancelToken for pause/abort control
        self._cancel_token: Optional[CancelToken] = None

        # Worker thread
        self._worker_thread: Optional[threading.Thread] = None

        # Subscribe to commands
        self._subscribe_to_commands()

    def _subscribe_to_commands(self):
        self._event_bus.subscribe(LoadProtocolCommand, self._on_load_protocol)
        self._event_bus.subscribe(StartExperimentCommand, self._on_start_experiment)
        self._event_bus.subscribe(PauseExperimentCommand, self._on_pause)
        self._event_bus.subscribe(ResumeExperimentCommand, self._on_resume)
        self._event_bus.subscribe(SkipToRoundCommand, self._on_skip_to_round)
        self._event_bus.subscribe(SkipCurrentFOVCommand, self._on_skip_fov)
        self._event_bus.subscribe(AbortExperimentCommand, self._on_abort)

    # === Command Handlers ===

    def _on_load_protocol(self, cmd: LoadProtocolCommand):
        """Load and validate a protocol file."""
        if self.state not in {OrchestratorState.IDLE, OrchestratorState.COMPLETED, OrchestratorState.FAILED}:
            _log.warning("Cannot load protocol while experiment is running")
            return

        self._transition_to(OrchestratorState.LOADING)

        try:
            loader = ProtocolLoader()
            self._protocol_path = Path(cmd.protocol_path)
            self._protocol = loader.load(self._protocol_path)

            self._transition_to(OrchestratorState.VALIDATING)

            # Validate channels exist
            errors = self._validate_channels()
            if errors:
                raise ProtocolValidationError(errors)

            # Calculate totals
            total_fovs = self._calculate_total_fovs()
            estimated_hours = self._estimate_duration_hours()

            self._transition_to(OrchestratorState.READY)

            self._event_bus.publish(ProtocolLoaded(
                protocol_name=self._protocol.name,
                total_rounds=len(self._protocol.rounds),
                total_fovs=total_fovs,
                estimated_duration_hours=estimated_hours,
            ))

        except Exception as e:
            _log.error(f"Failed to load protocol: {e}")
            self._transition_to(OrchestratorState.FAILED)
            self._event_bus.publish(ProtocolLoadFailed(
                path=cmd.protocol_path,
                errors=[str(e)],
            ))

    def _on_start_experiment(self, cmd: StartExperimentCommand):
        """Start or resume the experiment."""
        if self.state != OrchestratorState.READY:
            _log.warning(f"Cannot start experiment in state {self.state}")
            return

        # Create CancelToken with callbacks for state management
        self._cancel_token = CancelToken()
        self._cancel_token.on_paused(self._on_token_paused)
        self._cancel_token.on_resumed(self._on_token_resumed)

        # Start worker thread
        self._worker_thread = threading.Thread(
            target=self._run_experiment,
            args=(cmd.resume_from_checkpoint,),
            daemon=True,
        )
        self._worker_thread.start()

    def _on_pause(self, cmd: PauseExperimentCommand):
        """Request pause after current atomic operation."""
        if self.state in {OrchestratorState.RUNNING_FLUIDICS, OrchestratorState.RUNNING_IMAGING}:
            _log.info("Pause requested - will pause after current operation completes")
            if self._cancel_token:
                self._cancel_token.request_pause()

    def _on_resume(self, cmd: ResumeExperimentCommand):
        """Resume from paused state."""
        if self.state == OrchestratorState.PAUSED:
            _log.info("Resume requested")
            if self._cancel_token:
                self._cancel_token.request_resume()

    def _on_skip_to_round(self, cmd: SkipToRoundCommand):
        """Skip to a specific round (only while paused)."""
        if self.state == OrchestratorState.PAUSED:
            self._progress.current_round = cmd.round_index
            if cmd.skip_fluidics:
                self._progress.rounds[cmd.round_index].fluidics_complete = True
            _log.info(f"Skipping to round {cmd.round_index}")
            # Resume will pick up at new round
            if self._cancel_token:
                self._cancel_token.request_resume()

    def _on_skip_fov(self, cmd: SkipCurrentFOVCommand):
        """Skip current FOV - only effective while paused."""
        if self.state == OrchestratorState.PAUSED:
            # Increment FOV counter in current round
            round_progress = self._progress.rounds[self._progress.current_round]
            round_progress.current_fov += 1
            _log.info(f"Skipping to FOV {round_progress.current_fov}")

    def _on_abort(self, cmd: AbortExperimentCommand):
        """Abort the experiment after current atomic operation."""
        _log.info("Abort requested - will abort after current operation completes")
        if self._cancel_token:
            self._cancel_token.request_abort(AbortMode.SOFT)
        self._transition_to(OrchestratorState.ABORTING)

    # === CancelToken Callbacks ===

    def _on_token_paused(self, context: CheckpointContext):
        """Called when execution pauses at a checkpoint."""
        self._transition_to(OrchestratorState.PAUSED)
        self._progress.paused_at = datetime.now()
        self._save_checkpoint()
        _log.info(f"Paused at: {context.operation} - {context.detail}")

    def _on_token_resumed(self):
        """Called when execution resumes from pause."""
        # Calculate pause duration
        if self._progress.paused_at:
            pause_duration = (datetime.now() - self._progress.paused_at).total_seconds()
            self._progress.total_pause_duration_s += pause_duration
            self._progress.paused_at = None
            _log.info(f"Resumed after {pause_duration:.1f}s pause")

    # === Main Experiment Loop ===

    def _run_experiment(self, resume: bool = False):
        """
        Main experiment execution loop (runs in worker thread).

        Uses CancelToken for pause/abort control. Checkpoints occur:
        - Between rounds
        - Between fluidics steps (in FluidicsExecutor)
        - Between FOVs (in ImagingExecutor)
        """
        try:
            # Initialize experiment
            experiment_path = self._initialize_experiment(resume)
            self._checkpoint_manager = CheckpointManager(experiment_path)

            # Load checkpoint if resuming
            if resume:
                checkpoint = self._checkpoint_manager.load()
                if checkpoint:
                    self._progress = checkpoint.progress
                    _log.info(f"Resuming from round {self._progress.current_round}, "
                              f"FOV {self._progress.last_completed_fov}")

            # Create executors
            self._fluidics_executor = FluidicsExecutor(
                fluidics_service=self._fluidics_service,
                event_bus=self._event_bus,
            )
            self._imaging_executor = ImagingExecutor(
                experiment_manager=self._experiment_manager,
                acquisition_service=self._acquisition_service,
                position_controller=self._position_controller,
                event_bus=self._event_bus,
            )

            # Main round loop
            while self._progress.current_round < self._progress.total_rounds:
                round_config = self._protocol.rounds[self._progress.current_round]
                round_progress = self._progress.rounds[self._progress.current_round]

                # === CHECKPOINT: Between rounds ===
                try:
                    self._cancel_token.checkpoint(CheckpointContext(
                        operation="round",
                        round_index=self._progress.current_round,
                        detail=f"Starting round: {round_config.name}"
                    ))
                except AbortRequested:
                    _log.info("Aborted between rounds")
                    break

                self._event_bus.publish(RoundStarted(
                    round_index=self._progress.current_round,
                    round_name=round_config.name,
                    has_fluidics=round_config.fluidics is not None,
                    has_imaging=not round_config.skip_imaging,
                ))

                # === Fluidics Phase ===
                if round_config.fluidics and not round_progress.fluidics_complete:
                    self._transition_to(OrchestratorState.RUNNING_FLUIDICS)

                    # FluidicsExecutor has its own checkpoints between steps
                    success = self._fluidics_executor.execute(
                        round_config.fluidics,
                        round_index=self._progress.current_round,
                        cancel_token=self._cancel_token,
                    )

                    if not success:
                        if self._cancel_token.is_abort_requested:
                            break
                        # Handle fluidics failure
                        self._handle_failure("Fluidics step failed", round_progress)
                        continue

                    round_progress.fluidics_complete = True
                    self._save_checkpoint()

                # === Imaging Phase ===
                if not round_config.skip_imaging and not round_progress.imaging_complete:
                    self._transition_to(OrchestratorState.RUNNING_IMAGING)

                    # ImagingExecutor has its own checkpoints between FOVs
                    success = self._imaging_executor.execute(
                        imaging_config=round_config.imaging,
                        round_index=self._progress.current_round,
                        round_name=round_config.name,
                        positions=self._scan_coordinates,
                        start_fov=round_progress.current_fov,
                        cancel_token=self._cancel_token,
                        progress_callback=self._on_imaging_progress,
                    )

                    if not success:
                        if self._cancel_token.is_abort_requested:
                            break
                        self._handle_failure("Imaging failed", round_progress)
                        continue

                    round_progress.imaging_complete = True
                    self._save_checkpoint()

                # Round complete
                self._transition_to(OrchestratorState.COMPLETING_ROUND)
                self._event_bus.publish(RoundCompleted(
                    round_index=self._progress.current_round,
                    round_name=round_config.name,
                    success=True,
                ))

                self._progress.last_completed_round = self._progress.current_round
                self._progress.current_round += 1
                self._save_checkpoint()

            # Experiment complete
            self._finalize_experiment(success=not self._cancel_token.is_abort_requested)

        except AbortRequested:
            _log.info("Experiment aborted")
            self._finalize_experiment(success=False)

        except Exception as e:
            _log.exception(f"Experiment failed: {e}")
            self._transition_to(OrchestratorState.FAILED)
            self._event_bus.publish(ExperimentCompleted(
                experiment_id=self._progress.experiment_id,
                success=False,
                total_duration_hours=self._calculate_elapsed_hours(),
                error=str(e),
            ))

    def _on_imaging_progress(self, fov: int, total_fovs: int):
        """Callback from imaging executor for progress updates."""
        round_progress = self._progress.rounds[self._progress.current_round]
        round_progress.current_fov = fov
        round_progress.total_fovs = total_fovs

        self._progress.completed_fovs_all_rounds += 1
        self._progress.last_completed_fov = fov

        # Publish progress update
        self._event_bus.publish(ExperimentProgressUpdate(progress=self._progress))

        # Save checkpoint after each FOV
        self._save_checkpoint()

    def _save_checkpoint(self):
        """Save current state to checkpoint file."""
        if self._checkpoint_manager:
            checkpoint = ExperimentCheckpoint(
                progress=self._progress,
                state=self.state,
                checkpoint_time=datetime.now(),
            )
            self._checkpoint_manager.save(checkpoint)

    def _initialize_experiment(self, resume: bool) -> Path:
        """Initialize experiment folder and metadata."""
        if resume and self._progress:
            # Use existing experiment path
            return Path(self._progress.experiment_path)

        # Create new experiment
        experiment_id = f"orchestrated_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        context = self._experiment_manager.start_experiment(
            base_path=str(self._protocol_path.parent),
            experiment_id=experiment_id,
            configurations=[],  # Will be set per-round
            acquisition_params=None,  # Not using legacy params
        )

        # Initialize progress
        self._progress = ExperimentProgress(
            experiment_id=experiment_id,
            protocol_name=self._protocol.name,
            protocol_path=str(self._protocol_path),
            total_rounds=len(self._protocol.rounds),
            rounds=[RoundProgress(i, r.name) for i, r in enumerate(self._protocol.rounds)],
            total_fovs_all_rounds=self._calculate_total_fovs(),
            started_at=datetime.now(),
        )

        return Path(context.experiment_path)

    def _finalize_experiment(self, success: bool):
        """Finalize experiment - cleanup and publish completion."""
        duration_hours = self._calculate_elapsed_hours()

        if success:
            self._checkpoint_manager.clear()  # Remove checkpoint on success

        self._transition_to(OrchestratorState.COMPLETED)

        self._event_bus.publish(ExperimentCompleted(
            experiment_id=self._progress.experiment_id,
            success=success,
            total_duration_hours=duration_hours,
        ))

    # === Helper Methods ===

    def _calculate_total_fovs(self) -> int:
        """Calculate total FOVs across all imaging rounds."""
        num_positions = len(self._scan_coordinates.get_all_fov_coordinates())
        imaging_rounds = sum(1 for r in self._protocol.rounds if not r.skip_imaging)
        return num_positions * imaging_rounds

    def _estimate_duration_hours(self) -> float:
        """Estimate total experiment duration."""
        # Rough estimation: 10s per FOV + fluidics time
        fov_time_s = self._calculate_total_fovs() * 10
        fluidics_time_s = sum(
            sum(step.time_min or 1 for step in r.fluidics.steps) * 60
            for r in self._protocol.rounds if r.fluidics
        )
        return (fov_time_s + fluidics_time_s) / 3600

    def _calculate_elapsed_hours(self) -> float:
        """Calculate elapsed time excluding pauses."""
        if not self._progress.started_at:
            return 0.0
        total_s = (datetime.now() - self._progress.started_at).total_seconds()
        active_s = total_s - self._progress.total_pause_duration_s
        return active_s / 3600

    def _validate_channels(self) -> List[str]:
        """Validate that all referenced channels exist."""
        errors = []
        available_channels = {c.name for c in self._channel_config_manager.get_configurations()}

        for i, round in enumerate(self._protocol.rounds):
            if round.imaging:
                for channel in round.imaging.channels:
                    if channel not in available_channels:
                        errors.append(f"Round {i+1}: Unknown channel '{channel}'")

        return errors
```

### 3.3 Fluidics Executor

**File:** `software/src/squid/backend/controllers/orchestrator/fluidics_executor.py`

Uses CancelToken with checkpoints between fluidics steps. Each step (wash cycle, incubation, etc.) runs atomically to completion.

```python
from typing import Optional

from squid.core.events import EventBus
from squid.core.utils.cancel_token import CancelToken, CheckpointContext, AbortRequested
from squid.core.protocol.schema import FluidicsSequence, FluidicsStep, FluidicsAction
from squid.backend.services.fluidics_service import FluidicsService

import squid.logging
_log = squid.logging.get_logger(__name__)


class FluidicsExecutor:
    """
    Executes fluidics sequences with pause/abort support.

    Checkpoints occur BETWEEN steps only - each step runs atomically.
    """

    def __init__(
        self,
        fluidics_service: Optional[FluidicsService],
        event_bus: EventBus,
    ):
        self._fluidics = fluidics_service
        self._event_bus = event_bus

    def execute(
        self,
        sequence: FluidicsSequence,
        round_index: int,
        cancel_token: CancelToken,
    ) -> bool:
        """
        Execute a fluidics sequence.

        Checkpoints between steps allow pause/abort at step boundaries.
        Each individual step runs to completion (atomic).

        Args:
            sequence: The fluidics steps to execute
            round_index: Current round number (for events)
            cancel_token: Token for pause/abort control

        Returns:
            True on success, False on failure/abort
        """
        if not self._fluidics:
            _log.warning("No fluidics service available - skipping fluidics")
            return True

        total_steps = len(sequence.steps)

        try:
            for step_idx, step in enumerate(sequence.steps):
                # === CHECKPOINT: Between fluidics steps ===
                # Can pause/abort here, before starting next step
                cancel_token.checkpoint(CheckpointContext(
                    operation="fluidics",
                    round_index=round_index,
                    detail=f"Step {step_idx + 1}/{total_steps}: {self._describe_step(step)}"
                ))

                # Publish step start
                self._event_bus.publish(FluidicsStepStarted(
                    round_index=round_index,
                    step_index=step_idx,
                    action=step.action.value,
                    description=self._describe_step(step),
                ))

                # === ATOMIC: Execute step to completion ===
                # No pause/abort during step execution
                try:
                    self._execute_step(step)

                    self._event_bus.publish(FluidicsStepCompleted(
                        round_index=round_index,
                        step_index=step_idx,
                        success=True,
                    ))

                except Exception as e:
                    _log.error(f"Fluidics step {step_idx} failed: {e}")
                    self._event_bus.publish(FluidicsStepCompleted(
                        round_index=round_index,
                        step_index=step_idx,
                        success=False,
                    ))
                    return False

            return True

        except AbortRequested:
            _log.info("Fluidics aborted at checkpoint")
            return False

    def _execute_step(self, step: FluidicsStep):
        """
        Execute a single fluidics step.

        This is ATOMIC - runs to completion without pause/abort checks.
        """
        if step.action == FluidicsAction.ADD_PROBE:
            self._fluidics.set_volume(step.volume_ul)
            self._fluidics.run_before_imaging()
            self._fluidics.wait_for_completion()

        elif step.action == FluidicsAction.WASH:
            # All wash cycles are atomic - no pause between cycles
            for cycle in range(step.cycles or 1):
                _log.debug(f"Wash cycle {cycle + 1}/{step.cycles}")
                self._fluidics.run_wash_cycle()
                self._fluidics.wait_for_completion()

        elif step.action == FluidicsAction.INCUBATE:
            import time
            time.sleep((step.time_min or 0) * 60)

        elif step.action == FluidicsAction.CLEAVE:
            self._fluidics.run_cleavage()
            self._fluidics.wait_for_completion()
            if step.time_min:
                import time
                time.sleep(step.time_min * 60)

        elif step.action == FluidicsAction.CUSTOM:
            if step.custom_command:
                self._fluidics.send_command(step.custom_command)
                self._fluidics.wait_for_completion()

    def _describe_step(self, step: FluidicsStep) -> str:
        """Generate human-readable step description."""
        if step.action == FluidicsAction.ADD_PROBE:
            return f"Add probe {step.probe} ({step.volume_ul}uL)"
        elif step.action == FluidicsAction.WASH:
            return f"Wash ({step.cycles} cycles)"
        elif step.action == FluidicsAction.INCUBATE:
            return f"Incubate ({step.time_min} min)"
        elif step.action == FluidicsAction.CLEAVE:
            return f"Cleave ({step.time_min} min)"
        elif step.action == FluidicsAction.CUSTOM:
            return f"Custom: {step.custom_command}"
        return str(step.action.value)
```

### 3.4 Imaging Executor

**File:** `software/src/squid/backend/controllers/orchestrator/imaging_executor.py`

Uses CancelToken with checkpoints between FOVs. Each FOV acquisition (z-stack + all channels) is atomic.

```python
from typing import Optional, Callable, List

from squid.core.events import EventBus
from squid.core.utils.cancel_token import CancelToken, CheckpointContext, AbortRequested
from squid.core.protocol.schema import ImagingConfig
from squid.backend.controllers.multipoint.experiment_manager import ExperimentManager
from squid.backend.controllers.multipoint.position_zstack import PositionController, ZStackExecutor
from squid.backend.controllers.multipoint.image_capture import ImageCaptureExecutor
from squid.backend.controllers.multipoint.progress_tracking import ProgressTracker, CoordinateTracker
from squid.backend.services.acquisition_service import AcquisitionService
from squid.backend.managers.scan_coordinates import ScanCoordinates

import squid.logging
_log = squid.logging.get_logger(__name__)


class ImagingExecutor:
    """
    Executes imaging for a single round.

    Checkpoints occur BETWEEN FOVs only - each FOV acquisition is atomic.
    A single FOV includes: move, autofocus (optional), z-stack, all channels.
    """

    def __init__(
        self,
        experiment_manager: ExperimentManager,
        acquisition_service: AcquisitionService,
        position_controller: PositionController,
        image_capture_executor: ImageCaptureExecutor,
        z_stack_executor: ZStackExecutor,
        event_bus: EventBus,
        channel_config_manager,
    ):
        self._experiment_manager = experiment_manager
        self._acquisition_service = acquisition_service
        self._position_controller = position_controller
        self._image_capture = image_capture_executor
        self._z_stack = z_stack_executor
        self._event_bus = event_bus
        self._channel_config_manager = channel_config_manager

    def execute(
        self,
        imaging_config: ImagingConfig,
        round_index: int,
        round_name: str,
        positions: ScanCoordinates,
        start_fov: int,
        cancel_token: CancelToken,
        progress_callback: Callable[[int, int], None],
    ) -> bool:
        """
        Execute imaging for all positions with the given configuration.

        Checkpoints between FOVs allow pause/abort at FOV boundaries.
        Each FOV acquisition runs to completion (atomic).

        Args:
            imaging_config: Channels, z-stack, autofocus settings
            round_index: Current round number
            round_name: Name of current round
            positions: FOV coordinates to image
            start_fov: FOV index to start from (for resume)
            cancel_token: Token for pause/abort control
            progress_callback: Called after each FOV completes

        Returns:
            True on success, False on failure/abort
        """
        # Resolve channel configurations
        channels = self._resolve_channels(imaging_config.channels)
        if not channels:
            _log.error("No valid channels found")
            return False

        # Get all FOV coordinates
        all_fovs = positions.get_all_fov_coordinates()
        total_fovs = len(all_fovs)

        # Create trackers for this round
        progress_tracker = ProgressTracker(self._event_bus, f"round_{round_index}")
        coordinate_tracker = CoordinateTracker()
        coordinate_tracker.initialize(use_piezo=False)

        progress_tracker.start_acquisition()

        try:
            for fov_idx, (region_id, x, y, z) in enumerate(all_fovs):
                # Skip already-completed FOVs (for resume)
                if fov_idx < start_fov:
                    continue

                # === CHECKPOINT: Between FOVs ===
                # Can pause/abort here, before starting next FOV
                cancel_token.checkpoint(CheckpointContext(
                    operation="imaging",
                    round_index=round_index,
                    detail=f"FOV {fov_idx + 1}/{total_fovs} ({region_id})"
                ))

                # === ATOMIC: Entire FOV acquisition ===
                # No pause/abort during FOV - includes move, AF, z-stack, all channels
                self._acquire_fov(
                    fov_idx=fov_idx,
                    region_id=region_id,
                    x=x, y=y, z=z,
                    channels=channels,
                    imaging_config=imaging_config,
                    round_name=round_name,
                )

                # FOV complete - update progress
                progress_callback(fov_idx, total_fovs)

            progress_tracker.finish_acquisition(success=True)
            return True

        except AbortRequested:
            _log.info(f"Imaging aborted at FOV checkpoint")
            progress_tracker.finish_acquisition(success=False)
            return False

        except Exception as e:
            _log.exception(f"Imaging failed: {e}")
            progress_tracker.finish_acquisition(success=False, error=e)
            return False

    def _acquire_fov(
        self,
        fov_idx: int,
        region_id: str,
        x: float, y: float, z: float,
        channels: List,
        imaging_config: ImagingConfig,
        round_name: str,
    ) -> None:
        """
        Acquire a single FOV.

        This is ATOMIC - runs to completion without pause/abort checks.
        Includes: stage move, autofocus, z-stack (if configured), all channels.
        """
        # Move to position
        self._position_controller.move_to_coordinate(x, y, z)

        # Autofocus if needed (part of atomic FOV operation)
        if self._should_autofocus(imaging_config.autofocus, fov_idx):
            self._perform_autofocus(imaging_config.autofocus)

        # Acquire images - z-stack or single plane
        if imaging_config.z_stack:
            self._acquire_z_stack(
                channels=channels,
                z_config=imaging_config.z_stack,
                region_id=region_id,
                fov_idx=fov_idx,
                round_name=round_name,
            )
        else:
            self._acquire_single_plane(
                channels=channels,
                region_id=region_id,
                fov_idx=fov_idx,
                round_name=round_name,
            )

    def _resolve_channels(self, channel_names: List[str]) -> List:
        """Resolve channel names to ChannelMode configurations."""
        channels = []
        all_configs = {c.name: c for c in self._channel_config_manager.get_configurations()}

        for name in channel_names:
            if name in all_configs:
                channels.append(all_configs[name])
            else:
                _log.warning(f"Unknown channel: {name}")

        return channels

    def _should_autofocus(self, af_config, fov_idx: int) -> bool:
        """Determine if autofocus should run at this FOV."""
        if not af_config or not af_config.enabled:
            return False
        return (fov_idx % af_config.interval_fovs) == 0

    def _perform_autofocus(self, af_config):
        """Perform autofocus using configured method."""
        # Delegate to autofocus controller
        # This would use the refactored AutofocusExecutor
        pass

    def _acquire_z_stack(self, channels, z_config, region_id, fov_idx, round_name):
        """
        Acquire a z-stack at current position.

        Z-stack is part of atomic FOV - no pause/abort mid-stack.
        """
        z_levels = self._calculate_z_levels(z_config)

        self._z_stack.prepare_for_stack(z_config)

        for z_idx, z_offset in enumerate(z_levels):
            self._z_stack.advance_z_level(z_offset)

            for channel in channels:
                self._acquisition_service.apply_configuration(channel)
                self._image_capture.capture_single_image(
                    # ... capture context
                )

        self._z_stack.return_after_stack(z_config)

    def _acquire_single_plane(self, channels, region_id, fov_idx, round_name):
        """Acquire single plane at current position."""
        for channel in channels:
            self._acquisition_service.apply_configuration(channel)
            self._image_capture.capture_single_image(
                # ... capture context
            )

    def _calculate_z_levels(self, z_config) -> List[float]:
        """Calculate z-level offsets for stack."""
        half_range = z_config.range_um / 2
        n_steps = int(z_config.range_um / z_config.step_um) + 1
        return [
            -half_range + i * z_config.step_um
            for i in range(n_steps)
        ]
```

---

## Phase 4: Performance Mode UI

**Integration:** Performance Mode is a **tab in `imageDisplayTabs`** (alongside "Live View", "Mosaic View", etc.) - not a separate window. Users can switch between tabs as needed during experiments.

### 4.1 Performance Mode Widget

**File:** `software/src/squid/ui/widgets/orchestrator/performance_mode.py`

```python
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QFrame, QScrollArea,
    QGroupBox, QGridLayout, QSplitter
)
from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtGui import QFont

from squid.core.events import EventBus
from squid.backend.controllers.orchestrator.state import (
    OrchestratorState, ExperimentProgress
)


class PerformanceModeWidget(QWidget):
    """
    Tab widget for monitoring orchestrated experiments.

    Added to imageDisplayTabs alongside Live View, Mosaic View, etc.

    Shows:
    - Overall progress dashboard
    - Protocol timeline with current position
    - Intervention controls (Start/Pause/Resume/Stop, Skip)
    """

    def __init__(self, event_bus: EventBus, parent=None):
        super().__init__(parent)
        self._event_bus = event_bus
        self._current_state = OrchestratorState.IDLE
        self._progress: Optional[ExperimentProgress] = None

        self._setup_ui()
        self._subscribe_to_events()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # Header with experiment name and status
        self._header = self._create_header()
        layout.addWidget(self._header)

        # Main content: splitter with progress and timeline
        splitter = QSplitter(Qt.Horizontal)

        # Left: Progress dashboard
        progress_panel = self._create_progress_panel()
        splitter.addWidget(progress_panel)

        # Right: Timeline view
        timeline_panel = self._create_timeline_panel()
        splitter.addWidget(timeline_panel)

        splitter.setSizes([400, 600])
        layout.addWidget(splitter, stretch=1)

        # Bottom: Control buttons
        controls = self._create_controls()
        layout.addWidget(controls)

    def _create_header(self) -> QWidget:
        header = QFrame()
        header.setFrameStyle(QFrame.StyledPanel)
        layout = QHBoxLayout(header)

        # Experiment name
        self._experiment_label = QLabel("No experiment loaded")
        self._experiment_label.setFont(QFont("", 14, QFont.Bold))
        layout.addWidget(self._experiment_label)

        layout.addStretch()

        # Status indicator
        self._status_label = QLabel("IDLE")
        self._status_label.setStyleSheet("""
            QLabel {
                background-color: #666;
                color: white;
                padding: 4px 12px;
                border-radius: 4px;
                font-weight: bold;
            }
        """)
        layout.addWidget(self._status_label)

        return header

    def _create_progress_panel(self) -> QWidget:
        panel = QGroupBox("Progress")
        layout = QGridLayout(panel)

        # Overall progress bar
        layout.addWidget(QLabel("Overall:"), 0, 0)
        self._overall_progress = QProgressBar()
        self._overall_progress.setRange(0, 100)
        layout.addWidget(self._overall_progress, 0, 1)
        self._overall_percent_label = QLabel("0%")
        layout.addWidget(self._overall_percent_label, 0, 2)

        # Current round progress
        layout.addWidget(QLabel("Current Round:"), 1, 0)
        self._round_progress = QProgressBar()
        self._round_progress.setRange(0, 100)
        layout.addWidget(self._round_progress, 1, 1)
        self._round_label = QLabel("- / -")
        layout.addWidget(self._round_label, 1, 2)

        # Current FOV
        layout.addWidget(QLabel("FOV:"), 2, 0)
        self._fov_progress = QProgressBar()
        self._fov_progress.setRange(0, 100)
        layout.addWidget(self._fov_progress, 2, 1)
        self._fov_label = QLabel("- / -")
        layout.addWidget(self._fov_label, 2, 2)

        # Time info
        layout.addWidget(QLabel("Elapsed:"), 3, 0)
        self._elapsed_label = QLabel("--:--:--")
        layout.addWidget(self._elapsed_label, 3, 1, 1, 2)

        layout.addWidget(QLabel("ETA:"), 4, 0)
        self._eta_label = QLabel("--:--:--")
        layout.addWidget(self._eta_label, 4, 1, 1, 2)

        # Current activity
        layout.addWidget(QLabel("Activity:"), 5, 0)
        self._activity_label = QLabel("Idle")
        self._activity_label.setWordWrap(True)
        layout.addWidget(self._activity_label, 5, 1, 1, 2)

        layout.setRowStretch(6, 1)  # Spacer

        return panel

    def _create_timeline_panel(self) -> QWidget:
        panel = QGroupBox("Protocol Timeline")
        layout = QVBoxLayout(panel)

        # Scrollable timeline
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._timeline_container = QWidget()
        self._timeline_layout = QVBoxLayout(self._timeline_container)
        self._timeline_layout.setSpacing(4)
        self._timeline_layout.addStretch()

        scroll.setWidget(self._timeline_container)
        layout.addWidget(scroll)

        return panel

    def _create_controls(self) -> QWidget:
        controls = QFrame()
        controls.setFrameStyle(QFrame.StyledPanel)
        layout = QHBoxLayout(controls)

        # Pause/Resume
        self._pause_btn = QPushButton("Pause")
        self._pause_btn.clicked.connect(self._on_pause_clicked)
        layout.addWidget(self._pause_btn)

        # Skip FOV
        self._skip_fov_btn = QPushButton("Skip FOV")
        self._skip_fov_btn.clicked.connect(self._on_skip_fov_clicked)
        layout.addWidget(self._skip_fov_btn)

        # Skip to Round (dropdown would be better)
        self._skip_round_btn = QPushButton("Skip to Round...")
        self._skip_round_btn.clicked.connect(self._on_skip_round_clicked)
        layout.addWidget(self._skip_round_btn)

        layout.addStretch()

        # Abort
        self._abort_btn = QPushButton("Abort")
        self._abort_btn.setStyleSheet("QPushButton { background-color: #d32f2f; color: white; }")
        self._abort_btn.clicked.connect(self._on_abort_clicked)
        layout.addWidget(self._abort_btn)

        return controls

    def _subscribe_to_events(self):
        self._event_bus.subscribe(ExperimentStateChanged, self._on_state_changed)
        self._event_bus.subscribe(ExperimentProgressUpdate, self._on_progress_update)
        self._event_bus.subscribe(ProtocolLoaded, self._on_protocol_loaded)
        self._event_bus.subscribe(RoundStarted, self._on_round_started)
        self._event_bus.subscribe(RoundCompleted, self._on_round_completed)
        self._event_bus.subscribe(FluidicsStepStarted, self._on_fluidics_step)

    # === Event Handlers ===

    @pyqtSlot(object)
    def _on_state_changed(self, event):
        self._current_state = event.state
        self._update_status_display()
        self._update_button_states()

    @pyqtSlot(object)
    def _on_progress_update(self, event):
        self._progress = event.progress
        self._update_progress_display()

    @pyqtSlot(object)
    def _on_protocol_loaded(self, event):
        self._experiment_label.setText(event.protocol_name)
        self._populate_timeline(event.total_rounds)

    @pyqtSlot(object)
    def _on_round_started(self, event):
        self._update_timeline_highlight(event.round_index)
        self._activity_label.setText(f"Round {event.round_index + 1}: {event.round_name}")

    @pyqtSlot(object)
    def _on_round_completed(self, event):
        self._mark_timeline_round_complete(event.round_index, event.success)

    @pyqtSlot(object)
    def _on_fluidics_step(self, event):
        self._activity_label.setText(f"Fluidics: {event.description}")

    # === UI Updates ===

    def _update_status_display(self):
        status_colors = {
            OrchestratorState.IDLE: ("#666", "IDLE"),
            OrchestratorState.READY: ("#2196F3", "READY"),
            OrchestratorState.RUNNING_FLUIDICS: ("#FF9800", "FLUIDICS"),
            OrchestratorState.RUNNING_IMAGING: ("#4CAF50", "IMAGING"),
            OrchestratorState.PAUSED: ("#9C27B0", "PAUSED"),
            OrchestratorState.ABORTING: ("#f44336", "ABORTING"),
            OrchestratorState.COMPLETED: ("#4CAF50", "COMPLETED"),
            OrchestratorState.FAILED: ("#f44336", "FAILED"),
        }

        color, text = status_colors.get(self._current_state, ("#666", "UNKNOWN"))
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"""
            QLabel {{
                background-color: {color};
                color: white;
                padding: 4px 12px;
                border-radius: 4px;
                font-weight: bold;
            }}
        """)

    def _update_progress_display(self):
        if not self._progress:
            return

        # Overall progress
        overall_pct = self._progress.progress_percent
        self._overall_progress.setValue(int(overall_pct))
        self._overall_percent_label.setText(f"{overall_pct:.1f}%")

        # Round progress
        round_pct = 0
        if self._progress.total_rounds > 0:
            round_pct = (self._progress.current_round / self._progress.total_rounds) * 100
        self._round_progress.setValue(int(round_pct))
        self._round_label.setText(
            f"{self._progress.current_round + 1} / {self._progress.total_rounds}"
        )

        # FOV progress (current round)
        if self._progress.rounds and self._progress.current_round < len(self._progress.rounds):
            rp = self._progress.rounds[self._progress.current_round]
            if rp.total_fovs > 0:
                fov_pct = (rp.current_fov / rp.total_fovs) * 100
                self._fov_progress.setValue(int(fov_pct))
                self._fov_label.setText(f"{rp.current_fov} / {rp.total_fovs}")

        # Time
        if self._progress.started_at:
            elapsed = (datetime.now() - self._progress.started_at).total_seconds()
            elapsed -= self._progress.total_pause_duration_s
            self._elapsed_label.setText(self._format_duration(elapsed))

            if overall_pct > 0:
                eta_s = (elapsed / overall_pct) * (100 - overall_pct)
                self._eta_label.setText(self._format_duration(eta_s))

    def _update_button_states(self):
        is_running = self._current_state in {
            OrchestratorState.RUNNING_FLUIDICS,
            OrchestratorState.RUNNING_IMAGING,
        }
        is_paused = self._current_state == OrchestratorState.PAUSED

        self._pause_btn.setEnabled(is_running or is_paused)
        self._pause_btn.setText("Resume" if is_paused else "Pause")

        self._skip_fov_btn.setEnabled(is_running or is_paused)
        self._skip_round_btn.setEnabled(is_paused)
        self._abort_btn.setEnabled(is_running or is_paused)

    def _populate_timeline(self, total_rounds: int):
        # Clear existing timeline items
        while self._timeline_layout.count() > 1:  # Keep stretch
            item = self._timeline_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Add round items
        # This would create visual timeline items for each round
        ...

    # === Button Handlers ===

    def _on_pause_clicked(self):
        if self._current_state == OrchestratorState.PAUSED:
            self._event_bus.publish(ResumeExperimentCommand())
        else:
            self._event_bus.publish(PauseExperimentCommand())

    def _on_skip_fov_clicked(self):
        self._event_bus.publish(SkipCurrentFOVCommand())

    def _on_skip_round_clicked(self):
        # Show dialog to select round
        # Then publish SkipToRoundCommand
        ...

    def _on_abort_clicked(self):
        # Confirm dialog
        # Then publish AbortExperimentCommand
        ...

    @staticmethod
    def _format_duration(seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
```

### 4.2 Protocol Loader Dialog

**File:** `software/src/squid/ui/widgets/orchestrator/protocol_loader.py`

```python
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QTextEdit, QGroupBox,
    QTreeWidget, QTreeWidgetItem, QMessageBox
)
from PyQt5.QtCore import Qt
from pathlib import Path

from squid.core.protocol.loader import ProtocolLoader, ProtocolValidationError
from squid.core.protocol.schema import Protocol


class ProtocolLoaderDialog(QDialog):
    """Dialog for loading and previewing experiment protocols."""

    def __init__(self, event_bus, parent=None):
        super().__init__(parent)
        self._event_bus = event_bus
        self._protocol: Optional[Protocol] = None
        self._protocol_path: Optional[Path] = None

        self.setWindowTitle("Load Experiment Protocol")
        self.setMinimumSize(600, 500)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # File selection
        file_layout = QHBoxLayout()
        self._path_label = QLabel("No file selected")
        file_layout.addWidget(self._path_label, stretch=1)

        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_file)
        file_layout.addWidget(browse_btn)

        layout.addLayout(file_layout)

        # Protocol preview
        preview_group = QGroupBox("Protocol Preview")
        preview_layout = QVBoxLayout(preview_group)

        self._preview_tree = QTreeWidget()
        self._preview_tree.setHeaderLabels(["Property", "Value"])
        self._preview_tree.setColumnWidth(0, 200)
        preview_layout.addWidget(self._preview_tree)

        layout.addWidget(preview_group, stretch=1)

        # Validation messages
        self._validation_text = QTextEdit()
        self._validation_text.setReadOnly(True)
        self._validation_text.setMaximumHeight(100)
        layout.addWidget(self._validation_text)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._load_btn = QPushButton("Load Protocol")
        self._load_btn.setEnabled(False)
        self._load_btn.clicked.connect(self._load_protocol)
        btn_layout.addWidget(self._load_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Protocol File",
            "",
            "Protocol Files (*.yaml *.yml);;All Files (*)"
        )

        if path:
            self._protocol_path = Path(path)
            self._path_label.setText(str(self._protocol_path))
            self._try_load_preview()

    def _try_load_preview(self):
        """Try to load and preview the protocol."""
        self._preview_tree.clear()
        self._validation_text.clear()
        self._load_btn.setEnabled(False)

        try:
            loader = ProtocolLoader()
            self._protocol = loader.load(self._protocol_path)

            # Populate preview tree
            self._populate_preview()

            self._validation_text.setStyleSheet("color: green;")
            self._validation_text.setText("Protocol is valid!")
            self._load_btn.setEnabled(True)

        except ProtocolValidationError as e:
            self._validation_text.setStyleSheet("color: red;")
            self._validation_text.setText("Validation errors:\n" + "\n".join(e.errors))

        except Exception as e:
            self._validation_text.setStyleSheet("color: red;")
            self._validation_text.setText(f"Error loading protocol: {e}")

    def _populate_preview(self):
        """Populate the preview tree with protocol info."""
        if not self._protocol:
            return

        # Protocol info
        info_item = QTreeWidgetItem(["Protocol", self._protocol.name])
        info_item.addChild(QTreeWidgetItem(["Version", self._protocol.version]))
        if self._protocol.author:
            info_item.addChild(QTreeWidgetItem(["Author", self._protocol.author]))
        self._preview_tree.addTopLevelItem(info_item)

        # Microscope
        micro_item = QTreeWidgetItem(["Microscope", ""])
        micro_item.addChild(QTreeWidgetItem(["Objective", self._protocol.microscope.objective]))
        micro_item.addChild(QTreeWidgetItem(["Binning", str(self._protocol.microscope.binning)]))
        self._preview_tree.addTopLevelItem(micro_item)

        # Rounds
        rounds_item = QTreeWidgetItem(["Rounds", str(len(self._protocol.rounds))])
        for i, round in enumerate(self._protocol.rounds):
            round_item = QTreeWidgetItem([f"Round {i+1}", round.name])

            if round.fluidics:
                fluidics_item = QTreeWidgetItem(["Fluidics", f"{len(round.fluidics.steps)} steps"])
                round_item.addChild(fluidics_item)

            if round.imaging and not round.skip_imaging:
                imaging_item = QTreeWidgetItem(["Imaging", ", ".join(round.imaging.channels)])
                round_item.addChild(imaging_item)
            elif round.skip_imaging:
                round_item.addChild(QTreeWidgetItem(["Imaging", "Skipped"]))

            rounds_item.addChild(round_item)

        self._preview_tree.addTopLevelItem(rounds_item)

        # Expand all
        self._preview_tree.expandAll()

    def _load_protocol(self):
        """Emit command to load the protocol."""
        if self._protocol_path:
            self._event_bus.publish(LoadProtocolCommand(
                protocol_path=str(self._protocol_path)
            ))
            self.accept()
```

---

## Phase 5: Integration & Testing

### 5.1 Application Integration

**File:** `software/src/squid/application.py` (additions)

```python
# In Application class __init__ or _create_controllers():

def _create_orchestrator(self):
    """Create the experiment orchestrator controller."""
    from squid.backend.controllers.orchestrator.orchestrator_controller import OrchestratorController

    self._orchestrator = OrchestratorController(
        event_bus=self._event_bus,
        experiment_manager=self._experiment_manager,
        acquisition_planner=self._acquisition_planner,
        acquisition_service=self._acquisition_service,
        position_controller=self._position_controller,
        fluidics_service=self._fluidics_service,
        scan_coordinates=self._scan_coordinates,
        channel_config_manager=self._channel_config_manager,
        objective_store=self._objective_store,
    )

    return self._orchestrator
```

### 5.2 Main Window Integration

**File:** `software/src/squid/ui/main_window.py` (additions)

```python
# Add Performance Mode as a tab in imageDisplayTabs (in setupImageDisplayTabs)
def setupImageDisplayTabs(self):
    # ... existing tabs ...

    # Add Performance Mode tab
    from squid.ui.widgets.orchestrator.performance_mode import PerformanceModeWidget
    self.performanceModeWidget = PerformanceModeWidget(self._event_bus)
    self.imageDisplayTabs.addTab(self.performanceModeWidget, "Performance Mode")

# Add Experiment menu
def _create_orchestrator_menu(self):
    orchestrator_menu = self.menuBar().addMenu("Experiment")

    load_action = QAction("Load Protocol...", self)
    load_action.triggered.connect(self._show_protocol_loader)
    orchestrator_menu.addAction(load_action)

    orchestrator_menu.addSeparator()

    # Switch to Performance Mode tab
    goto_action = QAction("Go to Performance Mode", self)
    goto_action.triggered.connect(self._goto_performance_mode)
    orchestrator_menu.addAction(goto_action)

def _show_protocol_loader(self):
    from squid.ui.widgets.orchestrator.protocol_loader import ProtocolLoaderDialog
    dialog = ProtocolLoaderDialog(self._event_bus, self)
    dialog.exec_()

def _goto_performance_mode(self):
    # Switch to the Performance Mode tab
    self.imageDisplayTabs.setCurrentWidget(self.performanceModeWidget)
```

### 5.3 Test Structure

```
software/tests/
├── unit/
│   └── squid/
│       ├── core/
│       │   └── protocol/
│       │       ├── test_schema.py
│       │       └── test_loader.py
│       └── backend/
│           └── controllers/
│               └── orchestrator/
│                   ├── test_orchestrator_state.py
│                   ├── test_checkpoint_manager.py
│                   ├── test_fluidics_executor.py
│                   └── test_imaging_executor.py
└── integration/
    └── squid/
        └── controllers/
            └── test_orchestrator_integration.py
```

---

## Implementation Phases Summary

| Phase | Components | Files | Effort |
|-------|------------|-------|--------|
| 1 | Protocol Schema + Loader | 3 new | 2 days |
| 2 | State + Checkpoint | 2 new | 1 day |
| 3 | Orchestrator Controller | 3 new | 3 days |
| 4 | Performance Mode UI | 2 new | 2 days |
| 5 | Integration + Tests | Modifications + tests | 2 days |

**Total: ~10 days**

---

## Risk Assessment

| Component | Risk | Mitigation |
|-----------|------|------------|
| Protocol Schema | Low | YAML is well-understood, validation upfront |
| Checkpoint Persistence | Low | Atomic writes, simple JSON format |
| Fluidics Integration | Medium | Depends on existing FluidicsService API |
| Pause/Resume | Medium | Careful flag checking at every operation |
| State Machine | Low | Existing StateMachine base class |
| Performance UI | Low | Pure display, no business logic |

---

## Future Enhancements

1. **Visual Protocol Designer** - Drag-and-drop round builder
2. **Conditional Logic** - "If cell count > N, run extra wash"
3. **Time-Based Triggers** - Start imaging at specific times
4. **Remote Monitoring** - Web dashboard for experiment status
5. **Protocol Templates** - Pre-built protocols for common experiments
6. **Analysis Hooks** - Trigger analysis pipelines after each round
