# Orchestrator Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the orchestrator robust for unattended overnight runs with clear monitoring, consolidated state, and no data races.

**Architecture:** Three phases: (1) backend bug fixes that are small and independent, (2) RunState consolidation that replaces scattered state with a single frozen snapshot, (3) monitoring dashboard with pyqtgraph plots and health-at-a-glance panel. Phase 2 is the foundation for phase 3.

**Tech Stack:** Python 3.12, PyQt5, pyqtgraph, Pydantic, threading

**Design doc:** `docs/plans/2026-03-09-orchestrator-improvements-design.md`

---

## Phase 1: Backend Bug Fixes

### Task 1: Fix `_resolve_resources` — pop legacy keys and copy before iterating

**Files:**
- Modify: `software/src/squid/core/protocol/loader.py:220-261`
- Test: `software/tests/unit/orchestrator/test_protocol_v3_schema.py` (already written)

**Step 1: Fix legacy key handling — pop from data after copying to resources**

In `_resolve_resources` (line 233-234), the legacy top-level keys are copied into `resources` but not removed from `data`. If both a top-level key and a `resources.*` key exist, the top-level is silently dropped.

Edit `loader.py:223-234`:
```python
# Preserve legacy top-level keys but normalize to resources for the canonical model.
for field in (
    "imaging_protocols",
    "fluidics_protocols",
    "imaging_protocol_file",
    "fluidics_protocols_file",
    "fluidics_config_file",
    "fov_sets",
    "fov_file",
):
    if field in data:
        if field not in resources:
            resources[field] = data.pop(field)
        else:
            _log.warning(
                f"Ignoring top-level '{field}' — already defined in resources block"
            )
            data.pop(field)
```

**Step 2: Copy dicts before iterating to avoid mutation during iteration**

Edit `loader.py:237-261` — wrap each iteration in `list(...)`:
```python
# Resolve imaging protocols with file: references
for name, config in list(resources.get("imaging_protocols", {}).items()):
    if isinstance(config, dict) and "file" in config:
        file_path = protocol_dir / config["file"]
        if not file_path.exists():
            raise ProtocolValidationError(
                f"Imaging protocol file not found: {file_path}"
            )
        with open(file_path, "r") as f:
            resources.setdefault("imaging_protocols", {})[name] = yaml.safe_load(f)

# Resolve fluidics_protocols with file: references
for name, proto in list(resources.get("fluidics_protocols", {}).items()):
    if isinstance(proto, dict) and "file" in proto:
        file_path = protocol_dir / proto["file"]
        if not file_path.exists():
            raise ProtocolValidationError(
                f"Fluidics protocol file not found: {file_path}"
            )
        with open(file_path, "r") as f:
            resources.setdefault("fluidics_protocols", {})[name] = yaml.safe_load(f)

# Make FOV set paths absolute
for name, csv_path in list(resources.get("fov_sets", {}).items()):
    if csv_path and not Path(csv_path).is_absolute():
        resources.setdefault("fov_sets", {})[name] = str(protocol_dir / csv_path)
```

**Step 3: Run tests**

Run: `pytest tests/unit/orchestrator/test_protocol.py tests/unit/orchestrator/test_protocol_v3_schema.py -v`
Expected: All pass

**Step 4: Commit**

```
fix: Pop legacy keys in _resolve_resources, copy dicts before iteration
```

---

### Task 2: Move `start_from_fov` bounds check after `_auto_load_resources()`

**Files:**
- Modify: `software/src/squid/backend/controllers/orchestrator/orchestrator_controller.py:490-570`
- Test: `software/tests/unit/orchestrator/test_orchestrator_controller.py`

**Step 1: Restructure start_experiment to load resources before bounds-checking FOV**

The current order is: bounds check (line 525-535) → load resources (line 553). The FOV file isn't loaded yet when we check `start_from_fov` against scan coordinates.

Move the `start_from_fov` bounds check block (lines 518-535 — the `if start_from_fov > 0:` block) to after `_auto_load_resources()` (after current line 553). Keep `start_from_fov < 0` check (line 512-517) in the original location since that's a simple negativity check.

The new order in `start_experiment` should be:
1. Load protocol
2. Check round/step bounds (lines 498-511)
3. Check `start_from_fov < 0` (lines 512-517)
4. Set protocol, load fluidics, auto-load resources (lines 537-553)
5. **Then** check `start_from_fov > 0` against loaded coordinates (moved from lines 518-535)

**Step 2: Run tests**

Run: `pytest tests/unit/orchestrator/test_orchestrator_controller.py -v`
Expected: All pass

