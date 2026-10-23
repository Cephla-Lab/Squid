import os

import pytest

from control.models.fluidics_protocol import ProtocolFile
from control.models.fluidics_run import RunCursor, RunManifest, StepRecord
from control.widgets_fluidics.dialogs import AddRoundsDialog, PreflightDialog, RecoveryDialog
from tests.control.fluidics_test_config import sipper_protocol


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
                "folder": "image",
                "settings": "cur",
                "coordinates": "cur",
            },
        ],
    )


def test_system_panel_initializes_the_simulated_service_off_thread(qtbot, tmp_path, monkeypatch, fluidics_config_path):
    pytest.importorskip("fluidics")
    from control.fluidics_system import FluidicsService
    from control.widgets_fluidics.system_panel import DeviceStatusGroup, SystemPanel

    monkeypatch.chdir(tmp_path)  # UI-state cache lands in tmp cwd
    service = FluidicsService(default_config_path=fluidics_config_path, simulated=True)
    monkeypatch.setattr(SystemPanel, "_INITIALIZE_KWARGS", {"instant": True})
    panel = SystemPanel(service)
    qtbot.addWidget(panel)
    try:
        with qtbot.waitSignal(panel.initialized, timeout=15000):
            panel.initialize_button.click()
        assert service.initialized
        assert "Flow Cell" in panel.status_label.text()
        assert not panel.initialize_button.isEnabled()

        status = DeviceStatusGroup()
        qtbot.addWidget(status)
        status.attach(service)
        status.refresh("idle")
        assert status.labels["Valves"].text().startswith("port ")
        assert status.labels["Temperature"].text() == "no controller"
        assert status.labels["Run"].text() == "idle"
    finally:
        assert service.close() == []


def test_add_rounds_dialog_builds_expand_kwargs_and_previews(qtbot):
    dialog = AddRoundsDialog(_protocol())
    qtbot.addWidget(dialog)
    dialog.count_spin.setValue(2)
    dialog.port_row_combo.setCurrentText("probe")
    dialog.ports_edit.setText("2-3")
    preview = dialog.preview.toPlainText()
    assert "R02" in preview and "port 2" in preview and "R02_image" in preview

    dialog.accept()
    assert dialog.result_kwargs == dict(
        template_round="R01",
        count=2,
        label_pattern="R{n:02d}",
        start=2,
        port_row_name="probe",
        ports=[2, 3],
        wells=None,
    )
    assert dialog.wells_edit.isHidden()  # no row of this round draws from a well


def test_add_rounds_dialog_proposes_the_next_wells_for_a_sipper_round(qtbot):
    dialog = AddRoundsDialog(sipper_protocol())
    qtbot.addWidget(dialog)
    dialog.template_combo.setCurrentText("R01")
    assert not dialog.wells_edit.isHidden()
    dialog.count_spin.setValue(3)
    assert dialog.wells_edit.text() == "A2-A4"  # after R01's A1, along the row
    preview = dialog.preview.toPlainText().splitlines()
    assert preview[0] == "R02 · A2, ssct/A2" and preview[2].startswith("R04 · A4, ssct/A4")

    qtbot.keyClicks(dialog.wells_edit, ", B1")  # typed: the operator's list from here on
    dialog.count_spin.setValue(4)
    assert dialog.wells_edit.text() == "A2-A4, B1"
    dialog.accept()
    assert dialog.result_kwargs["wells"] == ["A2", "A3", "A4", "B1"]


def test_add_rounds_refuses_rather_than_reuses_when_the_plate_has_no_next_well(qtbot):
    from qtpy.QtCore import Qt
    from qtpy.QtWidgets import QDialogButtonBox

    protocol = sipper_protocol()
    for row in protocol.sequences:
        if row.get("round") == "R01" and row.get("well"):
            row["well"] = row["well"].replace("A1", "H12")  # the plate's last well
    dialog = AddRoundsDialog(protocol)
    qtbot.addWidget(dialog)
    dialog.template_combo.setCurrentText("R01")
    assert dialog.wells_edit.text() == ""  # nothing after H12 to propose
    assert not dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
    assert "no wells after H12 on its plate" in dialog.preview.toPlainText()

    qtbot.keyClicks(dialog.wells_edit, "A1")
    assert dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
    for _ in range(2):  # cleared by the operator: every round reuses the same wells, on purpose
        qtbot.keyClick(dialog.wells_edit, Qt.Key_Backspace)
    dialog.accept()
    assert dialog.result_kwargs["wells"] is None


def test_add_rounds_names_a_template_well_that_is_not_one(qtbot):
    from qtpy.QtWidgets import QDialogButtonBox

    protocol = sipper_protocol()
    for row in protocol.sequences:
        if row.get("round") == "R01" and row.get("well"):
            row["well"] = row["well"].replace("A1", "A13")  # a typo the editor flags too
    dialog = AddRoundsDialog(protocol)
    qtbot.addWidget(dialog)
    dialog.template_combo.setCurrentText("R01")
    assert not dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
    preview = dialog.preview.toPlainText()
    assert "'A13' is not a well" in preview and "fix the round's well row" in preview


