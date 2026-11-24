"""start_fluidics_protocol: the control server starts a run through the Fluidics Protocol tab's own start path,
on the GUI thread, without opening a dialog. Offscreen, on the fake fluidics and imaging ports."""

import os
import threading
from unittest.mock import MagicMock

import pytest

import control.widgets_fluidics.protocol_widget as protocol_widget_module
from control.core.fluidics_protocol import runner as runner_module
from control.core.fluidics_protocol.events import RunnerState
from control.core.fluidics_protocol import manifest as manifest_io
from control.microscope_control_server import MicroscopeControlServer
from control.models.fluidics_protocol import save_protocol
from tests.control.core.fluidics_protocol.fakes import wait_until
from tests.control.widgets_fluidics.test_protocol_widget import _protocol, quiet_dialogs, widget  # noqa: F401


@pytest.fixture
def no_dialogs(monkeypatch, quiet_dialogs):  # noqa: F811
    """Any dialog the server path opened would hang a real GUI: record it instead."""
    opened = []
    monkeypatch.setattr(protocol_widget_module.PreflightDialog, "exec_", lambda self: opened.append(self) or 0)
    yield opened, quiet_dialogs


def _server(w, control=True):
    server = MicroscopeControlServer(microscope=MagicMock(stage_homing_in_progress=False), port=0, gui=None)
    server.gui = MagicMock(fluidicsProtocolWidget=w)
    server.set_fluidics_protocol_control_enabled(control)
    return server


def _from_another_thread(qtbot, server, command, **params):
    """As a client: the command runs on a socket thread and must marshal to the GUI thread to start."""
    reply = {}
    thread = threading.Thread(target=lambda: reply.update(server._execute(command, params)))
    thread.start()
    qtbot.waitUntil(lambda: not thread.is_alive(), timeout=15000)  # keeps the GUI event loop turning
    return reply


def test_without_confirm_nothing_starts_and_the_reply_says_what_the_dialog_would_ask(qtbot, widget, no_dialogs):
    w, fluidics, _imaging, save_to = widget
    opened, warnings = no_dialogs
    reply = _from_another_thread(qtbot, _server(w), "start_fluidics_protocol", run_name="kidney")
    assert not reply["success"]
    assert "asks to confirm" in reply["error"] and "1 imaging session(s)" in reply["error"]
    assert "confirm=true" in reply["error"]
    assert w.runner is None and fluidics.starts == [] and not any(save_to.iterdir())
    assert w.run_name_edit.text() == "liver"  # a refused start leaves the tab as it was
    assert opened == [] and warnings == []


def test_confirm_true_starts_the_run_like_the_start_button(qtbot, widget, no_dialogs):
    w, fluidics, imaging, save_to = widget
    opened, warnings = no_dialogs
    started = []
    w.signal_acquisition_started.connect(started.append)
    server = _server(w)
    reply = _from_another_thread(qtbot, server, "start_fluidics_protocol", confirm=True, run_name="kidney")
    assert reply["success"], reply
    result = reply["result"]
    assert result["started"] and result["run_name"] == "kidney" and result["protocol_name"] == "demo"
    assert result["run_dir"] == str(w.runner.run_dir) and result["run_dir"].startswith(str(save_to / "kidney_"))
    assert any(line.startswith("run folder:") for line in result["summary"])
    assert started[:1] == [True]  # the tab took the run as its own (it may have finished already on fakes)
    assert opened == [] and warnings == []

    status = server._execute("get_fluidics_protocol_status", {})["result"]
    assert status["has_run"] and status["run_name"] == "kidney"
    assert w.runner.wait(15) and w.runner.outcome == "finished"  # wait(): the outcome is set before the save
    assert manifest_io.read_manifest(w.runner.run_dir).status == "finished"
    assert len(imaging.requests) == 1


def test_a_start_the_button_would_refuse_is_refused_without_a_message_box(qtbot, widget, no_dialogs):
    w, fluidics, _imaging, _save_to = widget
    opened, warnings = no_dialogs
    w.set_fluidics_port(None)
    reply = _from_another_thread(qtbot, _server(w), "start_fluidics_protocol", confirm=True)
    assert not reply["success"] and "Initialize the fluidics system first" in reply["error"]
    assert w.runner is None and opened == [] and warnings == []


def test_a_second_start_while_a_run_is_active_is_refused(qtbot, widget, no_dialogs):
    w, fluidics, _imaging, _save_to = widget
    fluidics.script = [("hold",)]
    server = _server(w)
    assert _from_another_thread(qtbot, server, "start_fluidics_protocol", confirm=True)["success"]
    first = w.runner
    reply = _from_another_thread(qtbot, server, "start_fluidics_protocol", confirm=True, run_name="again")
    assert not reply["success"] and "already in progress" in reply["error"]
    assert w.runner is first
    first.abort_run()
    assert first.wait(10)