**Step 3: Commit**

```
fix: Check start_from_fov bounds after loading FOV resources
```

---

### Task 3: Fix `_resolve_imaging_config` error message

**Files:**
- Modify: `software/src/squid/backend/controllers/orchestrator/experiment_runner.py`

**Step 1: Find `_resolve_imaging_config` and replace bare KeyError**

Search for `_resolve_imaging_config` in experiment_runner.py. It raises `KeyError` when the config name isn't found. Replace with a descriptive error:

```python
def _resolve_imaging_config(self, step: ImagingStep) -> ImagingProtocol:
    config_name = step.protocol
    if config_name not in self._protocol.imaging_protocols:
        raise ValueError(
            f"Imaging protocol '{config_name}' not found in protocol. "
            f"Available: {list(self._protocol.imaging_protocols.keys())}"
        )
    # ... rest of method
```

**Step 2: Run tests**

Run: `pytest tests/unit/orchestrator/test_experiment_runner.py tests/unit/orchestrator/test_experiment_runner_threading.py -v`
Expected: All pass

**Step 3: Commit**

```
fix: Replace bare KeyError in _resolve_imaging_config with descriptive ValueError
```

---

## Phase 2: RunState Consolidation

### Task 4: Define RunState and ThroughputTracker in state.py

**Files:**
- Modify: `software/src/squid/backend/controllers/orchestrator/state.py`
- Create: `software/tests/unit/orchestrator/test_run_state.py`

**Step 1: Write tests for RunState and ThroughputTracker**

Create `test_run_state.py`:
```python
"""Tests for RunState frozen dataclass and ThroughputTracker."""
import time
from datetime import datetime
from unittest.mock import patch

import pytest

from squid.backend.controllers.orchestrator.state import (
    RunState,
    ThroughputTracker,
    OrchestratorState,
    Checkpoint,
)


class TestRunState:
    def test_frozen(self):
        rs = RunState(
            experiment_id="exp1", state=OrchestratorState.RUNNING,
            round_index=0, total_rounds=1, round_name="r0",
            step_index=0, total_steps=1, step_type="imaging", step_label="scan",
            fov_index=0, total_fovs=10,
            elapsed_s=5.0, active_s=5.0, paused_s=0.0, eta_s=10.0,
            attempt=1, focus_status=None, focus_error_um=None,
            throughput_fov_per_min=None,
            subsystem_seconds={}, started_at=None,
            snapshot_at=datetime.now(),
        )
        with pytest.raises(AttributeError):
            rs.round_index = 5

    def test_progress_percent(self):
        rs = RunState(
            experiment_id="exp1", state=OrchestratorState.RUNNING,
            round_index=0, total_rounds=2, round_name="r0",
            step_index=0, total_steps=2, step_type="imaging", step_label="scan",
            fov_index=5, total_fovs=10,
            elapsed_s=5.0, active_s=5.0, paused_s=0.0, eta_s=10.0,
            attempt=1, focus_status=None, focus_error_um=None,
            throughput_fov_per_min=None,
            subsystem_seconds={}, started_at=None,
            snapshot_at=datetime.now(),
        )
        pct = rs.progress_percent
        assert 0 <= pct <= 100
        # 0 completed rounds + (0 completed steps + 5/10 sub) / 2 steps / 2 rounds
        # = (0 + 0.25) * 100 = 12.5
        assert pct == pytest.approx(12.5)

    def test_to_checkpoint(self):
        rs = RunState(
            experiment_id="exp1", state=OrchestratorState.RUNNING,
            round_index=1, total_rounds=3, round_name="r1",
            step_index=2, total_steps=4, step_type="imaging", step_label="scan",
            fov_index=7, total_fovs=20,
            elapsed_s=120.0, active_s=100.0, paused_s=20.0, eta_s=60.0,
            attempt=1, focus_status="locked", focus_error_um=0.1,
            throughput_fov_per_min=3.5,
            subsystem_seconds={"imaging": 80.0, "fluidics": 20.0},
            started_at=datetime(2026, 3, 9, 10, 0, 0),
            snapshot_at=datetime.now(),
        )
        ckpt = rs.to_checkpoint(protocol_name="test", protocol_version="3.0",
                                 experiment_path="/tmp/exp1")
        assert ckpt.round_index == 1
        assert ckpt.step_index == 2
        assert ckpt.imaging_fov_index == 7
        assert ckpt.elapsed_seconds == 120.0
        assert ckpt.paused_seconds == 20.0


class TestThroughputTracker:
    def test_empty_returns_none(self):
        t = ThroughputTracker()
        assert t.fovs_per_minute() is None

    def test_single_fov_returns_none(self):
        t = ThroughputTracker()
        t.record_fov(0)
        assert t.fovs_per_minute() is None

    def test_two_fovs_computes_rate(self):
        t = ThroughputTracker()
        with patch("time.monotonic") as mono:
            mono.return_value = 100.0
            t.record_fov(0)
            mono.return_value = 130.0  # 30 seconds later
            t.record_fov(1)
            rate = t.fovs_per_minute(window_seconds=120)
        # 1 FOV in 30 seconds = 2 FOVs/min
        assert rate == pytest.approx(2.0)

    def test_window_excludes_old_entries(self):
        t = ThroughputTracker()
        with patch("time.monotonic") as mono:
            # Old entries outside 60s window
            mono.return_value = 0.0
            t.record_fov(0)
            mono.return_value = 10.0
            t.record_fov(1)
            # Recent entries inside 60s window
            mono.return_value = 100.0
            t.record_fov(2)
            mono.return_value = 120.0
            t.record_fov(3)
            mono.return_value = 140.0
            t.record_fov(4)
            rate = t.fovs_per_minute(window_seconds=60)
        # Within last 60s (from 140): entries at 100, 120, 140
        # 2 FOVs over 40 seconds = 3.0 FOVs/min
        assert rate == pytest.approx(3.0)

    def test_reset_clears_history(self):
        t = ThroughputTracker()
        t.record_fov(0)
        t.record_fov(1)
        t.reset()
        assert t.fovs_per_minute() is None
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/orchestrator/test_run_state.py -v`
Expected: ImportError — RunState and ThroughputTracker don't exist yet

