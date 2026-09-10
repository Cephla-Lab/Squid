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
