import pytest
from qtpy.QtWidgets import QComboBox, QMessageBox

from control._def import ILLUMINATION_CODE
from control.laser_engine_rev1 import LaserEngineRev1
from control.laser_engine_rev1_link import EngineLink
from control.laser_engine_rev1_sim import FakeEngine, FakeSource, build_simulated_engine
from control.laser_engine_rev1_widget import SHUTTER_NOTE, LaserEngineRev1Widget

DF_MAP = {  # a DF machine's illumination port map: one wavelength per engine line
    405: ILLUMINATION_CODE.ILLUMINATION_D1,
    488: ILLUMINATION_CODE.ILLUMINATION_D2,
    560: ILLUMINATION_CODE.ILLUMINATION_D3,
    638: ILLUMINATION_CODE.ILLUMINATION_D4,
    730: ILLUMINATION_CODE.ILLUMINATION_D5,
}


@pytest.fixture(autouse=True)
def _no_message_boxes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: pytest.fail(f"unexpected message box: {a[2:]}"))


def _widget(qtbot, saved=None, ttl_map=None):
    engine = build_simulated_engine()
    if ttl_map is not None:
        engine.ttl_map_provider = lambda: dict(ttl_map)
    engine.open()
    saver = (lambda mw: saved.append(mw) or True) if saved is not None else (lambda mw: True)
    widget = LaserEngineRev1Widget(engine, save_power=saver)
    qtbot.addWidget(widget)
    return engine, widget


def test_widget_shows_states_and_errors(qtbot):
    engine, widget = _widget(qtbot)
    engine.poll_once()
    assert widget.rows["L1"].state.text() == "NOT ARMED" and widget.state_pill.text() == "Disarmed"
    fake = engine.sim_engine
    fake.armed, fake.tok[0], fake.lines[0]["st"] = True, True, "ON"
    fake.set_tok(1, False)  # TOK lost on L1
    engine.poll_once()
    assert widget.rows["L1"].state.text() == "BLOCKED" and "FAULT:RESET" in widget.rows["L1"].reason.text()
    assert widget.state_pill.text() == "Fault"
    assert "TOK_LOST" in widget.event_label.text()
    fake.faults, fake.lines[0]["blocked"] = [], 0
    engine.poll_once()
    assert widget.state_pill.text() == "Armed"
    engine.close()


def test_widget_lines_table_wavelengths_set_points_and_unused_lines(qtbot):
    engine, widget = _widget(qtbot, ttl_map=DF_MAP)
    engine.set_line_intensity(3, 50.0)  # the 560 intensity = the AOM at 2.5 V
    engine.set_line_intensity(2, 50.0)
    engine.poll_once()
    rows = widget.rows
    assert [rows[k].wavelength.text() for k in rows] == ["405 nm", "488 nm", "560 nm (AOM)", "638 nm", "730 nm"]
    assert rows["L3"].setpoint.text() == "2.50 V (50 %)" and rows["L3"].max.text() == "5.00 V"
    assert rows["L1"].setpoint.text() == "0.000 A" and rows["L1"].max.text() == "0.541 A"
    assert rows["L2"].setpoint.text() == "1.022 A"
    engine.sim_engine.lines[4]["kind"] = "NONE"  # a variant with nothing on line 5: omitted
    engine.poll_once()
    assert not rows["L5"].state.isVisibleTo(widget) and rows["L4"].state.isVisibleTo(widget)
    engine.close()


def test_widget_wavelength_column_with_squid_default_map(qtbot):
    engine, widget = _widget(qtbot)  # no illumination config: Squid's default map shares ports
    engine.poll_once()
    assert widget.rows["L2"].wavelength.text() == "470 / 488 nm"
    assert widget.rows["L3"].wavelength.text().endswith("560 / 561 nm (AOM)")
    engine.close()


def test_widget_connection_lost(qtbot):
    engine, widget = _widget(qtbot)
    engine.connection_lost.emit("unplugged")
    assert widget.state_pill.text() == "Connection lost" and widget.state_pill.toolTip() == "unplugged"
    assert "Connection lost: unplugged" in widget.notice_label.text()
    engine.close()


def test_widget_shows_what_the_connect_reset_cleared(qtbot):
    engine, widget = _widget(qtbot)  # the simulated engine starts with its power-up latch set
    assert "cleared at connect: hardware fault latch" in widget.notice_label.text()
    assert "no AOM calibration" in widget.notice_label.text()
    engine.set_source_power_mw(600.0)
    assert "560 nm laser set to 600 mW" in widget.notice_label.text()
    engine.close()


