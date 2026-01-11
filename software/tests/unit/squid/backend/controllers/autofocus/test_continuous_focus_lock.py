"""Tests for ContinuousFocusLockController."""

from squid.backend.controllers.autofocus.continuous_focus_lock import (
    ContinuousFocusLockController,
)
from squid.core.config.focus_lock import FocusLockConfig
from squid.core.events import EventBus, FocusLockStatusChanged


class _DummyLaserAFProps:
    correlation_threshold = 0.8


class _DummyLaserAF:
    def __init__(self) -> None:
        self.laser_af_properties = _DummyLaserAFProps()
        self.on_calls = 0
        self.off_calls = 0

    def turn_on_laser(self, bypass_mode_gate: bool = False) -> None:  # noqa: ARG002
        self.on_calls += 1

    def turn_off_laser(self, bypass_mode_gate: bool = False) -> None:  # noqa: ARG002
        self.off_calls += 1


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

    assert controller._control_fn(1.0) < 0
    assert controller._control_fn(-1.0) > 0


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
