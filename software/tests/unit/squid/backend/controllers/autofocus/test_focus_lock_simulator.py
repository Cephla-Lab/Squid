"""Tests for FocusLockSimulator.

Includes both basic lifecycle tests (no laser AF) and integration tests
with a mock laser AF that exercise the actual lock state machine:
set_lock → _update_from_laser_af_result → _is_good_reading → state transitions.
"""

import time
from typing import Optional

import numpy as np

from squid.backend.controllers.autofocus.focus_lock_simulator import FocusLockSimulator
from squid.backend.controllers.autofocus.laser_auto_focus_controller import LaserAFResult
from squid.core.config.focus_lock import FocusLockConfig
from squid.core.events import (
    EventBus,
    FocusLockFrameUpdated,
    FocusLockMetricsUpdated,
    FocusLockModeChanged,
    FocusLockStatusChanged,
    LaserAFInitialized,
    SetFocusLockParamsCommand,
    SetFocusLockModeCommand,
    SetFocusLockReferenceCommand,
)


def _init_laser_af(bus: EventBus) -> None:
    """Publish LaserAFInitialized event to enable the simulator to start."""
    bus.publish(LaserAFInitialized(is_initialized=True, success=True))
    bus.drain()


def _make_good_result(displacement_um: float = 0.0) -> LaserAFResult:
    """Create a valid LaserAFResult with good SNR and spot detection."""
    return LaserAFResult(
        displacement_um=displacement_um,
        spot_intensity=100.0,
        spot_snr=10.0,
        correlation=0.95,
        spot_x_px=320.0,
        spot_y_px=240.0,
        timestamp=time.monotonic(),
        image=np.zeros((480, 640), dtype=np.uint8),
    )


def _make_bad_snr_result(displacement_um: float = 0.0) -> LaserAFResult:
    """Create a result with low SNR (spot barely visible to algorithm)."""
    return LaserAFResult(
        displacement_um=displacement_um,
        spot_intensity=5.0,
        spot_snr=1.0,  # Below min_spot_snr of 5.0
        correlation=0.3,
        spot_x_px=320.0,
        spot_y_px=240.0,
        timestamp=time.monotonic(),
        image=np.zeros((480, 640), dtype=np.uint8),
    )


def _make_no_spot_result() -> LaserAFResult:
    """Create a result where spot detection failed entirely."""
    return LaserAFResult(
        displacement_um=float("nan"),
        spot_intensity=0.0,
        spot_snr=0.0,
        correlation=None,
        spot_x_px=None,
        spot_y_px=None,
        timestamp=time.monotonic(),
        image=np.zeros((480, 640), dtype=np.uint8),
    )


class _FakeLaserAF:
    """Mock laser AF that returns controlled results for testing."""

    def __init__(self, result: Optional[LaserAFResult] = None) -> None:
        self.result = result or _make_good_result()
        self.laser_af_properties = type(
            "_Props",
            (),
            {"correlation_threshold": 0.7, "has_reference": True, "x_reference": 320.0, "pixel_to_um": 0.2},
        )()

    def measure_displacement_continuous(self) -> LaserAFResult:
        return self.result


class _FakePiezoService:
    def __init__(self, position: float = 150.0) -> None:
        self._position = position

    def get_position(self) -> float:
        return self._position

    def get_range(self):
        return (100.0, 200.0)

    def move_to(self, position_um: float) -> None:
        self._position = position_um

    def move_to_fast(self, position_um: float) -> None:
        self._position = position_um


# ---------------------------------------------------------------------------
# Basic lifecycle tests (no laser AF — existing tests preserved)
# ---------------------------------------------------------------------------


def test_simulator_publishes_events():
    """Simulator should publish status and metrics events when running."""
    bus = EventBus()
    config = FocusLockConfig(loop_rate_hz=50, metrics_rate_hz=20, buffer_length=3)
    sim = FocusLockSimulator(bus, config=config)
    _init_laser_af(bus)

    status_events = []
    metrics_events = []
    bus.subscribe(FocusLockStatusChanged, status_events.append)
    bus.subscribe(FocusLockMetricsUpdated, metrics_events.append)

    sim.start()
    time.sleep(0.3)
    sim.stop()
    bus.drain()

    assert status_events
    assert metrics_events