def test_widget_560_box_sets_and_saves_the_laser_power(qtbot):
    saved = []
    engine, widget = _widget(qtbot, saved=saved)
    assert widget.source_box is not None and widget.source_box.title() == "560 nm laser"
    assert not widget.findChildren(QComboBox)  # no "Shutter with AOM" choice any more
    assert widget.shutter_note.text() == SHUTTER_NOTE and "safety only" in SHUTTER_NOTE
    assert widget.source_limits_label.text() == "200 – 1000 mW"
    spin = widget.power_spin
    assert (spin.minimum(), spin.maximum(), spin.value()) == (200.0, 1000.0, 200.0)  # unset: the minimum
    assert not spin.keyboardTracking()
    spin.setValue(650.0)
    assert engine.source_power_setpoint_mw == 200.0 and saved == []  # nothing sent until Set
    widget.power_set_btn.click()
    assert engine.source_power_setpoint_mw == 650.0 and saved == [650.0]
    assert widget.source_power_label.text() == "measured — · set 650 mW"
    engine.poll_once()
    assert widget.source_state_label.text() == "OFF"
    engine.close()


def test_widget_560_box_follows_the_source(qtbot):
    engine, widget = _widget(qtbot)
    engine.sim_engine.tok = [True] * 5
    engine.wake_up("L3")
    engine.source_step()
    engine.poll_once()
    assert widget.source_state_label.text() == "STARTING"
    for _ in range(3):
        engine.source_step()
    engine.poll_once()
    assert widget.source_state_label.text() == "READY"
    assert widget.source_power_label.text() == "measured 200 mW · set 200 mW"
    engine.sim_source.needs_key = True
    engine.source_step()
    engine.poll_once()
    assert widget.source_state_label.text() == "NEEDS KEY" and "key OFF then ON" in widget.source_detail_label.text()
    engine.sim_source.silent = True
    engine.source_step()
    engine.poll_once()
    assert widget.source_state_label.text() == "LINK LOST"
    engine.close()


def test_widget_unsaved_power_is_said(qtbot):
    engine = build_simulated_engine()
    engine.open()
    widget = LaserEngineRev1Widget(engine, save_power=lambda mw: False)
    qtbot.addWidget(widget)
    widget.power_spin.setValue(300.0)
    widget.power_set_btn.click()
    assert engine.source_power_setpoint_mw == 300.0  # applied for this session
    assert "not saved to the machine .ini" in widget.notice_label.text()
    engine.close()


def test_widget_set_takes_a_typed_value_not_yet_committed(qtbot):
    saved = []
    engine, widget = _widget(qtbot, saved=saved)
    widget.power_spin.lineEdit().setText("800")  # typed, no Enter or focus-out (a Mac button click takes no focus)
    assert widget.power_spin.value() == 200.0  # keyboard tracking off: not committed yet
    widget.power_set_btn.click()
    assert engine.source_power_setpoint_mw == 800.0 and saved == [800.0]
    engine.close()


def test_widget_saves_to_the_machine_ini_only_outside_simulation(qtbot, monkeypatch):
    calls = []
    monkeypatch.setattr("control.laser_engine_rev1_widget.save_source_power_mw", lambda mw: calls.append(mw) or True)
    sim = build_simulated_engine()  # FakeSource: 200-1000 mW, not the machine's limits
    sim.open()
    widget = LaserEngineRev1Widget(sim)  # the default saver
    qtbot.addWidget(widget)
    widget.power_spin.setValue(900.0)
    widget.power_set_btn.click()
    assert sim.source_power_setpoint_mw == 900.0 and calls == []
    assert "not saved to the machine .ini" in widget.notice_label.text()
    sim.close()
    fake = FakeEngine(tok_delay_polls=0)
    real = LaserEngineRev1(link_factory=lambda: EngineLink(fake), source_factory=FakeSource)
    real.open()
    widget = LaserEngineRev1Widget(real)
    qtbot.addWidget(widget)
    widget.power_spin.setValue(900.0)
    widget.power_set_btn.click()
    assert calls == [900.0]
    real.close()


def test_widget_idle_off_control(qtbot):
    engine, widget = _widget(qtbot)
    assert widget.idle_off_spin.value() == 30
    widget.idle_off_spin.setValue(0)
    assert widget.idle_off_spin.text() == "24 h" and engine.source_idle_off_s == 24 * 3600
    engine.close()


def test_widget_without_a_source_has_no_560_box(qtbot):
    engine = LaserEngineRev1(link_factory=lambda: EngineLink(FakeEngine()))
    engine.open()
    widget = LaserEngineRev1Widget(engine, save_power=lambda mw: True)
    qtbot.addWidget(widget)
    engine.poll_once()
    assert widget.source_box is None and widget.rows["L3"].state.text() == "NOT CONFIGURED"
    engine.close()


def test_widget_shows_the_startup_state_at_once(qtbot):
    engine = build_simulated_engine()
    engine.open()
    engine.on_startup()  # the bring-up is now running: the sim TECs need 3 polls
    widget = LaserEngineRev1Widget(engine)  # no poll_once() in between: the tab must not wait for the next status
    qtbot.addWidget(widget)
    assert widget.startup_label.text().startswith("Startup: ")
    assert widget.rows["L1"].state.text() != "—"  # seeded from the latest status
    engine.close()
