from types import SimpleNamespace

import numpy as np

from squid.ui.widgets.display.napari_mosaic import (
    MosaicWorker,
    NapariMosaicDisplayWidget,
    TileUpdate,
)


def _make_update(channel: str, x_mm: float) -> TileUpdate:
    return TileUpdate(
        channel=channel,
        tile=np.ones((4, 4), dtype=np.uint16),
        tile_top_left=(0.0, x_mm),
        extents=(0.0, 0.004, 0.0, max(0.004, x_mm + 0.004)),
        top_left=(0.0, 0.0),
        pixel_size_mm=0.001,
    )


def test_mosaic_worker_emits_incremental_tile_updates_not_full_mosaic(qtbot):
    worker = MosaicWorker(target_pixel_size_um=2.0)
    updates: list[TileUpdate] = []
    worker.mosaic_updated.connect(lambda update: updates.append(update))

    image = np.arange(100 * 80, dtype=np.uint16).reshape(100, 80)
    info0 = SimpleNamespace(
        pixel_size_um=1.0,
        position=SimpleNamespace(x_mm=0.0, y_mm=0.0),
    )
    info1 = SimpleNamespace(
        pixel_size_um=1.0,
        position=SimpleNamespace(x_mm=0.2, y_mm=0.0),
    )

    worker.process_tile(image, info0, "ch1")
    worker.process_tile(image, info1, "ch1")

    assert len(updates) == 2

    first = updates[0]
    second = updates[1]

    assert hasattr(first, "tile")
    assert not hasattr(first, "mosaic")
    assert first.tile.shape == (50, 40)  # target pixel size doubles source pixel size
    assert np.max(first.tile) > 0

    assert second.tile.shape == first.tile.shape
    assert second.tile_top_left[1] > first.tile_top_left[1]
    assert second.extents[3] > first.extents[3]


def test_pending_updates_preserve_order_without_channel_coalescing():
    class _Layer:
        def __init__(self, name: str):
            self.name = name
            self.refresh_count = 0

        def refresh(self) -> None:
            self.refresh_count += 1

    widget = NapariMosaicDisplayWidget.__new__(NapariMosaicDisplayWidget)
    widget._pending_updates = []
    widget._log = SimpleNamespace(error=lambda *args, **kwargs: None)
    layer_ch1 = _Layer("ch1")
    layer_ch2 = _Layer("ch2")
    widget.viewer = SimpleNamespace(layers=[layer_ch1, layer_ch2])

    applied: list[tuple[str, float]] = []
    widget._apply_tile_update = lambda update: applied.append((update.channel, update.tile_top_left[1]))

    first = _make_update("ch1", 0.000)
    second = _make_update("ch1", 0.005)

    NapariMosaicDisplayWidget._on_mosaic_updated(widget, first)
    NapariMosaicDisplayWidget._on_mosaic_updated(widget, second)
    NapariMosaicDisplayWidget._flush_pending_updates(widget)

    assert applied == [("ch1", 0.000), ("ch1", 0.005)]
    assert widget._pending_updates == []
    assert layer_ch1.refresh_count == 1
    assert layer_ch2.refresh_count == 0


def test_apply_tile_update_recreates_layer_when_channel_switches_to_rgb():
    class _Signal:
        def __init__(self):
            self._callbacks = []

        def connect(self, callback):
            self._callbacks.append(callback)

    class _Layer:
        def __init__(self, name: str, data: np.ndarray, rgb: bool):
            self.name = name
            self.data = np.array(data, copy=True)
            self.rgb = rgb
            self.scale = None
            self.translate = None
            self.mouse_double_click_callbacks = []
            self.events = SimpleNamespace(contrast_limits=_Signal())
            self.contrast_limits = (0.0, 1.0)

    class _Layers:
        def __init__(self):
            self._by_name: dict[str, _Layer] = {}

        def __contains__(self, name: str) -> bool:
            return name in self._by_name

        def __getitem__(self, name: str) -> _Layer:
            return self._by_name[name]

        def __iter__(self):
            return iter(self._by_name.values())

        def add(self, layer: _Layer) -> None:
            self._by_name[layer.name] = layer

        def remove(self, layer: _Layer) -> None:
            self._by_name.pop(layer.name, None)

    class _Viewer:
        def __init__(self):
            self.layers = _Layers()

        def add_image(
            self,
            data,
            name,
            rgb,
            colormap,
            visible,
            blending,
            scale,
            translate,
        ):
            layer = _Layer(name=name, data=data, rgb=rgb)
            layer.scale = scale
            layer.translate = translate
            self.layers.add(layer)
            return layer

    widget = NapariMosaicDisplayWidget.__new__(NapariMosaicDisplayWidget)
    widget.viewer = _Viewer()
    widget.layers_initialized = True
    widget.top_left_coordinate = [0.0, 0.0]
    widget._event_bus = None
    widget._has_mosaic_image_layers = lambda: True
    widget.onDoubleClick = lambda *_args, **_kwargs: None
    widget.signalContrastLimits = lambda *_args, **_kwargs: None
    widget.contrastManager = SimpleNamespace(update_limits=lambda *args, **kwargs: None)
    widget.updateLayer = lambda **kwargs: None

    old_layer = widget.viewer.add_image(
        data=np.zeros((4, 4), dtype=np.uint16),
        name="ch1",
        rgb=False,
        colormap="gray",
        visible=True,
        blending="additive",
        scale=(1.0, 1.0),
        translate=(0.0, 0.0),
    )

    update = TileUpdate(
        channel="ch1",
        tile=np.ones((4, 4, 3), dtype=np.uint16),
        tile_top_left=(0.0, 0.0),
        extents=(0.0, 0.004, 0.0, 0.004),
        top_left=(0.0, 0.0),
        pixel_size_mm=0.001,
        contrast_min=0.0,
        contrast_max=100.0,
    )

    NapariMosaicDisplayWidget._apply_tile_update(widget, update)

    new_layer = widget.viewer.layers["ch1"]
    assert new_layer is not old_layer
    assert new_layer.rgb is True
    assert new_layer.scale == (1.0, 1.0)
