# Continuous Focus Lock - Implementation Guide

## Overview

This document describes how to add continuous "focus lock" functionality to the laser autofocus system. Focus lock runs a background control loop that continuously measures displacement and applies Z corrections to maintain focus during live imaging.

**Current State:** `LaserAutofocusController` only has one-shot operations (`measure_displacement()`, `move_to_target()`).

**Goal:** Add a background loop that continuously maintains focus without blocking the UI.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Main Thread                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐  │
│  │ Focus Lock  │───▶│   Start/    │───▶│ Qt Signals for UI   │  │
│  │   Widget    │    │    Stop     │    │ (displacement, status)│  │
│  └─────────────┘    └─────────────┘    └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Focus Lock Thread                           │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐  │
│  │  Measure    │───▶│  Calculate  │───▶│  Apply Z Correction │  │
│  │Displacement │    │    Error    │    │  (piezo or stage)   │  │
│  └─────────────┘    └─────────────┘    └─────────────────────┘  │
│         │                                        │               │
│         └────────────── Loop ────────────────────┘               │
└─────────────────────────────────────────────────────────────────┘
```

**Key Components:**

| Component | Purpose |
|-----------|---------|
| `ThreadSafeFlag` | Clean start/stop control |
| `ThreadSafeValue` | Share target displacement and current error |
| Qt Signals | Marshal UI updates to main thread |
| Daemon Thread | Run control loop without blocking UI |

---

## Implementation Tasks

### Task 1: Add Focus Lock State to Controller

**File:** `control/core/laser_auto_focus_controller.py`

**Add imports:**
```python
import threading
from squid.utils.thread_safe_state import ThreadSafeFlag, ThreadSafeValue
```

**Add to `__init__`:**
```python
def __init__(self, ...):
    # ... existing init ...

    # Focus lock state
    self._focus_lock_running = ThreadSafeFlag(initial=False)
    self._focus_lock_thread: Optional[threading.Thread] = None
    self._target_displacement_um = ThreadSafeValue[float](0.0)
    self._current_error_um = ThreadSafeValue[float](0.0)

    # Control parameters (can be tuned)
    self._lock_interval_ms = 50  # 20 Hz update rate
    self._max_correction_um = 1.0  # Max correction per iteration
    self._deadband_um = 0.1  # Don't correct below this error
```

**Add signal:**
```python
class LaserAutofocusController(QObject):
    # Existing signals...
    image_to_display = Signal(np.ndarray)
    signal_displacement_um = Signal(float)
    signal_cross_correlation = Signal(float)
    signal_piezo_position_update = Signal()

    # New signal
    signal_focus_lock_status = Signal(bool)  # True = running, False = stopped
```

**Test:**
```python
def test_focus_lock_state_initialized():
    """Focus lock state should be initialized to stopped."""
    controller = create_test_controller()

    assert not controller._focus_lock_running.is_set()
    assert controller._focus_lock_thread is None
    assert controller._target_displacement_um.get() == 0.0
```

**Commit:** `Add focus lock state variables to LaserAutofocusController`

---

### Task 2: Implement start_focus_lock()

**File:** `control/core/laser_auto_focus_controller.py`

**Add method:**
```python
def start_focus_lock(self, target_um: float = 0.0) -> bool:
    """Start continuous focus lock in background.

    The focus lock runs a background loop that:
    1. Measures current displacement from reference
    2. Calculates error from target
    3. Applies Z correction (via piezo or stage)
    4. Repeats at ~20 Hz

    Args:
        target_um: Target displacement from reference position.
                   Usually 0.0 to maintain focus at reference.
                   Can be non-zero for offset focusing.

    Returns:
        bool: True if started successfully, False if:
              - Not initialized
              - No reference set
              - Already running
    """
    if not self.is_initialized:
        self._log.error("Cannot start focus lock - laser AF not initialized")
        return False

    if not self.laser_af_properties.has_reference:
        self._log.error("Cannot start focus lock - no reference position set")
        return False

    if self._focus_lock_running.is_set():
        self._log.warning("Focus lock already running")
        return False

    self._target_displacement_um.set(target_um)
    self._focus_lock_running.set()

    self._focus_lock_thread = threading.Thread(
        target=self._focus_lock_loop,
        name="FocusLockThread",
        daemon=True
    )
    self._focus_lock_thread.start()

    self.signal_focus_lock_status.emit(True)
    self._log.info(f"Focus lock started with target={target_um:.2f} μm")
    return True
