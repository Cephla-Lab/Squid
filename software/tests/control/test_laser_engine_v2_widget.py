import pytest
import yaml
from qtpy.QtWidgets import QApplication, QMessageBox, QTabWidget

from control._def import ILLUMINATION_CODE
from control.laser_engine_v2 import LaserEngineV2
from control.laser_engine_v2_link import EngineLink
from control.laser_engine_v2_sim import FakeEngine, FakeSource, build_simulated_engine
from control.laser_engine_v2_widget import SHUTTER_MODE_HINTS, LaserEngineV2Widget

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


@pytest.fixture(autouse=True)
def cache_file(tmp_path, monkeypatch):
    """The tab's 560 settings file, in a temporary directory: these tests never write Squid's cache/."""
    path = tmp_path / "cache" / "laser_engine_v2.yaml"
    monkeypatch.setattr("control.laser_engine_v2_settings.DEFAULT_CACHE_PATH", path)
    return path


def _widget(qtbot, saved=None, ttl_map=None):
    engine = build_simulated_engine()
    if ttl_map is not None:
        engine.ttl_map_provider = lambda: dict(ttl_map)
    engine.open()
    saver = (lambda mw: saved.append(mw) or True) if saved is not None else (lambda mw: True)
    widget = LaserEngineV2Widget(engine, save_power=saver)
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
    engine.set_source_power_mw(600.0)
    engine.set_source_power_mw(700.0)
    assert "560 nm laser set to 700 mW" in widget.notice_label.text()
    assert "cleared at connect" not in widget.notice_label.text()  # the latest two are shown ...
    assert "cleared at connect: hardware fault latch" in widget.notice_label.toolTip()  # ... the older ones are here
    engine.close()


def test_widget_scrolls_instead_of_squashing_in_a_short_panel(qtbot):
    engine, widget = _widget(qtbot)
    tabs = QTabWidget()
    qtbot.addWidget(tabs)
    tabs.addTab(widget, "Laser Engine")
    width = widget.sizeHint().width() + 40  # wide enough for the content in this style (no horizontal scrollbar)
    tabs.resize(width, 400)
    tabs.show()
    QApplication.processEvents()
    tabs.resize(width, widget.sizeHint().height() + tabs.tabBar().height())  # Squid's cap: everything fits
    QApplication.processEvents()
    assert not widget.scroll.verticalScrollBar().isVisible()
    assert not widget.scroll.horizontalScrollBar().isVisible()
    tabs.resize(width, 200)  # Squid's side panel when its column has no room left
    QApplication.processEvents()
    assert widget.scroll.verticalScrollBar().isVisible()
    assert widget.power_spin.height() >= widget.power_spin.minimumSizeHint().height()  # not squashed
    engine.close()


def test_widget_hides_a_finished_startup(qtbot):
    engine = build_simulated_engine()
    engine.open()
    engine.on_startup()
    for _ in range(10):
        engine.source_step()
        engine.poll_once()
    assert engine.bringup_state == "done"
    widget = LaserEngineV2Widget(engine, save_power=lambda mw: True)
    qtbot.addWidget(widget)
    assert widget.startup_label.isHidden()  # the pill and the lines say it
    engine.close()


def test_widget_560_box_sets_and_saves_the_laser_power(qtbot):
    saved = []
    engine, widget = _widget(qtbot, saved=saved)
    assert widget.source_box is not None and widget.source_box.title() == "560 nm laser"
    combo = widget.shutter_combo
    assert [combo.itemText(i) for i in range(combo.count())] == ["On use", "Always open"]
    assert combo.currentData() == "on_use" and widget.shutter_row.isVisibleTo(widget)  # the default
    assert widget.shutter_note.text() == SHUTTER_MODE_HINTS["on_use"]
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
    widget = LaserEngineV2Widget(engine, save_power=lambda mw: False)
    qtbot.addWidget(widget)
    widget.power_spin.setValue(300.0)
    widget.power_set_btn.click()
    assert engine.source_power_setpoint_mw == 300.0  # applied for this session
    assert "560 nm laser power not saved (see the log): this session only" in widget.notice_label.text()
    engine.close()


def test_widget_set_takes_a_typed_value_not_yet_committed(qtbot):
    saved = []
    engine, widget = _widget(qtbot, saved=saved)
    widget.power_spin.lineEdit().setText("800")  # typed, no Enter or focus-out (a Mac button click takes no focus)
    assert widget.power_spin.value() == 200.0  # keyboard tracking off: not committed yet
    widget.power_set_btn.click()
    assert engine.source_power_setpoint_mw == 800.0 and saved == [800.0]
    engine.close()


