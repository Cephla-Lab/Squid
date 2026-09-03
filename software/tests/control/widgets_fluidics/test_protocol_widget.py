import threading
from types import SimpleNamespace

import pytest
from qtpy.QtCore import QTimer
from qtpy.QtWidgets import QDialog

import control.widgets_fluidics.protocol_widget as protocol_widget_module
from control.core.fluidics_protocol import manifest as manifest_io
from control.core.fluidics_protocol.events import RunnerState
from control.core.fluidics_protocol.ports import ImagingResult
from control.models.fluidics_protocol import ProtocolFile
from control.widgets_fluidics.protocol_tab import ProtocolTab
from control.widgets_fluidics.protocol_widget import FluidicsProtocolWidget
from tests.control.core.fluidics_protocol.fakes import FakeFluidicsPort, FakeImagingPort, wait_until

SETTINGS = {"channels": ["A"], "z_stack": {"nz": 1}}
COORDS = {"regions": [{"name": "A1", "fovs": [[1.0, 2.0, 3.0]]}]}


def _protocol():
    return ProtocolFile(
        name="demo",
        imaging={"settings": {"cur": SETTINGS}, "coordinates": {"cur": COORDS}},
        sequences=[
            {
                "type": "flow_reagent",
                "round": "R01",
                "name": "probe",
                "fluidic_port": 1,
                "flow_rate": 500,
                "volume": 500,
            },
            {
                "type": "imaging",
                "round": "R01",
                "name": "image",
                "folder": "R01_image",
                "settings": "cur",
                "coordinates": "cur",
            },
            {
                "type": "flow_reagent",
                "round": "R01",
                "name": "rinse",
                "fluidic_port": 2,
                "flow_rate": 500,
                "volume": 300,
            },
        ],
    )


