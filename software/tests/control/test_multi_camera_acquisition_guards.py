"""Guards that keep a mixed-camera channel selection from starting a bad acquisition.

Two independent checks, both pure functions in control.core.multi_point_utils:
  * unavailable camera: a channel bound to a camera id that never opened would silently
    be imaged on whatever camera happens to be active, so it must block the start.
  * mixed frame geometry: Zarr stores one uniform array per region, so a selection that
    spans cameras differing in frame size, color-ness, storage bit depth or pixel size
    cannot be saved.

Plus the widget layer that surfaces both before Start is pressed.
"""

import inspect
import json
from types import SimpleNamespace

import numpy as np
import pytest
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

import control._def
import control.microscope
import control.widgets
import squid.config
import tests.control.test_stubs as ts
from control.core.multi_point_controller import MultiPointController
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


def test_mono8_vs_mono16_mismatch_detected():
    """Same size, same pixel size, both mono — but uint8 vs uint16 frames. Zarr fixes the
    array dtype from the first frame, so the other camera's frames would be silently
    up/down-cast."""
    cameras = {
        1: _sim("SN1", pixel_format=CameraPixelFormat.MONO8),
        2: _sim("SN2", pixel_format=CameraPixelFormat.MONO16),
    }
    channels = [_Ch("A", camera=1), _Ch("B", camera=2)]
    message = get_camera_geometry_mismatch(channels, cameras)
    assert message is not None
    assert "uint8" in message and "uint16" in message


def test_pixel_format_storage_bit_depth_buckets():
    """RGB24/RGB32 are 3x8 and 4x8 bits per pixel, i.e. uint8 frames; everything above
    8 bits per component needs uint16."""
    for eight_bit in (
        CameraPixelFormat.MONO8,
        CameraPixelFormat.RGB24,
        CameraPixelFormat.RGB32,
        CameraPixelFormat.BAYER_RG8,
    ):
        assert CameraPixelFormat.storage_bit_depth(eight_bit) == 8
    for sixteen_bit in (
        CameraPixelFormat.MONO10,
        CameraPixelFormat.MONO12,
        CameraPixelFormat.MONO14,
        CameraPixelFormat.MONO16,
        CameraPixelFormat.RGB48,
        CameraPixelFormat.BAYER_RG12,
    ):
        assert CameraPixelFormat.storage_bit_depth(sixteen_bit) == 16


def test_unavailable_camera_channels_listed():
    cameras = {1: _sim("SN1")}
    channels = [_Ch("OK", camera=1), _Ch("Ghost1", camera=2), _Ch("Ghost2", camera=2)]
    assert get_unavailable_camera_channels(channels, cameras) == ["Ghost1", "Ghost2"]
    assert get_unavailable_camera_channels([_Ch("OK", camera=None)], cameras) == []


# ------------------------------------------------------ per-channel pixel size


def test_per_channel_pixel_sizes_computed(monkeypatch):
    """compute_channel_pixel_sizes maps each selected channel to its camera's pixel size."""
    from control.core.multi_point_utils import compute_channel_pixel_sizes

    cameras = {1: _sim("SN1"), 2: _sim("SN2")}
    cameras[2].set_binning(2, 2)  # cam2 pixel size = 2x cam1
    channels = [_Ch("DAPI", camera=1), _Ch("BF Color", camera=2), _Ch("GFP", camera=None)]
    sizes = compute_channel_pixel_sizes(channels, cameras, pixel_size_factor=0.5)
    assert sizes["DAPI"] == pytest.approx(0.5 * cameras[1].get_pixel_size_binned_um())
    assert sizes["GFP"] == sizes["DAPI"]
    assert sizes["BF Color"] == pytest.approx(0.5 * cameras[2].get_pixel_size_binned_um())


def test_per_channel_pixel_sizes_omit_what_cannot_be_computed():
    """No camera (or no objective factor) means no pixel size to record: the key is left
    out rather than filled with the active camera's number, which would be wrong."""
    from control.core.multi_point_utils import compute_channel_pixel_sizes

    cameras = {1: _sim("SN1")}
    channels = [_Ch("DAPI", camera=1), _Ch("Ghost", camera=2)]
    assert list(compute_channel_pixel_sizes(channels, cameras, 0.5)) == ["DAPI"]
    assert compute_channel_pixel_sizes(channels, cameras, None) == {}


