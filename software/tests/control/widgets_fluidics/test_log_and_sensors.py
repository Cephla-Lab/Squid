import pytest

import squid.logging

pytest.importorskip("fluidics")

from control.widgets_fluidics.log_view import FluidicsLogView, ReagentsTable


def test_log_view_receives_fluidics_records_and_autoscrolls(qtbot):
    import logging

    view = FluidicsLogView()
    qtbot.addWidget(view)
    view.connect_logging()
    try:
        log = logging.getLogger("fluidics.test_tab")  # a library-style logger
        log.setLevel(logging.INFO)
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


def test_reagent_export_quotes_awkward_names(qtbot, tmp_path, monkeypatch):
    import csv as csv_module

    import control.widgets_fluidics.log_view as log_view_module
    from control.widgets_fluidics.log_view import ReagentsTable

    table = ReagentsTable()
    qtbot.addWidget(table)
    table.set_rows([(1, 'probe, "red"', 10.0, 20.0, 30.0)])
    target = tmp_path / "reagents.csv"
    monkeypatch.setattr(log_view_module.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    table._export()
    rows = list(csv_module.reader(target.open()))
    assert rows[1][1] == 'probe, "red"' and rows[1][0] == "1"


def test_log_view_catches_fluidics_loggers_only(qtbot):
    import logging

    from control.fluidics_system import install_logging_bridge

    install_logging_bridge()  # sets the library loggers to DEBUG, as in a live session
    view = FluidicsLogView()
    qtbot.addWidget(view)
    view.connect_logging()
    try:
        logging.getLogger("fluidics.control.syringe_pump").info("library record")
        logging.getLogger("XCaliburD").info("xcalibur record")
        squid.logging.get_logger("control.widgets_fluidics.system_panel").info("squid fluidics record")
        # not fluidics — must never reach the pane
        squid.logging.get_logger("Microcontroller").info("microcontroller noise")
        squid.logging.get_logger("control.core.multi_point_worker").info("acquisition noise")
        qtbot.waitUntil(lambda: "squid fluidics record" in view.text_edit.toPlainText(), timeout=3000)
        qtbot.wait(50)
        text = view.text_edit.toPlainText()
        assert "library record" in text and "xcalibur record" in text
        assert "microcontroller noise" not in text and "acquisition noise" not in text
    finally:
        view.disconnect_logging()


def _config_text(application="Flow Cell", tec=False, flow_sensors=True, monitor="off"):
    import yaml

    from tests.control.fluidics_test_config import CONFIG_YAML, TEC_CONFIG_YAML

    config = yaml.safe_load(TEC_CONFIG_YAML if tec else CONFIG_YAML)
    config["application"] = application
    if application == "Open Chamber":
        # the sections that application requires, as in the library's open_chamber_config.yaml fixture
        config["sample_selection_inlet"] = {"common_tubing_fluid_amount_ul": 900}
        config["samples"] = {"chamber_volume_ul": 1300}
    if flow_sensors:
        for sensor in config["flow_sensors"]:
            sensor["monitor"] = monitor
    else:
        del config["flow_sensors"]
    return yaml.safe_dump(config)


@pytest.fixture
def initialized_display_tab(qtbot, tmp_path, monkeypatch):
    """Build a FluidicsDisplayTab over a simulated system initialized from the given config text."""
    from control.fluidics_system import FluidicsService
    from control.widgets_fluidics.display_tab import FluidicsDisplayTab
    import control.widgets_fluidics.system_panel as system_panel_module
    from control.widgets_fluidics.system_panel import SystemPanel

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(SystemPanel, "_INITIALIZE_KWARGS", {"instant": True})
    # a failed Initialize raises a modal, which would hang the offscreen run; fail the test instead
    monkeypatch.setattr(system_panel_module.QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    built = []

    def build(config_text, shown_at=None):
        config = tmp_path / "fluidics_config.yaml"
        config.write_text(config_text)
        service = FluidicsService(default_config_path=str(config), simulated=True)
        tab = FluidicsDisplayTab(service)
        qtbot.addWidget(tab)
        built.append((tab, service))
        if shown_at is not None:  # the application's order: on screen first, Initialize later
            tab.resize(*shown_at)
            tab.show()
            qtbot.waitExposed(tab)
        outcome = []
        tab.system_ready.connect(lambda: outcome.append(None))
        tab.system_panel.initialize_failed.connect(outcome.append)
        tab.system_panel.initialize_button.click()
        qtbot.waitUntil(lambda: bool(outcome), timeout=15000)
        assert outcome == [None], f"Initialize failed: {outcome[0]}"
        return tab

    yield build
    for tab, service in built:
        tab.shutdown()
        assert service.close() == []


def _tab_titles(tab):
    return [tab.tabs.tabText(i) for i in range(tab.tabs.count())]


def test_flow_tab_sits_between_temperature_and_reagents(initialized_display_tab):
    tab = initialized_display_tab(_config_text(tec=True))
    assert _tab_titles(tab) == ["Log", "Temperature", "Flow", "Reagents"]
    sensors = tab.service.system.devices.flow_sensors
    assert [w.sensor for w in tab.flow_tab.plot_widgets] == list(sensors)


def test_flow_tab_without_a_temperature_controller(initialized_display_tab):
    tab = initialized_display_tab(_config_text(tec=False))
    assert _tab_titles(tab) == ["Log", "Flow", "Reagents"]


def test_no_flow_tab_without_flow_sensors(initialized_display_tab):
    tab = initialized_display_tab(_config_text(flow_sensors=False))
    assert tab.flow_tab is None
    assert "Flow" not in _tab_titles(tab)


def test_draw_protection_is_switchable_on_a_flow_cell(initialized_display_tab):
    tab = initialized_display_tab(_config_text(monitor="warn"))
    (panel,) = tab.flow_tab.plot_widgets
    assert panel.monitor_combo.isEnabled() and panel.monitor_combo.currentText() == "warn"
    panel.monitor_combo.setCurrentText("stop")
    assert panel.sensor.monitor == "stop"


def test_inert_draw_protection_is_switched_off_and_said_so(initialized_display_tab, qtbot):
    # Only Flow Cell arms the sensors; the library's bring-up switches an inert mode off and
    # reports it as an issue. What is Squid's: the issue is shown, and the combo greys out.
    from fluidics.devices import ISSUE_DRAW_PROTECTION

    tab = initialized_display_tab(_config_text(application="Open Chamber", monitor="stop"))
    (panel,) = tab.flow_tab.plot_widgets
    assert not panel.monitor_combo.isEnabled() and panel.monitor_combo.currentText() == "off"
    ((kind, message),) = tab.service.issues
    assert kind == ISSUE_DRAW_PROTECTION and "syringe_draw" in message
    assert "bring-up issue" in tab.system_panel.status_label.text()


def test_shutdown_closes_an_open_flow_recording(initialized_display_tab, tmp_path, monkeypatch):
    import fluidics.qt.sensor_plots as sensor_plots

    tab = initialized_display_tab(_config_text())
    (panel,) = tab.flow_tab.plot_widgets
    target = tmp_path / "flow.csv"
    monkeypatch.setattr(sensor_plots.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    panel.record_btn.click()
    recording = panel.file
    assert recording is not None and not recording.closed
    tab.shutdown()
    assert recording.closed and panel.file is None


def _scrolled_out(column):
    """Pixels of the instrument block that do not fit its pane."""
    return column.instrument_scroll.verticalScrollBar().maximum()


def _wait_for_short_window_layout(column, qtbot):
    """Initialize's widgets arrive over a few layout passes; settled, a window too short for both
    panes leaves the tabs exactly their minimum and scrolls the block for the rest."""
    tabs = column.widget(1)
    qtbot.waitUntil(
        lambda: column.sizes()[1] == tabs.minimumSizeHint().height() and _scrolled_out(column) > 0, timeout=2000
    )


def test_a_short_window_scrolls_the_instrument_block_instead_of_flattening_the_plots(initialized_display_tab, qtbot):
    # The instrument block alone wants more height than a 900 px window has, and a matplotlib
    # canvas accepts any height down to nothing: a plain column squeezed the plots to a sliver.
    tab = initialized_display_tab(_config_text(tec=True), shown_at=(1500, 900))
    column = tab.instrument_column
    _wait_for_short_window_layout(column, qtbot)
    for sensor_tab in (tab.temperature_tab, tab.flow_tab):
        tab.tabs.setCurrentWidget(sensor_tab)
        canvas = sensor_tab.plot_widgets[0].canvas
        # the plot's declared height is what keeps the tabs from being squeezed
        qtbot.waitUntil(lambda: canvas.height() >= canvas.minimumSizeHint().height() > 0, timeout=2000)


def test_the_instrument_block_is_never_clipped_sideways(initialized_display_tab, qtbot):
    tab = initialized_display_tab(_config_text(), shown_at=(1500, 900))
    scroll = tab.instrument_column.instrument_scroll
    _wait_for_short_window_layout(tab.instrument_column, qtbot)
    assert scroll.viewport().width() >= scroll.widget().minimumSizeHint().width()


def test_a_tall_window_shows_the_whole_instrument_block(initialized_display_tab, qtbot):
    # ~1300 px is where both panes fit. With the Flow page shown its plot's height counts, so a
    # floor of the column's own over the tabs' minimum would scroll the block here.
    tab = initialized_display_tab(_config_text(), shown_at=(1500, 1300))
    column = tab.instrument_column
    tab.tabs.setCurrentWidget(tab.flow_tab)
    qtbot.waitUntil(lambda: column.sizes()[0] >= column.instrument_scroll.widget().sizeHint().height(), timeout=2000)
    assert _scrolled_out(column) == 0


def test_a_dragged_divider_is_left_to_the_operator(initialized_display_tab, qtbot):
    tab = initialized_display_tab(_config_text(), shown_at=(1500, 900))
    column = tab.instrument_column
    _wait_for_short_window_layout(column, qtbot)
    column.moveSplitter(250, 1)  # what dragging the handle does
    assert column.sizes()[0] == 250
    height = sum(column.sizes())
    tab.resize(1500, 1000)
    qtbot.waitUntil(lambda: sum(column.sizes()) > height, timeout=2000)
    # the window scales both panes, as any splitter does; the rule no longer puts the block back
    block, tabs_height = column.sizes()
    assert block == round(250 * (block + tabs_height) / height)
