from types import SimpleNamespace

import numpy as np

from squid.ui.widgets.display.napari_multichannel import NapariMultiChannelWidget


class _Signal:
    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)


class _Layer:
    def __init__(self, name: str, data: np.ndarray, rgb: bool) -> None:
        self.name = name
        self.data = np.array(data, copy=True)
        self.rgb = rgb
        self.contrast_limits = (0.0, 1.0)
        self.events = SimpleNamespace(contrast_limits=_Signal())
        self.refresh_count = 0

    def refresh(self) -> None:
        self.refresh_count += 1


class _LayerList:
    def __init__(self) -> None:
        self._layers: dict[str, _Layer] = {}

    def __contains__(self, name: str) -> bool:
        return name in self._layers

    def __getitem__(self, name: str) -> _Layer:
        return self._layers[name]

    def __iter__(self):
        return iter(self._layers.values())

    def add(self, layer: _Layer) -> None:
        self._layers[layer.name] = layer

    def remove(self, layer: _Layer) -> None:
        self._layers.pop(layer.name, None)

    def clear(self) -> None:
        self._layers.clear()


class _Viewer:
    def __init__(self) -> None:
        self.layers = _LayerList()
        self.dims = SimpleNamespace(
            set_point=lambda axis, value: setattr(self, "_last_dim_point", (axis, value))
        )
        self.reset_view_calls = 0

    def add_image(
        self,
        data: np.ndarray,
        name: str,
        visible: bool,
        rgb: bool,
        colormap,
        contrast_limits,
        blending: str,
        scale,
    ) -> _Layer:
        layer = _Layer(name=name, data=data, rgb=rgb)
        self.layers.add(layer)
        return layer

    def reset_view(self) -> None:
        self.reset_view_calls += 1


class _ContrastManager:
    def get_limits(self, _channel_name: str):
        return (0.0, 255.0)

    def get_default_limits(self):
        return (0.0, 255.0)

    def scale_contrast_limits(self, _dtype) -> None:
        pass


def _make_widget() -> NapariMultiChannelWidget:
    widget = NapariMultiChannelWidget.__new__(NapariMultiChannelWidget)
    widget.contrastManager = _ContrastManager()
    widget.image_width = 0
    widget.image_height = 0
    widget.dtype = np.dtype(np.uint16)
    widget.channels = set()
    widget.pixel_size_um = 1.0
    widget.dz_um = 1.0
    widget.Nz = 1
    widget.layers_initialized = True
    widget.acquisition_initialized = True
    widget.viewer_scale_initialized = True
    widget.update_layer_count = 0
    widget.viewer = _Viewer()
    return widget


def test_update_layers_reinitializes_when_image_shape_changes():
    widget = _make_widget()
    channel = "10x BF LED matrix full"

    widget.image_height = 1032
    widget.image_width = 1500
    widget.channels = {channel}
    widget.viewer.add_image(
        np.zeros((1, 1032, 1500), dtype=np.uint16),
        name=channel,
        visible=True,
        rgb=False,
        colormap="gray",
        contrast_limits=(0.0, 1.0),
        blending="additive",
        scale=(1.0, 1.0, 1.0),
    )

    image = np.full((2064, 3000), 7, dtype=np.uint16)
    widget.updateLayers(image, 0, 0, 0, channel)

    layer = widget.viewer.layers[channel]
    assert layer.data.shape == (1, 2064, 3000)
    np.testing.assert_array_equal(layer.data[0], image)


def test_update_layers_grows_z_stack_when_k_exceeds_current_nz():
    widget = _make_widget()
    channel = "10x Fluorescence 488 nm Ex"

    image = np.full((64, 96), 3, dtype=np.uint16)
    widget.updateLayers(image, 0, 0, 2, channel)

    layer = widget.viewer.layers[channel]
    assert widget.Nz == 3
    assert layer.data.shape == (3, 64, 96)
    np.testing.assert_array_equal(layer.data[2], image)


def test_update_layers_recreates_layer_when_switching_mono_to_rgb():
    widget = _make_widget()
    channel = "10x BF LED matrix full"
    widget.image_height = 50
    widget.image_width = 60
    widget.channels = {channel}

    old_layer = widget.viewer.add_image(
        np.zeros((1, 50, 60), dtype=np.uint16),
        name=channel,
        visible=True,
        rgb=False,
        colormap="gray",
        contrast_limits=(0.0, 1.0),
        blending="additive",
        scale=(1.0, 1.0, 1.0),
    )

    image_rgb = np.full((50, 60, 3), 11, dtype=np.uint16)
    widget.updateLayers(image_rgb, 0, 0, 0, channel)

    new_layer = widget.viewer.layers[channel]
    assert new_layer is not old_layer
    assert new_layer.rgb is True
    assert new_layer.data.shape == (1, 50, 60, 3)
    np.testing.assert_array_equal(new_layer.data[0], image_rgb)
