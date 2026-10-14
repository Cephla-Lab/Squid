"""Unit tests for HighContentScreeningGui.updateNapariConnections.

Runs the method against a stand-in object instead of the full GUI so the tests can
assert exact receiver counts: toggling performance mode back and forth must never stack
duplicate connections (plain ``signal.connect`` silently adds a second one; it does not raise).
"""

from types import SimpleNamespace

from qtpy.QtCore import QObject, Qt, Signal

from control.gui_hcs import HighContentScreeningGui


class _Emitter(QObject):
    sig = Signal(int)
    queued_sig = Signal(int)


class _View(QObject):
    def slot(self, _value):
        pass

    def queued_slot(self, _value):
        pass


def _make():
    emitter, view = _Emitter(), _View()
    gui = SimpleNamespace(
        performance_mode=False,
        napari_connections=[
            (emitter.sig, view.slot),
            (emitter.queued_sig, view.queued_slot, Qt.QueuedConnection),
        ],
    )
    return emitter, view, gui


def _update(gui, performance_mode):
    gui.performance_mode = performance_mode
    HighContentScreeningGui.updateNapariConnections(gui)


def test_repeated_refreshes_keep_exactly_one_connection(qtbot):
    emitter, _view, gui = _make()

    _update(gui, performance_mode=False)  # initial wiring (makeNapariConnections)
    _update(gui, performance_mode=False)  # a redundant refresh must not double-connect
    for mode in (True, False, True, False):  # the user toggles performance mode a few times
        _update(gui, performance_mode=mode)

    assert emitter.receivers(emitter.sig) == 1
    assert emitter.receivers(emitter.queued_sig) == 1


def test_performance_mode_disconnects_and_leaving_it_reconnects_once(qtbot):
    emitter, _view, gui = _make()

    _update(gui, performance_mode=False)
    _update(gui, performance_mode=True)
    _update(gui, performance_mode=True)  # a redundant refresh must not raise
    assert emitter.receivers(emitter.sig) == 0
    assert emitter.receivers(emitter.queued_sig) == 0

    _update(gui, performance_mode=False)
    assert emitter.receivers(emitter.sig) == 1
    assert emitter.receivers(emitter.queued_sig) == 1
