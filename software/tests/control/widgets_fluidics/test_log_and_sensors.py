import pytest

import squid.logging
from control.core.fluidics_protocol.sensor_recorder import SensorRecorder
from control.widgets_fluidics.log_view import FluidicsLogView, ReagentsTable


def test_log_view_receives_squid_records_and_autoscrolls(qtbot):
    view = FluidicsLogView()
    qtbot.addWidget(view)
    view.connect_logging()
    try:
        log = squid.logging.get_logger("fluidics.test_tab")
        for i in range(60):
            log.info(f"line {i}")
        qtbot.waitUntil(lambda: "line 59" in view.text_edit.toPlainText(), timeout=3000)
        bar = view.text_edit.verticalScrollBar()
        assert bar.value() == bar.maximum()
    finally:
        view.disconnect_logging()


def test_log_view_save_writes_the_visible_text(qtbot, tmp_path, monkeypatch):
    import control.widgets_fluidics.log_view as log_view_module

    view = FluidicsLogView()
    qtbot.addWidget(view)
    view.text_edit.appendPlainText("hello log")
    target = tmp_path / "out.log"
    monkeypatch.setattr(log_view_module.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    view._save_log()
    assert target.read_text() == "hello log"


def test_reagents_table_renders_rows(qtbot):
    table = ReagentsTable()
    qtbot.addWidget(table)
    table.set_rows([(1, "probe 1", 500.0, 1500.0, 4500.0), (25, None, 0.0, 2000.0, 9000.0)])
    assert table.table.rowCount() == 2
    assert table.table.item(0, 1).text() == "probe 1"
    assert table.table.item(1, 1).text() == "—"
    assert table.table.item(1, 4).text() == "9000"


def test_temperature_tab_reads_the_simulated_tec(qtbot, tmp_path, monkeypatch):
    pytest.importorskip("fluidics")
    from fluidics.control.temperature_controller import TCMControllerSimulation

    import control.widgets_fluidics.sensor_plots as sensor_plots_module
    from control.widgets_fluidics.sensor_plots import TemperatureTab

    tc = TCMControllerSimulation(channels=2)
    recorder = SensorRecorder()
    tab = TemperatureTab(tc, recorder)
    qtbot.addWidget(tab)
    try:
        tab.target_spinboxes[0].setValue(37.0)
        tab.set_buttons[0].click()
        assert tc.target_temperatures[0] == 37.0

        qtbot.waitUntil(lambda: len(recorder.channel("channel_1").window()[0]) > 0, timeout=5000)

        target = tmp_path / "t.csv"
        monkeypatch.setattr(
            sensor_plots_module.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
        )
        tab.record_button.setChecked(True)
        assert recorder.recording
        qtbot.waitUntil(lambda: len(target.read_text().splitlines()) > 1, timeout=5000)
        tab.record_button.setChecked(False)
        assert not recorder.recording
        assert target.read_text().startswith("time,channel,value,step")

        tab.set_run_active(True)
        assert not tab.set_buttons[0].isEnabled()
        tab.set_run_active(False)
        assert tab.set_buttons[0].isEnabled()
    finally:
        tab._timer.stop()
        tc.close()


def test_record_button_follows_a_recorder_stop(qtbot, tmp_path, monkeypatch):
    pytest.importorskip("fluidics")
    from fluidics.control.temperature_controller import TCMControllerSimulation

    import control.widgets_fluidics.sensor_plots as sensor_plots_module
    from control.widgets_fluidics.sensor_plots import TemperatureTab

    tc = TCMControllerSimulation(channels=2)
    recorder = SensorRecorder()
    tab = TemperatureTab(tc, recorder)
    qtbot.addWidget(tab)
    try:
        target = tmp_path / "t.csv"
        monkeypatch.setattr(
            sensor_plots_module.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
        )
        tab.record_button.setChecked(True)
        assert recorder.recording
        recorder.stop_recording()  # what a failed CSV write does internally
        tab._refresh()
        assert not tab.record_button.isChecked()
        assert "Record" in tab.record_button.text()
    finally:
        tab._timer.stop()
        tc.close()
