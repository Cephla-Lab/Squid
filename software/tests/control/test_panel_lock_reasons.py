"""The hardware control panel is locked while one reason or more is held."""

from control.panel_lock import PANEL_LOCK_AGENT, PANEL_LOCK_HOMING, PANEL_LOCK_LOADING, PanelLockReasons


def test_first_reason_locks_and_last_reason_unlocks():
    calls = []
    lock = PanelLockReasons(calls.append)
    lock.add(PANEL_LOCK_HOMING)
    lock.add(PANEL_LOCK_AGENT)
    assert calls == [True]
    lock.remove(PANEL_LOCK_HOMING)
    assert calls == [True] and lock.held() == frozenset({PANEL_LOCK_AGENT})  # the end of a homing run does not unlock
    lock.remove(PANEL_LOCK_AGENT)
    assert calls == [True, False] and lock.held() == frozenset()


def test_same_reason_twice_and_a_reason_that_is_not_held_are_no_ops():
    calls = []
    lock = PanelLockReasons(calls.append)
    lock.add(PANEL_LOCK_LOADING)
    lock.add(PANEL_LOCK_LOADING)
    lock.remove(PANEL_LOCK_AGENT)
    assert calls == [True] and lock.held() == frozenset({PANEL_LOCK_LOADING})
    lock.remove(PANEL_LOCK_LOADING)
    lock.remove(PANEL_LOCK_LOADING)
    assert calls == [True, False]


def test_loading_and_homing_together_unlock_only_when_the_two_end():
    calls = []
    lock = PanelLockReasons(calls.append)
    lock.add(PANEL_LOCK_LOADING)
    lock.add(PANEL_LOCK_HOMING)
    lock.remove(PANEL_LOCK_LOADING)
    assert calls == [True]
    lock.remove(PANEL_LOCK_HOMING)
    assert calls == [True, False]
