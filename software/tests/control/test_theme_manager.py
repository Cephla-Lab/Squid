import importlib

import pytest
from qtpy.QtGui import QColor, QPalette

import control.theme as theme


@pytest.fixture
def mgr(qapp):
    m = theme.ThemeManager(qapp)
    yield m
    m.apply("classic")


def test_apply_dark_sets_stylesheet_palette_and_font(qapp, mgr):
    mgr.apply("dark")
    assert "#202020" in qapp.styleSheet()
    assert qapp.palette().color(QPalette.Window) == QColor("#202020")
    assert mgr.current == "dark" and not mgr.is_classic()


def test_light_is_no_longer_a_theme(mgr):
    assert theme.THEMES == ("classic", "dark")
    with pytest.raises(ValueError):
        mgr.apply("light")


def test_apply_classic_restores_defaults(qapp, mgr):
    before_font = qapp.font().family()
    mgr.apply("dark")
    mgr.apply("classic")
    assert qapp.styleSheet() == ""
    assert qapp.font().family() == before_font
    assert mgr.is_classic()


def test_needs_restart_only_across_classic(mgr):
    mgr.apply("dark")
    assert not mgr.needs_restart("dark") and mgr.needs_restart("classic")
    mgr.apply("classic")
    assert mgr.needs_restart("dark") and not mgr.needs_restart("classic")


def test_theme_changed_is_emitted_with_the_name(qapp, mgr, qtbot):
    with qtbot.waitSignal(mgr.theme_changed) as blocker:
        mgr.apply("dark")
    assert blocker.args == ["dark"]


def test_color_and_row_brush_follow_current_theme(mgr):
    mgr.apply("dark")
    assert mgr.color("accent") == QColor("#4cc2ff")
    assert mgr.row_brush("row_disabled").color() == QColor("#3a3a3a")
    mgr.apply("classic")
    assert mgr.row_brush("row_disabled").color() == QColor(240, 240, 240)


def test_dock_label_style_is_patched_and_restored(mgr):
    dockmod = importlib.import_module("pyqtgraph.dockarea.Dock")
    original = dockmod.DockLabel.updateStyle
    mgr.apply("dark")
    assert dockmod.DockLabel.updateStyle is not original
    mgr.apply("classic")
    assert dockmod.DockLabel.updateStyle is original


def test_live_docks_are_restyled_and_restored(qapp, mgr):
    from pyqtgraph.dockarea import Dock

    dock = Dock("Plot")
    original_label = dock.label.styleSheet()
    original_area = dock.widgetArea.styleSheet()
    mgr.apply("dark")
    assert theme.DARK.header in dock.label.styleSheet()
    assert "#000" not in dock.widgetArea.styleSheet()
    assert theme.DARK.border in dock.widgetArea.styleSheet()
    mgr.apply("classic")
    assert dock.label.styleSheet() == original_label
    assert dock.widgetArea.styleSheet() == original_area
    dock.deleteLater()


def test_install_and_manager_singleton(qapp):
    m = theme.install(qapp, "dark")
    assert theme.manager() is m and m.current == "dark"
    m.apply("classic")


def test_manager_before_install_is_classic_and_touches_nothing(qapp, monkeypatch):
    monkeypatch.setattr(theme, "_manager", None, raising=False)  # the module-level singleton slot
    sheet_before = qapp.styleSheet()
    m = theme.manager()
    assert m.is_classic() and qapp.styleSheet() == sheet_before
    assert theme.manager() is m


def test_apply_rejects_unknown_theme(mgr):
    with pytest.raises(ValueError):
        mgr.apply("blue")


def test_live_switch_repaints_viewports_with_the_new_palette(qapp, qtbot, mgr):
    from qtpy.QtWidgets import QLabel, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget

    # Children of a shown window, as in the GUI: a top-level widget alone does not show the stale palette.
    window = QWidget()
    qtbot.addWidget(window)
    layout = QVBoxLayout(window)
    edit = QPlainTextEdit("log line")
    area = QScrollArea()
    area.setWidget(QLabel("child"))
    layout.addWidget(edit)
    layout.addWidget(area)
    window.show()
    qtbot.waitExposed(window)

    mgr.apply("dark")
    assert edit.viewport().palette().color(QPalette.Base) == QColor(theme.DARK.input_bg)
    assert area.viewport().palette().color(QPalette.Window) == QColor(theme.DARK.ground)

    mgr.apply("classic")
    mgr.apply("dark")
    assert edit.viewport().palette().color(QPalette.Base) == QColor(theme.DARK.input_bg)
    assert area.viewport().palette().color(QPalette.Window) == QColor(theme.DARK.ground)


def test_dock_theme_on_an_unfamiliar_pyqtgraph_does_not_raise(qapp, monkeypatch):
    dockmod = importlib.import_module("pyqtgraph.dockarea.Dock")
    monkeypatch.setattr(theme, "_dock_originals", {})
    monkeypatch.delattr(dockmod.DockLabel, "updateStyle")  # a pyqtgraph without the method the patch wraps
    theme._install_dock_theme(theme.DARK)
    assert "updateStyle" not in vars(dockmod.DockLabel)  # left as pyqtgraph has it: unpatched


def test_failed_apply_leaves_the_current_theme_and_classic_still_works(qapp, mgr, monkeypatch):
    mgr.apply("dark")

    def boom(*args, **kwargs):
        raise RuntimeError("icons failed")

    monkeypatch.setattr(theme, "write_theme_icons", boom)
    with pytest.raises(RuntimeError):
        mgr.apply("dark")
    assert mgr.current == "dark"
    assert qapp.palette().color(QPalette.Window) == QColor(theme.DARK.ground)
    mgr.apply("classic")
    assert mgr.is_classic() and qapp.styleSheet() == ""


def test_classic_on_a_pristine_app_touches_nothing(qapp, monkeypatch):
    calls = []
    font, palette, sheet = qapp.font(), qapp.palette(), qapp.styleSheet()
    monkeypatch.setattr(qapp, "setFont", lambda *a: calls.append("font"))
    monkeypatch.setattr(qapp, "setPalette", lambda *a: calls.append("palette"))
    monkeypatch.setattr(qapp, "setStyleSheet", lambda *a: calls.append("sheet"))
    m = theme.ThemeManager(qapp)
    m.apply("classic")
    assert calls == []
    assert qapp.font() == font and qapp.palette() == palette and qapp.styleSheet() == sheet
    assert m.is_classic()


def test_themed_dock_style_falls_back_to_pyqtgraphs_own():
    calls = []

    def themed(self):
        raise AttributeError("dim")  # a pyqtgraph the patch does not fit

    patched = theme._guarded(themed, lambda self: calls.append(self))
    patched("dock")
    assert calls == ["dock"]