def test_widget_saves_both_560_settings_to_the_cache(qtbot, cache_file):
    fake = FakeEngine(tok_delay_polls=0)
    real = LaserEngineV2(link_factory=lambda: EngineLink(fake), source_factory=FakeSource)
    real.open()
    widget = LaserEngineV2Widget(real)  # the default savers: cache/laser_engine_v2.yaml
    qtbot.addWidget(widget)
    assert not cache_file.exists()  # building the tab saves nothing
    widget.power_spin.setValue(900.0)
    widget.power_set_btn.click()
    widget.idle_off_spin.setValue(45)
    widget.shutter_combo.setCurrentIndex(widget.shutter_combo.findData("always_open"))
    assert yaml.safe_load(cache_file.read_text()) == {
        "power_560_mw": 900.0,
        "idle_off_560_min": 45.0,
        "shutter_mode_560": "always_open",
    }
    assert real.shutter_mode == "always_open"
    assert real.source_idle_off_s == 45 * 60
    widget.idle_off_spin.setValue(0)  # 24 h
    assert yaml.safe_load(cache_file.read_text())["idle_off_560_min"] == 0.0
    real.close()


def test_widget_never_saves_a_simulated_engine(qtbot, cache_file):
    sim = build_simulated_engine()  # FakeSource: 200-1000 mW, not the machine's limits
    sim.open()
    widget = LaserEngineV2Widget(sim)  # the default savers
    qtbot.addWidget(widget)
    widget.power_spin.setValue(900.0)
    widget.power_set_btn.click()
    widget.idle_off_spin.setValue(10)
    widget.shutter_combo.setCurrentIndex(widget.shutter_combo.findData("always_open"))
    assert sim.source_power_setpoint_mw == 900.0 and sim.source_idle_off_s == 10 * 60  # this session only
    assert sim.shutter_mode == "always_open"
    assert not cache_file.exists()
    assert "560 nm shutter mode not saved (see the log): this session only" in widget.notice_label.text()
    assert "560 nm idle-off not saved (see the log)" in widget.notice_label.toolTip()
    assert "560 nm laser power not saved (see the log)" in widget.notice_label.toolTip()
    sim.close()


def test_widget_idle_off_saved_and_unsaved(qtbot):
    saved = []
    engine = build_simulated_engine()
    engine.open()
    widget = LaserEngineV2Widget(engine, save_power=lambda mw: True, save_idle_off=lambda m: saved.append(m) or True)
    qtbot.addWidget(widget)
    widget.idle_off_spin.setValue(20)
    assert saved == [20] and "idle-off not saved" not in widget.notice_label.text()
    widget._save_idle_off = lambda m: False
    widget.idle_off_spin.setValue(25)
    assert engine.source_idle_off_s == 25 * 60 and "560 nm idle-off not saved" in widget.notice_label.text()
    engine.close()


def test_widget_without_a_source_has_no_560_box(qtbot):
    engine = LaserEngineV2(link_factory=lambda: EngineLink(FakeEngine()))
    engine.open()
    widget = LaserEngineV2Widget(engine, save_power=lambda mw: True)
    qtbot.addWidget(widget)
    engine.poll_once()
    assert widget.source_box is None and widget.rows["L3"].state.text() == "NOT CONFIGURED"
    engine.close()


def test_widget_shows_the_startup_state_at_once(qtbot):
    engine = build_simulated_engine()
    engine.open()
    engine.on_startup()  # the bring-up is now running: the sim TECs need 3 polls
    widget = LaserEngineV2Widget(engine)  # no poll_once() in between: the tab must not wait for the next status
    qtbot.addWidget(widget)
    assert widget.startup_label.text().startswith("Startup: ")
    assert widget.rows["L1"].state.text() != "—"  # seeded from the latest status
    engine.close()


def _wheel(widget, steps=3):
    from qtpy.QtCore import QPoint, QPointF, Qt
    from qtpy.QtGui import QWheelEvent

    centre = QPointF(widget.width() / 2, widget.height() / 2)
    event = QWheelEvent(
        centre, centre, QPoint(0, 0), QPoint(0, 120 * steps), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False
    )
    QApplication.sendEvent(widget, event)


def test_widget_560_spinboxes_ignore_the_mouse_wheel(qtbot):
    idle_saved = []
    engine = build_simulated_engine()
    engine.open()
    widget = LaserEngineV2Widget(
        engine, save_power=lambda mw: True, save_idle_off=lambda m: idle_saved.append(m) or True
    )
    qtbot.addWidget(widget)
    widget.show()
    power, idle = widget.power_spin.value(), widget.idle_off_spin.value()
    for spin in (widget.power_spin, widget.idle_off_spin):  # scrolling the tab past them must change nothing
        _wheel(spin)
    assert (widget.power_spin.value(), widget.idle_off_spin.value()) == (power, idle)
    assert idle_saved == [] and engine.source_power_setpoint_mw == 200.0
    engine.close()