def test_mode_command_publishes_mode_changed():
    """Simulator should respond to mode commands."""
    bus = EventBus()
    sim = FocusLockSimulator(bus)

    mode_events = []
    bus.subscribe(FocusLockModeChanged, mode_events.append)

    bus.publish(SetFocusLockModeCommand(mode="on"))
    bus.drain()

    assert sim.mode == "on"
    assert mode_events
    assert mode_events[-1].mode == "on"

    sim.stop()


def test_wait_for_lock_returns_false_when_not_running():
    """wait_for_lock should return False when simulator isn't running."""
    bus = EventBus()
    config = FocusLockConfig(loop_rate_hz=50, metrics_rate_hz=20, buffer_length=3)
    sim = FocusLockSimulator(bus, config=config)
    _init_laser_af(bus)

    # Simulator is not started, so is_running is False
    assert sim.is_running is False
    assert sim.wait_for_lock(timeout_s=0.1) is False


def test_wait_for_lock():
    """wait_for_lock should reflect lock acquisition state."""
    bus = EventBus()
    config = FocusLockConfig(loop_rate_hz=50, metrics_rate_hz=20, buffer_length=3)
    sim = FocusLockSimulator(bus, config=config)
    _init_laser_af(bus)

    sim.start()
    # After start, need to explicitly set lock to achieve locked state
    bus.publish(SetFocusLockReferenceCommand())
    bus.drain()
    # Give time for lock buffer to fill
    time.sleep(0.2)
    assert sim.wait_for_lock(timeout_s=1.0) is True
    sim.stop()


def test_start_does_not_auto_lock_without_explicit_reference():
    """Start should keep simulator ready until lock is explicitly engaged."""
    bus = EventBus()
    laser_af = _FakeLaserAF(_make_good_result(displacement_um=0.0))
    piezo = _FakePiezoService()
    sim = FocusLockSimulator(
        bus,
        config=FocusLockConfig(loop_rate_hz=60, metrics_rate_hz=10, buffer_length=3),
        laser_autofocus=laser_af,
        piezo_service=piezo,
    )
    _init_laser_af(bus)

    sim.start()
    try:
        time.sleep(0.25)
        assert sim.status == "ready"
        assert sim.wait_for_lock(timeout_s=0.2) is False
    finally:
        sim.stop()


def test_release_lock_stays_released():
    """Release should not be immediately undone by auto re-lock logic."""
    bus = EventBus()
    laser_af = _FakeLaserAF(_make_good_result(displacement_um=0.0))
    piezo = _FakePiezoService()
    sim = FocusLockSimulator(
        bus,
        config=FocusLockConfig(loop_rate_hz=60, metrics_rate_hz=10, buffer_length=3),
        laser_autofocus=laser_af,
        piezo_service=piezo,
    )
    _init_laser_af(bus)

    sim.start()
    sim.set_lock_reference()
    time.sleep(0.1)
    assert sim.status == "locked"

    sim.release_lock_reference()
    assert sim.status == "ready"
    time.sleep(0.2)
    assert sim.status == "ready"
    sim.stop()


def test_metrics_rate_throttle():
    """Metrics should be published at approximately the configured rate."""
    bus = EventBus()
    config = FocusLockConfig(loop_rate_hz=100, metrics_rate_hz=5, buffer_length=3)
    sim = FocusLockSimulator(bus, config=config)
    _init_laser_af(bus)

    metrics_events = []
    bus.subscribe(FocusLockMetricsUpdated, metrics_events.append)

    sim.start()
    time.sleep(0.6)
    sim.stop()
    bus.drain()

    expected = 0.6 * config.metrics_rate_hz
    assert len(metrics_events) >= max(1, int(expected) - 1)
    assert len(metrics_events) <= int(expected) + 5


