import logging
import threading
from pathlib import Path

import pytest
from qtpy.QtWidgets import QMessageBox

import control.laser_engine_v2_bench as bench
import squid.logging
from control.laser_engine_v2 import EngineOptions, LaserEngineV2, LaserEngineV2Error
from control.laser_engine_v2_bench import BenchWindow, LaserEngineV2ServicePanel, LogPane
from control.laser_engine_v2_link import EngineLink
from control.laser_engine_v2_settings import save_idle_off_560_min, save_power_560_mw
from control.laser_engine_v2_sim import FakeEngine, FakeSource, build_simulated_engine

NO_CALIBRATIONS = Path(__file__).parent / "no_such_calibration_dir"


@pytest.fixture(autouse=True)
def _fast_resync(monkeypatch):
    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)


@pytest.fixture(autouse=True)
def cache_file(tmp_path, monkeypatch):
    """The tab's 560 settings file, in a temporary directory: these tests never read or write Squid's cache/."""
    path = tmp_path / "cache" / "laser_engine_v2.yaml"
    monkeypatch.setattr("control.laser_engine_v2_settings.DEFAULT_CACHE_PATH", path)
    return path


def _panel(qtbot, options=None):
    engine = build_simulated_engine(options=options)
    engine.open()  # no threads: the tests call poll_once()
    panel = LaserEngineV2ServicePanel(engine)
    qtbot.addWidget(panel)
    return engine, panel


def test_panel_rows_intensity_and_readback(qtbot):
    engine, panel = _panel(qtbot)
    try:
        assert list(panel.rows) == ["L1", "L2", "L3", "L4", "L5"]
        assert panel.rows["L3"].name.text() == "L3 · 560 nm"
        engine.poll_once()
        assert panel.rows["L2"].state.text().startswith("NOT_ARMED")
        assert panel.rows["L2"].readback.text() == "0.000 / 0.000 A"
        assert panel.rows["L3"].readback.text().endswith(" V")  # DF line 3 = the AOM analog input
        fake = engine.sim_engine
        assert not any(s.startswith("LINE2:SET") for s in fake.sent)  # building the panel sends nothing
        panel.rows["L2"].spin.setValue(50.0)
        assert any(s.startswith("LINE2:SET") for s in fake.sent)
        engine.poll_once()
        assert panel.rows["L2"].readback.text().startswith("1.02")  # 50 % of 2.045 A (no calibration file)
    finally:
        engine.close()


def test_panel_560_row_is_the_aom_with_no_floor(qtbot):
    engine, panel = _panel(qtbot)  # FakeSource 200-1000 mW
    try:
        assert all(row.spin.minimum() == 0 for row in panel.rows.values())  # no floor: 0 % is the AOM at 0 V
        panel.rows["L3"].spin.setValue(50.0)
        assert "LINE3:SET 2.500" in engine.sim_engine.sent  # 50 % = 2.5 V on the AOM input (no calibration)
        assert engine.source_power_setpoint_mw == 200.0 and engine.sim_source.calls == []  # the laser power untouched
    finally:
        engine.close()


def test_panel_errors_are_shown_not_raised(qtbot):
    engine, panel = _panel(qtbot)
    try:
        engine.sim_engine.unplug()
        engine.poll_once()  # connection lost
        panel.rows["L1"].spin.setValue(10.0)  # must not raise
        assert "L1 intensity" in panel.message_label.text()
        panel.rows["L1"].sleep.click()
        assert "L1 sleep" in panel.message_label.text()
    finally:
        engine.close()


