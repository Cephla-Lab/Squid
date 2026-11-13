import pytest
from qtpy.QtGui import QColor
from qtpy.QtWidgets import QTableWidget, QTableWidgetItem
import control.theme as theme


def test_status_widget_tokens_pin_classic_literals():
    # Every token WarningErrorWidget reads, at the literal the widget used before the skin: a Light tweak must
    # not reach Classic through CLASSIC = replace(LIGHT, ...).
    c = theme.CLASSIC
    assert {name: getattr(c, name) for name in CLASSIC_STATUS_LITERALS} == CLASSIC_STATUS_LITERALS


CLASSIC_STATUS_LITERALS = {
    "pill_bg": "#666",
    "pill_hover": "#444",
    "pill_pressed": "#222",
    "icon_button_fg": "#888",
    "icon_button_hover_fg": "#000",
    "popup_bg": "white",
    "popup_border": "#aaa",
    "popup_header_bg": "#f5f5f5",
    "popup_header_border": "#ddd",
    "popup_row_border": "#eee",
    "popup_time_fg": "#666",
    "popup_msg_fg": "#333",
    "clear_bg": "#e74c3c",
    "clear_hover": "#c0392b",
    "dismiss_bg": "#ddd",
    "dismiss_fg": "#666",
    "dismiss_hover_bg": "#ccc",
    "dismiss_hover_fg": "#333",
    "error_bg": "#fef2f2",
    "error_fg": "#b91c1c",
    "error_icon_bg": "#dc2626",
    "warn_bg": "#fefce8",
    "warn_fg": "#a16207",
    "warn_icon_bg": "#eab308",
    "warn_dot": "#ca8a04",
}


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
    # The white bold glyph on the 20 px status disc. Light's #ca8a04 is 2.94:1 (Classic's #eab308 was 1.9:1).
    for fg, bg in (("#ffffff", t.warn_icon_bg), ("#ffffff", t.error_icon_bg)):
        assert theme.contrast_ratio(fg, bg) >= 2.9, (fg, bg)


# The theme is fixed for the process (a change restarts Squid), so widgets read their colours once, when built.


def test_controller_port_rows_use_the_theme_brush(qapp, qtbot):
    import control.microscope, control.widgets as widgets

    m = theme.install(qapp, "dark")
    try:
        scope = control.microscope.Microscope.build_from_global_config(True)
        dlg = widgets.ControllerPortMappingDialog(scope.config_repo)  # as gui_hcs.py constructs it
        qtbot.add_widget(dlg)
        assert dlg.table.item(0, 0).background().color() == QColor(theme.DARK.row_disabled)
    finally:
        m.apply("classic")


def test_workflow_rows_use_the_theme_brush(qapp, qtbot):
    import control.widgets_workflow as ww

    m = theme.install(qapp, "dark")
    try:
        dlg = ww.WorkflowRunnerDialog()  # gui_hcs.openWorkflowRunner passes only parent=
        qtbot.add_widget(dlg)
        assert dlg.table.item(0, dlg.COL_NAME).background().color() in (
            QColor(theme.DARK.row_highlight),
            QColor(theme.DARK.input_bg),
        )
    finally:
        m.apply("classic")


@pytest.mark.parametrize("name, pill", [("classic", "#666"), ("dark", theme.DARK.pill_bg)])
def test_warning_widget_styles_for_the_theme(qapp, qtbot, name, pill):
    import control.widgets as widgets

    m = theme.install(qapp, name)
    try:
        w = widgets.WarningErrorWidget()
        qtbot.add_widget(w)
        assert pill in w.btn_expand.styleSheet()
    finally:
        m.apply("classic")


@pytest.mark.parametrize("tokens", [theme.CLASSIC, theme.DARK], ids=["classic", "dark"])
def test_laser_autofocus_background_follows_the_theme(qapp, qtbot, tokens):
    from unittest.mock import MagicMock
    import control.widgets as widgets

    m = theme.install(qapp, "dark" if tokens is theme.DARK else "classic")
    try:
        controller = MagicMock()
        controller.laser_af_properties.pixel_to_um = 1.0
        controller.laser_af_properties.calibration_timestamp = "never"
        w = widgets.LaserAutofocusSettingWidget(MagicMock(), MagicMock(), controller)
        qtbot.add_widget(w)
        assert w.palette().color(w.backgroundRole()) == QColor(tokens.row_disabled)
    finally:
        m.apply("classic")
