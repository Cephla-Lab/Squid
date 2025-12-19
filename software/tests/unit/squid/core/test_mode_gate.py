"""Tests for GlobalModeGate."""

from squid.core.events import EventBus, GlobalModeChanged
from squid.core.mode_gate import GlobalMode, GlobalModeGate


def test_set_mode_publishes_global_mode_changed() -> None:
    bus = EventBus()
    gate = GlobalModeGate(bus)

    received: list[GlobalModeChanged] = []
    bus.subscribe(GlobalModeChanged, received.append)

    gate.set_mode(GlobalMode.LIVE, reason="live start")
    bus.drain()

    assert len(received) == 1
    assert received[0].old_mode == "IDLE"
    assert received[0].new_mode == "LIVE"
    assert received[0].reason == "live start"


def test_try_set_mode_respects_expected_mode() -> None:
    bus = EventBus()
    gate = GlobalModeGate(bus)

    assert gate.try_set_mode(GlobalMode.IDLE, GlobalMode.ACQUIRING, reason="acq") is True
    assert gate.try_set_mode(GlobalMode.IDLE, GlobalMode.LIVE, reason="nope") is False