def test_panel_bench_gate(qtbot):
    engine, panel = _panel(qtbot)
    fake = engine.sim_engine
    try:
        panel.rows["L1"].gate.setChecked(True)
        assert fake.sent[-1] == "LINE1:GATE 1" and fake.lines[0]["gate"] == 1
        panel.rows["L1"].gate.setChecked(False)
        assert fake.sent[-1] == "LINE1:GATE 0" and fake.lines[0]["gate"] == 0
        panel.rows["L2"].gate.setChecked(True)
        panel.release_gates()
        assert fake.sent[-1] == "LINE2:GATE 0" and not panel.rows["L2"].gate.isChecked()
        fake.i2c_fail_count = 1  # the next GATE is refused: the box must not stay checked
        panel.rows["L4"].gate.setChecked(True)
        assert not panel.rows["L4"].gate.isChecked() and "I2C write failed" in panel.message_label.text()
    finally:
        engine.close()


def test_panel_raw_command_line(qtbot):
    engine, panel = _panel(qtbot)
    try:
        panel.raw_edit.setText("VAR?")
        panel.raw_send_btn.click()
        assert panel.raw_reply.text() == "DF"
        assert panel.send_raw("LINE1:EN 1") == "ERR not armed"  # refused: shown, not raised
        assert panel.raw_reply.text() == "ERR not armed"
        assert panel.send_raw("HOST:TIMEOUT 5") == "OK"
        for i in range(4):
            panel.send_raw(f"LINE{i + 1}:GATE 0")
        history = panel.raw_history_label.text().splitlines()
        assert len(history) == 5 and history[-1].startswith("> LINE4:GATE 0") and "VAR?" not in history[0]
    finally:
        engine.close()
    assert panel.send_raw("VAR?").startswith("error: ")  # engine closed: shown, not raised


def test_panel_source_box(qtbot):
    engine, panel = _panel(qtbot)
    try:
        assert panel.source_box is not None
        assert panel.source_limits_label.text() == "200 – 1000 mW"
        engine.poll_once()
        assert panel.source_state_label.text().startswith("SOURCE_OFF")
        assert panel.source_setpoint_label.text() == "200 mW"  # unset: the source's minimum
        panel.rows["L3"].spin.setValue(50.0)  # the AOM: not the laser power
        engine.set_source_power_mw(500.0)  # what the tab's Set does
        engine.poll_once()
        assert panel.source_setpoint_label.text() == "500 mW"
        engine.sim_source.needs_key = True
        engine.source_step()
        engine.poll_once()
        assert panel.source_state_label.text().startswith("NEEDS_KEY")
    finally:
        engine.close()


def test_panel_without_a_source_has_no_source_box(qtbot):
    engine = LaserEngineV2(link_factory=lambda: EngineLink(FakeEngine()))
    engine.open()
    try:
        panel = LaserEngineV2ServicePanel(engine)
        qtbot.addWidget(panel)
        assert panel.source_box is None
    finally:
        engine.close()


def test_source_accessors():
    engine = build_simulated_engine()
    engine.open()
    try:
        assert engine.source_limits_mw == (200.0, 1000.0)
        status = engine.source_status
        assert status is not None and status.link_ok and status.off
    finally:
        engine.close()
    assert engine.source_limits_mw is None and engine.source_status is None  # the source is closed with the engine
    bare = LaserEngineV2(link_factory=lambda: EngineLink(FakeEngine()))
    bare.open()
    try:
        assert bare.source_limits_mw is None and bare.source_status is None
    finally:
        bare.close()


def test_log_pane_shows_squid_records_from_any_thread(qtbot):
    pane = LogPane()
    qtbot.addWidget(pane)
    try:
        squid.logging.get_logger("x").warning("hello")
        qtbot.waitUntil(lambda: "hello" in pane.toPlainText(), timeout=2000)
        t = threading.Thread(target=lambda: squid.logging.get_logger("y").warning("from a thread"))
        t.start()
        t.join()
        qtbot.waitUntil(lambda: "from a thread" in pane.toPlainText(), timeout=2000)
    finally:
        pane.detach()
    assert pane.handler not in squid.logging.get_logger().handlers


def test_log_pane_detaches_when_destroyed(qtbot):
    pane = LogPane()
    handler = pane.handler
    assert handler in squid.logging.get_logger().handlers
    pane.deleteLater()
    qtbot.waitUntil(lambda: handler not in squid.logging.get_logger().handlers, timeout=2000)


