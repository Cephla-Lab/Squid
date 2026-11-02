"""NapariMultiChannelWidget must keep up with large z-stacks.

Regression: updateLayers refreshed every layer (and, in 3D, max-projected the whole
(Nz, H, W) canvas several times) for every frame. On a 378-plane 3200x3200 stack that took
~1.5 s per frame against a ~0.3 s acquisition period, so frames queued up in the GUI thread
and the window froze for minutes after the acquisition had finished.
"""

import time

import napari.layers
import numpy as np
import pytest

import control.microscope
from control import widgets

NZ = 300
DZ_UM = 3.0
CHANNEL = "20x Fluorescence 488 nm Ex"


@pytest.fixture
def thumbnail_calls(monkeypatch):
    """Count napari thumbnail recomputes: each one is a pass over the displayed data
    (in 3D, a max projection over the whole z-stack)."""
    calls = []
    original = napari.layers.Image._update_thumbnail

    def counting(self):
        calls.append(self.name)
        original(self)

    monkeypatch.setattr(napari.layers.Image, "_update_thumbnail", counting)
    return calls


@pytest.fixture
def refresh_calls(monkeypatch):
    calls = []
    original = widgets.NapariMultiChannelWidget._refreshDisplay

    def counting(self):
        calls.append(time.monotonic())
        original(self)

    # Patched on the class before construction, so the refresh timer connects to it.
    monkeypatch.setattr(widgets.NapariMultiChannelWidget, "_refreshDisplay", counting)
    return calls


@pytest.fixture
def napari_widget(qtbot, refresh_calls):
    scope = control.microscope.Microscope.build_from_global_config(True)
    widget = widgets.NapariMultiChannelWidget(scope.objective_store, scope.camera, scope.contrast_manager)
    qtbot.addWidget(widget)
    yield widget
    # Qt faults dispatching a still-pending refresh to the refresh_calls wrapper once that
    # fixture has been torn down and qtbot deletes the widget (the unpatched slot is fine).
    widget._refresh_timer.stop()
    if hasattr(widget.viewer.window, "_qt_window"):
        widget.viewer.close()
    scope.close()


def _start_acquisition(widget, nz=NZ, height=64, width=64):
    widget.initChannels([CHANNEL])
    widget.initLayersShape(nz, DZ_UM)
    widget.initLayers(height, width, np.uint16)


def _frame(k, height=64, width=64):
    return np.full((height, width), k + 1, dtype=np.uint16)


def test_acquisition_displays_2d_and_restores_3d(napari_widget):
    napari_widget.viewer.dims.ndisplay = 3

    _start_acquisition(napari_widget)
    assert napari_widget.viewer.dims.ndisplay == 2
    for k in range(5):
        napari_widget.updateLayers(_frame(k), 0.0, 0.0, k, CHANNEL)
    assert napari_widget.viewer.dims.ndisplay == 2

    napari_widget.finishAcquisition()
    assert napari_widget.viewer.dims.ndisplay == 3

    # The next acquisition switches to 2D again and restores 3D again.
    _start_acquisition(napari_widget)
    assert napari_widget.viewer.dims.ndisplay == 2
    napari_widget.finishAcquisition()
    assert napari_widget.viewer.dims.ndisplay == 3


def test_acquisition_started_in_2d_ends_in_2d(napari_widget):
    _start_acquisition(napari_widget)
    napari_widget.updateLayers(_frame(0), 0.0, 0.0, 0, CHANNEL)
    napari_widget.finishAcquisition()
    assert napari_widget.viewer.dims.ndisplay == 2


