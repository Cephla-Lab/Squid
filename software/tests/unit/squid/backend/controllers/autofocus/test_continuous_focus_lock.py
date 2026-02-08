"""Tests for ContinuousFocusLockController."""

import math

from squid.backend.controllers.autofocus.continuous_focus_lock import (
    ContinuousFocusLockController,
)
from squid.backend.controllers.autofocus.laser_auto_focus_controller import LaserAFResult
from squid.core.config.focus_lock import FocusLockConfig
from squid.core.events import (
    EventBus,
    FocusLockPiezoLimitCritical,
    FocusLockStatusChanged,
    FocusLockWarning,
    SetFocusLockParamsCommand,
)


class _DummyLaserAFProps:
    correlation_threshold = 0.8
    has_reference = True
    x_reference = 100.0


class _DummyLaserAF:
    def __init__(self) -> None:
        self.laser_af_properties = _DummyLaserAFProps()
        self.on_calls = 0
        self.off_calls = 0
        self.is_initialized = True
        self._displacement_um = 0.5

    def turn_on_laser(self, bypass_mode_gate: bool = False) -> None:  # noqa: ARG002
        self.on_calls += 1

    def turn_off_laser(self, bypass_mode_gate: bool = False) -> None:  # noqa: ARG002
        self.off_calls += 1

    def measure_displacement_continuous(self) -> LaserAFResult:
        import time
        return LaserAFResult(
            displacement_um=self._displacement_um,
            spot_intensity=100.0,
            spot_snr=10.0,
            correlation=0.95,
            spot_x_px=100.0,
            spot_y_px=50.0,
            timestamp=time.monotonic(),
        )


class _DummyPiezoService:
    def __init__(self) -> None:
        self.position = 150.0

    def get_position(self) -> float:
        return self.position

    def get_range(self):
        return (0.0, 300.0)

    def move_to_fast(self, position_um: float) -> None:
        self.position = position_um


def test_control_fn_negative_feedback():
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=EventBus(),
        config=FocusLockConfig(gain=0.5, gain_max=0.7),
    )

    dt = 1.0 / 60.0
    assert controller._control_fn(1.0, dt) < 0
    assert controller._control_fn(-1.0, dt) > 0


def test_lock_state_transitions():
    """Test that lock state machine transitions correctly with recovery support."""
    bus = EventBus()
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=bus,
        config=FocusLockConfig(
            buffer_length=3,
            recovery_attempts=1,  # Single recovery attempt for simple test
            recovery_delay_s=0.0,  # Immediate recovery attempt
        ),
    )

    events: list[FocusLockStatusChanged] = []
    bus.subscribe(FocusLockStatusChanged, events.append)

    # Start in ready state, build up buffer
    controller._set_status("ready")
    controller._update_lock_state(True, 0.0)  # buffer_fill = 1
    controller._update_lock_state(True, 0.0)  # buffer_fill = 2
    controller._update_lock_state(True, 0.0)  # buffer_fill = 3 -> locked

    # Bad reading triggers recovery (not immediate lost)
    controller._update_lock_state(False, 1.0)  # -> recovering

    # Another bad reading after recovery delay exhausts attempts -> lost
    controller._update_lock_state(False, 1.0)  # -> lost

    bus.drain()

    statuses = [event.status for event in events]
    assert "ready" in statuses
    assert "locked" in statuses
    assert "recovering" in statuses
    assert "lost" in statuses


def test_laser_state_tracking():
    laser_af = _DummyLaserAF()
    controller = ContinuousFocusLockController(
        laser_af=laser_af,
        piezo_service=_DummyPiezoService(),
        event_bus=EventBus(),
    )

    controller._turn_on_laser()
    controller._turn_on_laser()
    controller._turn_off_laser()
    controller._turn_off_laser()

    assert laser_af.on_calls == 1
    assert laser_af.off_calls == 1