def test_a_given_protocol_file_is_opened_first_unless_the_open_one_has_unsaved_changes(
    qtbot, widget, no_dialogs, tmp_path
):
    w, fluidics, _imaging, _save_to = widget
    other = _protocol()
    other.name = "other"
    path = tmp_path / "other.yaml"
    save_protocol(other, str(path))
    server = _server(w)

    w.protocol_tab._mark_changed()  # an edit the GUI would ask to discard
    reply = _from_another_thread(qtbot, server, "start_fluidics_protocol", confirm=True, protocol_path=str(path))
    assert not reply["success"] and "unsaved changes" in reply["error"]
    assert w.protocol_tab.protocol.name == "demo" and w.runner is None

    w.protocol_tab.set_protocol(_protocol())  # saved / discarded
    reply = _from_another_thread(qtbot, server, "start_fluidics_protocol", protocol_path=str(path))
    assert not reply["success"] and w.protocol_tab.protocol.name == "demo"  # not confirmed: the tab is put back

    reply = _from_another_thread(qtbot, server, "start_fluidics_protocol", confirm=True, protocol_path=str(path))
    assert reply["success"] and reply["result"]["protocol_name"] == "other"
    assert w.protocol_tab.protocol_path == str(path)
    assert wait_until(lambda: w.runner.outcome is not None, timeout=15)


def test_confirm_must_be_a_boolean(qtbot, widget, no_dialogs):
    w = widget[0]
    reply = _server(w)._execute("start_fluidics_protocol", {"confirm": "yes"})
    assert not reply["success"] and "confirm must be true or false" in reply["error"] and w.runner is None


def test_a_second_start_while_the_first_run_is_still_opening_is_refused(qtbot, widget, no_dialogs, monkeypatch):
    """The runner is IDLE until its thread has opened the run folder (mkdir, run.log, protocol copy, hash); a
    second start in that window must not replace the first runner."""
    w, fluidics, _imaging, _save_to = widget
    fluidics.script = [("hold",)]
    opening = threading.Event()
    release = threading.Event()
    original_open_run = runner_module.ProtocolRunner._open_run

    def slow_open_run(runner):
        opening.set()
        release.wait(10)  # e.g. a slow network Save to drive
        return original_open_run(runner)

    monkeypatch.setattr(runner_module.ProtocolRunner, "_open_run", slow_open_run)
    server = _server(w)
    assert _from_another_thread(qtbot, server, "start_fluidics_protocol", confirm=True, run_name="first")["success"]
    first = w.runner
    try:
        assert opening.wait(5) and first.state is RunnerState.IDLE
        assert w.is_run_active()
        reply = _from_another_thread(qtbot, server, "start_fluidics_protocol", confirm=True, run_name="second")
        assert not reply["success"] and "already in progress" in reply["error"]
        assert w.runner is first
    finally:
        release.set()
        first.abort_run()
        assert first.wait(10)


def test_a_launch_that_fails_releases_the_run(qtbot, widget, no_dialogs, monkeypatch):
    w, _fluidics, _imaging, _save_to = widget
    started = []
    w.signal_acquisition_started.connect(started.append)

    original_start = runner_module.ProtocolRunner.start
    fail = [True]

    def start(runner):
        if fail[0]:
            raise RuntimeError("can't start new thread")
        original_start(runner)

    monkeypatch.setattr(runner_module.ProtocolRunner, "start", start)
    reply = _from_another_thread(qtbot, _server(w), "start_fluidics_protocol", confirm=True)
    assert not reply["success"] and "can't start new thread" in reply["error"]
    assert w.runner is None and not w.is_run_active()
    assert started == [] and w.running_box.isHidden() and w.protocol_tab.add_step_button.isEnabled()

    fail[0] = False
    reply = _from_another_thread(qtbot, _server(w), "start_fluidics_protocol", confirm=True, run_name="again")
    assert reply["success"], reply
    assert wait_until(lambda: w.runner.outcome is not None, timeout=15)


@pytest.mark.parametrize("bad", [10**6, False, 1.5, ["a.yaml"], ""])  # never an open fd, nor 0-2
def test_protocol_path_must_be_a_string_path(qtbot, widget, no_dialogs, bad):
    w = widget[0]
    reply = _server(w)._execute("start_fluidics_protocol", {"confirm": True, "protocol_path": bad})
    assert not reply["success"] and "protocol_path" in reply["error"], reply
    assert w.runner is None and w.protocol_tab.protocol.name == "demo"