def test_bench_window_simulate_connect_disconnect(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: pytest.fail(f"unexpected message box: {a[2:]}"))
    win = BenchWindow()
    qtbot.addWidget(win)
    win.simulate_cb.setChecked(True)
    assert win.bringup_cb.isChecked()
    win.connect_btn.click()
    engine = win.engine
    try:
        assert engine is not None and engine.variant == "DF"
        assert win.panel_splitter.indexOf(win.engine_widget) >= 0
        assert win.panel_splitter.indexOf(win.service_panel) >= 0
        assert not win.connect_btn.isEnabled() and win.disconnect_btn.isEnabled()
        fake = engine.sim_engine
        win.service_panel.rows["L1"].gate.setChecked(True)
        win.disconnect_btn.click()
        assert fake.sent[-1] == "DISARM"  # the poll thread has stopped: nothing after the DISARM
        assert "LINE1:GATE 0" in fake.sent[fake.sent.index("LINE1:GATE 1") :]  # gate released before the DISARM
        assert win.engine is None and win.connect_btn.isEnabled()
        with pytest.raises(LaserEngineV2Error, match="not open"):
            engine.link
    finally:
        engine.close()
        win.log_pane.detach()


def test_bench_window_connect_error_stays_disconnected(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    win = BenchWindow()
    qtbot.addWidget(win)
    try:
        win.port_combo.clear()  # no Teensy port and not simulating
        win.connect_btn.click()
        assert win.engine is None and shown and "no Teensy port" in shown[0]
        assert win.connect_btn.isEnabled() and not win.disconnect_btn.isEnabled()
    finally:
        win.log_pane.detach()


# ---- reconnects, refusals and shutdown ------------------------------------------------------------------------------
def _shared_sim(monkeypatch):
    """Every simulated connect talks to one fake engine; it keeps set-points across a DISARM, as the firmware does."""
    fake, source = FakeEngine(tok_delay_polls=0), FakeSource()

    def build(options=None):
        engine = LaserEngineV2(
            link_factory=lambda: EngineLink(fake),
            source_factory=lambda: source,
            options=options,
            calibration_dir=NO_CALIBRATIONS,
        )
        engine.sim_engine, engine.sim_source = fake, source
        return engine

    monkeypatch.setattr(bench, "build_simulated_engine", build)
    return fake


def _window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: pytest.fail(f"unexpected message box: {a[2:]}"))
    win = BenchWindow()
    qtbot.addWidget(win)
    win.simulate_cb.setChecked(True)
    return win


def test_reconnect_starts_dark_and_the_spinbox_matches(qtbot, monkeypatch):
    fake = _shared_sim(monkeypatch)
    win = _window(qtbot, monkeypatch)
    try:
        win.connect_btn.click()
        win.service_panel.rows["L2"].spin.setValue(50.0)
        win.disconnect_btn.click()
        assert fake.lines[1]["target"] > 1.0  # the engine keeps the set-point across the DISARM
        mark = len(fake.sent)
        win.connect_btn.click()
        assert "LINE2:SET 0.0000" in fake.sent[mark:] and fake.lines[1]["target"] == 0.0
        assert "LINE3:SET 0.000" in fake.sent[mark:]  # the 560 line = the AOM: dark at connect
        assert win.service_panel.rows["L2"].spin.value() == 0.0
        assert win.service_panel.rows["L3"].spin.value() == 0.0
    finally:
        win.close()


def test_panel_seeds_the_spinboxes_from_the_engine(qtbot):
    engine = build_simulated_engine()
    engine.open()
    try:
        engine.sim_engine.lines[3]["target"] = 0.598  # L4 left at 50 % of 1.196 A
        engine.sim_engine.lines[2]["target"] = 1.25  # the AOM left at 25 % of 5 V
        engine.poll_once()
        panel = LaserEngineV2ServicePanel(engine)
        qtbot.addWidget(panel)
        assert panel.rows["L4"].spin.value() == 50.0
        assert panel.rows["L1"].spin.value() == 0.0 and panel.rows["L3"].spin.value() == 25.0
        assert not any(s.startswith("LINE4:SET") for s in engine.sim_engine.sent)  # seeding sends nothing
    finally:
        engine.close()


