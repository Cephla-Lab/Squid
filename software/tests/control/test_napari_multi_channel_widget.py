"""Napari multi-channel display under a dual-camera (mixed dtype) acquisition.

A mono camera delivers MONO16 (uint16, HxW) and a colour camera RGB24 (uint8, HxWx3), so a
multipoint run mixing cameras interleaves the two. The display must keep one layer per
channel across those switches. Rebuilding the whole LayerList on every switch cost ~750ms of
GUI-thread time per FOV (vs ~11ms), which saturates the event loop and makes Windows report
the window as "Not Responding"; closing it during a stall kills the app with no traceback.

These tests drive updateLayers against a stand-in viewer rather than a real napari one: the
behaviour under test is the widget's own decision about when to rebuild a layer, and
constructing a napari Viewer pulls in vispy, which refuses to initialise in a process where
another Qt binding is already loaded (the reason CI runs the GUI test in its own process).
"""

from unittest.mock import MagicMock

import numpy as np
import pytest

from control.core.contrast_manager import ContrastManager

MONO_CHANNEL = "Fluorescence 488 nm Ex"
COLOR_CHANNEL = "BF LED matrix full"


class FakeLayer:
    def __init__(self, data, name, rgb, contrast_limits=(0, 1), scale=(1, 1, 1)):
        self.data = data
        self.name = name
        self.rgb = rgb
        self.contrast_limits = contrast_limits
        self.scale = scale
        self.events = MagicMock()
        self.refresh_count = 0

    def refresh(self):
        self.refresh_count += 1


class FakeLayerList:
    """Enough of napari's LayerList for updateLayers, plus bookkeeping for the assertions."""

    def __init__(self):
        self._layers = {}
        self.clear_calls = 0
        self.add_calls = 0
        self.remove_calls = 0

    def __contains__(self, name):
        return name in self._layers

    def __getitem__(self, name):
        return self._layers[name]

    def __iter__(self):
        return iter(list(self._layers.values()))

    def __len__(self):
        return len(self._layers)

    def clear(self):
        self.clear_calls += 1
        self._layers.clear()

    def remove(self, layer):
        self.remove_calls += 1
        del self._layers[layer.name]

    def names(self):
        return sorted(self._layers)


class FakeViewer:
    def __init__(self):
        self.layers = FakeLayerList()
        self.dims = MagicMock()

    def add_image(self, data, name, visible, rgb, colormap, contrast_limits, blending, scale):
        layer = FakeLayer(data, name, rgb, contrast_limits=contrast_limits, scale=scale)
        self.layers._layers[name] = layer
        self.layers.add_calls += 1
        return layer


@pytest.fixture
def widget():
    # Built without __init__ so no napari Viewer (and therefore no vispy/Qt backend) is
    # needed; every attribute updateLayers touches is set explicitly below.
    from control.widgets import NapariMultiChannelWidget

    w = NapariMultiChannelWidget.__new__(NapariMultiChannelWidget)
    w.objectiveStore = MagicMock()
    w.camera = MagicMock()
    w.contrastManager = ContrastManager()
    w.viewer = FakeViewer()
    w.dtype = np.uint8
    w.channels = set()
    w.pixel_size_um = 1
    w.dz_um = 1
    w.Nz = 1
    w.layers_initialized = False
    w.acquisition_initialized = False
    w.viewer_scale_initialized = True  # skip resetView, which touches the real viewer
    w.update_layer_count = 0
    w.grid_enabled = False
    return w


def mono_frame(size=8):
    return np.full((size, size), 1000, dtype=np.uint16)


def color_frame(size=8):
    return np.full((size, size, 3), 40, dtype=np.uint8)


def start_acquisition(widget, dtype=np.uint16, size=8):
    """Init the canvas the way the acquisition-start signal does, then zero the churn
    counters: initLayers legitimately clears the LayerList once, and what these tests care
    about is churn *after* that point."""
    widget.initLayers(size, size, dtype)
    widget.viewer.layers.clear_calls = 0
    widget.viewer.layers.add_calls = 0
    widget.viewer.layers.remove_calls = 0


def run_fovs(widget, n):
    """n FOVs in the order the worker emits them: colour BF, then a mono channel."""
    for _ in range(n):
        widget.updateLayers(color_frame(), x=0.0, y=0.0, k=0, channel_name=COLOR_CHANNEL)
        widget.updateLayers(mono_frame(), x=0.0, y=0.0, k=0, channel_name=MONO_CHANNEL)


def test_mixed_dtype_channels_keep_their_own_layers(widget):
    start_acquisition(widget)
    run_fovs(widget, 3)

    assert widget.viewer.layers.names() == sorted([MONO_CHANNEL, COLOR_CHANNEL]), (
        "both channels must still have a layer; a dtype switch used to clear the whole "
        "LayerList, leaving only the channel whose frame arrived most recently"
    )

    mono_layer = widget.viewer.layers[MONO_CHANNEL]
    color_layer = widget.viewer.layers[COLOR_CHANNEL]
    # Each layer keeps the geometry of the camera feeding it, not that of whichever camera
    # sent the acquisition's first frame.
    assert (mono_layer.data.dtype, mono_layer.data.shape) == (np.uint16, (1, 8, 8))
    assert (color_layer.data.dtype, color_layer.data.shape) == (np.uint8, (1, 8, 8, 3))
    # ...and the frames landed without being cast to the other camera's dtype.
    assert mono_layer.data[0].max() == 1000
    assert color_layer.data[0].max() == 40


