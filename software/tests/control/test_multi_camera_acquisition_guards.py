"""Guards that keep a mixed-camera channel selection from starting a bad acquisition.

Two independent checks, both pure functions in control.core.multi_point_utils:
  * unavailable camera: a channel bound to a camera id that never opened would silently
    be imaged on whatever camera happens to be active, so it must block the start.
  * mixed frame geometry: Zarr stores one uniform array per region, so a selection that
    spans cameras with different frame shape/color-ness/pixel size cannot be saved.
"""

from types import SimpleNamespace

import pytest
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

import control._def
import control.microscope
import squid.config
import tests.control.test_stubs as ts
from control.core.multi_point_utils import get_camera_geometry_mismatch, get_unavailable_camera_channels
from control.core.config.repository import ConfigRepository
from control.models.camera_registry import CameraDefinition, CameraRegistryConfig
from control.widgets import _MultiCameraGuardMixin
from squid.camera.utils import SimulatedCamera
from squid.config import CameraPixelFormat

# The ambient camera config is 4168x4168; every camera built here overrides it with a tiny
# frame so a test never materializes a ~100 MB image.
SMALL_FRAME = {"crop_width": 64, "crop_height": 48, "default_binning": (1, 1)}


class _Ch:
    """Minimal stand-in for AcquisitionChannel: the checkers only read name + camera."""

    def __init__(self, name, camera=None):
        self.name = name
        self.camera = camera


class _FakeCamera:
    """Camera-shaped stub for the geometry axes SimulatedCamera cannot vary (pixel size)."""

    def __init__(self, crop=(64, 48), pixel_format=CameraPixelFormat.MONO16, pixel_size_um=1.0):
        self._crop = crop
        self._pixel_format = pixel_format
        self._pixel_size_um = pixel_size_um

    def get_crop_size(self):
        return self._crop

    def get_resolution(self):
        return self._crop

    def get_pixel_format(self):
        return self._pixel_format

    def get_pixel_size_binned_um(self):
        return self._pixel_size_um


def _sim(serial, pixel_format=CameraPixelFormat.MONO16, crop=None, **overrides):
    updates = {"serial_number": serial, "default_pixel_format": pixel_format, **SMALL_FRAME}
    if crop is not None:
        updates["crop_width"], updates["crop_height"] = crop
    updates.update(overrides)
    config = squid.config.get_camera_config().model_copy(update=updates)
    return SimulatedCamera(config, hw_trigger_fn=None, hw_set_strobe_delay_ms_fn=None)


# ---------------------------------------------------------------- pure checkers


def test_single_camera_selection_is_compatible():
    cameras = {1: _sim("SN1"), 2: _sim("SN2", pixel_format=CameraPixelFormat.RGB24)}
    channels = [_Ch("DAPI", camera=1), _Ch("GFP", camera=None)]  # None -> primary
    assert get_camera_geometry_mismatch(channels, cameras) is None


def test_identical_geometry_two_cameras_is_compatible():
    cameras = {1: _sim("SN1"), 2: _sim("SN2")}
    channels = [_Ch("DAPI", camera=1), _Ch("BF", camera=2)]
    assert get_camera_geometry_mismatch(channels, cameras) is None


def test_color_vs_mono_mismatch_detected():
    cameras = {1: _sim("SN1"), 2: _sim("SN2", pixel_format=CameraPixelFormat.RGB24)}
    channels = [_Ch("DAPI", camera=1), _Ch("BF Color", camera=2)]
    message = get_camera_geometry_mismatch(channels, cameras)
    assert message is not None
    assert "color" in message and "mono" in message
    assert "Zarr" in message


def test_crop_mismatch_detected():
    cameras = {1: _sim("SN1", crop=(3000, 3000)), 2: _sim("SN2", crop=(2000, 2000))}
    channels = [_Ch("A", camera=1), _Ch("B", camera=2)]
    assert get_camera_geometry_mismatch(channels, cameras) is not None


def test_uncropped_camera_compares_by_resolution():
    """A camera with no configured crop reports (None, None) from get_crop_size(); the
    check must fall back to its resolution instead of treating every uncropped camera
    as identical."""
    cameras = {1: _sim("SN1"), 2: _sim("SN2", crop_width=None, crop_height=None)}
    channels = [_Ch("A", camera=1), _Ch("B", camera=2)]
    assert get_camera_geometry_mismatch(channels, cameras) is not None