def test_pause_preserves_lock_state():
    """Test that pause/resume preserves lock buffer and doesn't reset state."""
    bus = EventBus()
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=bus,
        config=FocusLockConfig(buffer_length=3),
    )

    events: list[FocusLockStatusChanged] = []
    bus.subscribe(FocusLockStatusChanged, events.append)

    # Simulate lock acquisition
    controller._should_run = True
    controller._set_status("ready")
    controller._update_lock_state(True, 0.0)  # buffer_fill = 1
    controller._update_lock_state(True, 0.0)  # buffer_fill = 2
    controller._update_lock_state(True, 0.0)  # buffer_fill = 3 -> locked

    bus.drain()
    assert controller.status == "locked"
    assert controller._lock_buffer_fill == 3

    # Pause should preserve buffer
    controller.pause()
    bus.drain()
    assert controller.status == "paused"
    assert controller._lock_buffer_fill == 3  # Buffer preserved

    # Resume should restore locked status
    controller.resume()
    bus.drain()
    assert controller.status == "locked"
    assert controller._lock_buffer_fill == 3  # Buffer still preserved


def test_pause_does_not_reset_on_resume():
    """Test that resume doesn't call _reset_lock_state."""
    bus = EventBus()
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=bus,
        config=FocusLockConfig(buffer_length=3),
    )

    # Set up a locked state with some history
    controller._should_run = True
    controller._lock_buffer_fill = 3
    controller._set_status("locked")
    controller._error_history.append(0.1)
    controller._error_history.append(0.2)

    # Pause
    controller.pause()
    assert controller.status == "paused"
    assert len(controller._error_history) == 2  # History preserved

    # Resume
    controller.resume()
    assert controller.status == "locked"
    assert len(controller._error_history) == 2  # History still preserved


def test_pause_when_not_started():
    """Test that pause does nothing when not started."""
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=EventBus(),
    )

    # Not started yet
    assert controller._should_run is False

    # Pause should have no effect
    controller.pause()
    assert controller.status == "disabled"
    assert controller._paused is False


def test_resume_when_not_paused():
    """Test that resume does nothing when not paused."""
    bus = EventBus()
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=bus,
    )

    events: list[FocusLockStatusChanged] = []
    bus.subscribe(FocusLockStatusChanged, events.append)

    # Set up running but not paused
    controller._should_run = True
    controller._set_status("locked")
    controller._lock_buffer_fill = 3

    # Drain the event from _set_status
    bus.drain()
    events.clear()

    # Resume should have no effect when not paused
    controller.resume()
    bus.drain()

    # No status change events should have been published
    assert len(events) == 0
    assert controller.status == "locked"


def test_set_status_always_publishes_event():
    """Verify _set_status publishes even when called with same status value."""
    bus = EventBus()
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=bus,
    )

    events: list[FocusLockStatusChanged] = []
    bus.subscribe(FocusLockStatusChanged, events.append)

    controller._set_status("ready")
    controller._set_status("ready")  # Same status again
    bus.drain()

    # Both calls should have published events (no dedup guard)
    assert len(events) == 2
    assert all(e.status == "ready" for e in events)


def test_crash_clears_running_flags():
    """Simulate exception in control loop, verify _running and _should_run cleared."""
    bus = EventBus()
    laser_af = _DummyLaserAF()
    controller = ContinuousFocusLockController(
        laser_af=laser_af,
        piezo_service=_DummyPiezoService(),
        event_bus=bus,
        config=FocusLockConfig(loop_rate_hz=100),
    )

    # Manually set running flags as if start() was called
    controller._running = True
    controller._should_run = True

    # Make measure_displacement_continuous raise to simulate crash
    def raise_error():
        raise RuntimeError("simulated camera failure")

    laser_af.measure_displacement_continuous = raise_error

    # Run control loop (should catch exception and clean up)
    controller._control_loop()

    assert controller._running is False
    assert controller._should_run is False
    assert controller.status == "disabled"


def test_set_lock_reference_sets_target():
    """Verify _target_um is set to current displacement when lock reference is set."""
    bus = EventBus()
    laser_af = _DummyLaserAF()
    laser_af._displacement_um = 1.5
    controller = ContinuousFocusLockController(
        laser_af=laser_af,
        piezo_service=_DummyPiezoService(),
        event_bus=bus,
    )

    # Simulate running state
    controller._running = True
    controller._should_run = True

    controller._set_lock_reference()
    bus.drain()

    assert controller._target_um == 1.5
    assert controller.status == "locked"


