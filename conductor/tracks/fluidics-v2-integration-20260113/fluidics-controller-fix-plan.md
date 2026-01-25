# FluidicsController Fix Plan

## Summary

Fix the FluidicsController and FluidicsWidget implementation to address architectural issues. The main problems are:
1. FluidicsController's internal simulation mode bypasses FluidicsService, so no events are published
2. SimulatedFluidicsController with `simulate_timing=False` doesn't emit progress callbacks
3. Abort handling treats intentional pause/skip as failures

## Critical Architecture Issue

**Root Cause:** FluidicsController has TWO simulation paths that don't publish events:

1. **Internal simulation (lines 662-668):** When `is_available=False` (no FluidicsService or service has no driver), the controller simulates steps internally without calling FluidicsService - NO EVENTS PUBLISHED.

2. **SimulatedFluidicsController with `simulate_timing=False`:** Even when FluidicsService exists, the simulated driver returns early from `_simulate_delay()` without calling progress callbacks (line 215-219).

**Result:** GUI doesn't receive FluidicsOperationProgress, FluidicsPhaseChanged, or FluidicsStatusChanged events during protocol execution.

---

## Issues to Fix

### HIGH Priority

#### 1. FluidicsController Simulation Mode Bypasses Events
**Location:** `fluidics_controller.py:662-668`

```python
if not self.is_available:
    # Simulation mode - NO EVENTS PUBLISHED!
    _log.debug(f"[SIMULATED] {step.get_description()}")
    self._wait_with_cancel(...)
    return True
```

**Fix:** When FluidicsService is unavailable, the FluidicsController should still publish protocol-level events (FluidicsProtocolStepStarted, etc.). For operation-level events, either:
- Option A: Publish synthetic FluidicsOperationProgress events from controller
- Option B: Use a SimulatedFluidicsService that publishes events
- **Recommended: Option A** - simpler, keeps simulation self-contained

#### 2. SimulatedFluidicsController Doesn't Emit Progress Callbacks
**Location:** `simulation.py:215-219`

```python
if not self._simulate_timing:
    if end_syringe_vol is not None:
        self._syringe_volume_ul = end_syringe_vol
    return  # NO PROGRESS CALLBACKS!
```

**Fix:** Even when `simulate_timing=False`, emit at least initial (0%) and final (100%) progress callbacks so GUI updates.

#### 3. Protocols Not Loaded into FluidicsController
**Location:** `orchestrator_controller.py` lines 431-442

**Status:** ALREADY FIXED - The orchestrator correctly calls `self._fluidics_controller.load_protocols(fluidics_file)` at line 439 when `fluidics_protocols_file` is specified in the experiment protocol.

**Remaining Issue:** Protocols loaded via GUI (FluidicsWidget file picker) should also be visible to the FluidicsController. Currently this works via `LoadFluidicsProtocolsCommand` event -> `_on_load_protocols_command()` handler.

### MEDIUM Priority

#### 4. Pause/Skip Abort Handling
**Location:** `fluidics_controller.py:589-615`

**Problem:** When pause()/skip() calls abort(), the operation returns False, which is treated as failure.

**Fix:** Check abort signals before treating operation failure as actual failure:
```python
if not result and (self._pause_event.is_set() or self._skip_event.is_set() or self._stop_event.is_set()):
    # Intentional abort, not failure
    return True  # or handle appropriately
```

#### 5. GUI Not Receiving Real-Time Updates
**Location:** `fluidics.py` widget

**Symptoms:**
- Current port not showing -> Depends on FluidicsPhaseChanged/FluidicsStatusChanged
- Operation progress bar not updating -> Depends on FluidicsOperationProgress
- Syringe volume not updating -> Depends on progress/status events

**Root Cause:** Events not being published (see issues #1 and #2 above)

---

## Files to Modify

| File | Changes |
|------|---------|
| `fluidics_controller.py` | Add synthetic event publishing in simulation mode; fix abort handling |
| `simulation.py` | Emit progress callbacks even with `simulate_timing=False` |

---

## Implementation Steps

### Step 1: Fix SimulatedFluidicsController Progress Callbacks
In `simulation.py`, modify `_simulate_delay()` to always emit progress callbacks:

```python
def _simulate_delay(self, volume_ul, flow_rate_ul_per_min, ...):
    # Always emit initial progress
    if self._progress_callback:
        self._progress_callback(operation, 0.0, 0.0, estimated_duration, start_syringe_vol)

    if not self._simulate_timing:
        if end_syringe_vol is not None:
            self._syringe_volume_ul = end_syringe_vol
        # Emit final progress even without timing
        if self._progress_callback:
            self._progress_callback(operation, 100.0, 0.0, 0.0, end_syringe_vol)
        return

    # ... rest of timed simulation
```

### Step 2: Fix FluidicsController Internal Simulation
In `fluidics_controller.py`, when `is_available=False`, publish synthetic events:

```python
def _execute_step(self, step: FluidicsProtocolStep) -> bool:
    if self._stop_event.is_set():
        return False

    if not self.is_available:
        # Publish synthetic operation events even in simulation
        if self._event_bus:
            self._event_bus.publish(FluidicsOperationStarted(
                operation=step.operation.value,
                solution=step.solution,
                volume_ul=step.volume_ul,
                ...
            ))

        # Simulate duration
        self._wait_with_cancel(min(step.estimated_duration_s(), 2.0))

        if self._event_bus:
            self._event_bus.publish(FluidicsOperationCompleted(
                operation=step.operation.value,
                success=True,
                ...
            ))
        return True

    # ... real execution via FluidicsService
```

### Step 3: Fix Abort Handling in Worker Loop
In `fluidics_controller.py`, modify `_run_protocol_worker()` to handle aborted steps:

The current code at lines 589-615 already checks `_stop_event`, `_skip_event`, and `_pause_event` after `_execute_step()` returns False. Verify this logic is correct.

---

## Verification

1. **Unit Tests:**
   - Test FluidicsController simulation mode publishes events
   - Test SimulatedFluidicsController emits progress callbacks with `simulate_timing=False`
   - Test protocol loading flows through to FluidicsController

2. **Integration Test:**
   - Run protocol in simulation mode
   - Verify GUI receives FluidicsOperationStarted/Progress/Completed events
   - Verify syringe volume updates
   - Verify current port/solution display updates

3. **Manual Test:**
   - Launch GUI with `--simulation`
   - Load fluidics protocols via UI
   - Run a protocol
   - Verify operation progress bar updates
   - Verify syringe gauge updates
   - Verify current port display shows correct values
   - Test pause/resume/skip/stop buttons
