import pytest
from qtpy.QtGui import QColor
from qtpy.QtWidgets import QTableWidget, QTableWidgetItem
import control.theme as theme


def test_status_widget_tokens_pin_classic_literals():
    c = theme.CLASSIC
    assert (c.pill_bg, c.popup_bg, c.error_bg, c.warn_fg) == ("#666", "white", "#fef2f2", "#a16207")


@pytest.mark.parametrize("t", [theme.LIGHT, theme.DARK])
def test_status_widget_tokens_are_readable(t):
    pairs = [
        (t.popup_msg_fg, t.popup_bg),
        (t.error_fg, t.error_bg),
        (t.warn_fg, t.warn_bg),
        ("#ffffff", t.pill_bg),
        (t.dismiss_fg, t.dismiss_bg),
        ("#ffffff", t.pill_hover),
        ("#ffffff", t.pill_pressed),
        ("#ffffff", t.clear_bg),
        ("#ffffff", t.clear_hover),
        (t.dismiss_hover_fg, t.dismiss_hover_bg),
    ]
    for fg, bg in pairs:
        assert theme.contrast_ratio(fg, bg) >= 4.5, (fg, bg)


def test_controller_port_rows_repaint_on_switch(qapp, qtbot):
    import control.microscope, control.widgets as widgets

    m = theme.install(qapp, "light")
    try:
        scope = control.microscope.Microscope.build_from_global_config(True)
        dlg = widgets.ControllerPortMappingDialog(scope.config_repo)  # as gui_hcs.py:2573 constructs it
        qtbot.add_widget(dlg)
        item = dlg.table.item(0, 0)
        assert item.background().color() == QColor("#f3f3f3")
        # an unsaved edit in the editable column must survive the switch (no reload, only a recolour);
        # column 1 holds a SourceCodeWidget cell widget: set_source_code(int | None) drives a checkbox and
        # a QSpinBox, get_source_code() returns the int or None
        editor = dlg.table.cellWidget(0, 1)
        editor.set_source_code(17)
        m.apply("dark")
        qtbot.waitUntil(lambda: dlg.table.item(0, 0).background().color() == QColor("#3a3a3a"))
        assert dlg.table.cellWidget(0, 1) is editor, "theme switch rebuilt the editor controls"
        assert editor.get_source_code() == 17
    finally:
        m.apply("classic")


def test_workflow_rows_keep_edits_across_switch(qapp, qtbot):
    import control.widgets_workflow as ww

    m = theme.install(qapp, "light")
    try:
        dlg = ww.WorkflowRunnerDialog()  # gui_hcs.openWorkflowRunner passes only parent=
        qtbot.add_widget(dlg)
        # the dialog loads its default one-row workflow (an Acquisition) in __init__
        dlg._load_workflow_to_table()
        # COL_INCLUDE holds a checkbox cell widget (item() is None there); COL_NAME is a populated item
        dlg.table.item(0, dlg.COL_NAME).setText("edited by test")
        m.apply("dark")
        assert dlg.table.item(0, dlg.COL_NAME).text() == "edited by test"
        assert dlg.table.item(0, dlg.COL_NAME).background().color() in (
            QColor(theme.DARK.row_highlight),
            QColor(theme.DARK.input_bg),
        )
    finally:
        m.apply("classic")


def test_warning_widget_restyles_on_switch(qapp, qtbot):
    import control.widgets as widgets

    m = theme.install(qapp, "light")
    try:
        w = widgets.WarningErrorWidget()
        qtbot.add_widget(w)
        m.apply("dark")
        assert theme.DARK.pill_bg in w.btn_expand.styleSheet()
        m.apply("classic")
        assert "#666" in w.btn_expand.styleSheet()
    finally:
        m.apply("classic")


def test_laser_autofocus_background_follows_switch(qapp, qtbot):
    from unittest.mock import MagicMock
    import control.widgets as widgets

    m = theme.install(qapp, "light")
    try:
        controller = MagicMock()
        controller.laser_af_properties.pixel_to_um = 1.0
        controller.laser_af_properties.calibration_timestamp = "never"
        w = widgets.LaserAutofocusSettingWidget(MagicMock(), MagicMock(), controller)
        qtbot.add_widget(w)
        assert w.palette().color(w.backgroundRole()) == QColor(theme.LIGHT.row_disabled)
        m.apply("dark")
        assert w.palette().color(w.backgroundRole()) == QColor(theme.DARK.row_disabled)
        m.apply("classic")
        assert w.palette().color(w.backgroundRole()) == QColor(theme.CLASSIC.row_disabled)
    finally:
        m.apply("classic")
