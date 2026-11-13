"""The protection of the microscope while the agent drives: shield, panel lock, banner and stop sequence follow
the session, and the agent gets the control only after the protection is in place."""

import threading
from types import SimpleNamespace

import pytest
from qtpy.QtCore import Qt, QThread, QTimer
from qtpy.QtWidgets import QApplication, QDialog, QInputDialog, QMainWindow, QMessageBox, QPushButton

import control.agent_session_stop as stop_module
from control.agent_session_protection import AgentSessionProtection
from control.agent_session_stop import WAIT_STAGE, AgentSessionStopSequence
from control.agent_session_widgets import AgentSessionBanner, InputShield
from control.core.agent_session import AgentSession, AgentSessionRefused, SessionState

AGENT_HAS_CONTROL = (SessionState.AGENT, SessionState.STOPPING)


class FakeController:
    def __init__(self):
        self.running = False

    def acquisition_in_progress(self):
        return self.running

    def request_abort_aquisition(self):
        self.running = False


class FakeStage:
    def __init__(self):
        self.stopped = True

    def reports_stopped(self):
        return self.stopped


class Panel:
    """The control panel of the test: lock() and unlock() as the window gives them to the protection."""

    def __init__(self):
        self.locked = False
        self.fail = False
        self.calls = []
        self.lock_threads = []

    def lock(self):
        self.lock_threads.append(QThread.currentThread())
        if self.fail:
            raise RuntimeError("no answer from the controller")
        if not self.locked:
            self.calls.append("lock")
        self.locked = True

    def unlock(self):
        if self.locked:
            self.calls.append("unlock")
        self.locked = False


@pytest.fixture
def rig(qtbot, monkeypatch):
    monkeypatch.setattr(stop_module, "AGENT_SESSION_STOP_POLL_MS", 5)
    window = QMainWindow()
    window.outside = QPushButton("outside")
    window.setCentralWidget(window.outside)
    banner = AgentSessionBanner()
    window.addToolBar(banner)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)

    session = AgentSession()
    r = SimpleNamespace(
        window=window, banner=banner, session=session, mpc=FakeController(), stage=FakeStage(), panel=Panel(),
        blocker_reason=None, snapshots=[], snapshot_fails=False, now=0.0,
    )  # fmt: skip

    def snapshot(with_position):
        if r.snapshot_fails:
            raise RuntimeError("the camera does not answer")
        r.snapshots.append(with_position)
        return {"x_mm": 1.0 if with_position else None}

    r.shield = InputShield(banner)
    r.stop = AgentSessionStopSequence(session, r.mpc, r.stage, snapshot, clock=lambda: r.now)
    r.protection = AgentSessionProtection(
        session=session,
        banner=banner,
        shield=r.shield,
        stop_sequence=r.stop,
        lock_panel=r.panel.lock,
        unlock_panel=r.panel.unlock,
        blocker=lambda: r.blocker_reason,
        snapshot=snapshot,
        parent=window,
    )
    yield r
    r.protection.shut_down()


def protected(r) -> bool:
    return r.shield.is_active() and r.panel.locked


def begin(r) -> None:
    assert r.protection.transition("begin") is None


def take_over_and_wait(r, qtbot) -> None:
    r.session.request_stop(SessionState.HUMAN, "take over")
    qtbot.waitUntil(lambda: r.session.state is SessionState.HUMAN and not r.shield.is_active(), timeout=2000)


def test_begin_puts_the_protection_on_before_the_agent_gets_the_control(rig):
    seen = []
    rig.session.add_listener(lambda old, new: seen.append((new, rig.shield.is_active(), rig.panel.locked)))
    begin(rig)
    assert seen == [(SessionState.AGENT, True, True)]
    assert rig.session.state is SessionState.AGENT and protected(rig)
    assert rig.banner.label.text() == "The agent is driving." and not rig.banner.isHidden()