def test_refused_gate_0_keeps_the_box_checked_and_warns(qtbot, caplog):
    engine, panel = _panel(qtbot)
    fake = engine.sim_engine
    try:
        panel.rows["L2"].gate.setChecked(True)
        fake.i2c_fail_count = 1  # the GATE 0 below is refused
        with caplog.at_level(logging.WARNING):
            panel.rows["L2"].gate.setChecked(False)
        assert fake.lines[1]["gate"] == 1 and panel.rows["L2"].gate.isChecked()  # the box shows the engine
        text = "GATE 0 refused (I2C write failed) - gate may still be ON: Sleep the line or Disconnect"
        assert text in panel.message_label.text()
        assert any(text in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)
        panel.rows["L2"].gate.setChecked(False)  # accepted now
        assert fake.lines[1]["gate"] == 0 and not panel.rows["L2"].gate.isChecked()
    finally:
        engine.close()


def test_refused_intensity_set_reverts_the_spinbox(qtbot):
    engine, panel = _panel(qtbot)
    try:
        panel.rows["L1"].spin.setValue(10.0)  # accepted
        engine.sim_engine.unplug()
        engine.poll_once()  # connection lost
        panel.rows["L1"].spin.setValue(30.0)
        assert panel.rows["L1"].spin.value() == 10.0 and "L1 intensity" in panel.message_label.text()
    finally:
        engine.close()


def test_window_close_releases_gates_then_disarms(qtbot, monkeypatch):
    fake = _shared_sim(monkeypatch)
    win = _window(qtbot, monkeypatch)
    win.connect_btn.click()
    win.service_panel.rows["L1"].gate.setChecked(True)
    win.service_panel.rows["L4"].gate.setChecked(True)
    mark = len(fake.sent)
    win.close()
    tail = fake.sent[mark:]
    assert tail[-1] == "DISARM" and "LINE1:GATE 0" in tail and "LINE4:GATE 0" in tail
    assert win.engine is None


def test_connection_lost_disables_the_panel_and_reconnect_recovers(qtbot, monkeypatch):
    win = _window(qtbot, monkeypatch)
    try:
        win.connect_btn.click()
        engine = win.engine
        engine.sim_engine.unplug()
        engine.poll_once()  # the loss is seen here (or by the poll thread: queued)
        qtbot.waitUntil(lambda: not win.service_panel.isEnabled(), timeout=3000)
        assert "DISCONNECTED - link lost" in win.link_label.text()
        win.disconnect_btn.click()  # the refused GATE 0 / DISARM must not raise
        assert win.engine is None and win.connect_btn.isEnabled()
        win.connect_btn.click()
        assert win.engine is not None and win.service_panel.isEnabled() and win.link_label.text() == ""
    finally:
        win.close()


def test_failed_panel_build_closes_the_engine(qtbot, monkeypatch):
    fake = _shared_sim(monkeypatch)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    win = BenchWindow()
    qtbot.addWidget(win)
    win.simulate_cb.setChecked(True)

    def broken(engine):
        raise RuntimeError("panel bug")

    monkeypatch.setattr(bench, "LaserEngineV2ServicePanel", broken)
    win.connect_btn.click()
    assert win.engine is None and shown and "panel bug" in shown[0]
    assert fake.sent[-1] == "DISARM" and win.connect_btn.isEnabled() and not win.disconnect_btn.isEnabled()
    assert win.panel_splitter.count() == 0 or not win.panel_splitter.widget(0).isVisible()
    win.close()