def test_pixel_size_mismatch_detected():
    cameras = {1: _FakeCamera(pixel_size_um=1.0), 2: _FakeCamera(pixel_size_um=2.0)}
    channels = [_Ch("A", camera=1), _Ch("B", camera=2)]
    assert get_camera_geometry_mismatch(channels, cameras) is not None


def test_pixel_size_difference_below_rounding_is_compatible():
    cameras = {1: _FakeCamera(pixel_size_um=1.0), 2: _FakeCamera(pixel_size_um=1.000001)}
    channels = [_Ch("A", camera=1), _Ch("B", camera=2)]
    assert get_camera_geometry_mismatch(channels, cameras) is None


def test_unavailable_camera_is_not_a_geometry_mismatch():
    """An unavailable camera is reported by the other checker, not as a geometry conflict."""
    cameras = {1: _sim("SN1")}
    channels = [_Ch("OK", camera=1), _Ch("Ghost", camera=2)]
    assert get_camera_geometry_mismatch(channels, cameras) is None


def test_unavailable_camera_channels_listed():
    cameras = {1: _sim("SN1")}
    channels = [_Ch("OK", camera=1), _Ch("Ghost1", camera=2), _Ch("Ghost2", camera=2)]
    assert get_unavailable_camera_channels(channels, cameras) == ["Ghost1", "Ghost2"]
    assert get_unavailable_camera_channels([_Ch("OK", camera=None)], cameras) == []


# ------------------------------------------------- controller backstop (headless)

TWO_CAMERA_REGISTRY = CameraRegistryConfig(
    cameras=[
        CameraDefinition(name="Main Camera", id=1, serial_number="SIM-1", type="Toupcam"),
        CameraDefinition(
            name="Side Camera",
            id=2,
            serial_number="SIM-2",
            type="Toupcam",
            hardware_trigger=False,
            default_pixel_format="RGB24",
        ),
    ]
)


class _PastTheGuards(Exception):
    """Raised in place of the first real acquisition step, so a test can assert that
    run_acquisition got past the camera guards without starting an acquisition."""


def _controller_stopped_after_guards(monkeypatch, registry):
    """A simulated MultiPointController whose run_acquisition raises _PastTheGuards at
    the first step after the camera guards."""
    monkeypatch.setattr(ConfigRepository, "get_camera_registry", lambda self: registry)
    scope = control.microscope.Microscope.build_from_global_config(simulated=True, skip_init=True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)

    def _stop(*args, **kwargs):
        raise _PastTheGuards()

    monkeypatch.setattr(mpc, "_start_per_acquisition_log", _stop)
    return mpc


def _channels_on_cameras(mpc, camera_ids):
    """The first len(camera_ids) configured channels, rebound to the given camera ids."""
    channels = mpc.liveController.get_channels(mpc.objectiveStore.current_objective)
    assert len(channels) >= len(camera_ids)
    return [ch.model_copy(update={"camera": camera_id}) for ch, camera_id in zip(channels, camera_ids)]


def test_zarr_rejects_mixed_camera_geometry(monkeypatch):
    mpc = _controller_stopped_after_guards(monkeypatch, TWO_CAMERA_REGISTRY)
    mpc.selected_configurations = _channels_on_cameras(mpc, [1, 2])
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.ZARR_V3)

    with pytest.raises(ValueError) as excinfo:
        mpc.run_acquisition()
    assert "Zarr" in str(excinfo.value)


def test_ome_tiff_allows_mixed_camera_geometry(monkeypatch):
    mpc = _controller_stopped_after_guards(monkeypatch, TWO_CAMERA_REGISTRY)
    mpc.selected_configurations = _channels_on_cameras(mpc, [1, 2])
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.OME_TIFF)

    with pytest.raises(_PastTheGuards):
        mpc.run_acquisition()


def test_unavailable_camera_channel_blocks_start(monkeypatch):
    mpc = _controller_stopped_after_guards(monkeypatch, None)  # single-camera build
    mpc.selected_configurations = _channels_on_cameras(mpc, [2])  # camera 2 never opened
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.OME_TIFF)

    with pytest.raises(ValueError) as excinfo:
        mpc.run_acquisition()
    assert "not " in str(excinfo.value) and "available" in str(excinfo.value)


def test_single_camera_selection_passes_both_guards(monkeypatch):
    """The guards must be invisible on a normal single-camera system, Zarr included."""
    mpc = _controller_stopped_after_guards(monkeypatch, None)
    mpc.selected_configurations = _channels_on_cameras(mpc, [None, 1])
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.ZARR_V3)

    with pytest.raises(_PastTheGuards):
        mpc.run_acquisition()