```

**Test:**
```python
def test_start_focus_lock_requires_initialization():
    """start_focus_lock should fail if not initialized."""
    controller = create_test_controller()
    controller.is_initialized = False

    result = controller.start_focus_lock()

    assert result is False
    assert not controller._focus_lock_running.is_set()


def test_start_focus_lock_requires_reference():
    """start_focus_lock should fail if no reference set."""
    controller = create_test_controller()
    controller.is_initialized = True
    controller.laser_af_properties = controller.laser_af_properties.model_copy(
        update={"has_reference": False}
    )

    result = controller.start_focus_lock()

    assert result is False


def test_start_focus_lock_prevents_double_start():
    """start_focus_lock should fail if already running."""
    controller = create_test_controller()
    controller.is_initialized = True
    controller.laser_af_properties = controller.laser_af_properties.model_copy(
        update={"has_reference": True}
    )

    # Mock the loop to not actually run
    controller._focus_lock_loop = lambda: None

    assert controller.start_focus_lock() is True
    assert controller.start_focus_lock() is False  # Second call fails
```

**Commit:** `Add start_focus_lock() method`

---

### Task 3: Implement stop_focus_lock()

**File:** `control/core/laser_auto_focus_controller.py`

**Add method:**
```python
def stop_focus_lock(self) -> None:
    """Stop the focus lock loop.

    This method:
    1. Signals the loop to stop
    2. Waits for the thread to exit (with timeout)
    3. Emits status signal

    Safe to call even if focus lock is not running.
    """
    if not self._focus_lock_running.is_set():
        return

    self._log.info("Stopping focus lock...")
    self._focus_lock_running.clear()

    if self._focus_lock_thread is not None:
        self._focus_lock_thread.join(timeout=2.0)
        if self._focus_lock_thread.is_alive():
            self._log.warning("Focus lock thread did not exit cleanly")
        self._focus_lock_thread = None

    self.signal_focus_lock_status.emit(False)
    self._log.info("Focus lock stopped")
```

**Test:**
```python
def test_stop_focus_lock_clears_flag():
    """stop_focus_lock should clear the running flag."""
    controller = create_test_controller()
    controller._focus_lock_running.set()

    controller.stop_focus_lock()

    assert not controller._focus_lock_running.is_set()


def test_stop_focus_lock_safe_when_not_running():
    """stop_focus_lock should be safe to call when not running."""
    controller = create_test_controller()

    # Should not raise
    controller.stop_focus_lock()
```

**Commit:** `Add stop_focus_lock() method`

---

### Task 4: Implement Focus Lock Loop

**File:** `control/core/laser_auto_focus_controller.py`

**Add method:**
```python
def _focus_lock_loop(self) -> None:
    """Background loop that continuously maintains focus.

    This runs on a daemon thread and:
    1. Turns on the AF laser
    2. Loops until stopped:
       a. Measures spot displacement
       b. Calculates error from target
       c. Applies clamped Z correction
       d. Sleeps to maintain loop rate
    3. Turns off laser on exit (in finally block)
    """
    self._log.info("Focus lock loop starting")

    # Turn laser on for continuous operation
    try:
        self.microcontroller.turn_on_AF_laser()
        self.microcontroller.wait_till_operation_is_completed()
    except TimeoutError:
        self._log.error("Failed to turn on laser for focus lock")
        self._focus_lock_running.clear()
        self.signal_focus_lock_status.emit(False)
        return

    try:
        while self._focus_lock_running.is_set():
            loop_start = time.perf_counter()

            try:
                self._focus_lock_iteration()
            except Exception as e:
                self._log.error(f"Focus lock iteration error: {e}", exc_info=True)
                # Continue running - don't let one bad frame stop the lock

            # Maintain loop rate
            elapsed = time.perf_counter() - loop_start
            sleep_time = (self._lock_interval_ms / 1000.0) - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    finally:
        # Always turn off laser when loop exits
        try:
            self.microcontroller.turn_off_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.error("Failed to turn off laser after focus lock stopped")

    self._log.info("Focus lock loop exited")