**Step 3: Implement RunState and ThroughputTracker in state.py**

Add to `state.py` (after the existing classes, before the events section):

```python
import time as _time  # at top of file

class ThroughputTracker:
    """Tracks FOV completion timestamps to compute rolling throughput."""

    def __init__(self):
        self._timestamps: list[float] = []

    def record_fov(self, fov_index: int) -> None:
        self._timestamps.append(_time.monotonic())

    def fovs_per_minute(self, window_seconds: float = 120.0) -> Optional[float]:
        if len(self._timestamps) < 2:
            return None
        now = _time.monotonic()
        cutoff = now - window_seconds
        recent = [t for t in self._timestamps if t >= cutoff]
        if len(recent) < 2:
            return None
        elapsed = recent[-1] - recent[0]
        if elapsed <= 0:
            return None
        fovs_completed = len(recent) - 1
        return (fovs_completed / elapsed) * 60.0

    def reset(self) -> None:
        self._timestamps.clear()


@dataclass(frozen=True)
class RunState:
    """Immutable snapshot of the entire experiment state at a point in time.

    Replaces the scattered ExperimentProgress + _current_operation + timing
    fields with a single frozen object. Published as RunStateUpdated events.
    """

    # Identity
    experiment_id: str
    state: OrchestratorState

    # Position
    round_index: int
    total_rounds: int
    round_name: str
    step_index: int
    total_steps: int
    step_type: str  # "imaging", "fluidics", "intervention", ""
    step_label: str
    fov_index: int
    total_fovs: int

    # Timing
    elapsed_s: float
    active_s: float  # elapsed minus paused
    paused_s: float
    eta_s: Optional[float]

    # Health
    attempt: int
    focus_status: Optional[str] = None  # "locked", "searching", "lost"
    focus_error_um: Optional[float] = None
    throughput_fov_per_min: Optional[float] = None

    # Subsystem timing
    subsystem_seconds: Dict[str, float] = field(default_factory=dict)

    # Timestamps
    started_at: Optional[datetime] = None
    snapshot_at: datetime = field(default_factory=datetime.now)

    @property
    def progress_percent(self) -> float:
        """Calculate overall progress percentage (same formula as ExperimentProgress)."""
        if self.total_rounds == 0:
            return 0.0
        round_progress = self.round_index / self.total_rounds
        if self.round_index < self.total_rounds and self.total_steps > 0:
            round_frac = 1.0 / self.total_rounds
            step_frac = round_frac / self.total_steps
            completed_steps = min(self.step_index, self.total_steps)
            round_progress += completed_steps * step_frac
            if completed_steps < self.total_steps:
                sub = 0.0
                if self.step_type == "imaging" and self.total_fovs > 0:
                    sub = min(self.fov_index / self.total_fovs, 1.0)
                elif self.step_type == "fluidics" and self.total_fovs > 0:
                    sub = min(self.fov_index / self.total_fovs, 1.0)
                round_progress += sub * step_frac
        return round_progress * 100.0

    def to_checkpoint(
        self,
        protocol_name: str,
        protocol_version: str,
        experiment_path: str,
    ) -> "Checkpoint":
        """Derive a Checkpoint from this state snapshot."""
        return Checkpoint(
            protocol_name=protocol_name,
            protocol_version=protocol_version,
            experiment_id=self.experiment_id,
            experiment_path=experiment_path,
            round_index=self.round_index,
            step_index=self.step_index,
            imaging_fov_index=self.fov_index,
            created_at=self.snapshot_at,
            current_attempt=self.attempt,
            elapsed_seconds=self.elapsed_s,
            paused_seconds=self.paused_s,
            effective_run_seconds=self.active_s,
        )
```