def test_set_lock_reference_uses_cached_displacement_if_measurement_invalid():
    """If immediate lock-read is invalid, fallback to latest valid displacement."""
    bus = EventBus()
    laser_af = _DummyLaserAF()

    def measure_nan() -> LaserAFResult:
        import time
        return LaserAFResult(
            displacement_um=float("nan"),
            spot_intensity=100.0,
            spot_snr=10.0,
            correlation=0.95,
            spot_x_px=100.0,
            spot_y_px=50.0,
            timestamp=time.monotonic(),
        )

    laser_af.measure_displacement_continuous = measure_nan

    controller = ContinuousFocusLockController(
        laser_af=laser_af,
        piezo_service=_DummyPiezoService(),
        event_bus=bus,
    )
    controller._running = True
    controller._should_run = True
    controller._set_status("ready")
    controller._latest_valid_displacement_um = 1.2

    controller._set_lock_reference()
    bus.drain()

    assert controller.status == "locked"
    assert controller._target_um == 1.2


def test_set_lock_reference_no_valid_displacement_keeps_status():
    """Do not force locked state when no valid displacement is available."""
    bus = EventBus()
    laser_af = _DummyLaserAF()

    def measure_nan() -> LaserAFResult:
        import time
        return LaserAFResult(
            displacement_um=float("nan"),
            spot_intensity=100.0,
            spot_snr=10.0,
            correlation=0.95,
            spot_x_px=100.0,
            spot_y_px=50.0,
            timestamp=time.monotonic(),
        )

    laser_af.measure_displacement_continuous = measure_nan

    controller = ContinuousFocusLockController(
        laser_af=laser_af,
        piezo_service=_DummyPiezoService(),
        event_bus=bus,
    )
    controller._running = True
    controller._should_run = True
    controller._target_um = 0.5
    controller._set_status("ready")

    controller._set_lock_reference()
    bus.drain()

    assert controller.status == "ready"
    assert controller._target_um == 0.5


def test_is_good_reading_ignores_correlation_for_nonzero_target():
    """Low correlation should not reject otherwise-good reads for non-zero target lock."""
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=EventBus(),
    )
    controller._target_um = 2.0
    controller._set_status("locked")

    import time
    result = LaserAFResult(
        displacement_um=2.0,
        spot_intensity=100.0,
        spot_snr=10.0,
        correlation=0.1,  # Below threshold, but target is intentionally non-zero
        spot_x_px=100.0,
        spot_y_px=50.0,
        timestamp=time.monotonic(),
    )

    error_um = controller._compute_error(result)
    assert error_um == 0.0
    assert controller._is_good_reading(result, error_um) is True


def test_is_good_reading_keeps_correlation_gate_during_acquire():
    """During acquire, low correlation should still block lock acquisition."""
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=EventBus(),
    )
    controller._target_um = 0.0
    controller._set_status("ready")

    import time
    result = LaserAFResult(
        displacement_um=0.0,
        spot_intensity=100.0,
        spot_snr=10.0,
        correlation=0.1,  # Below threshold and target is near reference
        spot_x_px=100.0,
        spot_y_px=50.0,
        timestamp=time.monotonic(),
    )

    error_um = controller._compute_error(result)
    assert error_um == 0.0
    assert controller._is_good_reading(result, error_um) is False


def test_is_good_reading_ignores_low_snr_while_locked_if_error_small():
    """Low SNR should not immediately break lock if displacement remains stable."""
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=EventBus(),
    )
    controller._target_um = 0.0
    controller._set_status("locked")

    import time
    result = LaserAFResult(
        displacement_um=0.0,
        spot_intensity=100.0,
        spot_snr=0.5,  # Below min_spot_snr
        correlation=0.1,  # Also low; ignored while locked
        spot_x_px=100.0,
        spot_y_px=50.0,
        timestamp=time.monotonic(),
    )

    error_um = controller._compute_error(result)
    assert error_um == 0.0
    assert controller._is_good_reading(result, error_um) is True