def test_begin_that_a_blocker_refuses_touches_nothing(rig):
    rig.blocker_reason = "the stage is homing"
    assert rig.protection.transition("begin") == "the stage is homing"
    assert rig.session.state is SessionState.NONE and not rig.shield.is_active()
    assert rig.panel.calls == [] and rig.panel.lock_threads == []


def test_begin_is_refused_while_a_dialog_is_open(rig, qtbot):
    dialog = QDialog(rig.window)
    dialog.setModal(True)
    dialog.show()
    qtbot.waitExposed(dialog)
    assert rig.protection.transition("begin") == "a dialog or a menu is open in Squid"
    assert rig.session.state is SessionState.NONE and rig.panel.calls == []
    dialog.close()
    begin(rig)


def test_a_panel_lock_that_fails_refuses_begin_and_leaves_no_shield(rig):
    rig.panel.fail = True
    assert rig.protection.transition("begin") == "could not lock the control panel"
    assert rig.session.state is SessionState.NONE and not rig.shield.is_active() and not rig.panel.locked


def test_a_refusal_after_the_protection_takes_the_protection_off_again(rig):
    answers = iter([None, "an acquisition is running"])  # free at the first question, not at the change itself
    rig.protection.shut_down()
    rig.protection = AgentSessionProtection(
        session=rig.session, banner=rig.banner, shield=rig.shield, stop_sequence=rig.stop,
        lock_panel=rig.panel.lock, unlock_panel=rig.panel.unlock, blocker=lambda: next(answers),
        snapshot=lambda with_position: {}, parent=rig.window,
    )  # fmt: skip
    assert rig.protection.transition("begin") == "an acquisition is running"
    assert rig.session.state is SessionState.NONE and not rig.shield.is_active()
    assert rig.panel.calls == ["lock", "unlock"]


def test_begin_two_times_keeps_the_protection_of_the_first(rig):
    begin(rig)
    assert rig.protection.transition("begin") == "a session exists"
    assert rig.session.state is SessionState.AGENT and protected(rig) and rig.panel.calls == ["lock"]


def test_an_unknown_kind_is_refused(rig):
    assert "kind" in rig.protection.transition("end")
    assert rig.session.state is SessionState.NONE and rig.panel.calls == []


def test_take_over_from_the_banner_gives_the_control_to_the_human(rig, qtbot):
    begin(rig)
    qtbot.mouseClick(rig.banner.take_over_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: rig.session.state is SessionState.HUMAN and not rig.shield.is_active(), timeout=2000)
    assert not rig.panel.locked and rig.banner.label.text() == "You are driving. The agent is waiting."
    assert rig.session.status()["stop_cause"] == "take over"
    with qtbot.waitSignal(rig.window.outside.clicked, timeout=1000):
        qtbot.mouseClick(rig.window.outside, Qt.LeftButton)


def test_hand_back_from_the_banner_gives_the_control_to_the_agent_with_the_line(rig, qtbot):
    begin(rig)
    take_over_and_wait(rig, qtbot)
    rig.banner.signal_hand_back.emit("looked at one organism")
    status = rig.session.status()
    assert status["state"] == "agent" and protected(rig)
    assert status["handover"]["note"] == "looked at one organism" and status["handover"]["after"] == {"x_mm": 1.0}
    assert rig.banner.label.text() == "The agent is driving."


def test_hand_back_from_the_banner_with_the_real_dialog(rig, qtbot):
    """The dialog for the line of the human is modal. It must be closed for Qt when the change is asked, or the
    change would be refused with "a dialog or a menu is open in Squid"."""
    begin(rig)
    take_over_and_wait(rig, qtbot)
    answers = QTimer()

    def answer():
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, QInputDialog):
            answers.stop()
            dialog.setTextValue("moved the stage")
            dialog.accept()

    answers.timeout.connect(answer)
    answers.start(50)
    try:
        qtbot.mouseClick(rig.banner.hand_back_button, Qt.LeftButton)  # returns when the dialog is closed
    finally:
        answers.stop()
    status = rig.session.status()
    assert status["state"] == "agent" and protected(rig) and status["handover"]["note"] == "moved the stage"


