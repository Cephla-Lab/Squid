import os
import sys
import types
from enum import Enum
from types import SimpleNamespace

import numpy as np
import pytest
from qtpy.QtWidgets import QApplication

try:
    import control._def as cdef  # type: ignore
except Exception:  # pragma: no cover - testing fallback
    stub = types.ModuleType("control._def")

    class FileSavingOption(Enum):
        INDIVIDUAL_IMAGES = "INDIVIDUAL_IMAGES"
        MULTI_PAGE_TIFF = "MULTI_PAGE_TIFF"
        OME_TIFF = "OME_TIFF"

    stub.FileSavingOption = FileSavingOption
    stub.FILE_SAVING_OPTION = FileSavingOption.INDIVIDUAL_IMAGES
    stub.FILE_ID_PADDING = 3
    stub.CHANNEL_COLORS_MAP = {"default": {"hex": 0xFFFFFF, "name": "gray"}}
    sys.modules["control._def"] = stub
    cdef = stub  # type: ignore

from control._def import FileSavingOption  # type: ignore
from control.nd_view_widget import NapariNDViewWidget


class _DummyObjectiveStore:
    def get_pixel_size_factor(self) -> float:
        return 1.0


class _DummyCamera:
    def get_pixel_size_binned_um(self) -> float:
        return 1.0


class _DummyContrast:
    def get_default_limits(self):
        return (0, 65535)

    def get_limits(self, channel_name: str):
        return (0, 65535)

    def update_limits(self, channel_name: str, min_val: float, max_val: float) -> None:
        pass


class _DummyDims:
    def __init__(self):
        self.axis_labels = []
        self.ranges = {}
        self.points = {}

    def set_range(self, axis: int, value):
        self.ranges[axis] = value

    def set_point(self, axis: int, value):
        self.points[axis] = value


class _DummyViewer:
    def __init__(self):
        self.layers = []
        self.grid = SimpleNamespace(enabled=False)
        self.dims = _DummyDims()


class DummyNDViewWidget(NapariNDViewWidget):
    """Test double that avoids depending on the real napari viewer."""

    def __init__(self, objectiveStore, camera, contrastManager):  # type: ignore[override]
        from qtpy.QtWidgets import QWidget

        QWidget.__init__(self)
        self.objectiveStore = objectiveStore
        self.camera = camera
        self.contrastManager = contrastManager

        self.image_width = 0
        self.image_height = 0
        self.dtype = np.uint8
        self.channels = set()
        self.pixel_size_um = 1.0
        self.dz_um = 1.0
        self.Nz = 1
        self.layers_initialized = False
        self.acquisition_initialized = False
        self.viewer_scale_initialized = False
        self.dims_initialized = False
        self.grid_enabled = False
        self.update_layer_count = 0

        self.channel_layers = {}
        self.channel_rgb = {}
        self.channel_dtype = {}
        self.channel_config_index = {}
        self.channel_shape = {}
        self.position_map = {}
        self.position_metadata = {}
        self.acquisition_store = {}
        self.available_time_indices = set()
        self.available_position_indices = set()
        self.max_time_index = -1
        self.max_position_index = -1

        self.viewer = _DummyViewer()
        self.layout = None

    def initNapariViewer(self):  # pragma: no cover - not used in tests
        self.viewer = _DummyViewer()


@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture()
def nd_widget():
    widget = DummyNDViewWidget(_DummyObjectiveStore(), _DummyCamera(), _DummyContrast())
    widget.initLayersShape(1, 0)
    widget.initLayers(5, 5, np.uint16)
    return widget


def _make_info(tmp_dir, configuration_name="ConfigA", region=0, fov=0, time_point=0):
    return SimpleNamespace(
        configuration=SimpleNamespace(name=configuration_name),
        file_id="image1",
        save_directory=str(tmp_dir),
        region_id=region,
        fov=fov,
        time_point=time_point,
        position=SimpleNamespace(x_mm=1.0, y_mm=2.0, z_mm=0.5),
        configuration_idx=0,
        experiment_path=str(tmp_dir),
    )


def test_get_position_index_tracks_unique_positions(nd_widget):
    info = _make_info(tmp_dir="/tmp")
    first = nd_widget._get_position_index(info, 10.0, 20.0)
    assert first == 0
    # Same key should return identical index
    assert nd_widget._get_position_index(info, 11.0, 21.0) == first

    new_info = _make_info("/tmp", region=1, fov=2)
    second = nd_widget._get_position_index(new_info, 5.0, 6.0)
    assert second == 1
    assert len(nd_widget.position_map) == 2