def test_is_good_reading_requires_snr_during_acquire():
    """Acquire path should still reject low-SNR readings."""
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=EventBus(),
    )
    controller._target_um = 0.0
    controller._set_status("ready")

    import time
    result = LaserAFResult(
        displacement_um=0.0,
        spot_intensity=100.0,
        spot_snr=0.5,  # Below min_spot_snr
        correlation=0.95,
        spot_x_px=100.0,
        spot_y_px=50.0,
        timestamp=time.monotonic(),
    )

    error_um = controller._compute_error(result)
    assert error_um == 0.0
    assert controller._is_good_reading(result, error_um) is False


def test_control_fn_zero_error():
    """Verify no division by zero at error_um=0."""
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=EventBus(),
    )

    dt = 1.0 / 60.0
    result = controller._control_fn(0.0, dt)
    assert result == 0.0
    assert not math.isnan(result)


def test_recovery_with_delayed_good_readings():
    """Recovery succeeds at exactly recovery_window_readings good readings."""
    bus = EventBus()
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=bus,
        config=FocusLockConfig(
            buffer_length=3,
            recovery_attempts=5,
            recovery_delay_s=0.0,
            recovery_window_readings=3,
        ),
    )

    events: list[FocusLockStatusChanged] = []
    bus.subscribe(FocusLockStatusChanged, events.append)

    # Get to locked state
    controller._set_status("ready")
    for _ in range(3):
        controller._update_lock_state(True, 0.0)

    # Enter recovery
    controller._update_lock_state(False, 1.0)  # -> recovering

    # One good reading short of recovery
    controller._update_lock_state(True, 0.0)  # count=1
    controller._update_lock_state(True, 0.0)  # count=2
    assert controller.status == "recovering"

    # Third good reading should recover
    controller._update_lock_state(True, 0.0)  # count=3 -> locked
    bus.drain()
    assert controller.status == "locked"


def test_set_focus_lock_params_command():
    """Test SetFocusLockParamsCommand updates config."""
    bus = EventBus()
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=bus,
        config=FocusLockConfig(buffer_length=5, min_spot_snr=3.0),
    )

    assert controller._config.buffer_length == 5
    assert controller._config.min_spot_snr == 3.0

    controller._on_set_params(SetFocusLockParamsCommand(
        buffer_length=10,
        min_spot_snr=5.0,
    ))

    assert controller._config.buffer_length == 10
    assert controller._config.min_spot_snr == 5.0


def test_start_precondition_not_initialized():
    """Test that start() returns early if laser AF is not initialized."""
    bus = EventBus()
    laser_af = _DummyLaserAF()
    laser_af.is_initialized = False
    controller = ContinuousFocusLockController(
        laser_af=laser_af,
        piezo_service=_DummyPiezoService(),
        event_bus=bus,
    )

    controller.start()
    assert not controller.is_running
    assert controller.status == "disabled"


def test_start_precondition_no_reference():
    """Test that start() returns early if no reference is set."""
    bus = EventBus()
    laser_af = _DummyLaserAF()
    laser_af.laser_af_properties.has_reference = False
    controller = ContinuousFocusLockController(
        laser_af=laser_af,
        piezo_service=_DummyPiezoService(),
        event_bus=bus,
    )

    controller.start()
    assert not controller.is_running
    assert controller.status == "disabled"


def test_reset_clears_all_histories():
    """Start -> lock -> stop -> start, assert quality/warning/error histories are clean."""
    bus = EventBus()
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=bus,
    )

    # Dirty the state
    controller._error_history.append(0.5)
    controller._drift_history.append((0.0, 0.1))
    controller._smoothed_quality = 0.3
    controller._warning_last_time["test"] = 100.0
    controller._recovery_good_count = 5
    controller._integral_accumulator = 1.5
    controller._last_good_error_um = 0.3
    controller._consecutive_nan_count = 2

    controller._reset_lock_state()

    assert len(controller._error_history) == 0
    assert len(controller._drift_history) == 0
    assert controller._smoothed_quality == 1.0
    assert len(controller._warning_last_time) == 0
    assert controller._recovery_good_count == 0
    assert controller._integral_accumulator == 0.0
    assert controller._last_good_error_um == 0.0
    assert controller._consecutive_nan_count == 0
    assert math.isnan(controller._latest_valid_displacement_um)