def test_hand_back_that_is_refused_tells_the_human_why(rig, qtbot, monkeypatch):
    begin(rig)
    take_over_and_wait(rig, qtbot)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: shown.append(text))
    rig.blocker_reason = "an acquisition is running"
    rig.banner.signal_hand_back.emit("")
    assert rig.session.state is SessionState.HUMAN and not rig.shield.is_active() and not rig.panel.locked
    assert len(shown) == 1 and "an acquisition is running" in shown[0]


def test_a_failure_inside_the_change_is_a_refusal_and_not_a_silent_success(rig, qtbot):
    begin(rig)
    take_over_and_wait(rig, qtbot)
    rig.snapshot_fails = True
    answers = []
    thread = threading.Thread(target=lambda: answers.append(rig.protection.transition("hand_back", "")))
    thread.start()
    qtbot.waitUntil(lambda: bool(answers), timeout=2000)
    thread.join()
    assert answers[0] and "RuntimeError" in answers[0]
    assert rig.session.state is SessionState.HUMAN and not rig.shield.is_active() and not rig.panel.locked


def test_end_from_the_banner_while_a_human_drives(rig, qtbot):
    begin(rig)
    take_over_and_wait(rig, qtbot)
    qtbot.mouseClick(rig.banner.end_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: rig.banner.isHidden(), timeout=2000)
    assert rig.session.state is SessionState.NONE and not rig.shield.is_active() and not rig.panel.locked


def test_an_end_while_the_agent_drives_goes_through_the_stop_sequence(rig, qtbot):
    begin(rig)
    rig.stage.stopped = False
    threading.Thread(target=rig.session.end).start()
    qtbot.waitUntil(lambda: rig.banner.label.text() == f"Stopping. Waiting: {WAIT_STAGE}", timeout=2000)
    assert rig.session.state is SessionState.STOPPING and protected(rig)
    rig.stage.stopped = True
    qtbot.waitUntil(lambda: rig.banner.isHidden(), timeout=2000)
    assert rig.session.state is SessionState.NONE and not rig.shield.is_active() and not rig.panel.locked


def test_the_change_from_a_server_thread_runs_on_the_gui_thread(rig, qtbot):
    answers = []
    thread = threading.Thread(target=lambda: answers.append(rig.protection.transition("begin")))
    thread.start()
    qtbot.waitUntil(lambda: bool(answers), timeout=2000)
    thread.join()
    assert answers == [None] and rig.session.state is SessionState.AGENT and protected(rig)
    gui_thread = QApplication.instance().thread()
    assert rig.panel.lock_threads and all(thread == gui_thread for thread in rig.panel.lock_threads)
    assert rig.panel.calls == ["lock"]  # more calls of the lock function change nothing


def test_a_state_that_changed_with_no_transition_gets_the_protection_at_the_next_sync(rig, qtbot):
    thread = threading.Thread(target=rig.session.begin)  # not the path of the server, but nothing forbids it
    thread.start()
    thread.join()
    qtbot.waitUntil(lambda: protected(rig), timeout=2000)
    assert not rig.banner.isHidden()


