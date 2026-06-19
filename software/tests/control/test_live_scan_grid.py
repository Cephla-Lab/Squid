"""Regression tests for the "live scan grid" (orange planned-coordinate overlay).

Bug: during an acquisition the stage steps through the planned FOVs. The live scan
grid is a *positioning preview* that re-centers on the current stage position
(`WellplateMultiPointWidget.update_live_coordinates` -> `set_live_scan_coordinates`).
It is wired to `MovementUpdater.position_after_move` only while the user is manually
navigating, and is supposed to be disconnected for the duration of an acquisition
(`HighContentScreeningGui.toggleAcquisitionStart`).

Two defects let the orange grid follow the stage during acquisition:
  1. `toggle_live_scan_grid(on=True)` connected the signal unconditionally. Because
     PyQt removes only one connection per `disconnect()`, any double-enable left a
     dangling connection that the single acquisition-start disconnect could not clear.
  2. `update_live_coordinates` did not check whether an acquisition was running, so
     a surviving connection would redraw the grid on every stage move.

These tests call the real methods with lightweight stand-ins for `self` so they
exercise the exact fixed code without constructing the full Qt GUI (which spawns
background threads that segfault at interpreter teardown in CI).
"""

import types

import control._def  # noqa: F401 - ensures config is loaded (sys.exit otherwise)
import control.gui_hcs
import control.widgets
import squid.abc
from qtpy.QtCore import QObject, Signal


def _pos(x_mm, y_mm):
    return squid.abc.Pos(x_mm=x_mm, y_mm=y_mm, z_mm=0.0, theta_rad=None)


class _Mover(QObject):
    position_after_move = Signal(object)


class _CountingWidget:
    """Stand-in for wellplateMultiPointWidget that records each slot invocation."""

    def __init__(self):
        self.calls = []

    def update_live_coordinates(self, pos):
        self.calls.append(pos)


def test_toggle_live_scan_grid_is_idempotent(qtbot):
    # qtbot ensures a QApplication exists so the Qt signal machinery works; no GUI is built.
    gui = types.SimpleNamespace(
        movement_updater=_Mover(),
        wellplateMultiPointWidget=_CountingWidget(),
        is_live_scan_grid_on=False,
    )
    toggle = control.gui_hcs.HighContentScreeningGui.toggle_live_scan_grid

    def live_connections():
        gui.wellplateMultiPointWidget.calls.clear()
        gui.movement_updater.position_after_move.emit(_pos(1.0, 2.0))
        return len(gui.wellplateMultiPointWidget.calls)

    # A redundant enable must not create a duplicate connection ...
    toggle(gui, on=True)
    toggle(gui, on=True)
    assert live_connections() == 1, "redundant enable must not create duplicate connections"

    # ... and a single disable (what acquisition-start does) must fully disconnect.
    toggle(gui, on=False)
    assert live_connections() == 0, "live grid still connected after disable (dangling connection)"


class _SpyScanCoordinates:
    def __init__(self):
        self.calls = []

    def set_live_scan_coordinates(self, *args, **kwargs):
        self.calls.append(args)


def _wellplate_stub(acquiring, spy):
    """Minimal stand-in carrying only what update_live_coordinates reads before redrawing."""
    return types.SimpleNamespace(
        tab_widget=None,
        multipointController=types.SimpleNamespace(acquisition_in_progress=lambda: acquiring),
        focusMapWidget=None,
        checkbox_xy=types.SimpleNamespace(isChecked=lambda: True),
        _last_update_time=0,
        _last_x_mm=None,
        _last_y_mm=None,
        entry_scan_size=types.SimpleNamespace(value=lambda: 1.0),
        entry_overlap=types.SimpleNamespace(value=lambda: 10.0),
        combobox_shape=types.SimpleNamespace(currentText=lambda: "Square"),
        scanCoordinates=spy,
    )


def test_live_scan_grid_does_not_follow_stage_during_acquisition():
    update = control.widgets.WellplateMultiPointWidget.update_live_coordinates

    # Sanity: while NOT acquiring, a stage move updates the live grid (the intended preview).
    spy = _SpyScanCoordinates()
    update(_wellplate_stub(acquiring=False, spy=spy), _pos(10.0, 20.0))
    assert len(spy.calls) == 1, "live grid should follow the stage during manual navigation"

    # Bug condition: while acquiring, a stage move must NOT redraw the planned grid.
    spy = _SpyScanCoordinates()
    update(_wellplate_stub(acquiring=True, spy=spy), _pos(30.0, 40.0))
    assert spy.calls == [], "live scan grid must not follow the stage during acquisition"