@pytest.fixture
def quiet_dialogs(monkeypatch):
    monkeypatch.setattr(protocol_widget_module.PreflightDialog, "exec_", lambda self: QDialog.Accepted)
    monkeypatch.setattr(protocol_widget_module.RecoveryDialog, "exec_", lambda self: QDialog.Accepted)
    monkeypatch.setattr(
        protocol_widget_module.QMessageBox,
        "question",
        staticmethod(lambda *a, **k: protocol_widget_module.QMessageBox.Yes),
    )
    warnings = []
    monkeypatch.setattr(
        protocol_widget_module.QMessageBox,
        "warning",
        staticmethod(lambda _p, title, text: warnings.append((title, text))),
    )
    monkeypatch.setattr(protocol_widget_module.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    return warnings


@pytest.fixture
def widget(qtbot, tmp_path, monkeypatch, quiet_dialogs, fluidics_config_path):
    monkeypatch.chdir(tmp_path)
    service = SimpleNamespace(initialized=True)
    tab = ProtocolTab(SimpleNamespace(initialized=False, default_config_path=fluidics_config_path))
    qtbot.addWidget(tab)
    tab.set_protocol(_protocol())
    imaging = FakeImagingPort()
    fluidics = FakeFluidicsPort()
    w = FluidicsProtocolWidget(service, tab, imaging_port=imaging)
    qtbot.addWidget(w)
    w.set_fluidics_port(fluidics)
    save_to = tmp_path / "runs"
    save_to.mkdir()
    w.save_to_edit.setText(str(save_to))
    w.run_name_edit.setText("liver")
    return w, fluidics, imaging, save_to


def test_start_runs_a_protocol_to_completion(qtbot, widget):
    w, fluidics, imaging, save_to = widget
    started = []
    w.signal_acquisition_started.connect(started.append)

    w.start_run()
    assert w.runner is not None
    assert started == [True]
    assert wait_until(lambda: w.runner.outcome is not None, timeout=15)
    assert w.runner.outcome == "finished"
    qtbot.waitUntil(lambda: started == [True, False], timeout=5000)

    run_dir = next(save_to.glob("liver_*"))
    manifest = manifest_io.read_manifest(run_dir)
    assert manifest.status == "finished"
    assert [s.kind for s in manifest.steps] == ["fluidics", "imaging", "fluidics"]
    assert len(imaging.requests) == 1 and imaging.requests[0].folder == "R01_image"
    assert w.protocol_tab.add_step_button.isEnabled()  # unlocked again
    assert w.running_box.isHidden() and not w.idle_box.isHidden()  # the stale status card leaves


def test_a_failed_fluidics_step_shows_the_held_panel_and_restart_recovers(qtbot, widget):
    w, _fluidics, imaging, save_to = widget
    fluidics = FakeFluidicsPort(script=[("failed", 0, "Flow fault on syringe_draw"), ("finished",), ("finished",)])
    w.set_fluidics_port(fluidics)
    notes = []
    w.signal_run_notification.connect(notes.append)

    w.start_run()
    qtbot.waitUntil(lambda: not w.held_box.isHidden() or w.runner.outcome is not None, timeout=15000)
    assert w.runner.state == RunnerState.HELD
    assert any("Flow fault" in n for n in notes)

    w.runner.hold_action(protocol_widget_module.HoldAction.RESTART)
    assert wait_until(lambda: w.runner.outcome == "finished", timeout=15)
    qtbot.waitUntil(lambda: w.held_box.isHidden(), timeout=5000)


def test_abort_run_button_stops_the_run(qtbot, widget):
    w, _fluidics, imaging, save_to = widget
    fluidics = FakeFluidicsPort(script=[("hold",)])
    w.set_fluidics_port(fluidics)

    w.start_run()
    assert wait_until(lambda: len(fluidics.starts) == 1, timeout=10)
    w._abort_run_clicked()
    assert wait_until(lambda: w.runner.outcome == "stopped", timeout=15)


def test_guard_refuses_without_a_port_or_names(qtbot, widget, quiet_dialogs):
    w, fluidics, imaging, save_to = widget
    w.run_name_edit.setText("")
    w.start_run()
    assert w.runner is None
    assert quiet_dialogs and "run a name" in quiet_dialogs[-1][1]


def test_recovery_reopens_a_crashed_run_held(qtbot, widget):
    w, fluidics, imaging, save_to = widget
    w.start_run()
    assert wait_until(lambda: w.runner.outcome is not None, timeout=15)
    run_dir = next(save_to.glob("liver_*"))
    crashed = manifest_io.read_manifest(run_dir)
    crashed.status = "running"
    crashed.pid = 2**22 - 7
    crashed.cursor.step, crashed.cursor.attempt, crashed.cursor.sequence = 0, 1, 0
    crashed.steps[0].attempts[0].outcome = None
    manifest_io.write_manifest(run_dir, crashed)
    first_runner = w.runner

    from control.models.fluidics_protocol import ProtocolFile as _PF

    w.protocol_tab.set_protocol(_PF(name="unrelated"))
    w.offer_recovery()
    assert w.runner is not first_runner
    assert w.protocol_tab.protocol.name == "demo"  # the run's protocol, not the open one
    qtbot.waitUntil(lambda: w.runner.state == RunnerState.HELD, timeout=15000)
    assert w.runner.hold.reason == "recovered"
    w.runner.hold_action(protocol_widget_module.HoldAction.END)
    assert wait_until(lambda: w.runner.outcome == "stopped", timeout=15)


def test_display_tab_initializes_and_builds_the_port(qtbot, tmp_path, monkeypatch):
    pytest.importorskip("fluidics")
    from control.fluidics_system import FluidicsService
    from control.widgets_fluidics.display_tab import FluidicsDisplayTab
    from control.widgets_fluidics.system_panel import SystemPanel

    monkeypatch.chdir(tmp_path)
    from tests.control.fluidics_test_config import TEC_CONFIG_YAML

    text = TEC_CONFIG_YAML
    config = tmp_path / "with_tec.yaml"
    config.write_text(text)
    monkeypatch.setattr(SystemPanel, "_INITIALIZE_KWARGS", {"instant": True})
    service = FluidicsService(default_config_path=str(config), simulated=True)
    tab = FluidicsDisplayTab(service)
    qtbot.addWidget(tab)
    try:
        with qtbot.waitSignal(tab.system_ready, timeout=15000):
            tab.system_panel.initialize_button.click()
        assert tab.fluidics_port is not None
        assert tab.temperature_tab is not None
        assert tab.tabs.tabText(1) == "Temperature"
        assert hasattr(tab, "manual_widget")  # the upstream fluidics.qt widget mounted
        tab.set_run_active(True)
        assert not tab.manual_group.isEnabled()
        tab.set_run_active(False)

        # one-off prime from the inline fields (no pop-ups), through the same library path
        # as any sequence; priming is minutes long even simulated, so the operator's Stop
        # button is what ends it.
        tab.ports_edit.setText("1-2")
        tab.wash_port_spin.setValue(1)
        tab.volume_spin.setValue(50)
        tab._quick_op("priming")
        assert tab.quick_op_active()
        assert not tab.prime_button.isEnabled() and not tab.ports_edit.isEnabled()
        assert not tab.stop_quick_button.isHidden()
        tab._stop_quick_op()
        qtbot.waitUntil(lambda: (tab._poll_quick_op() or not tab.quick_op_active()), timeout=30000)
        assert tab.prime_button.isEnabled() and tab.stop_quick_button.isHidden()
    finally:
        tab.shutdown()
        assert service.close() == []


class _GuiGatedImaging:
    """Completes only once a GUI-thread event fires - like the real QtImagingPort,
    whose acquisition_finished is a queued signal on the GUI thread."""

    def __init__(self):
        self.done = threading.Event()
        self.starts = []

    def start(self, request):
        self.starts.append(request)
        done = self.done

        class Handle:
            def wait(self, timeout):
                if done.wait(timeout):
                    return ImagingResult(end_reason="user_abort", image_count=0, folder=request.folder)
                return None

            def abort(self):
                pass

        return Handle()


def test_end_run_for_exit_pumps_the_event_loop_during_imaging(qtbot, widget):
    w, fluidics, _imaging, save_to = widget
    gated = _GuiGatedImaging()
    w.imaging_port = gated
    w.start_run()
    qtbot.waitUntil(lambda: len(gated.starts) == 1, timeout=15000)

    # The imaging step can only finish via this GUI-thread timer; a blocking
    # runner.wait() on the GUI thread would deadlock until the timeout.
    QTimer.singleShot(300, gated.done.set)
    assert w.end_run_for_exit(10)
    assert w.runner.outcome == "stopped"


def test_recovery_is_refused_while_the_controller_is_busy(qtbot, widget):
    w, fluidics, imaging, save_to = widget
    w.busy_check = lambda: "an acquisition is already in progress"
    w.offer_recovery()
    assert w.runner is None


def test_startup_recovery_waits_for_initialize(qtbot, widget, monkeypatch, quiet_dialogs):
    w, fluidics, imaging, save_to = widget
    w.fluidics_port = None
    dialogs = []
    monkeypatch.setattr(
        protocol_widget_module,
        "RecoveryDialog",
        lambda *a, **k: dialogs.append(a) or SimpleNamespace(exec_=lambda: QDialog.Rejected),
    )
    monkeypatch.setattr(
        protocol_widget_module.manifest_io, "find_unfinished_runs", lambda _d: [SimpleNamespace(run_dir="x")]
    )
    w.offer_recovery(startup=True)
    assert not dialogs and w.runner is None  # quiet: nothing actionable before Initialize
    w.offer_recovery()
    assert quiet_dialogs and "Initialize" in quiet_dialogs[-1][1]

    w.set_fluidics_port(fluidics)
    w.offer_recovery(startup=True)
    assert dialogs  # now the offer fires


def test_changing_the_save_to_directory_offers_recovery(qtbot, widget, monkeypatch, tmp_path):
    w, fluidics, imaging, save_to = widget
    offers = []
    monkeypatch.setattr(w, "offer_recovery", lambda startup=False: offers.append(startup))
    other = tmp_path / "other_runs"
    other.mkdir()
    w.save_to_edit.setText(str(other))
    w.save_to_edit.editingFinished.emit()
    assert offers == [True]
