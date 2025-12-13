# Architecture Proposal: Modular Squid (v2)

## Goal
Create an elegant, simple architecture that is modular, composable, and extensible.

---

## Part 1: Rethinking the Primitives

### The Problem with My Previous Decomposition

My initial proposal extracted "operations" that mirror what the current acquisition loop does:
- `TimepointLoop`, `ZStackCapture`, `GridIterator`, etc.

But this has problems:
1. **Too high-level** - These are workflows, not primitives
2. **Missing cross-cutting concerns** - Storage, data collection, continuous AF
3. **Inflexible** - Hard to compose in new ways or add new behaviors
4. **Doesn't handle feedback** - No closed-loop control

### What Are the TRUE Atomic Primitives?

Thinking about what microscopy workflows fundamentally need:

**1. Actions** - Irreducible hardware interactions
```
stage.move_to(x, y)      → MoveResult(actual_pos, duration)
stage.move_z(z)          → MoveResult(actual_z, duration)
piezo.move_to(z_um)      → MoveResult(actual_z_um, duration)
camera.trigger()         → TriggerResult(frame_id)
camera.read_frame(id)    → FrameResult(frame, timestamp)
illumination.set(ch, i)  → SetResult(actual_intensity)
filter.set(position)     → SetResult(actual_position)
fluidics.run(protocol)   → ProtocolResult(id)
```

**2. Gates** - Conditions that must be met before proceeding
```
wait_until(timestamp)         → elapsed_time
wait_for_focus_lock(timeout)  → (locked: bool, quality: float)
wait_for_fluidics(timeout)    → success: bool
wait_for_stabilization(ms)    → actual_wait
wait_for_frame(frame_id)      → frame
```

**3. Iterators** - Sequence generators (no side effects)
```
position_sequence(grid_spec)  → Iterator[Position]
z_sequence(stack_spec)        → Iterator[float]
timepoint_sequence(spec)      → Iterator[int] with timing
channel_sequence(channels)    → Iterator[ChannelConfig]
```

**4. Recorders** - Data collection that attaches to actions
```
record_position(action_result)   → FocusMapEntry
record_timing(action_result)     → TimingEntry
record_focus_quality(af_result)  → QualityEntry
```

### Separation of Concerns

| Concern | Handled By |
|---------|-----------|
| What to do | Sequence of Actions |
| When to do it | Gates (synchronization) |
| In what order | Iterators (sequence generation) |
| What happened | Recorders (data collection) |
| Where to put it | Storage Service |
| Feedback control | Background Services (AF, tracking) |

---

## Part 2: The Layer Architecture (Revised)

```
┌─────────────────────────────────────────────────────────────────┐
│                        UI LAYER                                  │
│  Widgets subscribe to State events, publish Commands            │
└─────────────────────────────────────────────────────────────────┘
                              ↕ UIEventBus
┌─────────────────────────────────────────────────────────────────┐
│                     CONTROLLER LAYER                             │
│  State machines that compile specs → action sequences           │
│  Handle abort, progress, error recovery                         │
└─────────────────────────────────────────────────────────────────┘
          ↓ compiles to                    ↕ monitors
┌─────────────────────────────────────────────────────────────────┐
│                  EXECUTION ENGINE (NEW)                          │
│  Runs action sequences with gates, collects data via recorders │
│  Handles parallelism (save while moving)                        │
│  Provides abort/pause/resume                                    │
└─────────────────────────────────────────────────────────────────┘
     ↓ executes           ↓ records to          ↓ waits on
┌─────────────┐    ┌─────────────────┐    ┌──────────────────────┐
│  SERVICES   │    │ STORAGE SERVICE │    │ BACKGROUND SERVICES  │
│  (hardware) │    │ (persistence)   │    │ (continuous AF, etc) │
└─────────────┘    └─────────────────┘    └──────────────────────┘
```

---

## Part 3: Key New Concepts

### 1. Storage Service (Not an Operation)

Storage is a **service**, not an operation. It:
- Receives frames asynchronously (queue-based)
- Writes in parallel (multiprocessing, as today)
- **NEW**: Provides query interface for collected data:
  - Focus map entries by position
  - Timing statistics
  - Frame metadata

```python
class StorageService:
    # Write path (async)
    def enqueue_frame(self, frame: Frame, metadata: CaptureInfo) -> JobId
    def wait_all_complete(self, timeout: float) -> bool

    # Read path (for feedback/analysis)
    def get_focus_map(self, region_id: str) -> FocusMap
    def get_timing_stats(self) -> TimingStats
    def get_acquisition_summary(self) -> Summary
```

### 2. Background Services (Continuous Processes)

