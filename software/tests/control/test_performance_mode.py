import pytest

import control.gui_hcs
import control.microscope
from qtpy.QtWidgets import QMessageBox


@pytest.fixture
def confirm_exit_yes(monkeypatch):
    """Auto-accept the 'Confirm Exit' dialog GUI shutdown shows, or teardown hangs forever."""

    def confirm_exit(parent, title, text, *args, **kwargs):
        if title == "Confirm Exit":
            return QMessageBox.Yes
        raise RuntimeError(f"Unexpected QMessageBox: {title} - {text}")

    monkeypatch.setattr(QMessageBox, "question", confirm_exit)


def test_performance_mode_defers_mosaic_and_renders_at_completion(qtbot, confirm_exit_yes, monkeypatch):
    """Performance mode keeps the mosaic's data feed connected (so its canvas still
    builds during acquisition) but holds its tab hidden/disabled so rendering is
    deferred; on completion the mosaic tab is re-enabled and shown so the single
    deferred refresh flushes.

    Lives in its own module because it forces the mosaic napari.Viewer on; grouping
    it with the other full-GUI tests accumulates enough napari/vispy viewers to hit
    the known STATUS_HEAP_CORRUPTION on OpenGL teardown at process exit (documented;
    run napari-heavy GUI test files separately). CI also runs this file in its own
    pytest process (main.yml): destroying the full GUI segfaults, and the deferred
    delete pytest-qt posts for the window at teardown would otherwise be flushed
    inside a later test's event loop. See gui_hcs.updateNapariConnections and
    gui_hcs.toggleAcquisitionStart.
    """
    # gui_hcs star-imports _def; force the mosaic view on before construction so the
    # widget exists regardless of this machine's cached [VIEWS] config.
    monkeypatch.setattr(control.gui_hcs, "USE_NAPARI_FOR_MOSAIC_DISPLAY", True)

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    mosaic = win.unifiedMosaicWidget
    assert mosaic is not None, "mosaic widget should be created when USE_NAPARI_FOR_MOSAIC_DISPLAY is on"
    mosaic_idx = win.imageDisplayTabs.indexOf(mosaic)

    def mosaic_feed_connected():
        """True if the controller's mosaic_tile_update is still wired to updateTile.
        Probes by disconnect (raises TypeError if not connected) then restores."""
        try:
            win.multipointController.mosaic_tile_update.disconnect(mosaic.updateTile)
        except TypeError:
            return False
        win.multipointController.mosaic_tile_update.connect(mosaic.updateTile)
        return True

    assert mosaic_feed_connected(), "mosaic feed should be connected in normal mode"

    # Enter performance mode via the toggle-button path.
    win.performanceModeToggle.setChecked(True)
    win.togglePerformanceMode()
    assert win.performance_mode is True

    # Key change: the mosaic's data feed stays CONNECTED in performance mode (so its
    # canvas still builds during acquisition; rendering is what gets deferred).
    assert mosaic_feed_connected(), "mosaic feed must remain connected in performance mode"

    # Start of run: the mosaic tab is hidden/disabled so rendering defers (no per-tile GL).
    win.toggleAcquisitionStart(True)
    assert win.imageDisplayTabs.isTabEnabled(mosaic_idx) is False

    # Completion: the mosaic tab is re-enabled and made current so its showEvent
    # flushes the single deferred render.
    win.toggleAcquisitionStart(False)
    qtbot.wait(20)
    assert win.imageDisplayTabs.isTabEnabled(mosaic_idx) is True
    assert win.imageDisplayTabs.currentWidget() is mosaic