Also add a new event class in the events section:

```python
@dataclass
class RunStateUpdated(Event):
    """Published when run state changes — single event replacing
    OrchestratorProgress and OrchestratorTimingSnapshot."""
    run_state: RunState
```

**Step 4: Run tests**

Run: `pytest tests/unit/orchestrator/test_run_state.py -v`
Expected: All pass

**Step 5: Commit**

```
feat: Add RunState frozen dataclass and ThroughputTracker
```

---

### Task 5: Wire RunState into ExperimentRunner

**Files:**
- Modify: `software/src/squid/backend/controllers/orchestrator/experiment_runner.py`
- Test: `software/tests/unit/orchestrator/test_experiment_runner_threading.py`

This is the largest task. The runner currently uses `ExperimentProgress` behind `_progress_lock` with separate fields for `_current_operation`, timing accumulators, etc. We add RunState snapshotting alongside the existing system first (additive), then the controller can publish it.

**Step 1: Add `_snapshot()` method to ExperimentRunner**

Add a method that reads all the scattered state under lock and produces a `RunState`:

```python
def snapshot(self, state: OrchestratorState) -> "RunState":
    """Produce an immutable RunState from current mutable state.

    Thread-safe: acquires _progress_lock internally.
    """
    from squid.backend.controllers.orchestrator.state import RunState

    with self._progress_lock:
        rp = self._progress.current_round
        round_index = self._progress.current_round_index
        total_rounds = self._progress.total_rounds
        round_name = rp.round_name if rp else ""
        step_index = self._progress.current_step_index
        total_steps = rp.total_steps if rp else 0
        step_type = rp.current_step_type if rp else ""
        step_label = self._progress.current_step_name
        fov_index = rp.imaging_fov_index if rp else 0
        total_fovs = rp.total_imaging_fovs if rp else 0
        attempt = self._progress.current_attempt
        started_at = self._progress.started_at

    # These are read outside lock but are only written from the worker thread
    # (same thread that calls snapshot from timing publisher)
    elapsed = max(0.0, time.monotonic() - self._run_start_time) if self._run_start_time > 0 else 0.0
    active = self._effective_run_elapsed()
    paused = self._total_paused_seconds
    eta = self.compute_eta()
    throughput = self._throughput.fovs_per_minute() if hasattr(self, '_throughput') else None

    return RunState(
        experiment_id=self._experiment_id,
        state=state,
        round_index=round_index,
        total_rounds=total_rounds,
        round_name=round_name,
        step_index=step_index,
        total_steps=total_steps,
        step_type=step_type,
        step_label=step_label,
        fov_index=fov_index,
        total_fovs=total_fovs,
        elapsed_s=elapsed,
        active_s=active,
        paused_s=paused,
        eta_s=eta,
        attempt=attempt,
        focus_status=getattr(self, '_focus_status', None),
        focus_error_um=getattr(self, '_focus_error_um', None),
        throughput_fov_per_min=throughput,
        subsystem_seconds=dict(self._subsystem_durations),
        started_at=started_at,
    )
```

**Step 2: Add ThroughputTracker to runner**

In `__init__`, add:
```python
self._throughput = ThroughputTracker()
self._focus_status: Optional[str] = None
self._focus_error_um: Optional[float] = None
```

In `_on_imaging_progress` closure (line 824-845), after updating `imaging_fov_index`, add:
```python
self._throughput.record_fov(fov_index)
```

Reset throughput at start of each imaging step (in `_execute_imaging_step`, before execute):
```python
self._throughput.reset()
```

**Step 3: Run tests**

