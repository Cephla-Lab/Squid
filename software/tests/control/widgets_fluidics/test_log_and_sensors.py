import pytest

import squid.logging

pytest.importorskip("fluidics")

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