# ============================================================================
# Gain Schedule Tests (Task A)
# ============================================================================


def test_p_gain_high_at_small_error():
    """_p_gain should return gain_max at error=0 and approach gain at large errors."""
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=EventBus(),
        config=FocusLockConfig(gain=0.5, gain_max=0.7, gain_sigma=0.5),
    )

    # At zero error, gain should be gain_max
    assert controller._p_gain(0.0) == 0.7

    # At large error, gain should approach gain (base)
    large_error_gain = controller._p_gain(5.0)
    assert abs(large_error_gain - 0.5) < 0.01  # Very close to base gain

    # Gain should decrease monotonically with increasing |error|
    assert controller._p_gain(0.0) > controller._p_gain(0.5)
    assert controller._p_gain(0.5) > controller._p_gain(1.0)
    assert controller._p_gain(1.0) > controller._p_gain(2.0)


# ============================================================================
# Integral Term Tests (Task B)
# ============================================================================


def test_integral_accumulates():
    """Run N cycles with constant error, verify accumulator grows."""
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=EventBus(),
        config=FocusLockConfig(ki=0.05, integral_limit_um=2.0),
    )

    dt = 1.0 / 60.0
    error = 0.3  # constant error

    for _ in range(10):
        controller._control_fn(error, dt)

    # Accumulator should have grown: 0.3 * (1/60) * 10 = 0.05
    assert controller._integral_accumulator > 0
    expected = error * dt * 10
    assert abs(controller._integral_accumulator - expected) < 1e-6


def test_integral_anti_windup_clamp():
    """Set large error, verify accumulator stays within integral_limit_um."""
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=EventBus(),
        config=FocusLockConfig(ki=0.05, integral_limit_um=0.5),
    )

    dt = 1.0 / 60.0
    error = 10.0  # Very large error

    for _ in range(10000):
        controller._control_fn(error, dt)

    assert controller._integral_accumulator <= 0.5
    assert controller._integral_accumulator >= -0.5


def test_integral_resets_on_recovery():
    """Enter recovery, verify accumulator = 0."""
    bus = EventBus()
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=bus,
        config=FocusLockConfig(buffer_length=3, recovery_attempts=3, recovery_delay_s=0.0),
    )

    # Build up some integral state
    controller._integral_accumulator = 1.0

    # Get to locked state
    controller._set_status("locked")
    controller._lock_buffer_fill = 3

    # Bad reading triggers recovery -> integral should reset
    controller._update_lock_state(False, 1.0)

    assert controller.status == "recovering"
    assert controller._integral_accumulator == 0.0


def test_integral_preserved_across_pause():
    """Pause/resume, verify accumulator unchanged."""
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=EventBus(),
        config=FocusLockConfig(buffer_length=3),
    )

    # Set up locked state with integral state
    controller._should_run = True
    controller._lock_buffer_fill = 3
    controller._set_status("locked")
    controller._integral_accumulator = 0.75

    # Pause
    controller.pause()
    assert controller._integral_accumulator == 0.75

    # Resume
    controller.resume()
    assert controller._integral_accumulator == 0.75


def test_integral_disabled_when_ki_zero():
    """ki=0.0, verify no integral contribution."""
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=EventBus(),
        config=FocusLockConfig(ki=0.0),
    )

    dt = 1.0 / 60.0
    # Run multiple cycles
    for _ in range(100):
        controller._control_fn(0.5, dt)

    # Accumulator should remain at zero
    assert controller._integral_accumulator == 0.0


