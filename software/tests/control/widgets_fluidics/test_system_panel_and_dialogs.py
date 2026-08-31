import os
import pathlib

import pytest

from control.models.fluidics_protocol import ProtocolFile
from control.models.fluidics_run import RunCursor, RunManifest, StepRecord
from control.widgets_fluidics.dialogs import AddRoundsDialog, PreflightDialog, RecoveryDialog

EXAMPLE_CONFIG = pathlib.Path(__file__).resolve().parents[3] / "machine_configs" / "fluidics_config.yaml.example"

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
        ],
    )


def test_system_panel_initializes_the_simulated_service_off_thread(qtbot, tmp_path, monkeypatch):
    pytest.importorskip("fluidics")
    from control.fluidics_system import FluidicsService
    from control.widgets_fluidics.system_panel import DeviceStatusGroup, SystemPanel

    monkeypatch.chdir(tmp_path)  # UI-state cache lands in tmp cwd
    service = FluidicsService(default_config_path=str(EXAMPLE_CONFIG), simulated=True)
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
        template_round="R01", count=2, label_pattern="R{n:02d}", start=2, port_row_name="probe", ports=[2, 3]
    )


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