def test_used_camera_ids_are_deduplicated_in_first_use_order():
    """The acquisition starts on the first channel's camera, so first-use order (not
    sorted order) is what a caller warming every camera up needs."""
    from control.core.multi_point_utils import get_used_camera_ids

    channels = [_Ch("BF", camera=2), _Ch("DAPI", camera=1), _Ch("GFP", camera=None), _Ch("TRITC", camera=2)]
    assert get_used_camera_ids(channels) == [2, 1]  # GFP's null camera is the primary, already seen
    assert get_used_camera_ids([]) == []


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


def _simulated_controller(monkeypatch, registry):
    """A MultiPointController on a simulated microscope built from `registry` (None = the
    single-camera build)."""
    monkeypatch.setattr(ConfigRepository, "get_camera_registry", lambda self: registry)
    scope = control.microscope.Microscope.build_from_global_config(simulated=True, skip_init=True)
    return ts.get_test_multi_point_controller(microscope=scope)


def _controller_stopped_after_guards(monkeypatch, registry):
    """A simulated MultiPointController whose run_acquisition raises _PastTheGuards at
    the first step after the camera guards."""
    mpc = _simulated_controller(monkeypatch, registry)

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


# ------------------------------------------------- warm-up + acquisition metadata


def _record_warm_up_grabs(monkeypatch, mpc, fail_on=None):
    """Replace the real frame grab with a recorder of the camera it ran on.

    Each grab returns a frame filled with its camera's id (so a caller can tell which
    camera's frame it kept) and reports camera 2 as the color one, matching
    TWO_CAMERA_REGISTRY.
    """
    grabbed_on = []

    def _grab():
        camera_id = mpc.microscope.active_camera_id
        grabbed_on.append(camera_id)
        if camera_id == fail_on:
            raise RuntimeError("camera fell over mid warm-up")
        return (np.full((2, 2), camera_id, dtype=np.uint16), camera_id == 2)

    monkeypatch.setattr(mpc, "_temporary_get_an_image_hack", _grab)
    return grabbed_on


def test_warm_up_grabs_one_frame_per_used_camera_and_ends_on_the_starting_one(monkeypatch):
    """Every camera the run will use pays its slow first frame before the run, not inside
    it. The run's starting camera goes last, so the loop ends where the run begins and the
    estimate keeps that camera's frame."""
    mpc = _simulated_controller(monkeypatch, TWO_CAMERA_REGISTRY)
    mpc.selected_configurations = _channels_on_cameras(mpc, [2, 1, 2])
    grabbed_on = _record_warm_up_grabs(monkeypatch, mpc)

    test_image, is_color = mpc._warm_up_cameras_and_get_test_image()

    assert grabbed_on == [1, 2]  # each used camera once, starting camera (2) last
    assert mpc.microscope.active_camera_id == 2  # where the run begins
    assert int(test_image[0][0]) == 2 and is_color is True


def test_warm_up_on_a_single_camera_system_is_one_grab_and_no_switch(monkeypatch):
    mpc = _simulated_controller(monkeypatch, None)
    mpc.selected_configurations = _channels_on_cameras(mpc, [None, 1])
    switches = []
    monkeypatch.setattr(mpc.microscope, "set_active_camera", lambda camera_id: switches.append(camera_id))
    grabbed_on = _record_warm_up_grabs(monkeypatch, mpc)

    mpc._warm_up_cameras_and_get_test_image()

    assert grabbed_on == [control._def.PRIMARY_CAMERA_ID]
    assert switches == []


def test_warm_up_with_no_selection_stays_on_the_active_camera(monkeypatch):
    """The disk estimate is reachable before any channel is selected; it must still grab
    exactly one frame, from whatever camera is active."""
    mpc = _simulated_controller(monkeypatch, TWO_CAMERA_REGISTRY)
    mpc.selected_configurations = []
    grabbed_on = _record_warm_up_grabs(monkeypatch, mpc)

    mpc._warm_up_cameras_and_get_test_image()

    assert grabbed_on == [mpc.microscope.active_camera_id]