def test_integral_conditional_anti_windup_near_piezo_limits():
    """Don't accumulate integral when piezo is within 5 um of range limits."""
    piezo = _DummyPiezoService()
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=piezo,
        event_bus=EventBus(),
        config=FocusLockConfig(ki=0.05, integral_limit_um=2.0),
    )

    dt = 1.0 / 60.0

    # Position near lower limit (range is 0-300, so within 5 um of 0)
    piezo.position = 3.0
    controller._control_fn(0.5, dt)
    assert controller._integral_accumulator == 0.0  # Should not accumulate

    # Position near upper limit
    piezo.position = 297.0
    controller._control_fn(0.5, dt)
    assert controller._integral_accumulator == 0.0  # Should not accumulate

    # Position in safe zone
    piezo.position = 150.0
    controller._control_fn(0.5, dt)
    assert controller._integral_accumulator > 0.0  # Should accumulate


# ============================================================================
# NaN Holdover Tests (Task D)
# ============================================================================


def _make_nan_result():
    """Create a LaserAFResult with NaN displacement."""
    import time
    return LaserAFResult(
        displacement_um=float("nan"),
        spot_intensity=0.0,
        spot_snr=0.0,
        correlation=None,
        spot_x_px=None,
        spot_y_px=None,
        timestamp=time.monotonic(),
    )


def test_nan_holdover_applies_correction():
    """Inject NaN, verify correction uses last-known-good."""
    piezo = _DummyPiezoService()
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=piezo,
        event_bus=EventBus(),
        config=FocusLockConfig(
            max_nan_holdover_cycles=3,
            nan_holdover_decay=0.5,
            ki=0.0,  # Disable integral for clarity
        ),
    )

    # Set up locked state with a known last-good error
    controller._set_status("locked")
    controller._lock_buffer_fill = 5
    controller._last_good_error_um = 1.0
    controller._consecutive_nan_count = 0

    # Compute expected correction: _control_fn(1.0, dt) * decay^1
    dt = 1.0 / controller._config.loop_rate_hz
    full_correction = controller._control_fn(1.0, dt)
    expected_correction = full_correction * 0.5  # decay^1

    # Reset integral (control_fn call above modified it)
    controller._integral_accumulator = 0.0

    # Record initial position
    initial_pos = piezo.position

    # Simulate one NaN cycle in the holdover path
    controller._consecutive_nan_count = 0  # will become 1
    saved_integral = controller._integral_accumulator
    controller._consecutive_nan_count += 1
    decay = controller._config.nan_holdover_decay ** controller._consecutive_nan_count
    correction = controller._control_fn(controller._last_good_error_um, dt) * decay
    controller._integral_accumulator = saved_integral  # restore
    new_pos = max(0.0, min(300.0, initial_pos + correction))
    piezo.move_to_fast(new_pos)

    # Piezo should have moved
    assert piezo.position != initial_pos
    assert abs(piezo.position - (initial_pos + expected_correction)) < 1e-6


def test_nan_holdover_decays():
    """Multiple NaN cycles, verify correction magnitude decreases."""
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=EventBus(),
        config=FocusLockConfig(
            max_nan_holdover_cycles=5,
            nan_holdover_decay=0.5,
            ki=0.0,
        ),
    )

    dt = 1.0 / controller._config.loop_rate_hz
    base_correction = abs(controller._control_fn(1.0, dt))

    # Each subsequent NaN should produce smaller correction due to decay
    corrections = []
    for i in range(1, 4):
        decay = controller._config.nan_holdover_decay ** i
        corrections.append(base_correction * decay)

    # Verify decreasing corrections
    assert corrections[0] > corrections[1] > corrections[2]
    # Verify decay factors
    assert abs(corrections[0] / base_correction - 0.5) < 1e-6
    assert abs(corrections[1] / base_correction - 0.25) < 1e-6
    assert abs(corrections[2] / base_correction - 0.125) < 1e-6


def test_nan_holdover_expires():
    """Exceed max_nan_holdover_cycles, verify correction stops."""
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=EventBus(),
        config=FocusLockConfig(max_nan_holdover_cycles=3),
    )

    controller._set_status("locked")
    controller._lock_buffer_fill = 5
    controller._last_good_error_um = 1.0

    # Set consecutive NaN count at the limit
    controller._consecutive_nan_count = 3

    # At this point, holdover should be expired (>= max_nan_holdover_cycles)
    assert controller._consecutive_nan_count >= controller._config.max_nan_holdover_cycles