def _focus_lock_iteration(self) -> None:
    """Single iteration of the focus lock loop.

    Separated from loop for testability.
    """
    # Get current spot position (laser already on)
    result = self._get_laser_spot_centroid()

    if result is None:
        self._log.debug("No spot detected in focus lock iteration")
        return

    x, y = result

    # Calculate displacement from reference
    current_um = (x - self.laser_af_properties.x_reference) * self.laser_af_properties.pixel_to_um
    target_um = self._target_displacement_um.get() or 0.0
    error_um = target_um - current_um

    # Update shared state and emit signal
    self._current_error_um.set(error_um)
    self.signal_displacement_um.emit(current_um)

    # Apply correction if error exceeds deadband
    if abs(error_um) > self._deadband_um:
        # Clamp correction to prevent large jumps from bad readings
        correction = max(-self._max_correction_um,
                        min(self._max_correction_um, error_um))
        self._move_z(correction)
        self._log.debug(f"Focus lock correction: {correction:.3f} μm (error: {error_um:.3f} μm)")
```

**Test:**
```python
def test_focus_lock_iteration_calculates_error():
    """Focus lock iteration should calculate error correctly."""
    controller = create_test_controller()
    controller.laser_af_properties = controller.laser_af_properties.model_copy(
        update={"x_reference": 100.0, "pixel_to_um": 0.5, "has_reference": True}
    )
    controller._target_displacement_um.set(0.0)

    # Mock spot detection to return x=102 (2 pixels = 1 μm displacement)
    controller._get_laser_spot_centroid = Mock(return_value=(102.0, 50.0))
    controller._move_z = Mock()

    controller._focus_lock_iteration()

    # Error should be -1.0 μm (target 0, current 1.0)
    assert abs(controller._current_error_um.get() - (-1.0)) < 0.01
    controller._move_z.assert_called()


def test_focus_lock_iteration_respects_deadband():
    """Focus lock should not correct within deadband."""
    controller = create_test_controller()
    controller._deadband_um = 0.1
    controller.laser_af_properties = controller.laser_af_properties.model_copy(
        update={"x_reference": 100.0, "pixel_to_um": 0.5, "has_reference": True}
    )
    controller._target_displacement_um.set(0.0)

    # Mock spot at reference (0 error)
    controller._get_laser_spot_centroid = Mock(return_value=(100.0, 50.0))
    controller._move_z = Mock()

    controller._focus_lock_iteration()

    controller._move_z.assert_not_called()


def test_focus_lock_iteration_clamps_correction():
    """Focus lock should clamp large corrections."""
    controller = create_test_controller()
    controller._max_correction_um = 1.0
    controller.laser_af_properties = controller.laser_af_properties.model_copy(
        update={"x_reference": 100.0, "pixel_to_um": 0.5, "has_reference": True}
    )
    controller._target_displacement_um.set(0.0)

    # Mock spot at 110 pixels = 5 μm displacement, -5 μm error
    controller._get_laser_spot_centroid = Mock(return_value=(110.0, 50.0))
    controller._move_z = Mock()

    controller._focus_lock_iteration()

    # Should clamp to -1.0 μm, not -5.0
    controller._move_z.assert_called_with(-1.0)
```

**Commit:** `Implement focus lock background loop`

---

### Task 5: Add Property Accessors

**File:** `control/core/laser_auto_focus_controller.py`

**Add properties:**
```python
@property
def is_focus_locked(self) -> bool:
    """Check if focus lock is currently active."""
    return self._focus_lock_running.is_set()

@property
def focus_error_um(self) -> float:
    """Get current focus error in micrometers.

    Returns the difference between target and measured displacement.
    Positive = sample too close, negative = sample too far.
    """
    return self._current_error_um.get() or 0.0

@property
def focus_lock_target_um(self) -> float:
    """Get the current focus lock target displacement."""
    return self._target_displacement_um.get() or 0.0

def set_focus_lock_target(self, target_um: float) -> None:
    """Update the focus lock target while running.

    Can be called while focus lock is active to shift the focus plane.

    Args:
        target_um: New target displacement from reference
    """
    self._target_displacement_um.set(target_um)
    self._log.info(f"Focus lock target updated to {target_um:.2f} μm")