def test_record_capture_records_metadata(monkeypatch, tmp_path, nd_widget):
    monkeypatch.setattr(cdef, "FILE_SAVING_OPTION", FileSavingOption.INDIVIDUAL_IMAGES)
    monkeypatch.setattr(
        "control.nd_view_widget.FILE_SAVING_OPTION", FileSavingOption.INDIVIDUAL_IMAGES, raising=False
    )

    data = (np.ones((5, 5), dtype=np.uint16) * 7)
    info = _make_info(tmp_path)
    nd_widget.channel_shape["Channel1"] = data.shape
    nd_widget.channel_rgb["Channel1"] = False
    nd_widget.channel_dtype["Channel1"] = data.dtype
    nd_widget._record_capture("Channel1", 0, 0, 0, info, data)

    entry = nd_widget.acquisition_store[("Channel1", 0, 0)]
    assert entry["config_name"] == "ConfigA"
    assert "meta" in entry
    meta = entry["meta"]
    assert meta["file_id"] == "image1"
    assert meta["save_directory"] == str(tmp_path)


def test_load_from_individual_images_discovers_file(monkeypatch, tmp_path, nd_widget):
    monkeypatch.setattr(cdef, "FILE_SAVING_OPTION", FileSavingOption.INDIVIDUAL_IMAGES)
    monkeypatch.setattr(
        "control.nd_view_widget.FILE_SAVING_OPTION", FileSavingOption.INDIVIDUAL_IMAGES, raising=False
    )

    nd_widget.Nz = 1
    nd_widget.image_height = 4
    nd_widget.image_width = 4
    nd_widget.channel_rgb["Channel1"] = False

    info = _make_info(tmp_path)
    nd_widget._record_capture("Channel1", 0, 0, 0, info, np.zeros((4, 4), dtype=np.uint16))
    entry = nd_widget.acquisition_store[("Channel1", 0, 0)]

    import imageio

    image_path = tmp_path / "image1_ConfigA.tiff"
    imageio.imwrite(image_path, np.full((4, 4), 9, dtype=np.uint16))

    stack = nd_widget._load_from_individual_images("Channel1", entry, np.uint16)
    assert stack.shape == (1, 4, 4)
    np.testing.assert_array_equal(stack[0], 9)


def test_build_channel_dask_returns_expected_array(monkeypatch, nd_widget, tmp_path):
    monkeypatch.setattr(cdef, "FILE_SAVING_OPTION", FileSavingOption.INDIVIDUAL_IMAGES)
    monkeypatch.setattr(
        "control.nd_view_widget.FILE_SAVING_OPTION", FileSavingOption.INDIVIDUAL_IMAGES, raising=False
    )

    nd_widget.Nz = 1
    nd_widget.image_height = 3
    nd_widget.image_width = 3

    data = np.full((3, 3), 9, dtype=np.uint16)
    nd_widget.channel_rgb["Channel1"] = False
    nd_widget.channel_dtype["Channel1"] = data.dtype
    nd_widget.channel_shape["Channel1"] = data.shape
    nd_widget.max_time_index = 0
    nd_widget.max_position_index = 0
    info = _make_info(tmp_path)
    nd_widget._record_capture("Channel1", 0, 0, 0, info, data)
    entry = nd_widget.acquisition_store[("Channel1", 0, 0)]

    import imageio

    tmp_file = os.path.join(info.save_directory, "image1_ConfigA.tiff")
    imageio.imwrite(tmp_file, data)
    entry["paths"][0] = tmp_file

    arr = nd_widget._build_channel_dask("Channel1")
    computed = arr.compute()
    assert computed.shape == (1, 1, 1, 3, 3)
    np.testing.assert_array_equal(computed[0, 0, 0], data)


def test_infer_capture_path_returns_latest_file(monkeypatch, tmp_path, nd_widget):
    monkeypatch.setattr(cdef, "FILE_SAVING_OPTION", FileSavingOption.INDIVIDUAL_IMAGES)
    monkeypatch.setattr(
        "control.nd_view_widget.FILE_SAVING_OPTION", FileSavingOption.INDIVIDUAL_IMAGES, raising=False
    )

    older = tmp_path / "image1_ConfigA.tiff"
    newer = tmp_path / "image1_ConfigA (1).tiff"
    older.write_bytes(b"older")
    os.utime(older, (1, 1))
    newer.write_bytes(b"newer")
    os.utime(newer, (10, 10))

    info = _make_info(tmp_path)
    latest = nd_widget._infer_capture_path(info)
    assert latest in {str(older), str(newer)}