def test_warm_up_skips_a_camera_that_never_opened(monkeypatch):
    """A selection the start-time guard would reject can still reach the disk estimate;
    warming up must not try to make a missing camera active."""
    mpc = _simulated_controller(monkeypatch, None)  # only the primary camera exists
    mpc.selected_configurations = _channels_on_cameras(mpc, [1, 2])
    grabbed_on = _record_warm_up_grabs(monkeypatch, mpc)

    mpc._warm_up_cameras_and_get_test_image()

    assert grabbed_on == [1]


def test_warm_up_survives_a_secondary_camera_failing(monkeypatch):
    """A camera that will not warm up costs the run its own first frame, nothing more — it
    must not discard the frame the estimate came for."""
    mpc = _simulated_controller(monkeypatch, TWO_CAMERA_REGISTRY)
    mpc.selected_configurations = _channels_on_cameras(mpc, [2, 1])  # starts on 2, so 1 is secondary
    grabbed_on = _record_warm_up_grabs(monkeypatch, mpc, fail_on=1)

    test_image, _ = mpc._warm_up_cameras_and_get_test_image()

    assert grabbed_on == [1, 2]  # tried the secondary, carried on to the starting camera
    assert int(test_image[0][0]) == 2
    assert mpc.microscope.active_camera_id == 2


def test_warm_up_reraises_when_the_starting_camera_fails_and_stays_on_it(monkeypatch):
    """The starting camera failing is what the caller's worst-case-image fallback is for,
    so that one still propagates — and must not strand the run on another camera."""
    mpc = _simulated_controller(monkeypatch, TWO_CAMERA_REGISTRY)
    mpc.selected_configurations = _channels_on_cameras(mpc, [2, 1])
    _record_warm_up_grabs(monkeypatch, mpc, fail_on=2)

    with pytest.raises(RuntimeError):
        mpc._warm_up_cameras_and_get_test_image()

    assert mpc.microscope.active_camera_id == 2


def test_warm_up_stops_live_before_it_switches_cameras(monkeypatch):
    """set_active_camera assumes triggering is quiesced; the live trigger timer is its own
    thread and would send a trigger into the middle of the switch."""
    mpc = _simulated_controller(monkeypatch, TWO_CAMERA_REGISTRY)
    mpc.selected_configurations = _channels_on_cameras(mpc, [2, 1])
    calls = []
    mpc.liveController.is_live = True

    def _stop_live():
        mpc.liveController.is_live = False
        calls.append("stop_live")

    monkeypatch.setattr(mpc.liveController, "stop_live", _stop_live)
    real_set_active_camera = mpc.microscope.set_active_camera

    def _set_active_camera(camera_id):
        calls.append(f"switch to {camera_id}")
        real_set_active_camera(camera_id)

    monkeypatch.setattr(mpc.microscope, "set_active_camera", _set_active_camera)
    _record_warm_up_grabs(monkeypatch, mpc)

    mpc._warm_up_cameras_and_get_test_image()

    assert calls[0] == "stop_live", calls
    assert "switch to 2" in calls
    assert mpc._live_stopped_for_warm_up is True  # so run_acquisition still resumes live


def test_warm_up_leaves_live_alone_on_a_single_camera_system(monkeypatch):
    """Parity: the single-camera path never switches, so it must not stop live either."""
    mpc = _simulated_controller(monkeypatch, None)
    mpc.selected_configurations = _channels_on_cameras(mpc, [None, 1])
    mpc.liveController.is_live = True
    stopped = []
    monkeypatch.setattr(mpc.liveController, "stop_live", lambda: stopped.append(True))
    _record_warm_up_grabs(monkeypatch, mpc)

    mpc._warm_up_cameras_and_get_test_image()

    assert stopped == []
    assert mpc.liveController.is_live is True
    assert mpc._live_stopped_for_warm_up is False


