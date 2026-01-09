"""Tests for FocusLockSimulator."""

import time

from squid.backend.controllers.autofocus.focus_lock_simulator import FocusLockSimulator
from squid.core.config.focus_lock import FocusLockConfig
from squid.core.events import (
    EventBus,
    FocusLockMetricsUpdated,
    FocusLockModeChanged,
    FocusLockStatusChanged,
    LaserAFInitialized,
    SetFocusLockModeCommand,
    SetFocusLockReferenceCommand,
)


def _init_laser_af(bus: EventBus) -> None:
    """Publish LaserAFInitialized event to enable the simulator to start."""
    bus.publish(LaserAFInitialized(is_initialized=True, success=True))
    bus.drain()


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


def test_wait_for_lock():
    """wait_for_lock should reflect lock acquisition state."""
    bus = EventBus()
    config = FocusLockConfig(loop_rate_hz=50, metrics_rate_hz=20, buffer_length=3)
    sim = FocusLockSimulator(bus, config=config)
    _init_laser_af(bus)

    # When not running, wait_for_lock returns True immediately
    assert sim.wait_for_lock(timeout_s=0.1) is True

    sim.start()
    # After start, need to explicitly set lock
    bus.publish(SetFocusLockReferenceCommand())
    bus.drain()
    # Give time for lock buffer to fill
    time.sleep(0.2)
    assert sim.wait_for_lock(timeout_s=1.0) is True
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