# ---------------------------------------------------------------------------
# Lock state machine tests WITH mock laser AF
# These exercise the actual code path: set_lock → _update_from_laser_af_result
# → _is_good_reading → state machine transitions
# ---------------------------------------------------------------------------


def test_set_lock_stays_locked_with_good_readings():
    """set_lock followed by good laser AF readings should maintain lock."""
    bus = EventBus()
    laser_af = _FakeLaserAF(_make_good_result(displacement_um=0.0))
    piezo = _FakePiezoService()
    config = FocusLockConfig(loop_rate_hz=60, metrics_rate_hz=10, buffer_length=3)
    sim = FocusLockSimulator(
        bus, config=config, laser_autofocus=laser_af, piezo_service=piezo,
    )
    _init_laser_af(bus)

    sim.start()
    sim.set_lock()

    # Let the loop run several iterations with good readings
    time.sleep(0.3)

    assert sim.status == "locked", (
        f"Expected 'locked' but got '{sim.status}' — "
        f"_is_good_reading check is rejecting valid measurements"
    )
    sim.stop()


def test_set_lock_recovers_from_transient_bad_reading():
    """Lock should recover if bad readings are transient."""
    bus = EventBus()
    laser_af = _FakeLaserAF(_make_good_result(displacement_um=0.0))
    piezo = _FakePiezoService()
    config = FocusLockConfig(
        loop_rate_hz=60, metrics_rate_hz=10, buffer_length=3,
        recovery_attempts=3, recovery_delay_s=0.1,
        recovery_window_readings=2,
    )
    sim = FocusLockSimulator(
        bus, config=config, laser_autofocus=laser_af, piezo_service=piezo,
    )
    _init_laser_af(bus)

    sim.start()
    sim.set_lock()
    time.sleep(0.15)  # Lock established
    assert sim.status == "locked"

    # Inject a large displacement error briefly
    laser_af.result = _make_good_result(displacement_um=5.0)
    time.sleep(0.05)  # Quick — should enter recovery
    assert sim.status == "recovering"

    # Restore good readings — should recover
    laser_af.result = _make_good_result(displacement_um=0.0)
    time.sleep(0.3)

    assert sim.status == "locked", (
        f"Expected recovery back to 'locked' but got '{sim.status}'"
    )
    sim.stop()


def test_set_lock_lost_after_persistent_bad_readings():
    """Lock should be lost after recovery attempts exhausted."""
    bus = EventBus()
    laser_af = _FakeLaserAF(_make_good_result(displacement_um=0.0))
    piezo = _FakePiezoService()
    config = FocusLockConfig(
        loop_rate_hz=60, metrics_rate_hz=10, buffer_length=3,
        recovery_attempts=2, recovery_delay_s=0.05,
    )
    sim = FocusLockSimulator(
        bus, config=config, laser_autofocus=laser_af, piezo_service=piezo,
    )
    _init_laser_af(bus)

    sim.start()
    sim.set_lock()
    time.sleep(0.15)
    assert sim.status == "locked"

    # Inject persistent bad readings
    laser_af.result = _make_no_spot_result()
    time.sleep(0.5)  # Wait for all recovery attempts to exhaust

    assert sim.status == "lost", (
        f"Expected 'lost' but got '{sim.status}' — "
        f"recovery should exhaust with persistent bad readings"
    )
    sim.stop()


def test_acquire_rejects_low_snr_and_low_correlation():
    """Acquire path should reject weak measurements when both SNR and correlation are low."""
    bus = EventBus()
    laser_af = _FakeLaserAF(_make_bad_snr_result(displacement_um=0.0))
    piezo = _FakePiezoService()
    config = FocusLockConfig(
        loop_rate_hz=60, metrics_rate_hz=10, buffer_length=3,
        recovery_attempts=1, recovery_delay_s=0.0,
    )
    sim = FocusLockSimulator(
        bus, config=config, laser_autofocus=laser_af, piezo_service=piezo,
    )
    _init_laser_af(bus)

    sim.start()
    time.sleep(0.25)

    assert sim.status == "ready", (
        f"Expected acquire to stay in 'ready' but got '{sim.status}'"
    )
    sim.stop()


