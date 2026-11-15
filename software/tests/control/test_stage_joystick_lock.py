"""A stage controller with a joystick of its own (a Prior ProScan) is locked together with the control panel: for
the loading position, for homing and for an agent session."""

import logging
from unittest.mock import MagicMock

import pytest

from control.agent_session_widgets import PANEL_NOT_LOCKED_STAGE_JOYSTICK_LOCKED_WARNING, PANEL_NOT_LOCKED_WARNING
from control.gui_hcs import HighContentScreeningGui as Gui
from control.panel_lock import PANEL_LOCK_AGENT, PANEL_LOCK_HOMING, PANEL_LOCK_LOADING, PanelLockReasons
from control.panel_lock import PanelLockRefused


class FakeController:
    """The Cephla controller: set_panel_lock() locks its control panel (firmware 1.8 or later)."""

    def __init__(self, can_lock=True):
        self.can_lock = can_lock
        self.sent = []
        self.fail = False
        self.no_answer = False

    def supports_panel_lock(self):
        return self.can_lock

    def set_panel_lock(self, locked):
        if self.fail:
            raise RuntimeError("the controller is gone")
        self.sent.append(locked)

    def wait_till_operation_is_completed(self, *args, **kwargs):
        if self.no_answer:
            raise TimeoutError("no answer from the controller")


class FakeStage:
    def __init__(self, own_joystick=True):
        self.own_joystick = own_joystick
        self.sent = []
        self.fail = False

    def has_own_joystick(self):
        return self.own_joystick

    def set_joystick_lock(self, locked):
        self.sent.append(locked)  # sent: the controller can have taken it even when the answer is wrong
        if self.fail:
            raise RuntimeError("the stage controller did not answer")


class _Window:
    """The data of the window that the lock uses; the methods are those of the window."""

    def __getattr__(self, name):
        return getattr(Gui, name).__get__(self)


def window(controller=None, stage=None):
    w = _Window()
    w.__dict__.update(
        microcontroller=controller if controller is not None else FakeController(),
        stage=stage if stage is not None else FakeStage(),
        log=logging.getLogger("test_stage_joystick_lock"),
        _is_simulation=False,
        _panel_lock_unsupported_logged=False,
        _agent_banner=MagicMock(),
    )
    w._panel_lock = PanelLockReasons(w._set_control_panel_lock)
    return w


@pytest.mark.parametrize("reason", [PANEL_LOCK_LOADING, PANEL_LOCK_HOMING])
def test_the_loading_position_and_homing_lock_the_joystick_of_the_stage(reason):
    w = window()
    w._panel_lock.add(reason)
    assert w.microcontroller.sent == [True] and w.stage.sent == [True]
    w._panel_lock.remove(reason)
    assert w.microcontroller.sent == [True, False] and w.stage.sent == [True, False]


def test_an_agent_session_locks_the_joystick_of_the_stage_and_its_end_unlocks_it():
    w = window()
    w._lock_panel_for_agent_session()
    assert w.microcontroller.sent == [True] and w.stage.sent == [True]
    w._agent_banner.set_panel_warning.assert_called_with("")
    w._panel_lock.add(PANEL_LOCK_HOMING)
    w._panel_lock.remove(PANEL_LOCK_HOMING)  # homing inside the session: still locked
    assert w.stage.sent == [True]
    w._panel_lock.remove(PANEL_LOCK_AGENT)
    assert w.microcontroller.sent == [True, False] and w.stage.sent == [True, False]


def test_a_failed_stage_joystick_lock_for_homing_is_logged_and_the_panel_still_changes(caplog):
    w = window()
    w.stage.fail = True
    with caplog.at_level(logging.ERROR):
        w._panel_lock.add(PANEL_LOCK_HOMING)
        w._panel_lock.remove(PANEL_LOCK_HOMING)
    assert w.microcontroller.sent == [True, False] and w.stage.sent == [True, False]
    assert [r.getMessage() for r in caplog.records] == [
        "Could not lock the joystick of the stage",
        "Could not unlock the joystick of the stage",
    ]


def test_a_failed_control_panel_unlock_still_unlocks_the_joystick_of_the_stage():
    w = window()
    w._panel_lock.add(PANEL_LOCK_LOADING)
    w.microcontroller.fail = True
    w._panel_lock.remove(PANEL_LOCK_LOADING)
    assert w.stage.sent == [True, False]


def test_an_agent_session_is_refused_when_the_stage_does_not_confirm_and_its_joystick_is_unlocked_again():
    w = window()
    w.stage.fail = True
    with pytest.raises(PanelLockRefused, match="^could not lock the joystick of the stage$"):
        w._lock_panel_for_agent_session()
    # The reason is held from before the send: the unlock on the failure path of the session unlocks both.
    assert PANEL_LOCK_AGENT in w._panel_lock.held()
    w.stage.fail = False
    w._panel_lock.remove(PANEL_LOCK_AGENT)
    assert w.microcontroller.sent == [True, False] and w.stage.sent == [True, False]


def test_an_agent_session_is_refused_when_the_controller_does_not_confirm_and_both_are_unlocked_again():
    w = window()
    w.microcontroller.no_answer = True
    with pytest.raises(PanelLockRefused, match="^could not lock the control panel$"):
        w._lock_panel_for_agent_session()
    assert w.stage.sent == []  # the session is refused at the first lock that is not confirmed
    w._panel_lock.remove(PANEL_LOCK_AGENT)
    assert w.microcontroller.sent == [True, False] and w.stage.sent == [False]


@pytest.mark.parametrize(
    "own_joystick, warning",
    [(True, PANEL_NOT_LOCKED_STAGE_JOYSTICK_LOCKED_WARNING), (False, PANEL_NOT_LOCKED_WARNING)],
    ids=["stage-with-joystick", "stage-without"],
)
def test_with_a_control_panel_that_cannot_lock_the_banner_says_what_is_locked(own_joystick, warning, caplog):
    """A controller with firmware older than 1.8 and the setting agent_session_allow_unlocked_panel on (the
    session blocker refuses the session otherwise)."""
    w = window(FakeController(can_lock=False), FakeStage(own_joystick=own_joystick))
    with caplog.at_level(logging.WARNING):
        w._lock_panel_for_agent_session()
    w._agent_banner.set_panel_warning.assert_called_once_with(warning)
    assert w.microcontroller.sent == [] and w.stage.sent == [True]
    messages = [r.getMessage() for r in caplog.records if "Agent session" in r.getMessage()]
    assert len(messages) == 1 and ("joystick of the stage is locked" in messages[0]) == own_joystick


def test_the_texts_for_a_stage_with_a_joystick_of_its_own():
    assert PANEL_NOT_LOCKED_WARNING == "The control panel is not locked: do not touch the joystick."
    assert PANEL_NOT_LOCKED_STAGE_JOYSTICK_LOCKED_WARNING == (
        "The control panel is not locked: do not touch it. The joystick of the stage is locked."
    )