def test_an_integer_protocol_path_never_reaches_open_as_a_file_descriptor(qtbot, widget, no_dialogs):
    w = widget[0]
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"name: injected\nsequences: []\n")
    os.close(write_fd)
    try:
        reply = _from_another_thread(qtbot, _server(w), "start_fluidics_protocol", confirm=True, protocol_path=read_fd)
        assert not reply["success"] and "protocol_path" in reply["error"]
        os.fstat(read_fd)  # still open: nothing read from it or closed it
        assert w.protocol_tab.protocol.name == "demo" and w.runner is None
    finally:
        os.close(read_fd)


def test_protocol_path_must_be_an_existing_regular_file(qtbot, widget, no_dialogs, tmp_path):
    w = widget[0]
    server = _server(w)
    for path in (tmp_path / "missing.yaml", tmp_path, "/dev/null"):
        reply = server._execute("start_fluidics_protocol", {"confirm": True, "protocol_path": str(path)})
        assert not reply["success"] and "not a protocol file" in reply["error"], (path, reply)
    assert w.runner is None and w.protocol_tab.protocol.name == "demo"


def test_a_protocol_the_tab_cannot_show_is_rolled_back(qtbot, widget, no_dialogs, tmp_path, monkeypatch):
    """set_protocol failing part-way through must still put the tab's own protocol back."""
    w = widget[0]
    other = _protocol()
    other.name = "other"
    path = tmp_path / "other.yaml"
    save_protocol(other, str(path))
    before_path = w.protocol_tab.protocol_path
    original_render = w.protocol_tab._render

    def render():
        if w.protocol_tab.protocol.name == "other":
            raise ValueError("cannot show this protocol")
        original_render()

    monkeypatch.setattr(w.protocol_tab, "_render", render)
    reply = _from_another_thread(qtbot, _server(w), "start_fluidics_protocol", confirm=True, protocol_path=str(path))
    assert not reply["success"] and "cannot show this protocol" in reply["error"]
    assert w.protocol_tab.protocol.name == "demo" and w.protocol_tab.protocol_path == before_path
    assert w.runner is None


@pytest.fixture
def record_tabs(qtbot, widget):
    """The record tabs as the GUI builds them, a multipoint tab current: the GUI keeps only the current record tab
    enabled while a run rides (toggleAcquisitionStart), so the protocol tab must be current when the run starts."""
    from qtpy.QtWidgets import QTabWidget, QWidget

    from control.widgets_fluidics import wire_record_tab

    w = widget[0]
    tabs = QTabWidget()
    qtbot.addWidget(tabs)
    wellplate = QWidget()
    tabs.addTab(wellplate, "Wellplate Multipoint")
    tabs.addTab(w, "Fluidics Protocol")
    tabs.setCurrentWidget(wellplate)
    wire_record_tab(tabs, w)
    current_at_start = []
    w.signal_acquisition_started.connect(lambda started: started and current_at_start.append(tabs.currentWidget() is w))
    return tabs, current_at_start


def test_a_server_start_selects_the_protocol_tab_before_the_run_starts(qtbot, widget, no_dialogs, record_tabs):
    w = widget[0]
    tabs, current_at_start = record_tabs
    assert _from_another_thread(qtbot, _server(w), "start_fluidics_protocol", confirm=True)["success"]
    assert current_at_start == [True] and tabs.currentWidget() is w
    assert wait_until(lambda: w.runner.outcome is not None, timeout=15)


def test_a_recovered_run_selects_the_protocol_tab_before_it_starts(qtbot, widget, record_tabs):
    w, _fluidics, _imaging, save_to = widget
    tabs, current_at_start = record_tabs
    w.start_run()
    assert w.runner.wait(15)  # its manifest is written (the outcome is set just before)
    run_dir = next(save_to.glob("liver_*"))
    crashed = manifest_io.read_manifest(run_dir)
    crashed.status = "running"
    crashed.pid = 2**22 - 7
    crashed.cursor.step, crashed.cursor.attempt, crashed.cursor.sequence = 0, 1, 0
    crashed.steps[0].attempts[0].outcome = None
    manifest_io.write_manifest(run_dir, crashed)
    qtbot.waitUntil(lambda: w.running_box.isHidden(), timeout=5000)  # the first run's end reached the tab
    tabs.setCurrentIndex(0)  # the operator went back to the multipoint tab
    current_at_start.clear()

    w.offer_recovery(startup=True)
    assert current_at_start == [True] and tabs.currentWidget() is w
    qtbot.waitUntil(lambda: w.runner.state == RunnerState.HELD, timeout=15000)
    w.runner.hold_action(protocol_widget_module.HoldAction.END)
    assert wait_until(lambda: w.runner.outcome == "stopped", timeout=15)