def test_is_good_reading_requires_spot_detection():
    """_is_good_reading should reject results where spot_x_px is None."""
    bus = EventBus()
    laser_af = _FakeLaserAF(_make_no_spot_result())
    piezo = _FakePiezoService()
    config = FocusLockConfig(
        loop_rate_hz=60, metrics_rate_hz=10, buffer_length=3,
        recovery_attempts=1, recovery_delay_s=0.0,
    )
    sim = FocusLockSimulator(
        bus, config=config, laser_autofocus=laser_af, piezo_service=piezo,
    )
    _init_laser_af(bus)

    sim.start()
    sim.set_lock()
    time.sleep(0.2)

    assert sim.status != "locked", (
        "Lock should not hold when spot detection fails"
    )
    sim.stop()


def test_is_good_reading_rejects_large_error():
    """_is_good_reading should reject results where error exceeds threshold."""
    bus = EventBus()
    # Start with good result for set_lock, then switch to high-error result
    laser_af = _FakeLaserAF(_make_good_result(displacement_um=0.0))
    piezo = _FakePiezoService()
    config = FocusLockConfig(
        loop_rate_hz=60, metrics_rate_hz=10, buffer_length=3,
        recovery_attempts=1, recovery_delay_s=0.0,
        maintain_threshold_um=0.8,
    )
    sim = FocusLockSimulator(
        bus, config=config, laser_autofocus=laser_af, piezo_service=piezo,
    )
    _init_laser_af(bus)

    sim.start()
    sim.set_lock()
    time.sleep(0.1)
    assert sim.status == "locked"

    # Switch to displacement far from target (error = 5.0 um >> 0.8 threshold)
    laser_af.result = _make_good_result(displacement_um=5.0)
    time.sleep(0.3)

    assert sim.status != "locked", (
        "Lock should not hold when error exceeds maintain_threshold_um"
    )
    sim.stop()


def test_acquire_rejects_low_snr_even_with_high_correlation():
    """Acquire path should match real controller: low SNR blocks acquisition."""
    bus = EventBus()
    low_snr_high_corr = LaserAFResult(
        displacement_um=0.0,
        spot_intensity=100.0,
        spot_snr=2.0,  # Below default min_spot_snr=5.0
        correlation=0.95,  # Strong match against reference
        spot_x_px=320.0,
        spot_y_px=240.0,
        timestamp=time.monotonic(),
        image=np.zeros((480, 640), dtype=np.uint8),
    )
    laser_af = _FakeLaserAF(low_snr_high_corr)
    piezo = _FakePiezoService()
    config = FocusLockConfig(loop_rate_hz=60, metrics_rate_hz=10, buffer_length=3)
    sim = FocusLockSimulator(
        bus, config=config, laser_autofocus=laser_af, piezo_service=piezo,
    )
    _init_laser_af(bus)

    sim.start()
    time.sleep(0.35)
    assert sim.status == "ready", (
        f"Expected acquire to remain 'ready' with low SNR, got '{sim.status}'"
    )
    sim.stop()


def test_set_focus_lock_params_command_updates_runtime_config():
    bus = EventBus()
    sim = FocusLockSimulator(bus, config=FocusLockConfig(buffer_length=5, min_spot_snr=5.0))
    _init_laser_af(bus)
    sim.start()
    try:
        assert sim._buffer_length == 5
        bus.publish(SetFocusLockParamsCommand(buffer_length=8, min_spot_snr=7.5))
        bus.drain()
        assert sim._buffer_length == 8
        assert sim._config.min_spot_snr == 7.5
    finally:
        sim.stop()