```

**Commit:** `Add focus lock property accessors`

---

### Task 6: Add Cleanup on Controller Destruction

**File:** `control/core/laser_auto_focus_controller.py`

**Ensure cleanup:**
```python
def cleanup(self) -> None:
    """Clean up resources. Call before destroying controller."""
    self.stop_focus_lock()
```

Or if using Qt parent/child lifecycle:
```python
def __del__(self):
    self.stop_focus_lock()
```

**Commit:** `Add cleanup to stop focus lock on controller destruction`

---

### Task 7: Add Focus Lock Widget Controls

**File:** `control/widgets/autofocus.py` (or wherever the laser AF widget is)

**Add UI controls:**
```python
class LaserAutofocusWidget(QWidget):
    def __init__(self, controller: LaserAutofocusController, ...):
        super().__init__()
        self.controller = controller

        # ... existing UI setup ...

        # Focus lock controls
        self.btn_focus_lock = QPushButton("Focus Lock")
        self.btn_focus_lock.setCheckable(True)
        self.btn_focus_lock.toggled.connect(self._on_focus_lock_toggled)

        self.label_focus_status = QLabel("Unlocked")
        self.label_focus_error = QLabel("Error: -- μm")

        # Connect signals
        self.controller.signal_focus_lock_status.connect(self._on_focus_lock_status_changed)
        self.controller.signal_displacement_um.connect(self._on_displacement_updated)

    def _on_focus_lock_toggled(self, checked: bool) -> None:
        """Handle focus lock button toggle."""
        if checked:
            success = self.controller.start_focus_lock(target_um=0.0)
            if not success:
                # Reset button state if start failed
                self.btn_focus_lock.blockSignals(True)
                self.btn_focus_lock.setChecked(False)
                self.btn_focus_lock.blockSignals(False)
        else:
            self.controller.stop_focus_lock()

    def _on_focus_lock_status_changed(self, is_locked: bool) -> None:
        """Update UI when focus lock status changes."""
        self.label_focus_status.setText("LOCKED" if is_locked else "Unlocked")
        self.label_focus_status.setStyleSheet(
            "color: green; font-weight: bold;" if is_locked else ""
        )

        # Sync button state (in case stopped externally)
        self.btn_focus_lock.blockSignals(True)
        self.btn_focus_lock.setChecked(is_locked)
        self.btn_focus_lock.blockSignals(False)

    def _on_displacement_updated(self, displacement_um: float) -> None:
        """Update error display."""
        error = self.controller.focus_error_um
        self.label_focus_error.setText(f"Error: {error:+.3f} μm")
```

**Commit:** `Add focus lock controls to laser autofocus widget`

---

### Task 8: Coordinate with Stage Movement

**File:** Where stage moves are initiated (e.g., `control/core/multi_point_worker.py`)

For acquisitions that move XY, pause focus lock during moves:

```python
def _move_to_position(self, x: float, y: float) -> None:
    """Move to position, pausing focus lock if active."""
    was_locked = self.laser_af_controller.is_focus_locked

    if was_locked:
        self.laser_af_controller.stop_focus_lock()

    # Move stage
    self.stage_service.move_to(x, y, blocking=True)

    # Re-enable focus lock
    if was_locked:
        # Wait for stage to settle
        time.sleep(0.1)
        self.laser_af_controller.start_focus_lock()
```

**Alternative - Add pause/resume methods:**
```python
# In LaserAutofocusController

def pause_focus_lock(self) -> bool:
    """Temporarily pause focus lock. Returns True if was running."""
    if self._focus_lock_running.is_set():
        self.stop_focus_lock()
        return True
    return False

def resume_focus_lock(self) -> None:
    """Resume focus lock after pause."""
    if not self._focus_lock_running.is_set():
        self.start_focus_lock(self._target_displacement_um.get() or 0.0)
```

**Commit:** `Add focus lock coordination with stage movement`

---

## Control Parameters

These parameters can be tuned based on your hardware:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `_lock_interval_ms` | 50 | Loop rate (50ms = 20 Hz) |
| `_max_correction_um` | 1.0 | Max Z correction per iteration |
| `_deadband_um` | 0.1 | Minimum error to trigger correction |

**Tuning guidance:**

- **Loop rate**: Limited by camera frame rate and processing time. 20 Hz is typical.
- **Max correction**: Prevents large jumps from bad readings. Should be ~2-3x typical drift rate.
- **Deadband**: Prevents jitter from noise. Should be ~1-2x measurement noise floor.

---

## Testing Strategy

### Unit Tests

**File:** `tests/unit/control/core/test_laser_auto_focus_controller.py`

```python
import pytest
from unittest.mock import Mock, patch
import threading
import time

