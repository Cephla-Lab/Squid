import threading

import pytest
from qtpy.QtWidgets import QMessageBox

import squid.logging
from control.laser_engine_rev1 import EngineOptions, LaserEngineRev1, LaserEngineRev1Error
from control.laser_engine_rev1_bench import BenchWindow, LaserEngineRev1ServicePanel, LogPane
from control.laser_engine_rev1_link import EngineLink
from control.laser_engine_rev1_sim import FakeEngine, build_simulated_engine


@pytest.fixture(autouse=True)
def _fast_resync(monkeypatch):
    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)


def _panel(qtbot, options=None):
    engine = build_simulated_engine(options=options)
    engine.open()  # no threads: the tests call poll_once()
    panel = LaserEngineRev1ServicePanel(engine)
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


def test_panel_560_floor_follows_the_aom(qtbot):
    engine, panel = _panel(qtbot)  # FakeSource 200-1000 mW, no AOM: the 560 floor is 20 %
    try:
        assert panel.rows["L3"].spin.minimum() == 20
        assert panel.rows["L2"].spin.minimum() == 0
    finally:
        engine.close()
    engine, panel = _panel(qtbot, EngineOptions(aom_in_path=True))
    try:
        assert panel.rows["L3"].spin.minimum() == 0
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
        panel.rows["L3"].spin.setValue(50.0)
        assert panel.source_requested_label.text().startswith("500 mW")
        engine.sim_source.needs_key = True
        engine.source_step()
        engine.poll_once()
        assert panel.source_state_label.text().startswith("NEEDS_KEY")
    finally:
        engine.close()


def test_panel_without_a_source_has_no_source_box(qtbot):
    engine = LaserEngineRev1(link_factory=lambda: EngineLink(FakeEngine()))
    engine.open()
    try:
        panel = LaserEngineRev1ServicePanel(engine)
        qtbot.addWidget(panel)
        assert panel.source_box is None
        assert panel.rows["L3"].spin.minimum() == 0  # no source: no floor
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
    bare = LaserEngineRev1(link_factory=lambda: EngineLink(FakeEngine()))
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
    assert win.bringup_cb.isChecked() and not win.shutter_combo.isEnabled()  # shutter mode needs the AOM
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
        with pytest.raises(LaserEngineRev1Error, match="not open"):
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
