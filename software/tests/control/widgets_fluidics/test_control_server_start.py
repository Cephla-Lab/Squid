"""start_fluidics_protocol: the control server starts a run through the Fluidics Protocol tab's own start path,
on the GUI thread, without opening a dialog. Offscreen, on the fake fluidics and imaging ports."""

import threading
from unittest.mock import MagicMock

import pytest

import control.widgets_fluidics.protocol_widget as protocol_widget_module
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
    assert wait_until(lambda: w.runner.outcome is not None, timeout=15) and w.runner.outcome == "finished"
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