from control.core.laser_auto_focus_controller import LaserAutofocusController


@pytest.fixture
def controller():
    """Create a test controller with mocked dependencies."""
    return LaserAutofocusController(
        microcontroller=Mock(),
        camera=Mock(),
        liveController=Mock(),
        stage=Mock(),
        piezo=Mock(),
    )


class TestFocusLock:
    def test_start_requires_initialization(self, controller):
        controller.is_initialized = False
        assert controller.start_focus_lock() is False

    def test_start_requires_reference(self, controller):
        controller.is_initialized = True
        controller.laser_af_properties = Mock(has_reference=False)
        assert controller.start_focus_lock() is False

    def test_start_creates_thread(self, controller):
        controller.is_initialized = True
        controller.laser_af_properties = Mock(has_reference=True)
        controller._focus_lock_loop = Mock()  # Don't run real loop

        controller.start_focus_lock()

        assert controller._focus_lock_thread is not None
        assert controller._focus_lock_running.is_set()

    def test_stop_clears_flag(self, controller):
        controller._focus_lock_running.set()
        controller.stop_focus_lock()
        assert not controller._focus_lock_running.is_set()

    def test_double_start_fails(self, controller):
        controller.is_initialized = True
        controller.laser_af_properties = Mock(has_reference=True)
        controller._focus_lock_loop = Mock()

        assert controller.start_focus_lock() is True
        assert controller.start_focus_lock() is False
```

### Integration Test

```python
def test_focus_lock_integration(controller):
    """Test full focus lock cycle with mocked hardware."""
    # Setup
    controller.is_initialized = True
    controller.laser_af_properties = Mock(
        has_reference=True,
        x_reference=100.0,
        pixel_to_um=0.5,
    )
    controller.microcontroller.wait_till_operation_is_completed = Mock()
    controller._get_laser_spot_centroid = Mock(return_value=(102.0, 50.0))
    controller._move_z = Mock()

    # Start focus lock
    assert controller.start_focus_lock() is True
    assert controller.is_focus_locked

    # Let it run a few iterations
    time.sleep(0.2)

    # Should have made corrections
    assert controller._move_z.call_count > 0

    # Stop
    controller.stop_focus_lock()
    assert not controller.is_focus_locked
```

### Manual Testing Checklist

- [ ] Start focus lock - laser turns on, status shows "LOCKED"
- [ ] Display updates with current displacement
- [ ] Move sample slightly - see correction applied
- [ ] Stop focus lock - laser turns off
- [ ] Start without reference - should fail with message
- [ ] Start without initialization - should fail with message
- [ ] Close application while locked - should stop cleanly

---

## File Summary

| File | Changes |
|------|---------|
| `control/core/laser_auto_focus_controller.py` | Add focus lock state, start/stop/loop methods |
| `control/widgets/autofocus.py` | Add focus lock button and status display |
| `control/core/multi_point_worker.py` | Add focus lock pause/resume around stage moves |
| `tests/unit/control/core/test_laser_auto_focus_controller.py` | Add focus lock tests |

---

## Commit Order

1. `Add focus lock state variables to LaserAutofocusController`
2. `Add start_focus_lock() method`
3. `Add stop_focus_lock() method`
4. `Implement focus lock background loop`
5. `Add focus lock property accessors`
6. `Add cleanup to stop focus lock on controller destruction`
7. `Add focus lock controls to laser autofocus widget`
8. `Add focus lock coordination with stage movement`

---

## Future Enhancements

These are out of scope for initial implementation but could be added later:

1. **PID Control**: Replace simple clamped correction with PID controller for smoother response
2. **Adaptive Rate**: Increase loop rate when error is large, decrease when stable
3. **Focus Quality Metric**: Track correlation score to detect when focus is lost
4. **Auto-Recovery**: Automatically stop if spot is lost for N iterations
5. **Focus Lock Events**: Emit events for lock acquired/lost for logging