def test_widget_idle_off_applies_a_typed_value_only_when_committed(qtbot):
    idle_saved = []
    engine = build_simulated_engine()
    engine.open()
    widget = LaserEngineV2Widget(
        engine, save_power=lambda mw: True, save_idle_off=lambda m: idle_saved.append(m) or True
    )
    qtbot.addWidget(widget)
    assert not widget.idle_off_spin.keyboardTracking()
    widget.idle_off_spin.lineEdit().setText("120")  # typed: not 1, 12, 120 to the engine and the cache
    assert idle_saved == []
    widget.idle_off_spin.interpretText()  # Enter / focus-out
    assert idle_saved == [120] and engine.source_idle_off_s == 120 * 60
    engine.close()


def test_widget_shutter_mode_applies_at_once_and_is_saved(qtbot):
    saved = []
    engine, widget = _widget(qtbot)
    widget._save_shutter_mode = lambda mode: saved.append(mode) or True
    engine.wake_up("L3")
    for _ in range(5):
        engine.source_step()
    engine.poll_once()
    fake = engine.sim_engine
    assert not fake.shut_open  # On use, nothing images with the 560
    widget.shutter_combo.setCurrentIndex(widget.shutter_combo.findData("always_open"))
    assert fake.shut_open and saved == ["always_open"]  # at once, not at the next poll
    assert widget.shutter_note.text() == SHUTTER_MODE_HINTS["always_open"]
    widget.shutter_combo.setCurrentIndex(widget.shutter_combo.findData("on_use"))
    assert not fake.shut_open and saved == ["always_open", "on_use"]
    engine.close()


def test_widget_shutter_mode_refused_goes_back_and_is_not_saved(qtbot, monkeypatch):
    saved = []
    engine, widget = _widget(qtbot)
    widget._save_shutter_mode = lambda mode: saved.append(mode) or True

    def refuse(mode):
        raise RuntimeError("laser engine connection lost")

    monkeypatch.setattr(engine, "set_shutter_mode", refuse)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    widget.shutter_combo.setCurrentIndex(widget.shutter_combo.findData("always_open"))
    assert shown == ["laser engine connection lost"]
    assert widget.shutter_combo.currentData() == "on_use" and engine.shutter_mode == "on_use" and saved == []
    engine.close()


def _ready_widget(qtbot):
    engine, widget = _widget(qtbot)
    widget._save_shutter_mode = lambda mode: True
    engine.wake_up("L3")
    for _ in range(5):
        engine.source_step()
    engine.poll_once()  # the tab updates on every status
    return engine, widget


def test_widget_shutter_button_opens_and_closes_by_hand(qtbot):
    engine, widget = _ready_widget(qtbot)
    fake, btn = engine.sim_engine, widget.shutter_btn
    assert btn.isVisibleTo(widget) and btn.isEnabled() and btn.text() == "Open shutter" and not fake.shut_open
    btn.click()
    assert fake.shut_open and btn.text() == "Close shutter" and btn.isEnabled()
    btn.click()
    assert not fake.shut_open and btn.text() == "Open shutter"
    engine.close()


def test_widget_shutter_button_while_the_560_is_used_or_not_ready_or_always_open(qtbot):
    engine, widget = _ready_widget(qtbot)
    fake, btn = engine.sim_engine, widget.shutter_btn
    btn.click()  # open by hand ...
    live = engine.use("live", [engine.light_source.channel_mappings[561]])  # ... then live takes over
    engine.poll_once()
    assert btn.text() == "Shutter in use" and not btn.isEnabled() and "live" in btn.toolTip() and fake.shut_open
    live.release()
    engine.poll_once()
    assert btn.text() == "Open shutter" and btn.isEnabled() and not fake.shut_open
    engine.put_to_sleep("L3")
    engine.poll_once()
    assert not btn.isEnabled() and "the 560 is not ready" in btn.toolTip()
    widget.shutter_combo.setCurrentIndex(widget.shutter_combo.findData("always_open"))
    assert not btn.isVisibleTo(widget)  # the mode keeps it open whenever the 560 is ready
    widget.shutter_combo.setCurrentIndex(widget.shutter_combo.findData("on_use"))
    assert btn.isVisibleTo(widget)
    engine.close()