def test_warm_up_leaves_live_alone_when_no_camera_switch_is_needed(monkeypatch):
    """Two cameras, but every selected channel is on the one already active."""
    mpc = _simulated_controller(monkeypatch, TWO_CAMERA_REGISTRY)
    mpc.selected_configurations = _channels_on_cameras(mpc, [1, None])
    mpc.liveController.is_live = True
    stopped = []
    monkeypatch.setattr(mpc.liveController, "stop_live", lambda: stopped.append(True))
    _record_warm_up_grabs(monkeypatch, mpc)

    mpc._warm_up_cameras_and_get_test_image()

    assert stopped == []
    assert mpc._live_stopped_for_warm_up is False


@pytest.mark.parametrize(
    "is_live, stopped_for_warm_up, expected",
    [(True, False, True), (False, True, True), (False, False, False)],
)
def test_run_acquisition_resumes_live_the_warm_up_stopped(monkeypatch, is_live, stopped_for_warm_up, expected):
    """The warm-up stopping live must not read, to run_acquisition, as "the user was not
    live" — that would silently drop the resume-live-afterwards behavior."""
    mpc = _simulated_controller(monkeypatch, None)
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.OME_TIFF)
    monkeypatch.setattr(mpc, "_start_per_acquisition_log", lambda *a, **kw: None)
    monkeypatch.setattr(mpc.liveController, "stop_live", lambda: setattr(mpc.liveController, "is_live", False))
    mpc.liveController.is_live = is_live
    mpc._live_stopped_for_warm_up = stopped_for_warm_up

    # The first step after the live-stop block, so the run never actually starts.
    def _stop(*args, **kwargs):
        raise _PastTheGuards()

    monkeypatch.setattr(mpc.camera, "enable_callbacks", _stop)

    with pytest.raises(_PastTheGuards):
        mpc.run_acquisition()

    assert mpc.liveController_was_live_before_multipoint is expected
    assert mpc._live_stopped_for_warm_up is False  # consumed, so it cannot leak into a later run


def test_acquisition_parameters_json_records_per_channel_pixel_sizes(monkeypatch, tmp_path):
    mpc = _simulated_controller(monkeypatch, TWO_CAMERA_REGISTRY)
    mpc.selected_configurations = _channels_on_cameras(mpc, [1, 2])
    mpc.microscope.cameras[2].set_binning(2, 2)  # so the two channels cannot share a number
    mpc.set_base_path(str(tmp_path))

    mpc.start_new_experiment("pixel sizes")

    written = json.loads((tmp_path / mpc.experiment_ID / "acquisition parameters.json").read_text())
    factor = mpc.objectiveStore.get_pixel_size_factor()
    assert written["sensor_pixel_size_um"] == mpc.camera.get_pixel_size_binned_um()  # unchanged
    assert written["channel_pixel_sizes_um"] == {
        channel.name: factor * mpc.microscope.cameras[channel.camera].get_pixel_size_binned_um()
        for channel in mpc.selected_configurations
    }


def test_both_acquisition_metadata_writers_record_per_channel_pixel_sizes():
    """parameters.json and acquisition.yaml are written by two different methods; a run
    is only fully described if both carry the per-channel map."""
    for method in (MultiPointController.start_new_experiment, MultiPointController.run_acquisition):
        assert "channel_pixel_sizes_um" in inspect.getsource(method), method.__qualname__


# ------------------------------------------------------ widget guard (Start disable)


class _GuardHost(_MultiCameraGuardMixin):
    """Only the attributes the mixin touches, so the guard is testable without building a
    whole multipoint widget (both real hosts wire the same pieces)."""

    def __init__(self, cameras, channels, dropped=()):
        self.list_configurations = QListWidget()
        self.list_configurations.setSelectionMode(QAbstractItemView.MultiSelection)
        self.btn_startAcquisition = QPushButton()
        self.objectiveStore = SimpleNamespace(current_objective="10x")
        # Stands in for ChannelSequenceController: `dropped` are the channels the user's
        # persisted sequence asks for that the list refuses to acquire (camera missing).
        self.channel_sequence = SimpleNamespace(unavailable_included_names=lambda: list(dropped))
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


def _host(qtbot, cameras, channels, selection, dropped=()):
    host = _GuardHost(cameras, channels, dropped=dropped)
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
    host.disable_the_start_aquisition_button()

    host._update_multi_camera_guard()

    assert host.label_multiCameraWarning.isHidden()
    assert not host.btn_startAcquisition.isEnabled()