Some things run continuously, not as discrete operations:

**ContinuousAFService** (NEW - doesn't exist today):
```python
class ContinuousAFService:
    def start(self) -> None           # Begin closed-loop tracking
    def stop(self) -> None            # Stop tracking
    def is_locked(self) -> bool       # Currently tracking?
    def get_correction(self) -> float # Current Z correction (µm)
    def wait_for_lock(self, timeout) -> bool
    def get_quality(self) -> float    # 0-1 tracking quality
```

This enables gates like:
```python
# Wait for AF to achieve lock before capturing
yield WaitForFocusLock(timeout=5.0, quality_threshold=0.8)
```

**FocusMapService** (builds map from collected data):
```python
class FocusMapService:
    def record_focus_point(self, pos: Position, z: float, quality: float)
    def predict_focus(self, pos: Position) -> float  # Interpolated Z
    def get_confidence(self, pos: Position) -> float
```

### 3. The Execution Engine

Instead of operations calling each other, we have an **engine** that:
1. Takes a sequence of actions/gates
2. Executes them in order (or parallel where safe)
3. Attaches recorders to collect data
4. Handles abort/pause/resume
5. Reports progress

```python
class ExecutionEngine:
    def run(self,
            actions: Iterable[Action | Gate],
            recorders: List[Recorder],
            context: ExecutionContext) -> ExecutionResult:

        for item in actions:
            if context.abort_requested:
                break

            if isinstance(item, Gate):
                if not item.wait(context, timeout=item.timeout):
                    raise GateTimeout(item)
            else:
                result = item.execute(context.services)
                for recorder in recorders:
                    recorder.record(item, result)
                context.report_progress(item)

        return ExecutionResult(...)
```

### 4. Workflow Compilation

Controllers "compile" high-level specs into action sequences:

```python
def compile_acquisition(spec: AcquisitionSpec) -> Iterable[Action | Gate]:
    """Convert spec into executable action sequence."""

    for t in range(spec.num_timepoints):
        # Fluidics before imaging
        if spec.fluidics:
            yield FluidicsAction(spec.fluidics.before_protocol)
            yield WaitForFluidics()

        for pos in spec.positions:
            yield MoveToAction(pos)

            # Focus strategy
            if spec.focus.strategy == "continuous":
                yield WaitForFocusLock(timeout=5.0)
            elif spec.focus.strategy == "per_position":
                yield AutofocusAction(spec.focus.config)
            elif spec.focus.strategy == "focus_map":
                yield ApplyFocusMapAction(pos)

            for z_offset in spec.z_stack.offsets:
                yield MoveZAction(z_offset, relative=True)

                for channel in spec.channels:
                    yield ConfigureAction(channel)
                    yield CaptureAction(channel, pos, z_offset, t)

            yield RestoreZAction()  # Return to nominal Z

        # Fluidics after imaging
        if spec.fluidics:
            yield FluidicsAction(spec.fluidics.after_protocol)
            yield WaitForFluidics()

        # Wait for next timepoint
        if t < spec.num_timepoints - 1:
            yield WaitUntilTime(spec.get_next_time(t))
```

---

## Part 4: The Minimal Set of Actions

After deep analysis, here are the TRUE atomic actions:

### Movement Actions
| Action | Input | Output | Service |
|--------|-------|--------|---------|
| `MoveXY` | target_pos | actual_pos, duration | StageService |
| `MoveZ` | target_z, relative? | actual_z, duration | StageService |
| `MovePiezo` | target_um | actual_um, duration | PiezoService |

### Imaging Actions
| Action | Input | Output | Service |
|--------|-------|--------|---------|
| `Configure` | ChannelConfig | actual_settings | ModeController |
| `Trigger` | capture_info | frame_id | CameraService |
| `WaitFrame` | frame_id, timeout | frame, timestamp | CameraService |

### External Actions
| Action | Input | Output | Service |
|--------|-------|--------|---------|
| `RunFluidics` | protocol_name | protocol_id | FluidicsService |
| `SetIllumination` | channel, intensity | actual | IlluminationService |

### Gates (Synchronization)
| Gate | Condition | Output |
|------|-----------|--------|
| `WaitUntil` | timestamp | elapsed |
| `WaitForFocusLock` | timeout, threshold | locked, quality |
| `WaitForFluidics` | timeout | success |
| `WaitForStabilization` | duration_ms | actual_wait |
| `WaitForTriggerReady` | timeout | ready |

### Recorders (Data Collection)
| Recorder | Attaches To | Collects |
|----------|-------------|----------|
| `PositionRecorder` | MoveXY, MoveZ | position, time → focus map |
| `TimingRecorder` | All actions | start, end, duration |
| `FocusRecorder` | WaitForFocusLock | quality, correction |
| `FrameRecorder` | WaitFrame | frame → storage |

---

## Part 5: Composition Patterns

### Pattern 1: Capture at Position
```python
def capture_at_position(pos, channels, z_stack) -> Iterable[Action]:
    yield MoveXY(pos)
    yield WaitForFocusLock(timeout=5.0)  # If using continuous AF

    for z in z_stack:
        yield MoveZ(z, relative=True)
        for ch in channels:
            yield Configure(ch)
            yield Trigger(build_capture_info(pos, z, ch))
            yield WaitFrame(timeout=10.0)
        yield MoveZ(-z, relative=True)  # Return
```

### Pattern 2: Tiled Acquisition with Fluidics
```python
def tiled_acquisition(spec) -> Iterable[Action]:
    for round_idx, round_config in enumerate(spec.rounds):
        # Sample prep
        yield RunFluidics(round_config.prep_protocol)
        yield WaitForFluidics()
        yield WaitForStabilization(1000)  # Let sample settle

        # Image all positions
        for pos in spec.positions:
            yield from capture_at_position(pos, round_config.channels, spec.z_stack)

        # Cleanup
        yield RunFluidics(round_config.cleanup_protocol)
        yield WaitForFluidics()
```

### Pattern 3: Focus Map Building
```python
def build_focus_map(positions, focus_service) -> Iterable[Action]:
    for pos in positions:
        yield MoveXY(pos)
        yield AutofocusAction()  # Find best focus
        # Recorder captures: (pos, z, quality) → focus_map
```

### Pattern 4: Parallel Save (Pipeline)
The engine can detect that `WaitFrame` → `FrameRecorder` (save) can overlap with `MoveXY`:

```
MoveXY(pos1) → WaitFocus → Configure → Trigger → WaitFrame → [save in background]
                                                      ↓
MoveXY(pos2) → WaitFocus → Configure → Trigger → WaitFrame → [save in background]
```

---

## Part 6: Handling Complexity

### Error Recovery
Each action can specify retry behavior:
```python
class Action:
    max_retries: int = 0
    retry_delay_ms: int = 100

    def on_failure(self, error, context) -> FailureAction:
        return FailureAction.ABORT  # or RETRY, SKIP, CONTINUE
```

### Conditional Execution
Gates can have fallback behaviors:
```python
yield WaitForFocusLock(timeout=5.0, on_timeout=OnTimeout.USE_PREDICTED)
# If lock fails, use focus map prediction instead of aborting
```

### Data Dependencies
Recorders feed back into the system:
```python
# Focus recorder populates focus map
focus_recorder = FocusRecorder(focus_map_service)

# Later actions can query the map
yield ApplyFocusMap(pos)  # Uses focus_map_service.predict(pos)
```

---

## Part 7: Directory Structure (Revised)

```
software/src/squid/
├── core/
│   ├── abc.py                    # Hardware ABCs
│   ├── events.py                 # EventBus + events
│   ├── config.py                 # Pydantic models
│   └── execution/                # NEW: Execution engine
│       ├── engine.py             # ExecutionEngine
│       ├── actions.py            # Base Action, Gate classes
│       ├── recorders.py          # Base Recorder class
│       └── context.py            # ExecutionContext
│
├── mcs/
│   ├── services/                 # Hardware services (unchanged)
│   │   ├── camera_service.py
│   │   ├── stage_service.py
│   │   └── ...
│   │
│   ├── controllers/              # Simplified: compile specs → actions
│   │   ├── acquisition_controller.py
│   │   ├── live_controller.py
│   │   └── ...
│   │
│   └── drivers/                  # Hardware implementations
│
├── services/                     # NEW: Non-hardware services
│   ├── storage_service.py        # Frame persistence + query
│   ├── focus_map_service.py      # Focus map building + prediction
│   └── continuous_af_service.py  # Background AF tracking
│
├── actions/                      # NEW: Action implementations
│   ├── movement.py               # MoveXY, MoveZ, MovePiezo
│   ├── imaging.py                # Configure, Trigger, WaitFrame
│   ├── fluidics.py               # RunFluidics
│   └── focus.py                  # AutofocusAction, ApplyFocusMap
│
├── gates/                        # NEW: Gate implementations
│   ├── timing.py                 # WaitUntil, WaitForStabilization
│   ├── focus.py                  # WaitForFocusLock
│   └── fluidics.py               # WaitForFluidics
│
├── recorders/                    # NEW: Recorder implementations
│   ├── timing.py                 # TimingRecorder
│   ├── position.py               # PositionRecorder
│   ├── focus.py                  # FocusRecorder
│   └── frame.py                  # FrameRecorder (to storage)
│
├── workflows/                    # NEW: Workflow compilers
│   ├── acquisition.py            # compile_acquisition()
│   ├── focus_map.py              # compile_focus_map_build()
│   └── timelapse.py              # compile_timelapse()
│
└── ui/                           # UI layer (unchanged)
```

---

## Part 8: Open Questions

### 1. Is the Action/Gate/Recorder split correct?

The current proposal separates:
- **Actions** - Do things (side effects)
- **Gates** - Wait for conditions (synchronization)
- **Recorders** - Observe and record (data collection)

Alternative: Could recorders be part of actions? Could gates be a type of action?

### 2. How much parallelism does the engine need?

Options:
- **Sequential only** - Simple, predictable
- **Pipeline parallelism** - Save overlaps with next move
- **Full DAG** - Complex dependencies

Recommendation: Start with sequential + async save, add pipeline later.

### 3. Should ContinuousAFService exist?

Current system: Laser AF is invoked per-FOV, not continuous.
Proposed: Background service that maintains lock.

This is a significant new capability. Should it be part of this refactor, or a separate effort?

### 4. How do we handle the current callback-based frame capture?

Current: Camera callback → ThreadSafeValue → worker reads
Proposed: `Trigger` → `WaitFrame` actions

Need to bridge these models or refactor camera streaming.

---

## Part 9: Migration Path

### Phase 1: Foundation
1. Create `core/execution/` with Engine, Action, Gate, Recorder base classes
2. Create `services/storage_service.py` wrapping current JobRunner

### Phase 2: Actions & Gates
3. Implement movement actions (MoveXY, MoveZ)
4. Implement imaging actions (Configure, Trigger, WaitFrame)
5. Implement basic gates (WaitUntil, WaitForStabilization)

### Phase 3: Recorders
6. Implement FrameRecorder (bridge to StorageService)
7. Implement TimingRecorder
8. Implement PositionRecorder

### Phase 4: Workflow Compiler
9. Create `workflows/acquisition.py` with `compile_acquisition()`
10. Refactor AcquisitionController to use compiler + engine

### Phase 5: Advanced Features
11. Add FocusMapService
12. Add ContinuousAFService (if desired)
13. Add fluidics integration

---

## Summary

**Key Changes from v1:**

1. **Actions are truly atomic** - Single hardware interactions, not workflows
2. **Gates handle synchronization** - Waiting is explicit, not hidden in actions
3. **Recorders collect data** - Systematic data collection, not ad-hoc
4. **Storage is a service** - Bidirectional (write + query), not just a sink
5. **Execution Engine** - Central executor with abort/progress/parallelism
6. **Workflow compilation** - Specs → action sequences, separation of concerns

**Benefits:**

- Each piece is small and testable
- New workflows = new compiler, reuse actions
- Data collection is systematic
- Parallelism handled by engine, not scattered in code
- Error handling centralized

---

## Part 10: Production Features for Long-Running Experiments

For large-scale MERFISH and similar long-running experiments, additional infrastructure is needed:

### A. Protocol DSL + Compiler

Replace ad-hoc scripts with a validated, declarative specification:

```yaml
# experiment.yaml
experiment:
  name: "MERFISH_round_1"
  version: "1.0"

regions:
  - id: "region_1"
    positions: { grid: { rows: 10, cols: 10, spacing_um: 200 } }

rounds:
  - id: "hyb_round_1"
    fluidics:
      before: ["flush", "hybridize_probe_1", "wash"]
      after: ["strip", "wash"]
    imaging:
      channels: ["DAPI", "Cy5", "Cy3"]
      z_stack: { num_z: 5, delta_um: 0.5 }
      autofocus: { strategy: "continuous", lock_timeout_s: 5 }

constraints:
  - "imaging requires fluidics.before.complete"
  - "next_round requires fluidics.after.complete"
```

**Compiled to execution graph:**
```
FluidicsStep(hyb_1_prep)
        ↓
AcquireTileSet(region_1, round_1)
  ├── for each position:
  │     MeasureDrift() → Autofocus() → CaptureChannels()
        ↓
FluidicsStep(hyb_1_cleanup)
        ↓
FluidicsStep(hyb_2_prep)
        ...
```

**Benefits:**
- Schema validation catches errors before starting
- Static analysis: "never image before hybridization complete"
- Reproducible: spec is the experiment definition
- Resumable: graph knows what's done vs pending

### B. Simulation and Record/Replay

**Simulated Devices:**
```python
class SimulatedStage(AbstractStage):
    def __init__(self, config: SimulationConfig):
        self._latency_ms = config.move_latency_ms
        self._failure_rate = config.failure_rate
        self._position_noise_um = config.position_noise_um

    def move_to(self, x, y):
        time.sleep(self._latency_ms / 1000)
        if random.random() < self._failure_rate:
            raise StageError("Simulated failure")
        # Add realistic noise
        actual_x = x + random.gauss(0, self._position_noise_um)
        ...
```

**Record/Replay:**
```python
# Recording mode
recorder = DeviceEventRecorder("session_2024_01_15.events")
stage = RecordingWrapper(real_stage, recorder)

# Replay mode (for debugging)
replayer = DeviceEventReplayer("session_2024_01_15.events")
stage = ReplayingWrapper(replayer)
# Reproduces exact sequence of moves, timings, failures
```

**Benefits:**
- Test full acquisition logic without hardware
- Reproduce bugs from production runs
- Stress testing with injected failures

### C. Structured Logging + Metrics

**Machine-Readable Logs (JSON Lines):**
```json
{"ts": "2024-01-15T03:17:42.123Z", "level": "ERROR", "component": "autofocus",
 "event": "lock_timeout", "position": {"x": 1.5, "y": 2.3}, "timeout_s": 5.0,
 "last_quality": 0.23, "region_id": "region_1", "fov": 42}
```

**Time-Series Metrics (Prometheus/InfluxDB compatible):**
```python
class MetricsCollector:
    def record_focus_quality(self, quality: float, region: str, fov: int)
    def record_frame_timing(self, exposure_ms: float, readout_ms: float)
    def record_stage_drift(self, dx_um: float, dy_um: float, dz_um: float)
    def record_fluidics_pressure(self, channel: str, pressure_psi: float)
    def record_disk_usage(self, used_gb: float, free_gb: float)
    def record_job_queue_depth(self, queue: str, depth: int)
    def record_dropped_frame(self, reason: str)
```

**Key Metrics for Overnight Runs:**
| Metric | Why It Matters |
|--------|----------------|
| Focus lock quality over time | Detect drift or sample degradation |
| Frame timing histogram | Detect I/O bottlenecks |
| Job queue depth | Are we falling behind on saves? |
| Disk free space | Will we run out? |
| Fluidics pressures/flows | Detect clogs or leaks |
| Stage position error | Detect mechanical issues |
| Dropped/retried frames | Early warning of problems |

### D. UI is Optional; Monitoring is Not

**Architecture for Robustness:**
```
┌─────────────────┐     ┌─────────────────┐
│   UI (PyQt)     │     │ Remote Dashboard│
│   (can crash)   │     │   (web-based)   │
└────────┬────────┘     └────────┬────────┘
         │                       │
         │ WebSocket/REST        │ WebSocket/REST
         ↓                       ↓
┌─────────────────────────────────────────┐
│           Monitoring Service            │
│  (receives events, serves dashboards)   │
└────────────────────┬────────────────────┘
                     │ Subscribe
                     ↓
┌─────────────────────────────────────────┐
│         Acquisition Engine              │
│    (runs independently of UI)           │
└─────────────────────────────────────────┘
```

**Key Properties:**
- Acquisition continues if UI crashes
- Monitoring is a separate process (can restart independently)
- Remote access via web dashboard (check status from home at 3am)
- Alerting: email/Slack when critical metrics exceed thresholds

### E. Transactional, Queryable Metadata Database

**Replace directory-name-as-metadata with proper database:**

```sql
-- Core tables
CREATE TABLE experiments (
    id TEXT PRIMARY KEY,
    name TEXT,
    spec_json TEXT,  -- Full experiment spec for reproducibility
    started_at TIMESTAMP,
    status TEXT  -- 'running', 'paused', 'completed', 'failed'
);

CREATE TABLE acquisition_units (
    id TEXT PRIMARY KEY,
    experiment_id TEXT REFERENCES experiments(id),
    region_id TEXT,
    round_id TEXT,
    fov INTEGER,
    channel TEXT,
    z_index INTEGER,

    -- Planning
    target_x_mm REAL,
    target_y_mm REAL,
    target_z_mm REAL,

    -- Actuals
    actual_x_mm REAL,
    actual_y_mm REAL,
    actual_z_mm REAL,
    capture_timestamp TIMESTAMP,

    -- Settings
    exposure_ms REAL,
    illumination_power REAL,
    camera_gain REAL,

    -- Focus
    focus_locked BOOLEAN,
    focus_quality REAL,
    focus_z_correction_um REAL,

    -- Fluidics
    fluidics_step_id TEXT,
    fluidics_completed BOOLEAN,

    -- Storage
    file_path TEXT,
    file_checksum TEXT,
    file_size_bytes INTEGER,

    -- Status
    status TEXT,  -- 'planned', 'in_progress', 'complete', 'failed', 'skipped'
    error_message TEXT,
    retry_count INTEGER DEFAULT 0
);

CREATE TABLE fluidics_events (
    id TEXT PRIMARY KEY,
    experiment_id TEXT,
    protocol_name TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    status TEXT,
    pressures_json TEXT,
    error_message TEXT
);

-- Indices for common queries
CREATE INDEX idx_units_status ON acquisition_units(experiment_id, status);
CREATE INDEX idx_units_region ON acquisition_units(experiment_id, region_id, fov);
```

**Query Examples:**
```python
# Resume after crash: what's left to do?
pending = db.query("""
    SELECT * FROM acquisition_units
    WHERE experiment_id = ? AND status IN ('planned', 'in_progress')
    ORDER BY region_id, fov, z_index
""", experiment_id)

# Quality report: focus issues?
focus_issues = db.query("""
    SELECT region_id, fov, focus_quality, capture_timestamp
    FROM acquisition_units
    WHERE experiment_id = ? AND focus_quality < 0.5
""", experiment_id)

# Storage audit: verify all files
for unit in db.query("SELECT file_path, file_checksum FROM acquisition_units"):
    assert compute_checksum(unit.file_path) == unit.file_checksum
```

**Benefits:**
- **Restartable**: Know exactly what's done, resume from failure point
- **Auditable**: Full record of what happened
- **Queryable**: Answer questions about the run
- **Transactional**: No half-written state

---

## Part 11: Revised Architecture with Production Features

```
┌─────────────────────────────────────────────────────────────────┐
│                    PROTOCOL LAYER (NEW)                          │
│  Protocol DSL (YAML) → Compiler → Execution Graph                │
│  Static validation, dependency checking                          │
└─────────────────────────────────────────────────────────────────┘
                              ↓ compiled graph
┌─────────────────────────────────────────────────────────────────┐
│                     CONTROLLER LAYER                             │
│  State machine, subscribes to commands                           │
│  Queries DB for resume state                                     │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                  EXECUTION ENGINE                                │
│  Runs actions, gates, recorders                                  │
│  Updates DB status, emits metrics                                │
└─────────────────────────────────────────────────────────────────┘
     ↓               ↓               ↓               ↓
┌─────────┐   ┌───────────┐   ┌───────────┐   ┌───────────────┐
│SERVICES │   │ STORAGE   │   │ DATABASE  │   │ METRICS/LOGS  │
│(hardware│   │ (frames)  │   │ (metadata)│   │ (monitoring)  │
└─────────┘   └───────────┘   └───────────┘   └───────────────┘
                                                      ↓
                                              ┌───────────────┐
                                              │  MONITORING   │
                                              │  (dashboard,  │
                                              │   alerts)     │
                                              └───────────────┘
```

---

## Part 12: Implementation Priority

Given the scope, here's a prioritized implementation order:

### Phase 1: Foundation (Required for any acquisition)
1. Execution Engine + Actions/Gates/Recorders
2. StorageService (wrap existing JobRunner)
3. Basic structured logging

### Phase 2: Reliability (Required for overnight runs)
4. Metadata database (SQLite)
5. Resume capability
6. Metrics collection
7. Simulation mode for testing

### Phase 3: Scale (Required for multi-day MERFISH)
8. Protocol DSL + compiler
9. Remote monitoring dashboard
10. Alerting

### Phase 4: Advanced (Nice to have)
11. ContinuousAFService
12. FocusMapService with interpolation
13. Record/replay debugging
14. Multi-node support (PostgreSQL)

---

## Part 13: Hardware-Fused Acquisition Path

For maximum speed and timing precision, we need a path where per-FOV acquisition is entirely hardware-controlled:

### The Two-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│              EXPERIMENT ORCHESTRATOR (Soft Real-Time)            │
│  - Compiles protocol → execution plan                            │
│  - State machine with timeouts, retries, recovery                │
│  - Owns: fluidics, XY stage moves, autofocus, drift correction  │
│  - Checkpointing + resume                                        │
│  - Runs for days/weeks                                           │
└─────────────────────────────────────────────────────────────────┘
                              ↓ "Acquire FOV" command
                              ↓ (position, channels, z-stack config)
┌─────────────────────────────────────────────────────────────────┐
│           REAL-TIME ACQUISITION CORE (Hard Real-Time)            │
│  - Hardware-triggered execution (TTL)                            │
│  - Microsecond jitter tolerance                                  │
│  - Separate process (or machine)                                 │
│  - Lock-free queues to data plane                                │
└─────────────────────────────────────────────────────────────────┘
         ↓                    ↓                    ↓
   ┌──────────┐        ┌──────────┐        ┌──────────┐
   │ Camera   │←──TTL──│ Trigger  │──TTL──→│Illumin.  │
   │ (stream) │        │ Router   │        │ Gating   │
   └──────────┘        └────┬─────┘        └──────────┘
                            │TTL
                       ┌────↓─────┐
                       │ Piezo Z  │
                       │ Stepper  │
                       └──────────┘
```

### A. Real-Time Acquisition Core

**Purpose:** Execute the fast path—camera exposure, illumination gating, Z stepping—with minimal jitter.

**Key Properties:**
- **Separate process** (or separate machine) from UI and orchestration
- **Hardware triggers (TTL)** for microsecond coordination independent of OS scheduling
- **Minimal scope** - only what needs hard real-time:
  - Camera streaming control
  - Trigger routing / timing
  - Illumination / shutter gating
  - Piezo Z stepping and homing
  - Minimal metadata stamping (frame counter, timestamp, hardware state hash)
- **Single event loop** + lock-free queues into data plane
- **No blocking calls** - everything async or hardware-timed

**Interface:**
```python
class RealtimeAcquisitionCore:
    """Runs in separate process. Communicates via IPC."""

    def configure(self, config: FOVAcquisitionConfig) -> None:
        """
        Set up hardware for acquisition:
        - channels: List[ChannelConfig]  # exposure, illumination, filter
        - z_stack: ZStackConfig          # num_z, delta_um, piezo vs stage
        - trigger_mode: TriggerMode      # hardware vs software
        """

    def arm(self) -> None:
        """Prepare hardware for triggered acquisition."""

    def trigger(self) -> AcquisitionHandle:
        """
        Start hardware-timed acquisition sequence.
        Returns handle to monitor progress / retrieve frames.
        """

    def wait_complete(self, handle: AcquisitionHandle, timeout_s: float) -> FOVResult:
        """
        Wait for all frames from this FOV.
        Returns: frames, timestamps, actual positions, any errors.
        """

    def abort(self) -> None:
        """Emergency stop."""
```

**Hardware-Timed Sequence (inside core):**
```
ARM
 │
 ├─→ Piezo to start position
 ├─→ Camera armed, streaming
 ├─→ Illumination ready
 │
TRIGGER (single software trigger or external)
 │
 ├─→ [Z=0] TTL → Camera exposure + Illumination Ch1
 │         ↓ exposure complete
 ├─→ [Z=0] TTL → Camera exposure + Illumination Ch2
 │         ...
 ├─→ [Z=0→Z=1] TTL → Piezo step
 ├─→ [Z=1] TTL → Camera exposure + Illumination Ch1
 │         ...
 └─→ [Z=N] All channels complete → DONE signal

Total: (num_z × num_channels) frames, hardware-timed
```

**Data Path (lock-free):**
```
Camera callback
     ↓ (zero-copy if possible)
Lock-free ring buffer
     ↓
Frame stamper (counter, timestamp, config hash)
     ↓
IPC queue to orchestrator / storage
```

### B. Experiment Orchestrator

**Purpose:** Compile protocol, supervise execution for days/weeks, handle recovery.

**Key Properties:**
- **Soft real-time** - milliseconds are fine, seconds for recovery
- **State machine** with explicit transitions, timeouts, retries
- **Transactional** - database tracks state, can resume from any point
- **Owns the "between FOV" operations:**
  - XY stage positioning
  - Focus strategy (continuous AF, focus map, contrast sweep)
  - Drift measurement and correction
  - Fluidics protocol execution
  - Health monitoring, pausing, alerting

**Orchestrator Workflow:**
```python
class ExperimentOrchestrator:
    def run_experiment(self, protocol: ExperimentProtocol):
        plan = self.compiler.compile(protocol)  # → execution graph

        for step in plan.iter_steps():
            if step.type == "fluidics":
                self.fluidics.run_protocol(step.protocol)
                self.wait_for_fluidics()

            elif step.type == "acquire_region":
                for fov in step.positions:
                    # Software-controlled: move to position
                    self.stage.move_to(fov.position)
                    self.stage.wait_for_idle()

                    # Software-controlled: focus
                    self.ensure_focus(fov)

                    # Hardware-controlled: fast acquisition
                    self.realtime_core.configure(fov.acquisition_config)
                    self.realtime_core.arm()
                    handle = self.realtime_core.trigger()

                    # Wait for hardware acquisition to complete
                    result = self.realtime_core.wait_complete(handle, timeout=30)

                    # Record in database
                    self.db.record_fov_complete(fov, result)

                    # Check health
                    if result.focus_quality < threshold:
                        self.handle_focus_degradation(fov, result)
```

### C. Software-Controlled Fallback

For debugging or when hardware triggers aren't available:

```python
class SoftwareAcquisitionCore:
    """Same interface as RealtimeAcquisitionCore, but software-timed."""

    def trigger(self) -> AcquisitionHandle:
        for z_idx in range(self.config.num_z):
            self.piezo.move_to(z_positions[z_idx])
            time.sleep(stabilization_time)

            for channel in self.config.channels:
                self.illumination.set(channel)
                self.camera.trigger()
                frame = self.camera.wait_frame(timeout=5.0)
                self.frame_queue.put((frame, z_idx, channel))

        return handle
```

**Same interface** → orchestrator doesn't care which is used.

### D. Integration with Action/Gate Model

The real-time core becomes a **fused Action**:

```python
class AcquireFOVAction(Action):
    """
    Fused hardware-timed acquisition at current position.
    Internally uses RealtimeAcquisitionCore or SoftwareAcquisitionCore.
    """

    def __init__(self, config: FOVAcquisitionConfig, use_hardware: bool = True):
        self.config = config
        self.core = RealtimeAcquisitionCore() if use_hardware else SoftwareAcquisitionCore()

    def execute(self, context: ExecutionContext) -> FOVResult:
        self.core.configure(self.config)
        self.core.arm()
        handle = self.core.trigger()
        return self.core.wait_complete(handle, timeout=30)
```

**Compiled workflow uses fused action:**
```python
def compile_acquisition(spec) -> Iterable[Action | Gate]:
    for region in spec.regions:
        for pos in region.positions:
            yield MoveXY(pos)                    # Software-controlled
            yield WaitForFocusLock(timeout=5)   # Software-controlled

            # Hardware-fused: entire Z-stack × channels at this FOV
            yield AcquireFOVAction(
                config=FOVAcquisitionConfig(
                    channels=spec.channels,
                    z_stack=spec.z_stack,
                ),
                use_hardware=spec.use_hardware_triggers,
            )
```

### E. Key Design Decisions

**1. Process Separation:**
```
┌──────────────┐     IPC      ┌──────────────┐
│ Orchestrator │◄────────────►│ RT Core      │
│ (Python)     │  (commands)  │ (Python/C++) │
└──────────────┘              └──────────────┘
                                    │
                              Hardware TTL
                                    ↓
                              Camera, Piezo,
                              Illumination
```

**2. Hardware Trigger Routing:**
The RT Core owns the trigger router (e.g., Arduino/FPGA/DAQ card):
```
Trigger Sequence Table:
  Step 0: Camera.Trigger + LED_405.On
  Step 1: Wait 10ms
  Step 2: LED_405.Off
  Step 3: Camera.Trigger + LED_488.On
  ...
  Step N: Piezo.Step(+0.5um)
  Step N+1: Camera.Trigger + LED_405.On
  ...
```

**3. Graceful Degradation:**
- If hardware triggers fail → fall back to software timing
- If RT core crashes → orchestrator detects timeout, logs, retries or skips
- Orchestrator is designed to survive RT core failures

### F. Timing Comparison

| Operation | Software-Timed | Hardware-Timed |
|-----------|----------------|----------------|
| Z-step + settle | 50-100ms | 5-10ms |
| Illumination switch | 1-10ms | <100µs |
| Camera trigger jitter | 1-10ms | <10µs |
| **Per-FOV (5z × 4ch)** | **~2-3s** | **~200-500ms** |

**10x faster** for the same FOV when hardware-timed.

---

## Part 14: Updated Implementation Priority

Given the hardware-fused path, revised priority:

### Phase 1: Foundation
1. Execution Engine + Actions/Gates/Recorders
2. StorageService
3. Basic structured logging
4. **SoftwareAcquisitionCore** (same interface as RT core)

### Phase 2: Reliability
5. Metadata database (SQLite)
6. Resume capability
7. Metrics collection
8. Simulation mode

### Phase 3: Production
9. Protocol DSL + compiler
10. Remote monitoring dashboard
11. **Process separation** (orchestrator vs acquisition core)

### Phase 4: Performance
12. **RealtimeAcquisitionCore** with hardware triggers
13. Trigger router integration (Arduino/FPGA/DAQ)
14. Lock-free frame pipeline

### Phase 5: Advanced
15. ContinuousAFService
16. FocusMapService
17. Record/replay debugging