@pytest.mark.parametrize(
    "start, switches, end",
    [
        (2, [3], 3),  # switched to 3D mid-run: stays 3D, not back to the 2D from before the run
        (3, [3, 2], 2),  # started in 3D, last chose 2D mid-run: stays 2D
    ],
)
def test_mid_acquisition_ndisplay_choice_is_kept(napari_widget, start, switches, end):
    napari_widget.viewer.dims.ndisplay = start
    _start_acquisition(napari_widget)
    napari_widget.updateLayers(_frame(0), 0.0, 0.0, 0, CHANNEL)
    for ndisplay in switches:
        napari_widget.viewer.dims.ndisplay = ndisplay
    napari_widget.updateLayers(_frame(1), 0.0, 0.0, 1, CHANNEL)

    napari_widget.finishAcquisition()
    assert napari_widget.viewer.dims.ndisplay == end

    # The choice doesn't leak into the next run: it starts from the mode the user ended in.
    _start_acquisition(napari_widget)
    assert napari_widget.viewer.dims.ndisplay == 2
    napari_widget.finishAcquisition()
    assert napari_widget.viewer.dims.ndisplay == end


def test_frames_do_not_refresh_the_viewer_synchronously(napari_widget, thumbnail_calls, refresh_calls, qtbot):
    napari_widget.viewer.dims.ndisplay = 3
    _start_acquisition(napari_widget)
    napari_widget.updateLayers(_frame(0), 0.0, 0.0, 0, CHANNEL)  # creates the layer
    thumbnail_calls.clear()

    # Frames queued faster than the GUI draws them: none of them may cost a viewer refresh.
    # (Before the fix every frame recomputed the thumbnail ~3 times.)
    for k in range(1, NZ):
        napari_widget.updateLayers(_frame(k), 0.0, 0.0, k, CHANNEL)
    assert len(thumbnail_calls) == 0

    # Once the event loop runs, one coalesced refresh shows the latest plane.
    qtbot.waitUntil(lambda: napari_widget.viewer.dims.point[0] == pytest.approx((NZ - 1) * DZ_UM), timeout=5000)
    assert len(refresh_calls) == 1
    layer = napari_widget.viewer.layers[CHANNEL]
    assert np.all(layer.data[NZ - 1] == NZ)


def test_refresh_rate_is_bounded_while_frames_stream(napari_widget, refresh_calls, qtbot):
    _start_acquisition(napari_widget)
    start = time.monotonic()
    for k in range(100):
        napari_widget.updateLayers(_frame(k), 0.0, 0.0, k, CHANNEL)
        qtbot.wait(1)  # deliver the next queued frame through the event loop, as Qt would
    elapsed = time.monotonic() - start

    allowed = elapsed / widgets.NapariMultiChannelWidget.MIN_REFRESH_INTERVAL_S + 2
    assert 1 <= len(refresh_calls) <= allowed, f"{len(refresh_calls)} refreshes in {elapsed:.2f} s"
    gaps = np.diff(refresh_calls)
    assert np.all(gaps >= 0.9 * widgets.NapariMultiChannelWidget.MIN_REFRESH_INTERVAL_S)


def test_finish_shows_the_last_plane_immediately(napari_widget):
    _start_acquisition(napari_widget)
    for k in range(NZ):
        napari_widget.updateLayers(_frame(k), 0.0, 0.0, k, CHANNEL)

    napari_widget.finishAcquisition()  # no event loop needed

    assert napari_widget.viewer.dims.point[0] == pytest.approx((NZ - 1) * DZ_UM)
    assert not napari_widget._refresh_timer.isActive()


def test_pending_refresh_after_viewer_close_is_harmless(napari_widget, qtbot):
    _start_acquisition(napari_widget)
    napari_widget.updateLayers(_frame(0), 0.0, 0.0, 0, CHANNEL)
    napari_widget.updateLayers(_frame(1), 0.0, 0.0, 1, CHANNEL)
    assert napari_widget._refresh_timer.isActive()

    # GUI shutdown closes the viewer directly, possibly with a refresh still pending and
    # acquisition_finished still queued behind it.
    napari_widget.viewer.close()
    qtbot.waitUntil(lambda: not napari_widget._refresh_timer.isActive(), timeout=5000)
    napari_widget.finishAcquisition()
