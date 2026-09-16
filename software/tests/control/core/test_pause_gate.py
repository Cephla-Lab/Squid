"""Tests for control.core.pause_gate.PauseGate — the single pause control point of an acquisition."""

import threading
import time

import pytest

from control.core.pause_gate import PauseGate, PauseState


def _wait_in_thread(gate: PauseGate, cancel_fn=lambda: False, poll_s: float = 0.05, tick=None):
    result = {}

    def _run():
        result["paused_s"] = gate.wait_while_paused(cancel_fn=cancel_fn, tick=tick, poll_s=poll_s)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread, result


def test_initial_state_is_not_paused():
    gate = PauseGate()
    assert not gate.is_paused()
    state = gate.state()
    assert isinstance(state, PauseState)
    assert state.paused is False
    assert state.reasons == ()
    assert state.since is None
    assert state.paused_total_s == 0.0


def test_hold_and_release_are_idempotent_and_support_multiple_holders():
    gate = PauseGate()
    gate.hold("disk_space")
    gate.hold("disk_space")
    assert gate.is_paused()
    assert gate.state().reasons == ("disk_space",)

    gate.hold("operator")
    assert gate.state().reasons == ("disk_space", "operator")

    # Releasing one holder keeps the gate paused while another holder remains.
    gate.release("disk_space")
    assert gate.is_paused()
    assert gate.state().reasons == ("operator",)

    gate.release("disk_space")  # already released: no-op
    assert gate.is_paused()

    gate.release("operator")
    assert not gate.is_paused()
    assert gate.state().reasons == ()


def test_release_of_unknown_reason_is_a_no_op():
    gate = PauseGate()
    gate.release("never_held")
    assert not gate.is_paused()


def test_wait_returns_immediately_when_not_paused():
    gate = PauseGate()
    ticks = []
    assert gate.wait_while_paused(cancel_fn=lambda: False, tick=lambda: ticks.append(1)) == 0.0
    assert ticks == []


def test_wait_blocks_until_all_holders_release_and_reports_elapsed():
    gate = PauseGate()
    gate.hold("disk_space")
    gate.hold("operator")
    thread, result = _wait_in_thread(gate)

    time.sleep(0.2)
    assert thread.is_alive()
    gate.release("disk_space")
    time.sleep(0.1)
    assert thread.is_alive(), "second holder must keep the waiter blocked"

    gate.release("operator")
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert result["paused_s"] >= 0.25


def test_release_wakes_waiter_without_waiting_for_the_poll_interval():
    gate = PauseGate()
    gate.hold("operator")
    thread, result = _wait_in_thread(gate, poll_s=5.0)
    time.sleep(0.1)
    gate.release("operator")
    thread.join(timeout=1.0)
    assert not thread.is_alive(), "release() must notify the waiter, not rely on the poll timeout"
    assert result["paused_s"] < 1.0


def test_cancel_wakes_waiter_within_one_poll_and_leaves_holders_in_place():
    gate = PauseGate()
    gate.hold("disk_space")
    cancel = threading.Event()
    thread, result = _wait_in_thread(gate, cancel_fn=cancel.is_set, poll_s=0.05)
    time.sleep(0.1)
    cancel.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    # Abort does not release the pause reasons; the acquisition simply stops waiting.
    assert gate.is_paused()
    assert gate.state().reasons == ("disk_space",)


def test_cancel_already_set_returns_immediately_even_when_paused():
    gate = PauseGate()
    gate.hold("operator")
    started = time.monotonic()
    paused_s = gate.wait_while_paused(cancel_fn=lambda: True, poll_s=5.0)
    assert time.monotonic() - started < 1.0
    assert paused_s < 1.0


def test_tick_is_called_every_poll_and_may_release_the_gate():
    gate = PauseGate()
    gate.hold("disk_space")
    ticks = []

    def tick():
        ticks.append(time.monotonic())
        if len(ticks) >= 3:
            gate.release("disk_space")  # e.g. disk guard re-check found space

    paused_s = gate.wait_while_paused(cancel_fn=lambda: False, tick=tick, poll_s=0.02)
    assert len(ticks) >= 3
    assert paused_s >= 0.0


def test_state_tracks_since_and_accumulates_total_across_pauses():
    gate = PauseGate()
    gate.hold("operator")
    since = gate.state().since
    assert since is not None
    assert since <= time.time()
    time.sleep(0.1)
    gate.release("operator")
    assert gate.state().since is None
    first_total = gate.state().paused_total_s
    assert first_total >= 0.08

    gate.hold("disk_space")
    time.sleep(0.1)
    gate.release("disk_space")
    assert gate.state().paused_total_s >= first_total + 0.08


def test_clear_releases_all_holders_and_wakes_waiter():
    gate = PauseGate()
    gate.hold("disk_space")
    gate.hold("operator")
    thread, _ = _wait_in_thread(gate, poll_s=5.0)
    time.sleep(0.05)
    gate.clear()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert not gate.is_paused()


def test_hold_requires_a_reason():
    gate = PauseGate()
    with pytest.raises(ValueError):
        gate.hold("")