def test_the_dialogs_explain_themselves_without_an_empty_help_button(qtbot):
    from qtpy.QtCore import Qt

    dialog = AddRoundsDialog(sipper_protocol())
    qtbot.addWidget(dialog)
    assert "Hover over a field" in dialog.help_label.text()
    fields = (dialog.template_combo, dialog.count_spin, dialog.pattern_edit, dialog.start_spin)
    fields += (dialog.port_row_combo, dialog.ports_edit, dialog.wells_label, dialog.wells_edit)
    assert all(field.toolTip() for field in fields)
    assert "ssct/A1 → ssct/A2" in dialog.wells_edit.toolTip()

    preflight = PreflightDialog([], ["ok"])
    qtbot.addWidget(preflight)
    for shown in (dialog, preflight):
        assert not shown.windowFlags() & Qt.WindowContextHelpButtonHint  # the "?" showed no help


def test_add_rounds_dialog_refuses_a_well_list_shorter_than_the_rounds(qtbot):
    from qtpy.QtWidgets import QDialogButtonBox

    dialog = AddRoundsDialog(sipper_protocol())
    qtbot.addWidget(dialog)
    dialog.template_combo.setCurrentText("R01")
    dialog.count_spin.setValue(2)
    dialog.wells_edit.clear()
    qtbot.keyClicks(dialog.wells_edit, "A2")
    assert not dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
    assert "need 2 wells" in dialog.preview.toPlainText()


def test_add_rounds_dialog_disables_ok_on_a_bad_port_list(qtbot):
    dialog = AddRoundsDialog(_protocol())
    qtbot.addWidget(dialog)
    dialog.port_row_combo.setCurrentText("probe")
    dialog.ports_edit.setText("nonsense")
    from qtpy.QtWidgets import QDialogButtonBox

    assert not dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
    assert "✗" in dialog.preview.toPlainText()


def test_preflight_dialog_blocks_start_on_problems(qtbot):
    blocked = PreflightDialog(["row 3: no coordinates"], [])
    qtbot.addWidget(blocked)
    assert not blocked.start_button.isEnabled()

    ready = PreflightDialog([], ["11 steps · 3 imaging", "fluidics est. 01:02:03"])
    qtbot.addWidget(ready)
    assert ready.start_button.isEnabled()


def test_recovery_dialog_names_the_cursor_step(qtbot):
    manifest = RunManifest(
        run_name="liver",
        run_dir="/data/liver_x",
        status="running",
        cursor=RunCursor(step=1, attempt=1, sequence=2),
        steps=[
            StepRecord(index=0, kind="fluidics", round="setup", label="setup", row_indices=[0]),
            StepRecord(index=1, kind="fluidics", round="R01", label="R01", row_indices=[1]),
        ],
        pid=os.getpid(),
        heartbeat_at=1725100000.0,
        started_at=1725090000.0,
    )
    dialog = RecoveryDialog(manifest)
    qtbot.addWidget(dialog)
    texts = " ".join(label.text() for label in dialog.findChildren(type(dialog.layout().itemAt(0).widget())))
    assert "step 2/2" in texts and "liver" in texts


def test_quick_op_calls_priming_or_clean_up_with_use_ports(qtbot, fluidics_config_path):
    pytest.importorskip("fluidics")
    from types import SimpleNamespace

    from control.widgets_fluidics.display_tab import FluidicsDisplayTab

    calls = []
    operations = SimpleNamespace(
        priming_or_clean_up=lambda port, flow, volume, use_ports=None, clean_up=False: calls.append(
            (port, flow, volume, tuple(use_ports), clean_up)
        )
    )
    system = SimpleNamespace(
        operations=operations,
        busy=False,
        run_manual=lambda verb, callbacks=None, source=None: verb(),  # synchronous for the test
        abort=lambda: None,
    )
    from fluidics.control.config import load_config

    service = SimpleNamespace(
        initialized=True,
        system=system,
        config=load_config(fluidics_config_path),
        default_config_path=fluidics_config_path,
    )
    tab = FluidicsDisplayTab(service)
    qtbot.addWidget(tab)
    tab.fluidics_port = object()  # marks the system ready
    tab.ports_edit.setText("2, 5, 7")
    tab.wash_port_spin.setValue(1)
    tab.volume_spin.setValue(50)
    tab.flow_spin.setValue(200)

    tab._quick_op("priming")
    assert calls == [(1, 200, 50, (2, 5, 7), False)]  # wash port 1, one final draw over use_ports 2,5,7

    calls.clear()
    tab._quick_running = False
    tab.repeat_spin.setValue(3)
    tab._quick_op("clean_up")
    # Clean repeats, and says it is a clean-up (a sipper's line then draws from the clean-up well)
    assert calls == [(1, 200, 50, (2, 5, 7), True)] * 3
    tab.log_view.disconnect_logging()