def test_control_fn_gain_schedule_is_highest_near_zero_error():
    sim = FocusLockSimulator(
        EventBus(),
        config=FocusLockConfig(gain=0.5, gain_max=0.7, gain_sigma=0.5),
    )
    small_err = 0.2
    large_err = 1.5
    small_gain = -sim._control_fn(small_err) / small_err
    large_gain = -sim._control_fn(large_err) / large_err
    assert small_gain > large_gain


def test_loop_crash_disables_simulator():
    class _CrashLaserAF:
        laser_af_properties = type(
            "_Props", (), {"correlation_threshold": 0.7, "has_reference": True, "x_reference": 320.0}
        )()

        def measure_displacement_continuous(self):
            raise RuntimeError("simulated failure")

    bus = EventBus()
    sim = FocusLockSimulator(
        bus,
        config=FocusLockConfig(loop_rate_hz=60, metrics_rate_hz=10),
        laser_autofocus=_CrashLaserAF(),
        piezo_service=_FakePiezoService(),
    )
    _init_laser_af(bus)

    sim.start()
    deadline = time.monotonic() + 1.0
    while sim.is_running and time.monotonic() < deadline:
        time.sleep(0.02)
    assert sim.is_running is False
    assert sim.status == "disabled"


def test_quality_drops_to_zero_when_lost():
    bus = EventBus()
    sim = FocusLockSimulator(bus, config=FocusLockConfig(loop_rate_hz=50, metrics_rate_hz=20))
    _init_laser_af(bus)

    events = []
    bus.subscribe(FocusLockMetricsUpdated, events.append)

    sim.start()
    try:
        sim._status = "lost"
        sim._publish_metrics()
        bus.drain()
        assert events
        assert events[-1].lock_quality == 0.0
    finally:
        sim.stop()


def test_pause_resume_preserves_lock():
    """Pausing and resuming should preserve lock state."""
    bus = EventBus()
    laser_af = _FakeLaserAF(_make_good_result(displacement_um=0.0))
    piezo = _FakePiezoService()
    config = FocusLockConfig(loop_rate_hz=60, metrics_rate_hz=10, buffer_length=3)
    sim = FocusLockSimulator(
        bus, config=config, laser_autofocus=laser_af, piezo_service=piezo,
    )
    _init_laser_af(bus)

    sim.start()
    sim.set_lock()
    time.sleep(0.15)
    assert sim.status == "locked"

    sim.pause()
    assert sim.status == "paused"

    sim.resume()
    time.sleep(0.15)
    assert sim.status == "locked", (
        f"Expected 'locked' after resume but got '{sim.status}'"
    )
    sim.stop()


def test_piezo_correction_applied_when_locked():
    """When locked, piezo corrections should be applied to track the target."""
    bus = EventBus()
    # Return displacement offset from target — controller should correct
    laser_af = _FakeLaserAF(_make_good_result(displacement_um=0.3))
    piezo = _FakePiezoService(position=150.0)
    config = FocusLockConfig(loop_rate_hz=60, metrics_rate_hz=10, buffer_length=3)
    sim = FocusLockSimulator(
        bus, config=config, laser_autofocus=laser_af, piezo_service=piezo,
    )
    _init_laser_af(bus)

    sim.start()
    sim.set_lock()  # target = 0.3 (current displacement)

    # Now change displacement to create an error
    laser_af.result = _make_good_result(displacement_um=0.5)
    initial_pos = piezo.get_position()
    time.sleep(0.2)

    # Piezo should have moved to correct the 0.2 um error
    assert piezo.get_position() != initial_pos, (
        "Piezo should move to correct tracking error"
    )
    sim.stop()


def test_control_fn_negative_feedback():
    """_control_fn should produce negative feedback (correction opposes error)."""
    bus = EventBus()
    sim = FocusLockSimulator(
        bus, config=FocusLockConfig(gain=0.5, gain_max=0.7),
    )

    # Positive error → negative correction
    assert sim._control_fn(1.0) < 0
    # Negative error → positive correction
    assert sim._control_fn(-1.0) > 0
    # Zero error → zero correction
    assert sim._control_fn(0.0) == 0.0