Run: `pytest tests/unit/orchestrator/ -v --timeout=30`
Expected: All pass (RunState is additive, doesn't break existing)

**Step 4: Commit**

```
feat: Add RunState snapshot() and ThroughputTracker to ExperimentRunner
```

---

### Task 6: Publish RunStateUpdated from OrchestratorController

**Files:**
- Modify: `software/src/squid/backend/controllers/orchestrator/orchestrator_controller.py`

**Step 1: Update `_publish_progress` to also publish RunStateUpdated**

In `_publish_progress` (line 1127+), after the existing `OrchestratorProgress` publish, add:

```python
# Also publish unified RunState snapshot
runner = self._runner
if runner is not None:
    from squid.backend.controllers.orchestrator.state import RunStateUpdated
    run_state = runner.snapshot(self.state)
    self._event_bus.publish(RunStateUpdated(run_state=run_state))
```

This is additive — existing subscribers to `OrchestratorProgress` continue to work. New code can subscribe to `RunStateUpdated` instead.

**Step 2: Move `_current_operation` updates under lock**

In the controller's `_on_operation_change` callback (search for where `self._current_operation` is written), wrap the write in `_progress_lock`:

```python
def _on_operation_change(self, operation: str) -> None:
    with self._progress_lock:
        self._current_operation = operation
```

**Step 3: Run tests**

Run: `pytest tests/unit/orchestrator/ -v --timeout=30`
Expected: All pass

**Step 4: Commit**

```
feat: Publish RunStateUpdated event, fix _current_operation data race
```

---

## Phase 3: Monitoring Dashboard

### Task 7: Replace SparklineWidget with pyqtgraph plots

**Files:**
- Modify: `software/src/squid/ui/widgets/orchestrator/orchestrator_widget.py:122-228, 515-537, 691-790`

**Step 1: Replace SparklineWidget and MetricPlotCard with pyqtgraph-based AccumulatingPlot**

Remove `SparklineWidget` (lines 122-194) and `MetricPlotCard` (lines 196-228). Replace with:

```python
import pyqtgraph as pg


class TimeAxisItem(pg.AxisItem):
    """X-axis that shows elapsed time as human-readable labels."""

    def tickStrings(self, values, scale, spacing):
        result = []
        for v in values:
            seconds = int(v)
            if seconds < 60:
                result.append(f"{seconds}s")
            elif seconds < 3600:
                result.append(f"{seconds // 60}m")
            else:
                h = seconds // 3600
                m = (seconds % 3600) // 60
                result.append(f"{h}h{m:02d}m" if m else f"{h}h")
        return result


class AccumulatingPlot(QWidget):
    """A pyqtgraph plot that accumulates data over the full experiment run.

    X-axis is wall-clock seconds since run start. Y-axis auto-scales
    with labeled units.
    """

    def __init__(
        self,
        title: str,
        y_label: str,
        line_color: str = "#4FC3F7",
        y_range: tuple = None,  # (min, max) or None for auto
        parent=None,
    ):
        super().__init__(parent)
        self._x_data: list[float] = []
        self._y_data: list[float] = []
        self._run_start: float = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        time_axis = TimeAxisItem(orientation="bottom")
        self._plot_widget = pg.PlotWidget(axisItems={"bottom": time_axis})
        self._plot_widget.setBackground("#171b21")
        self._plot_widget.setTitle(title, color="#b8c1cc", size="10pt")
        self._plot_widget.setLabel("left", y_label, color="#7f8b99")
        self._plot_widget.setLabel("bottom", "Time", color="#7f8b99")
        self._plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self._plot_widget.setMinimumHeight(120)

        if y_range is not None:
            self._plot_widget.setYRange(y_range[0], y_range[1])

        pen = pg.mkPen(color=line_color, width=2)
        self._curve = self._plot_widget.plot(pen=pen)

        # Current value marker
        self._marker = pg.ScatterPlotItem(
            size=8, pen=pg.mkPen(None), brush=pg.mkBrush(line_color)
        )
        self._plot_widget.addItem(self._marker)

        # Current value text
        self._value_text = pg.TextItem(color=line_color, anchor=(1, 1))
        self._plot_widget.addItem(self._value_text)

        layout.addWidget(self._plot_widget)

    def set_run_start(self, t: float):
        self._run_start = t
        self._x_data.clear()
        self._y_data.clear()

    def append(self, timestamp: float, value: float):
        elapsed = timestamp - self._run_start if self._run_start > 0 else 0.0
        self._x_data.append(elapsed)
        self._y_data.append(value)
        self._curve.setData(self._x_data, self._y_data)
        if self._x_data:
            self._marker.setData([self._x_data[-1]], [self._y_data[-1]])
            self._value_text.setPos(self._x_data[-1], self._y_data[-1])
            self._value_text.setText(f"{value:.1f}")

    def add_horizontal_line(self, y: float, color: str = "#ff6b6b", label: str = ""):
        pen = pg.mkPen(color=color, width=1, style=Qt.DashLine)
        line = pg.InfiniteLine(pos=y, angle=0, pen=pen, label=label,
                               labelOpts={"color": color, "position": 0.95})
        self._plot_widget.addItem(line)

    def clear_data(self):
        self._x_data.clear()
        self._y_data.clear()
        self._curve.setData([], [])
        self._marker.setData([], [])
```

**Step 2: Replace _create_metrics_section to use AccumulatingPlot**

Replace the 3 MetricPlotCards + SubsystemBreakdownWidget with 3 AccumulatingPlots:

```python
def _create_metrics_section(self):
    frame = QFrame()
    frame.setStyleSheet("background-color: #20252b; border-radius: 6px;")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(4)

    self._progress_plot = AccumulatingPlot(
        "Progress", "Complete %", line_color="#4FC3F7", y_range=(0, 100)
    )
    self._throughput_plot = AccumulatingPlot(
        "Throughput", "FOVs / min", line_color="#81C784"
    )
    self._focus_plot = AccumulatingPlot(
        "Focus Error", "Error (µm)", line_color="#FFB74D"
    )

    layout.addWidget(self._progress_plot)
    layout.addWidget(self._throughput_plot)
    layout.addWidget(self._focus_plot)
    layout.addWidget(self._subsystem_widget)

    return frame
```

**Step 3: Update event handlers to feed the new plots**

Subscribe to `RunStateUpdated` and feed the plots:

```python
@handles(RunStateUpdated)
def _on_run_state_updated(self, event):
    QMetaObject.invokeMethod(
        self, "_on_run_state_ui",
        Qt.QueuedConnection,
        Q_ARG(object, event.run_state),
    )

@pyqtSlot(object)
def _on_run_state_ui(self, rs):
    import time
    now = time.monotonic()

    # Feed plots
    self._progress_plot.append(now, rs.progress_percent)

    if rs.throughput_fov_per_min is not None:
        self._throughput_plot.append(now, rs.throughput_fov_per_min)

    if rs.focus_error_um is not None:
        self._focus_plot.append(now, rs.focus_error_um)

    # Update subsystem breakdown
    if rs.subsystem_seconds:
        self._subsystem_widget.set_data(rs.subsystem_seconds)
```

Keep the existing `_on_progress_updated_ui` handler for backward compatibility with the status labels and progress bar — those still read from `OrchestratorProgress` until we fully remove it.

**Step 4: Run app in simulation to visual-check**

Run: `python main_hcs.py --simulation`
Check: Plots should appear and accumulate data during a run

**Step 5: Commit**

```
feat: Replace sparklines with pyqtgraph accumulating plots
```

---

### Task 8: Build health strip (replaces 7 mini-cards)

**Files:**
- Modify: `software/src/squid/ui/widgets/orchestrator/orchestrator_widget.py:360-412`

**Step 1: Replace _create_status_section with health strip**

Replace the 7 mini-cards with 3 zones:

```python
def _create_status_section(self):
    frame = QFrame()
    frame.setStyleSheet("background-color: #20252b; border-radius: 6px;")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(10, 8, 10, 8)
    layout.setSpacing(12)

    # ── State Zone ──
    state_zone = QVBoxLayout()
    self._status_label = QLabel("IDLE")
    self._status_label.setStyleSheet(
        "font-size: 18px; font-weight: 700; color: #edf2f7;"
    )
    self._time_label = QLabel("")
    self._time_label.setStyleSheet("font-size: 11px; color: #7f8b99;")
    state_zone.addWidget(self._status_label)
    state_zone.addWidget(self._time_label)
    layout.addLayout(state_zone, 2)

    # ── Position Zone ──
    pos_zone = QVBoxLayout()
    self._round_label = QLabel("Round: —")
    self._round_label.setStyleSheet("font-size: 12px; color: #b8c1cc;")
    self._step_label = QLabel("Step: —")
    self._step_label.setStyleSheet("font-size: 12px; color: #b8c1cc;")
    self._fov_label = QLabel("FOV: —")
    self._fov_label.setStyleSheet("font-size: 12px; color: #b8c1cc;")
    self._attempt_label = QLabel("")
    self._attempt_label.setStyleSheet("font-size: 11px; color: #FFB74D;")
    self._attempt_label.hide()
    pos_zone.addWidget(self._round_label)
    pos_zone.addWidget(self._step_label)
    pos_zone.addWidget(self._fov_label)
    pos_zone.addWidget(self._attempt_label)
    layout.addLayout(pos_zone, 3)

    # ── Health Zone ──
    health_zone = QVBoxLayout()

    focus_row = QHBoxLayout()
    self._focus_dot = QLabel("●")
    self._focus_dot.setStyleSheet("font-size: 14px; color: #888888;")
    self._focus_label = QLabel("Focus: —")
    self._focus_label.setStyleSheet("font-size: 12px; color: #b8c1cc;")
    focus_row.addWidget(self._focus_dot)
    focus_row.addWidget(self._focus_label)
    focus_row.addStretch()

    self._throughput_label = QLabel("Throughput: —")
    self._throughput_label.setStyleSheet("font-size: 12px; color: #b8c1cc;")
    self._warnings_label = QLabel("")
    self._warnings_label.setStyleSheet("font-size: 11px; color: #7f8b99;")

    health_zone.addLayout(focus_row)
    health_zone.addWidget(self._throughput_label)
    health_zone.addWidget(self._warnings_label)
    layout.addLayout(health_zone, 2)

    return frame
```

**Step 2: Update _on_run_state_ui to populate health strip**

```python
@pyqtSlot(object)
def _on_run_state_ui(self, rs):
    import time
    now = time.monotonic()

    # ... plot updates from Task 7 ...

    # ── Health strip updates ──
    # Time
    elapsed_str = self._format_duration(rs.elapsed_s)
    eta_str = f"~{self._format_duration(rs.eta_s)} remaining" if rs.eta_s else ""
    self._time_label.setText(f"{elapsed_str} elapsed  {eta_str}")

    # Position
    self._round_label.setText(
        f"Round: {rs.round_index + 1} / {rs.total_rounds}  ({rs.round_name})"
    )
    self._step_label.setText(
        f"Step: {rs.step_index + 1} / {rs.total_steps}  ({rs.step_type}: {rs.step_label})"
    )
    self._fov_label.setText(f"FOV: {rs.fov_index + 1} / {rs.total_fovs}" if rs.total_fovs > 0 else "FOV: —")

    if rs.attempt > 1:
        self._attempt_label.setText(f"Attempt {rs.attempt} (retry)")
        self._attempt_label.show()
    else:
        self._attempt_label.hide()

    # Health
    focus_colors = {"locked": "#66BB6A", "searching": "#FFA726", "lost": "#EF5350"}
    if rs.focus_status:
        color = focus_colors.get(rs.focus_status, "#888888")
        self._focus_dot.setStyleSheet(f"font-size: 14px; color: {color};")
        err = f" ({rs.focus_error_um:.2f} µm)" if rs.focus_error_um is not None else ""
        self._focus_label.setText(f"Focus: {rs.focus_status}{err}")
    else:
        self._focus_dot.setStyleSheet("font-size: 14px; color: #888888;")
        self._focus_label.setText("Focus: —")

    if rs.throughput_fov_per_min is not None:
        self._throughput_label.setText(f"Throughput: {rs.throughput_fov_per_min:.1f} FOVs/min")
    else:
        self._throughput_label.setText("Throughput: —")

def _format_duration(self, seconds):
    if seconds is None or seconds < 0:
        return "—"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    h = s // 3600
    m = (s % 3600) // 60
    return f"{h}h {m:02d}m"
```

**Step 3: Commit**

```
feat: Replace 7 mini-cards with health strip (state/position/health zones)
```

---

### Task 9: Fix SubsystemBreakdownWidget legend overflow

**Files:**
- Modify: `software/src/squid/ui/widgets/orchestrator/orchestrator_widget.py:231-276`

**Step 1: Increase minimum height and wrap legend**

In `SubsystemBreakdownWidget.__init__`, change:
```python
self.setMinimumHeight(90)  # was 72
```

In `paintEvent`, replace the fixed `step = max(88, ...)` legend layout with wrapping:
```python
# Legend — wrap to multiple rows if needed
max_item_width = 100
items_per_row = max(1, rect.width() // max_item_width)
legend_y = bar_rect.bottom() + 14
for i, (name, secs) in enumerate(self._values.items()):
    col = i % items_per_row
    row = i // items_per_row
    x = rect.left() + col * max_item_width
    y = legend_y + row * 16
    color = self._colors.get(name, QColor("#90A4AE"))
    painter.setBrush(color)
    painter.setPen(Qt.NoPen)
    painter.drawRect(x, y, 8, 8)
    painter.setPen(QPen(QColor("#b8c1cc"), 1))
    mins = int(secs) // 60
    sec = int(secs) % 60
    painter.drawText(x + 12, y + 8, f"{name}: {mins}m{sec:02d}s")
```

**Step 2: Commit**

```
fix: Wrap subsystem legend to prevent overflow on narrow panels
```

---

### Task 10: Fix dark-theme colors in validation dialog

**Files:**
- Modify: `software/src/squid/ui/widgets/orchestrator/validation_dialog.py`

**Step 1: Replace light-theme colors**

Find and replace:
- `#FFEBEE` → `#2d1a1a` (error background)
- `#f44336` → `#ff6b6b` (error text)
- `#FFF3E0` → `#2d2213` (warning background)
- `#E65100` → `#ffb74d` (warning text)

**Step 2: Commit**

```
fix: Use dark-theme-compatible colors in validation dialog
```

---

### Task 11: Fix protocol_loader_dialog — load all FOV sets, remove dead signal

**Files:**
- Modify: `software/src/squid/ui/widgets/orchestrator/protocol_loader_dialog.py`

**Step 1: Load all FOV sets, not just the first**

In `_load_fov_positions` (or wherever `fov_sets` is iterated), replace the `next(iter(...))` pattern with a loop over all sets:

```python
# Load all FOV sets
for fov_name, fov_path in self._current_protocol.fov_sets.items():
    if fov_path and os.path.exists(fov_path):
        positions = self._parse_fov_csv(fov_path)
        self._fov_positions.update(positions)
```

**Step 2: Remove or comment the dead `protocol_selected` signal**

If `protocol_selected` (line 42) is never connected anywhere, remove the signal declaration and the `self.protocol_selected.emit(...)` call in `_on_start_clicked`. The dialog result is consumed via `exec_()` + getters.

**Step 3: Run tests**

Run: `pytest tests/unit/squid/ui/test_protocol_loader_dialog.py -v`
Expected: All pass

**Step 4: Commit**

```
fix: Load all FOV sets in protocol loader, remove dead signal
```

---

### Task 12: Initialize plots on experiment start, add focus threshold lines

**Files:**
- Modify: `software/src/squid/ui/widgets/orchestrator/orchestrator_widget.py`

**Step 1: Reset plots when experiment starts**

In the `_on_state_changed_ui` handler (or wherever `OrchestratorStateChanged` is processed), when transitioning to RUNNING:

```python
if new_state == "RUNNING" and old_state == "IDLE":
    import time
    now = time.monotonic()
    self._progress_plot.set_run_start(now)
    self._throughput_plot.set_run_start(now)
    self._focus_plot.set_run_start(now)
    self._progress_plot.clear_data()
    self._throughput_plot.clear_data()
    self._focus_plot.clear_data()
```

**Step 2: Add focus threshold lines from protocol validation**

When `ProtocolValidationComplete` is received and the protocol uses focus lock, add horizontal reference lines:

```python
# In _on_validation_complete or when protocol is loaded:
# Check if any imaging protocol uses focus_lock
for config in protocol.imaging_protocols.values():
    if hasattr(config, 'focus_gate') and config.focus_gate:
        fl = getattr(config.focus_gate, 'focus_lock', None)
        if fl:
            self._focus_plot.add_horizontal_line(
                fl.acquire_threshold_um, "#66BB6A", "acquire"
            )
            self._focus_plot.add_horizontal_line(
                fl.maintain_threshold_um, "#FFA726", "maintain"
            )
            break
```

**Step 3: Commit**

```
feat: Reset plots on run start, add focus threshold reference lines
```

---

## Final Verification

### Task 13: Run full test suite and visual check

**Step 1: Run all orchestrator tests**

```bash
cd software
pytest tests/unit/orchestrator/ -v --timeout=30
```
Expected: All pass

**Step 2: Run e2e smoke tests**

```bash
SQUID_TEST_SPEEDUP=1000 pytest tests/e2e/tests/test_protocol_smoke.py -v --timeout=60
```
Expected: All pass

**Step 3: Visual check in simulation**

```bash
python main_hcs.py --simulation
```
Load a protocol, validate, start. Verify:
- Health strip shows state/position/health
- Plots accumulate over time with labeled axes
- Focus plot shows threshold lines (if using focus lock)
- Throughput shows FOVs/min
- Dark theme colors look correct in validation dialog

**Step 4: Commit**

```
test: Verify orchestrator improvements end-to-end
```
