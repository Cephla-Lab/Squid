"""Unit tests for HighContentScreeningGui.updateNapariConnections.

Runs the method against a minimal stand-in object instead of the full GUI so the
tests can assert exact connection counts: toggling performance mode back and
forth must never stack duplicate connections (plain ``signal.connect`` silently
adds a second connection; it does not raise).
"""

from qtpy.QtCore import QObject, Qt, Signal

from control.gui_hcs import HighContentScreeningGui


class _Emitter(QObject):
    sig = Signal(int)
    queued_sig = Signal(int)


class _Receiver(QObject):
    def __init__(self):
        super().__init__()
        self.calls = 0
        self.queued_calls = 0

    def slot(self, _value):
        self.calls += 1

    def queued_slot(self, _value):
        self.queued_calls += 1


class _GuiStub:
    """Only the attributes updateNapariConnections reads."""

    def __init__(self, emitter, multichannel):
        self.performance_mode = False
        self.napari_connections = [
            (emitter.sig, multichannel.slot),
            (emitter.queued_sig, multichannel.queued_slot, Qt.QueuedConnection),
        ]

    def update(self, performance_mode):
        self.performance_mode = performance_mode
        HighContentScreeningGui.updateNapariConnections(self)


def _make():
    emitter, multichannel = _Emitter(), _Receiver()
    return emitter, multichannel, _GuiStub(emitter, multichannel)


def test_repeated_refreshes_keep_exactly_one_connection(qtbot):
    emitter, multichannel, gui = _make()

    gui.update(performance_mode=False)  # initial wiring (makeNapariConnections)
    gui.update(performance_mode=False)  # a redundant refresh must not double-connect
    for mode in (True, False, True, False):  # user toggles performance mode a few times
        gui.update(performance_mode=mode)

    emitter.sig.emit(1)
    emitter.queued_sig.emit(1)
    qtbot.waitUntil(lambda: multichannel.queued_calls >= 1, timeout=1000)
    qtbot.wait(20)  # let any stacked queued deliveries land before counting

    assert multichannel.calls == 1, "direct-connection slot must fire once per emit"
    assert multichannel.queued_calls == 1, "queued-connection slot must fire once per emit"
    assert emitter.receivers(emitter.sig) == 1
    assert emitter.receivers(emitter.queued_sig) == 1


def test_performance_mode_disconnects_and_leaving_it_reconnects_once(qtbot):
    emitter, multichannel, gui = _make()

    gui.update(performance_mode=False)
    gui.update(performance_mode=True)
    gui.update(performance_mode=True)  # a redundant refresh must not raise
    emitter.sig.emit(1)
    assert multichannel.calls == 0, "multichannel must be disconnected in performance mode"
    assert emitter.receivers(emitter.sig) == 0

    gui.update(performance_mode=False)
    emitter.sig.emit(1)
    assert multichannel.calls == 1
    assert emitter.receivers(emitter.sig) == 1