# ------------------------------------------------------ widget guard (Start disable)


class _GuardHost(_MultiCameraGuardMixin):
    """Only the attributes the mixin touches, so the guard is testable without building a
    whole multipoint widget (both real hosts wire the same pieces)."""

    def __init__(self, cameras, channels):
        self.list_configurations = QListWidget()
        self.list_configurations.setSelectionMode(QAbstractItemView.MultiSelection)
        self.btn_startAcquisition = QPushButton()
        self.objectiveStore = SimpleNamespace(current_objective="10x")
        self._live_controller = SimpleNamespace(
            microscope=SimpleNamespace(cameras=cameras),
            get_channels=lambda objective: channels,
        )
        self._create_multi_camera_warning_label()
        # Parent everything, so the label's visibility is a child's hidden-flag rather
        # than a stray top-level window.
        self.container = QWidget()
        layout = QVBoxLayout(self.container)
        layout.addWidget(self.list_configurations)
        layout.addWidget(self.label_multiCameraWarning)
        layout.addWidget(self.btn_startAcquisition)

    def _guard_live_controller(self):
        return self._live_controller

    def select(self, *name_and_label_pairs):
        """Add selected rows carrying the bare channel name in Qt.UserRole and a decorated
        visible label — the Task 9 identity convention the guard has to read through."""
        for name, label in name_and_label_pairs:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, name)
            self.list_configurations.addItem(item)
            item.setSelected(True)


def _host(qtbot, cameras, channels, selection):
    host = _GuardHost(cameras, channels)
    qtbot.addWidget(host.container)
    host.select(*selection)
    return host


def _mixed_geometry_host(qtbot):
    return _host(
        qtbot,
        {1: _sim("SN1"), 2: _sim("SN2", pixel_format=CameraPixelFormat.RGB24)},
        [_Ch("DAPI", camera=1), _Ch("BF Color", camera=2)],
        [("DAPI", "DAPI"), ("BF Color", "BF Color — Side Camera")],
    )


def test_widget_guard_blocks_start_for_zarr_mixed_geometry(qtbot, monkeypatch):
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.ZARR_V3)
    host = _mixed_geometry_host(qtbot)

    host._update_multi_camera_guard()

    assert not host.label_multiCameraWarning.isHidden()
    assert "Zarr" in host.label_multiCameraWarning.text()
    assert not host.btn_startAcquisition.isEnabled()


def test_widget_guard_allows_mixed_geometry_for_ome_tiff(qtbot, monkeypatch):
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.OME_TIFF)
    host = _mixed_geometry_host(qtbot)

    host._update_multi_camera_guard()

    assert host.label_multiCameraWarning.isHidden()
    assert host.btn_startAcquisition.isEnabled()


def test_widget_guard_blocks_unavailable_camera_in_any_format(qtbot, monkeypatch):
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.OME_TIFF)
    host = _host(
        qtbot,
        {1: _sim("SN1")},
        [_Ch("DAPI", camera=1), _Ch("Ghost", camera=2)],
        [("DAPI", "DAPI"), ("Ghost", "Ghost — camera 2 (unavailable)")],
    )

    host._update_multi_camera_guard()

    assert not host.label_multiCameraWarning.isHidden()
    assert "Ghost" in host.label_multiCameraWarning.text()
    assert not host.btn_startAcquisition.isEnabled()


def test_widget_guard_clears_once_the_conflict_is_gone(qtbot, monkeypatch):
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.ZARR_V3)
    host = _mixed_geometry_host(qtbot)
    host._update_multi_camera_guard()
    assert not host.btn_startAcquisition.isEnabled()

    self_and_primary_only = host.list_configurations.item(1)
    self_and_primary_only.setSelected(False)
    host._update_multi_camera_guard()

    assert host.label_multiCameraWarning.isHidden()
    assert host.btn_startAcquisition.isEnabled()


def test_widget_guard_is_inert_on_single_camera_systems(qtbot, monkeypatch):
    """Zarr on a one-camera system: no warning, and the Start button is left exactly as
    the widget's other owners set it (here: disabled by the loading-position lock)."""
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.ZARR_V3)
    host = _host(
        qtbot,
        {1: _sim("SN1")},
        [_Ch("DAPI", camera=None), _Ch("GFP", camera=1)],
        [("DAPI", "DAPI"), ("GFP", "GFP")],
    )
    host.btn_startAcquisition.setEnabled(False)

    host._update_multi_camera_guard()

    assert host.label_multiCameraWarning.isHidden()
    assert not host.btn_startAcquisition.isEnabled()