def test_begin_and_take_over_at_the_same_moment_never_leave_the_wrong_protection(rig, qtbot):
    for _ in range(200):
        barrier = threading.Barrier(2)

        def do_begin():
            barrier.wait()
            rig.protection.transition("begin")

        def do_take_over():
            barrier.wait()
            try:
                rig.session.request_stop(SessionState.HUMAN, "take over")
            except AgentSessionRefused:
                pass  # the take over came before the begin

        threads = [threading.Thread(target=do_begin), threading.Thread(target=do_take_over)]
        for thread in threads:
            thread.start()
        qtbot.waitUntil(lambda: not any(thread.is_alive() for thread in threads), timeout=5000)
        qtbot.waitUntil(lambda: rig.session.state is not SessionState.STOPPING, timeout=5000)
        QApplication.processEvents()
        state = rig.session.state
        assert state in (SessionState.AGENT, SessionState.HUMAN)
        assert rig.shield.is_active() == (state in AGENT_HAS_CONTROL)
        assert rig.panel.locked == (state in AGENT_HAS_CONTROL)
        # back to no session for the next round
        if state is SessionState.AGENT:
            take_over_and_wait(rig, qtbot)
        rig.session.end()
        QApplication.processEvents()
        assert rig.session.state is SessionState.NONE and not rig.shield.is_active() and not rig.panel.locked


def test_a_dialog_that_opens_while_the_agent_drives_takes_over(rig, qtbot):
    begin(rig)
    dialog = QDialog(rig.window)
    button = QPushButton("OK", dialog)
    button.clicked.connect(dialog.accept)
    dialog.setModal(True)
    dialog.show()
    qtbot.waitExposed(dialog)
    qtbot.waitUntil(lambda: rig.session.state is SessionState.HUMAN and not rig.shield.is_active(), timeout=2000)
    assert rig.session.status()["stop_cause"] == "a dialog opened in Squid"
    qtbot.mouseClick(button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not dialog.isVisible(), timeout=1000)


def test_a_dialog_under_a_command_in_progress_can_be_answered_after_the_time_limit(rig, qtbot):
    begin(rig)
    with rig.session.admit("run_acquisition_from_yaml"):  # the command waits for the dialog
        dialog = QDialog(rig.window)
        button = QPushButton("OK", dialog)
        button.clicked.connect(dialog.accept)
        dialog.setModal(True)
        dialog.show()
        qtbot.waitExposed(dialog)
        qtbot.waitUntil(lambda: "a command is in progress" in rig.banner.label.text(), timeout=2000)
        qtbot.mouseClick(button, Qt.LeftButton)
        assert dialog.isVisible() and rig.session.state is SessionState.STOPPING
        assert not rig.banner.unlock_anyway_button.isVisibleTo(rig.banner)
        rig.now = 1000.0  # the time limit passes
        qtbot.waitUntil(lambda: rig.banner.unlock_anyway_button.isVisibleTo(rig.banner), timeout=2000)
        qtbot.mouseClick(button, Qt.LeftButton)
        qtbot.waitUntil(lambda: not dialog.isVisible(), timeout=1000)
        assert rig.session.state is SessionState.STOPPING and protected(rig)  # the command did not end yet
    qtbot.waitUntil(lambda: rig.session.state is SessionState.HUMAN and not rig.shield.is_active(), timeout=2000)


def test_unlock_anyway_from_the_banner(rig, qtbot):
    begin(rig)
    rig.stage.stopped = False
    rig.session.request_stop(SessionState.HUMAN, "take over")
    qtbot.waitUntil(lambda: WAIT_STAGE in rig.banner.label.text(), timeout=2000)
    rig.now = 1000.0
    qtbot.waitUntil(lambda: rig.banner.unlock_anyway_button.isVisibleTo(rig.banner), timeout=2000)
    qtbot.mouseClick(rig.banner.unlock_anyway_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: rig.session.state is SessionState.HUMAN and not rig.shield.is_active(), timeout=2000)
    assert rig.session.status()["unlocked_anyway"] is True and not rig.panel.locked


def test_after_shut_down_nothing_follows_the_session(rig, qtbot):
    begin(rig)
    rig.session.request_stop(SessionState.HUMAN, "take over")
    rig.protection.shut_down()
    assert not rig.shield.is_active() and not rig.stop.is_running()
    qtbot.wait(100)  # the notification of the stop that was on its way does nothing
    assert not rig.shield.is_active() and rig.session.state is SessionState.STOPPING
    assert rig.protection.transition("begin") == "Squid is closing"