def test_steady_state_does_no_layer_churn(widget):
    """The GUI-thread cost of the bug was the churn itself: one clear + N re-adds per switch."""
    start_acquisition(widget)
    run_fovs(widget, 1)  # first FOV legitimately creates both layers

    adds_after_first_fov = widget.viewer.layers.add_calls
    assert adds_after_first_fov == 2

    run_fovs(widget, 8)

    assert widget.viewer.layers.add_calls == adds_after_first_fov, "layers were re-added"
    assert widget.viewer.layers.remove_calls == 0, "layers were removed"
    assert widget.viewer.layers.clear_calls == 0, "the LayerList was cleared mid-acquisition"


def test_single_camera_run_is_unchanged(widget):
    """One dtype throughout - the common case must behave exactly as before."""
    start_acquisition(widget)
    for _ in range(4):
        widget.updateLayers(mono_frame(), x=0.0, y=0.0, k=0, channel_name=MONO_CHANNEL)
        widget.updateLayers(mono_frame(), x=0.0, y=0.0, k=0, channel_name="Fluorescence 561 nm Ex")

    assert len(widget.viewer.layers) == 2
    assert widget.viewer.layers.clear_calls == 0
    assert widget.viewer.layers.add_calls == 2
    for layer in widget.viewer.layers:
        assert (layer.data.dtype, layer.data.shape) == (np.uint16, (1, 8, 8))


def test_each_layer_gets_contrast_limits_for_its_own_dtype(widget):
    """ContrastManager tracks one run-wide acquisition_dtype, so its default limits describe
    whichever camera arrived first. A uint8 RGB layer handed uint16 limits renders black (and
    a uint16 layer handed uint8 limits renders saturated white), so each layer's defaults
    must come from its own dtype. The old teardown hid this by re-running initLayers, which
    rescaled the limits before re-adding every layer."""
    # start_acquisition announces uint16, so ContrastManager.acquisition_dtype latches to
    # uint16 exactly as it does on a real run whose first frame is the mono camera's.
    start_acquisition(widget)
    run_fovs(widget, 2)

    assert widget.viewer.layers[MONO_CHANNEL].contrast_limits == (0, 65535)
    assert widget.viewer.layers[COLOR_CHANNEL].contrast_limits == (0, 255)


def test_user_set_contrast_limits_still_win(widget):
    """A limit the user dragged in napari must survive; only the default comes from dtype."""
    start_acquisition(widget)
    run_fovs(widget, 1)
    widget.contrastManager.update_limits(COLOR_CHANNEL, 10, 200)

    run_fovs(widget, 1)

    assert widget.viewer.layers[COLOR_CHANNEL].contrast_limits == (10, 200)


def test_each_layer_is_scaled_by_its_own_cameras_pixel_size(widget):
    """Layers fed by cameras with different pixel pitch must still overlay, so the scale
    comes from the pixel size passed with each frame - not from the widget-wide value, which
    is computed once from whichever camera was active at acquisition start."""
    start_acquisition(widget)
    widget.pixel_size_um = 999.0  # the wrong-sensor fallback; must not be used

    widget.updateLayers(color_frame(), x=0.0, y=0.0, k=0, channel_name=COLOR_CHANNEL, pixel_size_um=1.85)
    widget.updateLayers(mono_frame(), x=0.0, y=0.0, k=0, channel_name=MONO_CHANNEL, pixel_size_um=3.45)

    assert tuple(widget.viewer.layers[COLOR_CHANNEL].scale)[1:] == (1.85, 1.85)
    assert tuple(widget.viewer.layers[MONO_CHANNEL].scale)[1:] == (3.45, 3.45)


def test_pixel_size_falls_back_to_the_widget_value(widget):
    """Callers that pass no pixel size (older signal payloads, single-camera paths) keep the
    previous behaviour."""
    start_acquisition(widget)
    widget.pixel_size_um = 2.5

    widget.updateLayers(mono_frame(), x=0.0, y=0.0, k=0, channel_name=MONO_CHANNEL)

    assert tuple(widget.viewer.layers[MONO_CHANNEL].scale)[1:] == (2.5, 2.5)


def test_geometry_change_on_one_channel_rebuilds_only_that_layer(widget):
    """A channel whose frame geometry changes (e.g. its camera was re-binned) is rebuilt;
    the other channel's layer survives."""
    start_acquisition(widget)
    run_fovs(widget, 1)
    survivor = widget.viewer.layers[COLOR_CHANNEL]

    widget.updateLayers(mono_frame(size=16), x=0.0, y=0.0, k=0, channel_name=MONO_CHANNEL)

    assert widget.viewer.layers[MONO_CHANNEL].data.shape == (1, 16, 16)
    assert widget.viewer.layers[COLOR_CHANNEL] is survivor
    assert survivor.data.shape == (1, 8, 8, 3)
    assert widget.viewer.layers.clear_calls == 0