def test_guard_does_not_reenable_start_held_by_the_loading_position_lock(qtbot, monkeypatch):
    """guard disables -> stage reaches loading position -> user fixes the selection.
    The guard's veto lifts, but the loading-position veto is still on."""
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.ZARR_V3)
    host = _mixed_geometry_host(qtbot)
    host._update_multi_camera_guard()
    assert not host.btn_startAcquisition.isEnabled()  # guard veto

    host.disable_the_start_aquisition_button()  # stage reached the loading position
    host.list_configurations.item(1).setSelected(False)  # user drops the offending channel
    host._update_multi_camera_guard()

    assert host.label_multiCameraWarning.isHidden()
    assert not host.btn_startAcquisition.isEnabled(), "loading-position lock was overridden"

    host.enable_the_start_aquisition_button()  # stage left the loading position
    assert host.btn_startAcquisition.isEnabled()


def test_loading_position_lock_does_not_override_the_guard(qtbot, monkeypatch):
    """The mirror image: leaving the loading position must not enable Start while a
    camera conflict is still on screen."""
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.ZARR_V3)
    host = _mixed_geometry_host(qtbot)
    host._update_multi_camera_guard()

    host.disable_the_start_aquisition_button()
    host.enable_the_start_aquisition_button()

    assert not host.btn_startAcquisition.isEnabled()


def test_start_time_recheck_dialogs_after_a_live_switch_to_zarr(qtbot, monkeypatch):
    """Preferences can flip FILE_SAVING_OPTION to Zarr long after the last selection
    change, so Start re-runs the guard and refuses visibly instead of letting the
    controller backstop raise into a log-only handler."""
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.OME_TIFF)
    host = _mixed_geometry_host(qtbot)
    host._update_multi_camera_guard()
    assert host.btn_startAcquisition.isEnabled()

    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.ZARR_V3)
    shown = []
    monkeypatch.setattr(control.widgets, "error_dialog", lambda message, *a, **kw: shown.append(message))
    host.btn_startAcquisition.setChecked(True)

    assert host._reject_if_multi_camera_conflict() is True
    assert len(shown) == 1 and "Zarr" in shown[0]
    assert not host.btn_startAcquisition.isChecked()
    assert not host.btn_startAcquisition.isEnabled()


def test_start_time_recheck_is_silent_when_there_is_no_conflict(qtbot, monkeypatch):
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.OME_TIFF)
    host = _mixed_geometry_host(qtbot)
    shown = []
    monkeypatch.setattr(control.widgets, "error_dialog", lambda message, *a, **kw: shown.append(message))

    assert host._reject_if_multi_camera_conflict() is False
    assert shown == []


@pytest.mark.parametrize(
    "widget_class",
    [control.widgets.FlexibleMultiPointWidget, control.widgets.WellplateMultiPointWidget],
)
def test_multipoint_widgets_recheck_the_guard_when_start_is_pressed(widget_class):
    """Both Start paths must go through the re-check; without it a conflict created after
    the last selection change reaches the controller backstop, which only logs."""
    for method_name in ("toggle_acquisition", "on_snap_images"):
        source = inspect.getsource(getattr(widget_class, method_name))
        assert "_reject_if_multi_camera_conflict" in source, f"{widget_class.__name__}.{method_name}"


def test_silently_dropped_channels_are_named_without_blocking_start(qtbot, monkeypatch):
    """A dropped acquisition YAML (or a cached sequence) can name a channel whose camera
    never opened; the list quietly removes it. The remaining run is valid, so Start stays
    enabled, but the label has to say what will not be imaged."""
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", control._def.FileSavingOption.ZARR_V3)
    host = _host(
        qtbot,
        {1: _sim("SN1")},
        [_Ch("DAPI", camera=1), _Ch("Ghost", camera=2)],
        [("DAPI", "DAPI")],  # only DAPI is selectable; Ghost was dropped by the list
        dropped=["Ghost"],
    )

    assert host._update_multi_camera_guard() is None
    assert not host.label_multiCameraWarning.isHidden()
    assert "Ghost" in host.label_multiCameraWarning.text()
    assert host.btn_startAcquisition.isEnabled()
