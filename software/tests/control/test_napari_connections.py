"""Unit tests for HighContentScreeningGui.updateNapariConnections.

Runs the method against a minimal stand-in object instead of the full GUI so the
tests can assert exact connection counts: toggling performance mode back and
forth must never stack duplicate connections on the widgets that stay connected
(plain ``signal.connect`` silently adds a second connection; it does not raise).
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

    def __init__(self, emitter, mosaic, multichannel):
        self.performance_mode = False
        self.napariLiveWidget = None
        self.unifiedMosaicWidget = mosaic
        self.napariMultiChannelWidget = multichannel
        self.napari_connections = {
            "napariLiveWidget": [],
            "napariMultiChannelWidget": [(emitter.sig, multichannel.slot)],
            "unifiedMosaicWidget": [
                (emitter.sig, mosaic.slot),
                (emitter.queued_sig, mosaic.queued_slot, Qt.QueuedConnection),
            ],
        }

    def update(self, performance_mode):
        self.performance_mode = performance_mode
        HighContentScreeningGui.updateNapariConnections(self)


def _make():
    emitter, mosaic, multichannel = _Emitter(), _Receiver(), _Receiver()
    return emitter, mosaic, multichannel, _GuiStub(emitter, mosaic, multichannel)


def test_toggling_performance_mode_keeps_exactly_one_mosaic_connection(qtbot):
    emitter, mosaic, _multichannel, gui = _make()

    gui.update(performance_mode=False)  # initial wiring (makeNapariConnections)
    for mode in (True, False, True):  # user toggles performance mode a few times
        gui.update(performance_mode=mode)

    emitter.sig.emit(1)
    emitter.queued_sig.emit(1)
    qtbot.waitUntil(lambda: mosaic.queued_calls >= 1, timeout=1000)
    qtbot.wait(20)  # let any stacked queued deliveries land before counting

    assert mosaic.calls == 1, "direct-connection slot must fire once per emit"
    assert mosaic.queued_calls == 1, "queued-connection slot must fire once per emit"
    # In performance mode only the mosaic stays on `sig` (multichannel is disconnected).
    assert emitter.receivers(emitter.sig) == 1


def test_multichannel_disconnects_in_performance_mode_and_reconnects_once(qtbot):
    emitter, _mosaic, multichannel, gui = _make()

    gui.update(performance_mode=False)
    gui.update(performance_mode=True)
    emitter.sig.emit(1)
    assert multichannel.calls == 0, "multichannel must be disconnected in performance mode"

    gui.update(performance_mode=False)
    gui.update(performance_mode=False)  # a redundant refresh must not double-connect
    emitter.sig.emit(1)
    assert multichannel.calls == 1
    assert emitter.receivers(emitter.sig) == 2  # mosaic + multichannel, once each
