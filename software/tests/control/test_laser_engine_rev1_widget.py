from control.laser_engine_rev1 import EngineOptions
from control.laser_engine_rev1_sim import build_simulated_engine
from control.laser_engine_rev1_widget import LaserEngineRev1Widget


def _widget(qtbot, options=None):
    engine = build_simulated_engine(options=options)
    engine.open()
    widget = LaserEngineRev1Widget(engine)
    qtbot.addWidget(widget)
    return engine, widget


def test_widget_shows_states_and_errors(qtbot):
    engine, widget = _widget(qtbot)
    engine.poll_once()
    assert "NOT_ARMED" in widget.line_labels["L1"].text()
    assert widget.summary_label.text() == "Engine: Not armed"
    fake = engine.sim_engine
    fake.armed, fake.tok[0], fake.lines[0]["st"] = True, True, "ON"
    fake.set_tok(1, False)  # TOK lost on L1
    engine.poll_once()
    assert "BLOCKED" in widget.line_labels["L1"].text() and "FAULT:RESET" in widget.line_labels["L1"].text()
    assert widget.summary_label.text() == "Engine: Error"
    assert "TOK_LOST" in widget.event_label.text()
    engine.close()


def test_widget_banner_on_connection_lost(qtbot):
    engine, widget = _widget(qtbot)
    engine.connection_lost.emit("unplugged")
    assert widget.banner.isVisibleTo(widget) and "unplugged" in widget.banner.text()
    engine.close()


def test_widget_shows_what_the_connect_reset_cleared(qtbot):
    engine, widget = _widget(qtbot)  # the simulated engine starts with its power-up latch set
    assert "cleared at connect: hardware fault latch" in widget.notice_label.text()
    engine._notice("560 nm: 100 mW requested is below the 560 nm minimum of 200 mW - running at the minimum")
    assert "below the 560 nm minimum" in widget.notice_label.text()
    engine.close()


def test_widget_idle_off_and_shutter_controls(qtbot):
    engine, widget = _widget(qtbot)
    assert widget.idle_off_spin.value() == 30 and not widget.shutter_combo.isEnabled()  # no AOM in the beam path
    widget.idle_off_spin.setValue(0)
    assert widget.idle_off_spin.text() == "24 h" and engine.source_idle_off_s == 24 * 3600
    engine.close()
    engine2, widget2 = _widget(qtbot, EngineOptions(aom_in_path=True))
    assert widget2.shutter_combo.isEnabled()
    widget2.shutter_combo.setCurrentIndex(widget2.shutter_combo.findData("open"))
    assert engine2.shutter_with_aom == "open" and engine2.sim_engine.sent[-1] == "SHUT:SRC MCU"
    engine2.close()


def test_widget_shows_the_startup_state_at_once(qtbot):
    engine = build_simulated_engine()
    engine.open()
    engine.on_startup()  # the bring-up is now running: the sim TECs need 3 polls
    widget = LaserEngineRev1Widget(engine)  # no poll_once() in between: the tab must not wait for the next status
    qtbot.addWidget(widget)
    assert widget.startup_label.text().startswith("startup: ")
    engine.close()