def test_no_reference_uses_spot_offset_for_correction():
    """Without AF reference, locked loop should still correct from spot offset."""
    bus = EventBus()
    laser_af = _FakeLaserAF(_make_good_result(displacement_um=0.0))
    # Simulate missing AF reference.
    laser_af.laser_af_properties.has_reference = False
    laser_af.laser_af_properties.x_reference = None
    laser_af.laser_af_properties.pixel_to_um = 0.2

    piezo = _FakePiezoService(position=150.0)
    config = FocusLockConfig(loop_rate_hz=60, metrics_rate_hz=10, buffer_length=3)
    sim = FocusLockSimulator(
        bus, config=config, laser_autofocus=laser_af, piezo_service=piezo,
    )
    _init_laser_af(bus)

    sim.start()
    sim.set_lock()
    time.sleep(0.1)
    assert sim.status == "locked"

    # Simulate stage-Z perturbation as spot shift (+20 px => +4 um error @ 0.2 um/px).
    laser_af.result = LaserAFResult(
        displacement_um=0.0,  # No displacement available without reference
        spot_intensity=100.0,
        spot_snr=10.0,
        correlation=0.95,
        spot_x_px=340.0,
        spot_y_px=240.0,
        timestamp=time.monotonic(),
        image=np.zeros((480, 640), dtype=np.uint8),
    )

    initial_pos = piezo.get_position()
    time.sleep(0.2)
    assert piezo.get_position() < initial_pos - 0.2

    sim.stop()


def test_preview_publish_uses_full_frame_dimensions():
    """Preview should publish full camera frame dimensions and raw spot coordinates."""
    bus = EventBus()
    sim = FocusLockSimulator(bus)
    sim._preview_publish_period_s = 0.0
    sim._latest_frame = np.zeros((64, 256), dtype=np.uint8)
    sim._latest_spot_x = 12.0
    sim._latest_spot_y = 20.0

    frame_events: list[FocusLockFrameUpdated] = []
    bus.subscribe(FocusLockFrameUpdated, frame_events.append)

    sim._publish_frame()
    bus.drain()

    assert len(frame_events) == 1
    event = frame_events[0]
    assert event.frame.shape == (64, 256)
    assert event.frame_width == 256
    assert event.frame_height == 64
    assert event.spot_valid is True
    # Simulator adds small preview jitter when spot is valid.
    assert abs(event.spot_x_px - 12.0) < 15.0
    assert abs(event.spot_y_px - 20.0) < 15.0


def test_resume_grace_readings_absorb_bad_readings():
    """After resume, bad readings within grace period should not break lock."""
    bus = EventBus()
    laser_af = _FakeLaserAF(_make_good_result(displacement_um=0.0))
    piezo = _FakePiezoService()
    config = FocusLockConfig(loop_rate_hz=60, metrics_rate_hz=10, buffer_length=3)
    sim = FocusLockSimulator(
        bus, config=config, laser_autofocus=laser_af, piezo_service=piezo,
    )
    _init_laser_af(bus)

    # 1. Achieve lock
    sim.start()
    sim.set_lock()
    time.sleep(0.15)
    assert sim.status == "locked"

    # 2. Pause
    sim.pause()
    assert sim.status == "paused"

    # 3. Switch laser AF to return bad readings (no spot)
    laser_af.result = _make_no_spot_result()

    # 4. Resume — grace period of 5 readings should keep lock
    sim.resume()
    # Give time for a couple cycles but within grace (5 readings at 60Hz = ~83ms)
    time.sleep(0.04)
    assert sim.status == "locked", (
        f"Expected 'locked' during grace period but got '{sim.status}'"
    )

    # 5. After grace expires, bad readings should trigger recovery
    time.sleep(0.4)
    assert sim.status != "locked", (
        f"Expected lock to be lost after grace period with bad readings, got '{sim.status}'"
    )
    sim.stop()