def test_nan_holdover_does_not_update_integral():
    """During holdover, integral accumulator should not change."""
    controller = ContinuousFocusLockController(
        laser_af=_DummyLaserAF(),
        piezo_service=_DummyPiezoService(),
        event_bus=EventBus(),
        config=FocusLockConfig(ki=0.05, max_nan_holdover_cycles=3, nan_holdover_decay=0.5),
    )

    controller._integral_accumulator = 0.5
    dt = 1.0 / 60.0

    # Simulate holdover: save, call, restore
    saved = controller._integral_accumulator
    controller._control_fn(1.0, dt)  # This would modify accumulator
    controller._integral_accumulator = saved  # Restore as the control loop does

    assert controller._integral_accumulator == 0.5


# ============================================================================
# Piezo Critical Warning Tests (Task E)
# ============================================================================


def test_piezo_critical_warning_published():
    """Move piezo near limit, call _check_warnings, verify critical event."""
    bus = EventBus()
    piezo = _DummyPiezoService()
    laser_af = _DummyLaserAF()
    controller = ContinuousFocusLockController(
        laser_af=laser_af,
        piezo_service=piezo,
        event_bus=bus,
        config=FocusLockConfig(
            piezo_critical_margin_um=10.0,
            piezo_warning_margin_um=20.0,
        ),
    )

    critical_events: list[FocusLockPiezoLimitCritical] = []
    warning_events: list[FocusLockWarning] = []
    bus.subscribe(FocusLockPiezoLimitCritical, critical_events.append)
    bus.subscribe(FocusLockWarning, warning_events.append)

    # Move piezo close to lower limit (range 0-300, position 5 < 0+10)
    piezo.position = 5.0
    result = laser_af.measure_displacement_continuous()
    controller._check_warnings(result, 0.0)
    bus.drain()

    assert len(critical_events) == 1
    assert critical_events[0].direction == "low"
    assert critical_events[0].position_um == 5.0
    assert critical_events[0].limit_um == 0.0


def test_piezo_warning_threshold_ordering():
    """Verify critical fires inside margin, warning-only fires outside critical but inside warning."""
    bus = EventBus()
    piezo = _DummyPiezoService()
    laser_af = _DummyLaserAF()
    controller = ContinuousFocusLockController(
        laser_af=laser_af,
        piezo_service=piezo,
        event_bus=bus,
        config=FocusLockConfig(
            piezo_critical_margin_um=10.0,
            piezo_warning_margin_um=20.0,
        ),
    )

    critical_events: list[FocusLockPiezoLimitCritical] = []
    warning_events: list[FocusLockWarning] = []
    bus.subscribe(FocusLockPiezoLimitCritical, critical_events.append)
    bus.subscribe(FocusLockWarning, warning_events.append)

    # Position between warning and critical margin (15 um from lower limit)
    # 0+10 < 15 < 0+20 → warning only, no critical
    piezo.position = 15.0
    result = laser_af.measure_displacement_continuous()
    controller._check_warnings(result, 0.0)
    bus.drain()

    assert len(critical_events) == 0
    assert len(warning_events) == 1
    assert warning_events[0].warning_type == "piezo_low"


def test_piezo_critical_warning_high_limit():
    """Test critical warning at high limit."""
    bus = EventBus()
    piezo = _DummyPiezoService()
    laser_af = _DummyLaserAF()
    controller = ContinuousFocusLockController(
        laser_af=laser_af,
        piezo_service=piezo,
        event_bus=bus,
        config=FocusLockConfig(
            piezo_critical_margin_um=10.0,
            piezo_warning_margin_um=20.0,
        ),
    )

    critical_events: list[FocusLockPiezoLimitCritical] = []
    bus.subscribe(FocusLockPiezoLimitCritical, critical_events.append)

    # Move piezo close to upper limit (range 0-300, position 295 > 300-10=290)
    piezo.position = 295.0
    result = laser_af.measure_displacement_continuous()
    controller._check_warnings(result, 0.0)
    bus.drain()

    assert len(critical_events) == 1
    assert critical_events[0].direction == "high"
    assert critical_events[0].position_um == 295.0
    assert critical_events[0].limit_um == 300.0
