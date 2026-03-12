# Orchestrator Improvements Design

## Goal

Make the orchestrator robust enough for unattended overnight runs. When you check in the morning, the dashboard tells you clearly what happened, what went wrong, and where. The system doesn't auto-recover — it observes and surfaces problems so a human can intervene.

## Three workstreams

1. **Monitoring dashboard** — replace custom sparklines with pyqtgraph, add health-at-a-glance panel, focus tracking plot
2. **Consolidated RunState** — single frozen snapshot replaces scattered state, eliminates data races, simplifies testing
3. **Bug fixes** — address all issues found in code review

---

## 1. Monitoring Dashboard

### 1.1 Health Strip (replaces 7 mini-cards)

Three zones in a horizontal bar at the top:

**State zone:**
- Large status badge (RUNNING / PAUSED / WAITING_INTERVENTION / etc.)
- Elapsed time and ETA in human-readable format (e.g., "1h 23m elapsed, ~45m remaining")

**Position zone:**
- Round: "2 / 5" with round name
- Step: "1 / 3 (Imaging: FISH_config)"
- FOV: "34 / 120"
- Attempt: only shown when > 1, with "retry" indicator

**Health zone:**
- Focus status: colored dot (green = locked, yellow = searching, red = lost) with label
- Throughput: FOVs/min (rolling 2-minute window)
- Warnings: count badge, colored by max severity (yellow/red)

Layout: use QHBoxLayout with three QGroupBox-style frames. Each zone gets enough horizontal space to display without clipping on a ~400px dock panel.

### 1.2 Plots (pyqtgraph, accumulating over full run)

Replace SparklineWidget and MetricPlotCard with pyqtgraph PlotWidgets. All plots accumulate over the full experiment duration (no scrolling window). X-axis is wall-clock time since run start, with tick labels in human units (0m, 5m, 30m, 1h, 2h).

**Plot 1 — Progress (% complete vs time):**
- Y-axis: 0–100%, labeled "Progress %"
- Linear slope = healthy. Flat = stuck. Steepening = accelerating.
- Current value shown as text annotation at right edge

**Plot 2 — Throughput (FOVs/min vs time):**
- Y-axis: FOVs/min, auto-scaled
- Computed as rolling average over last 2 minutes of FOV completions
- Drops in throughput signal hardware issues or focus problems before they escalate
- Only shown during imaging steps (hidden or zeroed during fluidics/intervention)

**Plot 3 — Focus tracking (focus error in µm vs time):**
- Y-axis: focus error in µm, labeled with units
- Only shown when using laser AF / focus lock
- Plots `focus_error_um` from RunState snapshots
- Horizontal reference lines at acquire_threshold and maintain_threshold from protocol
- This is the single most valuable plot — directly answers "is focus holding" and "when did it drift"

**Subsystem breakdown bar** (keep existing SubsystemBreakdownWidget):
- Fix legend overflow: wrap to two rows when > 3 items, or increase minimum height
- Add time labels on the bar segments

### 1.3 Intervention Panel

Keep current layout (badge + message + action buttons) but enhance the message:
- Include **why**: "Focus lost at FOV 34: SNR dropped to 2.1 (threshold: 10.0)"
- Include context: round/step/FOV position, time since last successful FOV
- Include suggestion based on failure type (informational, not automatic)

### 1.4 Workflow Tree

No major changes needed. Keep discrete event-driven updates (StepStarted, StepCompleted, FovTaskStarted, etc.).

### 1.5 Dark Theme Fixes