def test_raw_gate_command_syncs_the_checkbox(qtbot):
    engine, panel = _panel(qtbot)
    try:
        assert panel.send_raw("LINE2:GATE ON") == "OK" and panel.rows["L2"].gate.isChecked()
        assert panel.send_raw("line2:gate 0") == "OK" and not panel.rows["L2"].gate.isChecked()
        engine.sim_engine.i2c_fail_count = 1
        assert panel.send_raw("LINE5:GATE 1").startswith("ERR") and not panel.rows["L5"].gate.isChecked()
        assert engine.sim_engine.sent[-1] == "LINE5:GATE 1"  # the box did not send anything of its own
    finally:
        engine.close()


def test_small_guards(qtbot):
    from qtpy.QtCore import Qt

    engine, panel = _panel(qtbot)
    try:
        assert panel.rows["L1"].spin.focusPolicy() == Qt.StrongFocus
        assert "AOM analog path" in panel.rows["L3"].gate.toolTip()
        assert bench._UNITS["CHASSIS"] == "A"
    finally:
        engine.close()


# ---- start dark (the AOM at 0 V); no AOM options; gate boxes follow the engine ----------------------------------------
def test_connect_starts_the_aom_dark(qtbot, monkeypatch):
    fake = _shared_sim(monkeypatch)
    win = _window(qtbot, monkeypatch)
    try:
        win.connect_btn.click()
        assert "LINE3:SET 0.000" in fake.sent  # the 560 line starts dark
        assert win.engine._aom_volts == 0.0 and win.service_panel.rows["L3"].spin.value() == 0.0
    finally:
        win.disconnect_btn.click()


def test_bench_has_no_aom_or_560_options_and_the_tab_sets_them(qtbot, monkeypatch, cache_file):
    from qtpy.QtWidgets import QComboBox, QLineEdit, QSpinBox

    _shared_sim(monkeypatch)
    assert save_power_560_mw(600) and save_idle_off_560_min(45)  # as the Laser Engine tab left them
    win = _window(qtbot, monkeypatch)
    try:
        assert not hasattr(win, "aom_cb") and not hasattr(win, "shutter_combo")
        assert not hasattr(win, "source_sn_edit") and not hasattr(win, "idle_spin")  # the 560 is found by its USB IDs
        assert win.findChildren(QComboBox) == [win.port_combo]  # only the Teensy port choice
        assert not win.findChildren(QSpinBox)
        assert [e for e in win.findChildren(QLineEdit) if e is not win.port_combo.lineEdit()] == []
        win.connect_btn.click()
        tab = win.engine_widget
        assert tab.source_box is not None and [tab.rows[k].wavelength.text() for k in ("L1", "L3")] == [
            "405 nm",
            "560 nm (AOM)",
        ]  # the bench's DF wavelengths
        assert win.engine.options == EngineOptions(source_idle_off_min=45, source_power_mw=600)  # from the cache
        assert tab.power_spin.value() == 600.0 and tab.idle_off_spin.value() == 45
        tab.idle_off_spin.setValue(50)  # the bench's tab saves like Squid's (this engine is not marked simulated)
        assert "idle_off_560_min: 50.0" in cache_file.read_text()
    finally:
        win.disconnect_btn.click()


def test_gate_box_follows_the_engine_after_the_guard(qtbot):
    engine, panel = _panel(qtbot)
    try:
        box = panel.rows["L2"].gate
        box.setChecked(True)  # GATE 1 sent now
        engine.sim_engine.lines[1]["gate"] = 0  # a status that predates our command must not flip the box
        engine.poll_once()
        assert box.isChecked()
        panel._gate_cmd_t[2] = 0.0  # the guard has passed
        engine.sim_engine.lines[3]["gate"] = 1  # e.g. a raw "LINE4:GATE TRUE" the regex did not catch
        engine.poll_once()
        assert not box.isChecked() and panel.rows["L4"].gate.isChecked()  # the boxes show the engine's gates
    finally:
        engine.close()