- validation_dialog.py: Replace light-theme colors (#FFEBEE, #FFF3E0) with dark-compatible:
  - Error: background #2d1a1a, text #ff6b6b
  - Warning: background #2d2213, text #ffb74d

---

## 2. Consolidated RunState

### 2.1 The Problem

State is scattered:
- `ExperimentProgress` in runner behind `_progress_lock`
- `_current_operation` on controller with no lock (data race)
- Checkpoint fields separate from progress
- Timing accumulators in runner
- Subsystem breakdown in runner

Three separate events (`OrchestratorProgress`, `OrchestratorTimingSnapshot`, and implicit operation string) carry overlapping subsets of this data.

### 2.2 RunState Frozen Dataclass

```python
@dataclass(frozen=True)
class RunState:
    # Identity
    experiment_id: str
    state: OrchestratorState

    # Position
    round_index: int
    total_rounds: int
    round_name: str
    step_index: int
    total_steps: int
    step_type: str          # "imaging", "fluidics", "intervention"
    step_label: str
    fov_index: int
    total_fovs: int

    # Timing
    elapsed_s: float
    active_s: float         # elapsed minus paused
    paused_s: float
    eta_s: Optional[float]

    # Health
    attempt: int
    focus_status: Optional[str]   # "locked", "searching", "lost", None
    focus_error_um: Optional[float]
    throughput_fov_per_min: Optional[float]

    # Subsystem timing
    subsystem_seconds: Dict[str, float]

    # Timestamps
    started_at: Optional[datetime]
    snapshot_at: datetime         # when this snapshot was taken
```

### 2.3 How It Works

1. Runner holds mutable `_MutableRunState` behind a single `_lock` (replaces `_progress_lock`)
2. All mutations (step start, FOV progress, timing tick) happen inside the lock
3. After mutation, call `_snapshot() -> RunState` which copies fields into a frozen dataclass
4. Publish `RunStateUpdated(run_state=snapshot)` — one event replaces `OrchestratorProgress` + `OrchestratorTimingSnapshot`
5. Timing publisher thread calls `_snapshot()` on interval and publishes
6. Checkpoints are derived from `RunState` — `RunState.to_checkpoint() -> Checkpoint`
7. UI subscribes to `RunStateUpdated` and reads fields as needed

### 2.4 What Gets Removed

- `ExperimentProgress` class and `RoundProgress` class (replaced by `RunState`)
- `_current_operation` on controller (moved into `_MutableRunState`)
- `OrchestratorTimingSnapshot` event (merged into `RunStateUpdated`)
- `OrchestratorProgress` event (merged into `RunStateUpdated`)
- Separate `_on_progress()` and `_publish_timing_snapshot()` methods (unified)
- `_last_checkpoint_fov` tracking (checkpoint derived from RunState)

### 2.5 What Stays

- Discrete workflow events: `RoundStarted`, `RoundCompleted`, `StepStarted`, `StepCompleted`, `AttemptUpdate`, `FovTaskStarted`, `FovTaskCompleted` — the tree needs these
- `OrchestratorStateChanged` — state transitions are important as discrete events
- `OrchestratorInterventionRequired` — intervention is a discrete event
- `OrchestratorError` — errors are discrete
- `WarningRaised` — warnings are discrete

### 2.6 Throughput Computation

Add a `ThroughputTracker` utility:
- Records `(timestamp, fov_index)` pairs
- `fovs_per_minute(window_seconds=120) -> Optional[float]`
- Lives inside `_MutableRunState`, result included in each snapshot
- Only active during imaging steps

### 2.7 Focus Status Integration

The runner already receives focus lock events. Add to `_MutableRunState`:
- `focus_status`: updated from `FocusLockStatusChanged` events
- `focus_error_um`: updated from focus lock measurements
- These flow into `RunState` snapshots and then into the focus tracking plot

---

## 3. Bug Fixes

### 3.1 UI Bugs (from code review)

| # | Fix | File |
|---|-----|------|
| 1 | Replace SparklineWidget with pyqtgraph (addresses: no units, no scale, no current-value marker, ETA sparkline empty, non-uniform time spacing, set_values inefficiency) | orchestrator_widget.py |
| 2 | Fix subsystem legend overflow: wrap to 2 rows or increase min height to 90px | orchestrator_widget.py |
| 3 | Fix dark-theme colors in validation dialog | validation_dialog.py |
| 4 | Remove dead `protocol_selected` signal or wire it | protocol_loader_dialog.py |
| 5 | Load all `fov_sets` in protocol loader, not just the first | protocol_loader_dialog.py |

### 3.2 Backend Bugs (from code review)

| # | Fix | File |
|---|-----|------|
| 6 | Eliminate `_current_operation` data race (absorbed into RunState) | orchestrator_controller.py |
| 7 | Move `start_from_fov` bounds check after `_auto_load_resources()` | orchestrator_controller.py |
| 8 | Pop legacy top-level keys from `data` in `_resolve_resources`, warn on conflicts | loader.py |
| 9 | Copy dicts before iterating in `_resolve_resources` (don't mutate during iteration) | loader.py |
| 10 | Replace bare `KeyError` in `_resolve_imaging_config` with descriptive error | experiment_runner.py |

### 3.3 Test Fixes (from code review)

| # | Fix | File |
|---|-----|------|
| 11 | Fix vacuously true assertion in `test_imaging_executor_called` | test_orchestrator_controller.py |
| 12 | Fix no-op `assert len(events) >= 0` in `test_progress_events_published` | test_orchestrator_controller.py |
| 13 | Add v3 schema tests (canonical `acquisition:` nested constructor) | test_protocol.py |
| 14 | Add test for `version: "3.0"` default through loader | test_protocol.py |
| 15 | Add v3 `resources:` block tests to `TestResourceFilePaths` | test_protocol.py |

---

## Implementation Order

1. **RunState consolidation** — this is foundational; monitoring depends on it
2. **Bug fixes (backend)** — items 6-10, some absorbed by RunState work
3. **Monitoring dashboard** — new health strip, pyqtgraph plots, focus tracking
4. **Bug fixes (UI)** — items 2-5, some absorbed by dashboard redesign
5. **Test fixes** — items 11-15

---

## Non-Goals

- No automatic recovery engine — the system observes and surfaces problems for human intervention
- No protocol editor changes — protocols are reused and stable
- No new step types — current fluidics/imaging/intervention is sufficient
- No headless/API operation (future work)
