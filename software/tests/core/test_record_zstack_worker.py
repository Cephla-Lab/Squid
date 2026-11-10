"""RecordZStackWorker / RecordZStackController ("Time at XY") on the simulated microscope.

Engine behaviour: streaming, pacing, sealing, drops, aborts, run state, .done, error
propagation, trigger-mode and camera restore, live preview, and the per-FOV order
Zi -> R rows -> Zf with the output layout that follows from it.  The parameter model and
its pure helpers are covered in tests/core/test_record_zstack_model.py.
"""

import json
import os
from pathlib import Path

import pytest

from control.core.camera_geometry import CameraGeometry
from control.core.record_zstack_controller import (
    ChannelPlan,
    RecordSliceSelection,
    RecordZStackAcquisitionParameters,
    ZStackPlan,
    frame_count,
)


def _stack_dirs(experiment_dir, phase):
    """Sorted experiment-relative paths of the z-stack stores of one phase ("initial" / "final")."""
    root = Path(experiment_dir) / f"zstack_{phase}"
    return sorted(str(p.relative_to(experiment_dir)).replace(os.sep, "/") for p in root.rglob("fov_*.ome.zarr"))


def _recording_dirs(experiment_dir):
    root = Path(experiment_dir) / "recording"
    return sorted(str(p.relative_to(experiment_dir)).replace(os.sep, "/") for p in root.rglob("*.ome.zarr"))


def _recording_attrs(store):
    return json.load(open(Path(store) / "zarr.json"))["attributes"]["_squid"]


# ---------------------------------------------------------------------------
# Smoke tests (simulated microscope, in-process Zarr runner)
#
# 2 wells x 2 FOV x 2 time points with every phase: an initial z-stack of two channels,
# two recordings (each at its own rate and duration), a final z-stack of one channel.
# ---------------------------------------------------------------------------


def _build_simulated_microscope(crop_w: int, crop_h: int):
    """Build a simulated microscope and shrink the camera frame for a fast test.

    The simulated camera reports its resolution as crop_width/crop_height
    divided by binning, so pin binning to 1x1 — some machine configs default
    the camera model to 2x2 binning, which would silently halve every frame —
    and give this camera its own copy of the config so each captured/recorded
    frame is exactly (crop_h, crop_w). The camera is built with the module-level
    CameraConfig; writing the crop into that object would shrink the frames of
    every microscope built later in the same pytest process.
    """
    import control.microscope

    scope = control.microscope.Microscope.build_from_global_config(True)
    scope.camera._config = scope.camera._config.model_copy(update={"crop_width": crop_w, "crop_height": crop_h})
    scope.camera.set_binning(1, 1)
    return scope


def test_build_simulated_microscope_leaves_the_global_camera_config_alone():
    """Only this camera is 64x48; the shared CameraConfig that every later microscope is built from is untouched."""
    import squid.config

    before = squid.config.get_camera_config().model_copy()
    scope = _build_simulated_microscope(64, 48)
    assert scope.camera.get_resolution() == (64, 48)
    assert squid.config.get_camera_config() == before


def _smoke_params(tmp_path, channels, experiment_id):
    """Zi: channels 0+1; R: channel 0 at 10 fps / 0.3 s and channel 1 at 20 fps / 0.2 s; Zf: channel 0.
    Three planes centred on the reference; recordings at Bottom and Top."""
    return RecordZStackAcquisitionParameters(
        base_path=str(tmp_path),
        experiment_id=experiment_id,
        Nt=2,
        dt_s=0.0,  # continuous: dt>0 would skip slots missed while working
        channels=[
            ChannelPlan(channel=channels[0], fps=10.0, duration_s=0.3, zi=True, r=True, zf=True),
            ChannelPlan(channel=channels[1], fps=20.0, duration_s=0.2, zi=True, r=True, zf=False),
        ],
        zstack=ZStackPlan(mode="Relative", dz_um=1.0, z_range_um=2.0),
        record_slices=RecordSliceSelection(roles=["bottom", "top"]),
    )


def _assert_smoke_output(base, channels, crop_w, crop_h):
    """The exact folder layout and store shapes of a _smoke_params run over A1 / A2 with 2 FOVs each."""
    import tensorstore as tstore

    def dirname(channel):
        return channel.name.replace(" ", "_")

    regions, fovs, timepoints = ("A1", "A2"), (0, 1), (0, 1)
    assert sorted(p.name for p in base.iterdir() if p.is_dir()) == ["recording", "zstack_final", "zstack_initial"]
    assert _stack_dirs(base, "initial") == sorted(
        f"zstack_initial/zarr/{r}/fov_{k}.ome.zarr" for r in regions for k in fovs
    )
    assert _stack_dirs(base, "final") == sorted(
        f"zstack_final/zarr/{r}/fov_{k}.ome.zarr" for r in regions for k in fovs
    )
    # recording/t<N>/<region>/<channel>/fov_<k>_z<i>: the channel directory is always there, and
    # i is the plane's index in the shared stack (Bottom = 0, Top = 2 of three planes).
    assert _recording_dirs(base) == sorted(
        f"recording/t{t}/{r}/{dirname(c)}/fov_{k}_z{i}.ome.zarr"
        for t in timepoints
        for r in regions
        for c in channels[:2]
        for k in fovs
        for i in (0, 2)
    )

    def shape(path):
        return tuple(tstore.open({"driver": "zarr3", "kvstore": {"driver": "file", "path": str(path)}}).result().shape)

    # Each R row is recorded at its own rate and duration.
    for channel, fps, duration_s in ((channels[0], 10.0, 0.3), (channels[1], 20.0, 0.2)):
        stores = [base / d for d in _recording_dirs(base) if f"/{dirname(channel)}/" in d]
        assert len(stores) == 2 * 2 * 2 * 2
        for store in stores:
            assert shape(store) == (frame_count(fps, duration_s), 1, 1, crop_h, crop_w), store
            attrs = _recording_attrs(store)
            assert attrs["requested_fps"] == fps and attrs["effective_fps"] == pytest.approx(fps)
            assert attrs.get("nominal_time_increment_s", attrs["time_increment_s"]) == pytest.approx(1.0 / fps)
            assert (attrs["n_planes"], attrs["stack_planes"]) == (2, 3)
            plane = attrs["plane_index"]
            assert store.name.endswith(f"_z{plane}.ome.zarr")
            assert attrs["plane_z_offset_um"] == pytest.approx({0: -1.0, 2: 1.0}[plane])
    # Z-stacks: (T, C, Z, Y, X), written across all time points.
    for store in _stack_dirs(base, "initial"):
        assert shape(base / store / "0") == (2, 2, 3, crop_h, crop_w)
    for store in _stack_dirs(base, "final"):
        assert shape(base / store / "0") == (2, 1, 3, crop_h, crop_w)


def test_record_zstack_worker_smoke(tmp_path):
    pytest.importorskip("tensorstore")  # optional dep; the worker writes real Zarr
    from control.core.multi_point_controller import NoOpCallbacks
    from control.core.record_zstack_worker import RecordZStackWorker

    scope, live_controller, laser_af, channels, (x0, y0) = _build_simulated_setup()
    assert len(channels) >= 2
    params = _smoke_params(tmp_path, channels, "rec_zstack_smoke")
    aborted = {"v": False}
    worker = RecordZStackWorker(
        scope=scope,
        live_controller=live_controller,
        laser_auto_focus_controller=laser_af,
        objective_store=scope.objective_store,
        params=params,
        callbacks=NoOpCallbacks,
        abort_requested_fn=lambda: aborted["v"],
        request_abort_fn=lambda: aborted.__setitem__("v", True),
        scan_region_fov_coords={"A1": [(x0, y0), (x0 + 0.5, y0)], "A2": [(x0 + 1.0, y0), (x0 + 1.5, y0)]},
    )

    worker.run()

    assert not aborted["v"], "worker requested abort during the smoke run"
    assert worker.end_reason == "completed"
    _assert_smoke_output(Path(params.base_path) / params.experiment_id, channels, 64, 48)


# ---------------------------------------------------------------------------
# The same acquisition driven end to end through RecordZStackController.run_acquisition() + join():
# the real controller and the real worker on the simulated microscope.
# ---------------------------------------------------------------------------


def test_record_zstack_controller_smoke(tmp_path):
    pytest.importorskip("tensorstore")  # optional dep; the worker writes real Zarr
    import yaml
    from control.core.multi_point_controller import NoOpCallbacks
    from control.core.record_zstack_controller import RecordZStackController
    from control.core.scan_coordinates import ScanCoordinates

    scope, live_controller, laser_af, channels, (x0, y0) = _build_simulated_setup()
    scan_coords = ScanCoordinates(objectiveStore=scope.objective_store, stage=scope.stage, camera=scope.camera)
    scan_coords.region_fov_coordinates = {
        "A1": [(x0, y0), (x0 + 0.5, y0)],
        "A2": [(x0 + 1.0, y0), (x0 + 1.5, y0)],
    }
    controller = RecordZStackController(
        microscope=scope,
        live_controller=live_controller,
        laser_autofocus_controller=laser_af,
        objective_store=scope.objective_store,
        scan_coordinates=scan_coords,
        callbacks=NoOpCallbacks,
    )
    params = _smoke_params(tmp_path, channels, "ctrl_smoke")

    assert controller.last_experiment_path is None and controller.last_end_reason is None
    controller.run_acquisition(params)
    controller.join(timeout=180)
    assert not controller.acquisition_in_progress(), "worker thread did not finish in time"

    # Determine the resolved experiment_id (has a timestamp appended).
    subdirs = [d for d in Path(tmp_path).iterdir() if d.is_dir()]
    assert len(subdirs) == 1, f"expected exactly one experiment dir, got {subdirs}"
    base = subdirs[0]

    # What an unattended caller (the Workflow Runner) reads to decide whether to build on this run.
    assert Path(controller.last_experiment_path) == base
    assert controller.last_end_reason == "completed"

    _assert_smoke_output(base, channels, 64, 48)
    assert (base / ".done").exists() and (base / "acquisition_channels.yaml").exists()
    saved = yaml.safe_load(open(base / "acquisition.yaml"))
    assert [(r["channel"]["name"], r["zi"], r["r"], r["zf"]) for r in saved["channels"]] == [
        (channels[0].name, True, True, True),
        (channels[1].name, True, True, False),
    ]
    controller.close()


def _probe_frame_shape_with(camera):
    """RecordZStackWorker._probe_frame_shape on a worker shell that has only this camera and a logger."""
    import squid.logging
    from control.core.record_zstack_worker import RecordZStackWorker

    worker = RecordZStackWorker.__new__(RecordZStackWorker)
    worker.camera = camera
    worker._log = squid.logging.get_logger("RecordZStackWorker")
    return worker._probe_frame_shape()


def test_probe_frame_shape_uses_processed_frame_not_resolution():
    """The recording dataset must be sized from a delivered (processed) frame:
    real cameras crop/rotate frames in _process_raw_frame, so get_resolution()
    (sensor/binned size) is the wrong source and yields blank recordings."""
    from types import SimpleNamespace

    import numpy as np

    from control.core.record_zstack_worker import RecordZStackWorker

    processed = np.zeros((50, 60), dtype=np.uint8)  # crop/rotation already applied

    class _FakeCamera:
        def get_resolution(self):
            return (100, 80)  # (width, height) sensor size — differs from frames

        def get_pixel_format(self):
            raise NotImplementedError  # force the dtype to come from the frame

        def set_acquisition_mode(self, mode):
            pass

        def start_streaming(self):
            pass

        def stop_streaming(self):
            pass

        def send_trigger(self):
            pass

        def read_camera_frame(self):
            return SimpleNamespace(frame=processed)

    fake_self = SimpleNamespace(camera=_FakeCamera())
    y, x, dtype = _probe_frame_shape_with(fake_self.camera)

    assert (y, x) == (50, 60), f"expected processed-frame shape, got ({y}, {x})"
    assert dtype == np.uint8


# ---------------------------------------------------------------------------
# Review-fix tests: post-run hardware state restore (F8, F9, F10)
# ---------------------------------------------------------------------------


def _build_simulated_setup():
    """A 64x48 simulated scope at mid-Z with its live and laser-AF controllers, its channels, and one valid FOV."""
    import control._def
    import tests.control.test_stubs as ts

    control._def.FILE_SAVING_OPTION = control._def.FileSavingOption.ZARR_V3
    scope = _build_simulated_microscope(64, 48)
    live_controller = ts.get_test_live_controller(scope, scope.objective_store.default_objective)
    laser_af = ts.get_test_laser_autofocus_controller(scope)
    channels = live_controller.get_channels(scope.objective_store.default_objective)
    scope.camera.set_exposure_time(1)

    z_cfg = scope.stage.get_config().Z_AXIS
    scope.stage.move_z_to((z_cfg.MAX_POSITION + z_cfg.MIN_POSITION) / 2.0)
    scope.low_level_drivers.microcontroller.wait_till_operation_is_completed()
    x0 = scope.stage.get_config().X_AXIS.MIN_POSITION + 1.0
    y0 = scope.stage.get_config().Y_AXIS.MIN_POSITION + 1.0
    return scope, live_controller, laser_af, channels, (x0, y0)


def _params(base_path, channels, **overrides):
    """Tiny Time at XY parameters: one timepoint, a 0.2 s recording of channels[0], then a final
    2-plane z-stack of channels[1]; overrides replace any field.

    The engine tests describe a run as one recording (recording_channel, fps, duration_s, and its planes
    recording_bottom_z_offset_um / recording_Nz / recording_dz_um) followed by one z-stack (zstack_channels
    over z_min_um..z_max_um in z_step_um steps, relative to the reference).  That is turned into channel
    rows (recording = R, stack = Zf), one z plan and the recorded planes by the same rules that load a file
    saved by the tab this one replaces, so the planes visited are exactly the ones described.
    """
    from control.acquisition_yaml_loader import _legacy_recording_plan, _legacy_stack_plan

    values = dict(
        base_path=str(base_path),
        experiment_id="state_restore",
        Nt=1,
        dt_s=0.0,
        use_laser_af=False,
        recording_enabled=True,
        recording_channel=channels[0],
        fps=10.0,
        duration_s=0.2,
        recording_bottom_z_offset_um=0.0,
        recording_Nz=1,
        recording_dz_um=1.0,
        zstack_enabled=True,
        zstack_channels=list(channels[1:2]),
        z_min_um=0.0,
        z_max_um=1.0,
        z_step_um=1.0,
    )
    values.update(overrides)
    rows, notes = [], []
    if values["recording_enabled"]:
        rows.append(
            ChannelPlan(channel=values["recording_channel"], fps=values["fps"], duration_s=values["duration_s"], r=True)
        )
    stack_on = bool(values["zstack_enabled"] and values["zstack_channels"])
    for channel in values["zstack_channels"] if stack_on else []:
        same = [row for row in rows if row.name == channel.name]
        if same:
            same[0].zf = True
        else:
            rows.append(ChannelPlan(channel=channel, zf=True))
    rec_offsets = [
        values["recording_bottom_z_offset_um"] + j * values["recording_dz_um"] for j in range(values["recording_Nz"])
    ]
    if stack_on:
        z, offsets = _legacy_stack_plan(values["z_min_um"], values["z_max_um"], values["z_step_um"], notes)
        recorded = [offsets.index(off) for off in rec_offsets]
    else:
        z, offsets, recorded = _legacy_recording_plan(
            values["recording_bottom_z_offset_um"], values["recording_Nz"], values["recording_dz_um"], notes
        )
    assert not notes, notes
    return RecordZStackAcquisitionParameters(
        base_path=values["base_path"],
        experiment_id=values["experiment_id"],
        Nt=values["Nt"],
        dt_s=values["dt_s"],
        use_laser_af=values["use_laser_af"],
        channels=rows,
        zstack=ZStackPlan(mode=z["z_mode"], dz_um=z["z_dz_um"], nz=z["z_nz"], z_range_um=z["z_range_um"]),
        record_slices=RecordSliceSelection(roles=[], indices=recorded),
    )


def _build_controller_harness():
    """A RecordZStackController on the simulated setup with one FOV; returns (scope, live_controller, channels, controller)."""
    import tests.control.test_stubs as ts
    from control.core.multi_point_controller import NoOpCallbacks
    from control.core.record_zstack_controller import RecordZStackController

    scope, live_controller, laser_af, channels, fov = _build_simulated_setup()
    scan_coords = ts.get_test_scan_coordinates(scope.objective_store, scope.stage, scope.camera)
    scan_coords.region_fov_coordinates = {"A1": [fov]}
    controller = RecordZStackController(
        microscope=scope,
        live_controller=live_controller,
        laser_autofocus_controller=laser_af,
        objective_store=scope.objective_store,
        scan_coordinates=scan_coords,
        callbacks=NoOpCallbacks,
    )
    return scope, live_controller, channels, controller


def _build_worker_harness(
    tmp_path,
    recording_enabled,
    zstack_enabled,
    zstack_channel_slice=slice(1, 2),
    callbacks=None,
    display_frame_fn=None,
    display_fps=0.0,
    **param_overrides,
):
    """A 1-FOV worker on the simulated setup; param_overrides replace fields of _params()."""
    from control.core.multi_point_controller import NoOpCallbacks
    from control.core.record_zstack_worker import RecordZStackWorker

    scope, live_controller, laser_af, channels, fov = _build_simulated_setup()
    params = _params(
        tmp_path,
        channels,
        recording_enabled=recording_enabled,
        zstack_enabled=zstack_enabled,
        zstack_channels=list(channels[zstack_channel_slice]) if zstack_enabled else [],
        **param_overrides,
    )
    aborted = {"v": False}
    worker = RecordZStackWorker(
        scope=scope,
        live_controller=live_controller,
        laser_auto_focus_controller=laser_af,
        objective_store=scope.objective_store,
        params=params,
        callbacks=NoOpCallbacks if callbacks is None else callbacks,
        abort_requested_fn=lambda: aborted["v"],
        request_abort_fn=lambda: aborted.__setitem__("v", True),
        scan_region_fov_coords={"A1": [fov]},
        display_frame_fn=display_frame_fn,
        display_fps=display_fps,
    )
    return scope, live_controller, channels, worker, aborted


def _record(worker, z_ref, row=0):
    """Record R row *row* at FOV 0 of A1, timepoint 0."""
    return worker.record(0, "A1", 0, z_ref, worker.r_plans[row], row)


def test_record_only_restores_trigger_mode(tmp_path):
    """F8: a record-only run must not leave the camera in SOFTWARE_TRIGGER when
    the LiveController was in CONTINUOUS (or HARDWARE) before the acquisition."""
    pytest.importorskip("tensorstore")
    from control._def import TriggerMode
    from squid.abc import CameraAcquisitionMode

    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=True, zstack_enabled=False
    )
    live_controller.set_microscope_mode(channels[0])
    live_controller.set_trigger_mode(TriggerMode.CONTINUOUS)

    worker.run()

    assert not aborted["v"]
    assert live_controller.trigger_mode == TriggerMode.CONTINUOUS
    assert (
        scope.camera.get_acquisition_mode() == CameraAcquisitionMode.CONTINUOUS
    ), "camera left out of sync with LiveController trigger mode after record-only run"


def test_run_restores_pre_acquisition_channel_config(tmp_path):
    """F10: after the acquisition, the hardware must be back on the channel the
    user was viewing, not the last z-stack channel."""
    pytest.importorskip("tensorstore")

    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=False, zstack_enabled=True, zstack_channel_slice=slice(1, 2)
    )
    live_controller.set_microscope_mode(channels[0])  # user was viewing channel 0

    worker.run()

    assert not aborted["v"]
    current = live_controller.currentConfiguration
    assert current is not None and current.name == channels[0].name, (
        f"expected channel config restored to {channels[0].name!r}, " f"got {current.name if current else None!r}"
    )


def test_zstack_trigger_fallback_keeps_livecontroller_in_sync(tmp_path, monkeypatch):
    """F9: if set_trigger_mode(SOFTWARE) fails once, the fallback must keep
    liveController.trigger_mode in sync with the camera — otherwise
    acquire_camera_image takes neither illumination branch and the whole
    z-stack is captured dark."""
    pytest.importorskip("tensorstore")
    from control._def import TriggerMode

    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=False, zstack_enabled=True
    )
    live_controller.set_microscope_mode(channels[0])
    live_controller.set_trigger_mode(TriggerMode.CONTINUOUS)

    orig_set_trigger_mode = live_controller.set_trigger_mode
    calls = {"n": 0}

    def flaky(mode):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated MCU ACK timeout")
        return orig_set_trigger_mode(mode)

    monkeypatch.setattr(live_controller, "set_trigger_mode", flaky)

    seen_modes = []
    orig_acquire = worker.acquire_camera_image

    def spy(*args, **kwargs):
        seen_modes.append(live_controller.trigger_mode)
        return orig_acquire(*args, **kwargs)

    monkeypatch.setattr(worker, "acquire_camera_image", spy)

    worker.run()

    assert seen_modes, "no z-stack captures happened"
    assert all(m == TriggerMode.SOFTWARE for m in seen_modes), (
        f"z-stack captured with stale liveController.trigger_mode {seen_modes[0]} "
        f"— illumination branch skipped (dark images)"
    )


def test_recording_uses_achievable_fps_when_camera_clamps(tmp_path):
    """F6: when the camera clamps below the requested fps (exposure-limited),
    the dataset size and time metadata must reflect the achievable rate —
    otherwise the run stalls to the timeout and trailing planes are blank."""
    pytest.importorskip("tensorstore")
    import json

    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=True, zstack_enabled=False
    )
    # record() applies the recording channel (exposure included) before probing
    # the frame rate, so the long exposure must live on the channel itself.
    channels[0].exposure_time = 200.0  # ms → achievable ≈ a few fps, well below 10
    scope.camera.set_exposure_time(200)
    achievable = scope.camera.set_frame_rate(10.0)
    assert achievable < 10.0, "precondition: camera must clamp below the requested rate"

    worker.run()
    assert not aborted["v"]

    rec = sorted((Path(tmp_path) / "state_restore" / "recording").rglob("*.ome.zarr"))
    assert len(rec) == 1
    meta = json.load(open(rec[0] / "zarr.json"))
    squid_attrs = meta["attributes"]["_squid"]
    expected_T = max(1, frame_count(achievable, 0.2))
    assert rec[0].parent.name == channels[0].name.replace(" ", "_")
    assert squid_attrs["shape"][0] == expected_T, (
        f"dataset sized for the requested fps (T={squid_attrs['shape'][0]}), "
        f"expected achievable-rate T={expected_T}"
    )
    assert squid_attrs["requested_fps"] == 10.0
    assert abs(squid_attrs["effective_fps"] - achievable) < 1e-6
    assert abs(squid_attrs["time_increment_s"] - 1.0 / achievable) < 1e-6, (
        f"time_increment_s={squid_attrs['time_increment_s']} does not match the "
        f"achievable rate (1/{achievable:.2f})"
    )


def test_move_xy_honors_z_component():
    """F11: (x, y, z) scan coordinates carry a stored per-FOV focus plane
    (flexible regions, update_fov_z); dropping z means recording/z-stacking at
    the previous FOV's focus on tilted samples."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from control.core.record_zstack_worker import RecordZStackWorker

    stage = MagicMock()
    fake_self = SimpleNamespace(stage=stage, _sleep=lambda s: None, wait_till_operation_is_completed=lambda: None)
    RecordZStackWorker._move_xy(fake_self, (1.0, 2.0, 3.5))
    stage.move_x_to.assert_called_once_with(1.0)
    stage.move_y_to.assert_called_once_with(2.0)
    stage.move_z_to.assert_called_once_with(3.5)


def test_move_xy_two_tuple_does_not_move_z():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from control.core.record_zstack_worker import RecordZStackWorker

    stage = MagicMock()
    fake_self = SimpleNamespace(stage=stage, _sleep=lambda s: None, wait_till_operation_is_completed=lambda: None)
    RecordZStackWorker._move_xy(fake_self, (1.0, 2.0))
    stage.move_z_to.assert_not_called()


def test_wait_for_dt_paces_from_acquisition_start(tmp_path):
    """F12: dt is the interval between timepoint STARTS (t0 + k*dt, matching
    MultiPointWorker and the recorded time_increment_s metadata), not a wait
    appended after each timepoint's work."""
    import time as _time

    pytest.importorskip("tensorstore")
    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=True, zstack_enabled=False
    )
    worker.params.dt_s = 2.0
    worker._acq_start_time = _time.monotonic() - 100.0  # the work already overran the interval

    t0 = _time.monotonic()
    ok = worker._wait_for_dt(worker._acq_start_time + 1 * worker.params.dt_s)
    took = _time.monotonic() - t0

    assert ok
    assert took < 1.0, f"_wait_for_dt slept {took:.1f}s though t=1's start time is long past"


def test_controller_writes_config_snapshot_and_done_file(tmp_path):
    """F15: every experiment dir must carry the settings snapshot
    (acquisition_channels.yaml) and completion marker (.done) that every
    multipoint acquisition produces — downstream watchers depend on both."""
    pytest.importorskip("tensorstore")
    import control._def
    import tests.control.test_stubs as ts
    from control.core.multi_point_controller import NoOpCallbacks
    from control.core.record_zstack_controller import RecordZStackController

    control._def.FILE_SAVING_OPTION = control._def.FileSavingOption.ZARR_V3
    scope = _build_simulated_microscope(64, 48)
    live_controller = ts.get_test_live_controller(scope, scope.objective_store.default_objective)
    laser_af = ts.get_test_laser_autofocus_controller(scope)
    channels = live_controller.get_channels(scope.objective_store.default_objective)
    scope.camera.set_exposure_time(1)

    controller = RecordZStackController(
        microscope=scope,
        live_controller=live_controller,
        laser_autofocus_controller=laser_af,
        objective_store=scope.objective_store,
        scan_coordinates=None,  # no FOVs — completion bookkeeping is what's under test
        callbacks=NoOpCallbacks,
    )
    params = RecordZStackAcquisitionParameters(
        base_path=str(tmp_path),
        experiment_id="bookkeeping",
        Nt=1,
        channels=[ChannelPlan(channel=channels[0], fps=10.0, duration_s=0.2, r=True)],
    )
    try:
        controller.run_acquisition(params)
        controller.join(timeout=60)
    finally:
        controller.close()

    exp = Path(params.base_path) / params.experiment_id
    assert exp.is_dir()
    assert (exp / "acquisition_channels.yaml").exists(), "settings snapshot missing from experiment dir"
    assert (exp / ".done").exists(), "completion marker missing from experiment dir"


def test_pace_timepoint_skips_missed_slots(tmp_path):
    """Round-2 parity: MultiPointWorker skips timepoints whose slot already
    passed (grid-preserving); running them late back-to-back silently breaks
    the recorded time_increment_s for the rest of the run."""
    import time as _time

    pytest.importorskip("tensorstore")
    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=True, zstack_enabled=False
    )
    worker.params.dt_s = 2.0

    worker._acq_start_time = _time.monotonic() - 100.0
    assert worker._pace_timepoint(1) == "skip"

    worker._acq_start_time = _time.monotonic()
    assert worker._pace_timepoint(0) == "run"

    aborted["v"] = True
    assert worker._pace_timepoint(1) in ("abort", "skip")  # never 'run' while aborted


def test_probe_frame_shape_passes_over_a_frame_from_before_its_stream():
    """The probe runs once per camera setting.  A camera whose latest frame predates the stream (the
    simulated camera after a binning change) must not size the dataset with the previous shape."""
    import time as _time
    from types import SimpleNamespace

    import numpy as np

    stale = SimpleNamespace(frame=np.zeros((48, 64), dtype=np.uint16), timestamp=_time.time() - 60.0)
    reads = []

    class _FakeCamera:
        def set_acquisition_mode(self, mode):
            pass

        def start_streaming(self):
            pass

        def stop_streaming(self):
            pass

        def read_camera_frame(self):
            reads.append(1)
            if len(reads) < 3:
                return stale
            return SimpleNamespace(frame=np.zeros((24, 32), dtype=np.uint16), timestamp=_time.time())

    assert _probe_frame_shape_with(_FakeCamera())[:2] == (24, 32)
    assert len(reads) == 3


def test_probe_frame_shape_settles_for_the_latest_frame_when_none_is_newer(monkeypatch):
    """A camera that never stamps a frame after the stream start (a clock that is not host time) still
    gets its dataset sized, from its latest frame, after the short wait."""
    from types import SimpleNamespace

    import numpy as np

    from control.core.record_zstack_worker import RecordZStackWorker

    monkeypatch.setattr(RecordZStackWorker, "PROBE_FRESH_FRAME_TIMEOUT_S", 0.05)

    class _FakeCamera:
        def set_acquisition_mode(self, mode):
            pass

        def start_streaming(self):
            pass

        def stop_streaming(self):
            pass

        def read_camera_frame(self):
            return SimpleNamespace(frame=np.zeros((50, 60), dtype=np.uint8), timestamp=12.5)

    assert _probe_frame_shape_with(_FakeCamera())[:2] == (50, 60)


def test_probe_frame_shape_rejects_color_frames():
    """Round-2: a color (Y,X,3) probe frame silently produced a 2-D dataset
    that every write then failed against; reject it with a clear error."""
    from types import SimpleNamespace

    import numpy as np

    from control.core.record_zstack_worker import RecordZStackWorker

    color = np.zeros((50, 60, 3), dtype=np.uint8)

    class _FakeColorCamera:
        def get_resolution(self):
            return (60, 50)

        def set_acquisition_mode(self, mode):
            pass

        def start_streaming(self):
            pass

        def stop_streaming(self):
            pass

        def send_trigger(self):
            pass

        def read_camera_frame(self):
            return SimpleNamespace(frame=color)

    fake_self = SimpleNamespace(camera=_FakeColorCamera())
    with pytest.raises(ValueError, match="monochrome"):
        _probe_frame_shape_with(fake_self.camera)


def test_probe_frame_shape_uses_continuous_and_never_triggers():
    """Regression: starting a recording with Live in SOFTWARE-trigger mode left
    an outstanding trigger, so the old probe's send_trigger() was rejected
    ("trigger too early") and it fell back to the wrong get_resolution() size,
    failing every write on cropped cameras.  The probe must run in CONTINUOUS
    mode (what recording uses) and never call send_trigger, so trigger state
    can't block it, and must size from the delivered (cropped) frame."""
    from types import SimpleNamespace

    import numpy as np

    from control.core.record_zstack_worker import RecordZStackWorker
    from squid.abc import CameraAcquisitionMode

    processed = np.zeros((2084, 2084), dtype=np.uint16)  # cropped delivered shape
    modes = []

    class _FakeCamera:
        def get_resolution(self):
            return (3104, 2084)  # raw binned sensor (width, height) — must NOT be used

        def set_acquisition_mode(self, mode):
            modes.append(mode)

        def start_streaming(self):
            pass

        def stop_streaming(self):
            pass

        def send_trigger(self, *args, **kwargs):
            raise AssertionError("probe must not send a trigger in CONTINUOUS mode")

        def read_camera_frame(self):
            return SimpleNamespace(frame=processed)

    fake_self = SimpleNamespace(camera=_FakeCamera())
    y, x, dtype = _probe_frame_shape_with(fake_self.camera)

    assert (y, x) == (2084, 2084), f"expected cropped delivered shape, got ({y}, {x})"
    assert dtype == np.uint16
    assert CameraAcquisitionMode.CONTINUOUS in modes, "probe must set CONTINUOUS mode"


def test_probe_frame_shape_aborts_when_no_frame_delivered():
    """If the camera delivers no probe frame, the probe must raise (aborting the
    run with a clear error) rather than sizing from get_resolution() — the raw
    sensor size, which makes every write fail on cropped cameras (a blank run)."""
    from types import SimpleNamespace

    from control.core.record_zstack_worker import RecordZStackWorker

    class _FakeCamera:
        def get_resolution(self):
            return (3104, 2084)

        def set_acquisition_mode(self, mode):
            pass

        def start_streaming(self):
            pass

        def stop_streaming(self):
            pass

        def read_camera_frame(self):
            return None

    fake_self = SimpleNamespace(camera=_FakeCamera())
    with pytest.raises(RuntimeError, match="probe"):
        _probe_frame_shape_with(fake_self.camera)


def test_record_fails_fast_on_dropped_frames(tmp_path, monkeypatch):
    """R2: sustained backpressure drops are the systematic slow-disk condition
    the fail-fast was written for — record() must abort the acquisition, not
    grind through hours of half-blank FOVs."""
    pytest.importorskip("tensorstore")
    import control.core.record_zstack_worker as worker_mod

    class _DroppyWriter:
        """Stand-in RecordingWriter that reports backpressure drops."""

        def __init__(self, cfg, **kwargs):
            self.dropped_count = 5
            self.write_error_count = 0
            self.finalize_wedged = False

        def start(self):
            pass

        def enqueue(self, frame, t, c, z):
            pass

        def mark_incomplete(self, captured, expected):
            pass

        def finalize(self, timeout_s=30.0):
            pass

        def abort(self):
            pass

    monkeypatch.setattr(worker_mod, "RecordingWriter", _DroppyWriter)

    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=True, zstack_enabled=False
    )
    worker.run()

    assert aborted["v"] is True, "acquisition continued despite dropped recording frames"


def test_config_snapshot_dedupes_channels_by_name(tmp_path):
    """R10: a channel used by more than one phase, or listed in two rows, must not produce two
    same-name entries in acquisition_channels.yaml (channels are identified by name; duplicate
    names make the snapshot ambiguous)."""
    pytest.importorskip("tensorstore")
    import yaml

    import control._def
    import tests.control.test_stubs as ts
    from control.core.multi_point_controller import NoOpCallbacks
    from control.core.record_zstack_controller import RecordZStackController

    control._def.FILE_SAVING_OPTION = control._def.FileSavingOption.ZARR_V3
    scope = _build_simulated_microscope(64, 48)
    live_controller = ts.get_test_live_controller(scope, scope.objective_store.default_objective)
    laser_af = ts.get_test_laser_autofocus_controller(scope)
    channels = live_controller.get_channels(scope.objective_store.default_objective)
    scope.camera.set_exposure_time(1)

    controller = RecordZStackController(
        microscope=scope,
        live_controller=live_controller,
        laser_autofocus_controller=laser_af,
        objective_store=scope.objective_store,
        scan_coordinates=None,
        callbacks=NoOpCallbacks,
    )
    params = RecordZStackAcquisitionParameters(
        base_path=str(tmp_path),
        experiment_id="dedupe",
        Nt=1,
        channels=[
            ChannelPlan(channel=channels[0], fps=10.0, duration_s=0.2, zi=True, r=True, zf=True),  # every phase
            ChannelPlan(channel=channels[0].model_copy(deep=True), zf=True),  # and listed again
            ChannelPlan(channel=channels[1], zf=True),
        ],
        zstack=ZStackPlan(nz=2, dz_um=1.0),
    )
    try:
        controller.run_acquisition(params)
        controller.join(timeout=60)
    finally:
        controller.close()

    snapshot = yaml.safe_load(open(Path(tmp_path) / params.experiment_id / "acquisition_channels.yaml"))
    names = [c["name"] for c in snapshot["channels"]]
    assert sorted(names) == sorted([channels[0].name, channels[1].name]), f"snapshot channels: {names}"


def test_recording_path_plane_naming():
    from types import SimpleNamespace

    from control.core.record_zstack_worker import RecordZStackWorker

    fake_self = SimpleNamespace(experiment_path="/exp")
    path = RecordZStackWorker._recording_path(fake_self, 0, "B2", 1, "GFP", 2).replace(os.sep, "/")
    # The channel directory and the stack-plane suffix are always present, single plane or not.
    assert path == "/exp/recording/t0/B2/GFP/fov_1_z2.ome.zarr"
    assert RecordZStackWorker._recording_path(fake_self, 3, "B2", 0, "GFP", 0).replace(os.sep, "/") == (
        "/exp/recording/t3/B2/GFP/fov_0_z0.ome.zarr"
    )


def test_record_multi_plane_writes_one_zarr_per_plane(tmp_path):
    """Nz=3 → three per-plane recordings per FOV with correct names, shapes,
    and plane metadata; Nz=1 filename compatibility is covered by the smoke test."""
    pytest.importorskip("tensorstore")
    import json

    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path,
        recording_enabled=True,
        zstack_enabled=False,
        recording_Nz=3,
        recording_dz_um=2.0,
        recording_bottom_z_offset_um=-2.0,
    )

    worker.run()
    assert not aborted["v"]

    rec_dir = Path(tmp_path) / "state_restore" / "recording" / "t0" / "A1" / channels[0].name.replace(" ", "_")
    names = sorted(p.name for p in rec_dir.glob("*.ome.zarr"))
    assert names == ["fov_0_z0.ome.zarr", "fov_0_z1.ome.zarr", "fov_0_z2.ome.zarr"]

    for j, name in enumerate(names):
        meta = json.load(open(rec_dir / name / "zarr.json"))
        squid_attrs = meta["attributes"]["_squid"]
        assert squid_attrs["plane_index"] == j
        assert squid_attrs["n_planes"] == 3 and squid_attrs["stack_planes"] == 3
        assert abs(squid_attrs["plane_z_offset_um"] - (-2.0 + j * 2.0)) < 1e-9
        assert squid_attrs["acquisition_complete"] is True
        assert squid_attrs["shape"][1:3] == [1, 1]  # (T,1,1,Y,X) per plane


def test_record_multi_plane_abort_between_planes(tmp_path):
    """Abort after the first plane completes → exactly one plane file, no error,
    run() still finishes (state restore + done marker)."""
    pytest.importorskip("tensorstore")

    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=True, zstack_enabled=False, recording_Nz=3, recording_dz_um=1.0
    )

    orig = worker._record_one_plane

    def record_then_abort(*args, **kwargs):
        emitted = orig(*args, **kwargs)
        aborted["v"] = True  # user presses Stop right after plane 0 finishes
        return emitted

    worker._record_one_plane = record_then_abort
    worker.run()

    rec_dir = Path(tmp_path) / "state_restore" / "recording" / "t0" / "A1" / channels[0].name.replace(" ", "_")
    names = sorted(p.name for p in rec_dir.glob("*.ome.zarr"))
    assert names == ["fov_0_z0.ome.zarr"], f"expected only plane 0 after abort, got {names}"
    assert (Path(tmp_path) / "state_restore" / ".done").exists()


# ---------------------------------------------------------------------------
# Final-review-fix tests: illumination timing + fail-fast restore
# ---------------------------------------------------------------------------


def test_record_moves_to_first_plane_before_illumination(tmp_path):
    """Finding 1: the first plane's Z move must happen BEFORE illumination
    turns on (pre-branch parity — the sample must not be illuminated during
    the Z move + settle). Later planes move while illumination is already on
    (it is not toggled between planes), and it turns off only after the last
    plane."""
    pytest.importorskip("tensorstore")

    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=True, zstack_enabled=False, recording_Nz=3, recording_dz_um=1.0
    )

    events = []
    call_count = {"n": 0}

    def fake_record_one_plane(*args, **kwargs):
        call_count["n"] += 1
        return 0

    worker._record_one_plane = fake_record_one_plane

    orig_move = worker._move_z_to_offset

    def move_spy(z_ref, offset):
        events.append(("move", offset))
        return orig_move(z_ref, offset)

    worker._move_z_to_offset = move_spy

    orig_on = live_controller.turn_on_illumination

    def on_spy():
        events.append(("illum_on",))
        return orig_on()

    live_controller.turn_on_illumination = on_spy

    orig_off = live_controller.turn_off_illumination

    def off_spy():
        events.append(("illum_off",))
        return orig_off()

    live_controller.turn_off_illumination = off_spy

    z_ref = scope.stage.get_pos().z_mm
    total = _record(worker, z_ref)

    assert call_count["n"] == 3, "all three planes must still be recorded"
    assert total == 0
    assert events == [
        ("move", 0.0),
        ("illum_on",),
        ("move", 1.0),
        ("move", 2.0),
        ("illum_off",),
    ], f"unexpected event order: {events}"


def test_record_restores_camera_mode_and_stage_z_when_plane_raises(tmp_path):
    """Finding 2: when _record_one_plane raises (fail-fast), the post-loop
    camera-mode restore + stage-Z restore + settle must still run (the stage
    must not be left at a plane offset), illumination must still turn off, and
    the RuntimeError must still propagate."""
    pytest.importorskip("tensorstore")
    from squid.abc import CameraAcquisitionMode

    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=True, zstack_enabled=False, recording_Nz=3, recording_dz_um=1.0
    )

    events = []
    call_count = {"n": 0}

    def fake_record_one_plane(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated fail-fast")
        return 0

    worker._record_one_plane = fake_record_one_plane

    orig_off = live_controller.turn_off_illumination

    def off_spy():
        events.append("illum_off")
        return orig_off()

    live_controller.turn_off_illumination = off_spy

    orig_move_z_to = scope.stage.move_z_to
    restore_calls = []

    def move_z_to_spy(z_mm):
        restore_calls.append(z_mm)
        return orig_move_z_to(z_mm)

    scope.stage.move_z_to = move_z_to_spy

    z_ref = scope.stage.get_pos().z_mm

    with pytest.raises(RuntimeError, match="simulated fail-fast"):
        _record(worker, z_ref)

    assert events == ["illum_off"], "illumination must still be turned off on the raising path"
    assert restore_calls and restore_calls[-1] == pytest.approx(
        z_ref
    ), "stage-Z restore must still run after _record_one_plane raises"
    assert (
        scope.camera.get_acquisition_mode() == CameraAcquisitionMode.SOFTWARE_TRIGGER
    ), "camera acquisition mode must be restored to SOFTWARE_TRIGGER after a raising plane"


def test_build_objective_info_reads_objective_and_camera():
    from unittest.mock import MagicMock
    from control.core.record_zstack_controller import _build_objective_info

    objective_store = MagicMock()
    objective_store.current_objective = "20x"
    objective_store.objectives_dict = {"20x": {"magnification": 20.0}}
    objective_store.get_pixel_size_factor.return_value = 1.0

    camera = MagicMock()
    camera.get_binning.return_value = (2, 2)
    camera.get_pixel_size_binned_um.return_value = 0.5

    info = _build_objective_info(objective_store, camera)

    assert info["name"] == "20x"
    assert info["magnification"] == 20.0
    assert info["camera_binning"] == [2, 2]
    assert info["pixel_size_um"] == pytest.approx(0.5)


def test_build_objective_info_handles_missing_camera():
    from unittest.mock import MagicMock
    from control.core.record_zstack_controller import _build_objective_info

    objective_store = MagicMock()
    objective_store.current_objective = "10x"
    objective_store.objectives_dict = {}

    info = _build_objective_info(objective_store, None)

    assert info["name"] == "10x"
    assert info["camera_binning"] is None
    assert info["pixel_size_um"] is None


def _yaml_channel(name="BF LED matrix full", exposure=50.0):
    from control.models.acquisition_config import AcquisitionChannel, CameraSettings, IlluminationSettings

    return AcquisitionChannel(
        name=name,
        camera_settings=CameraSettings(exposure_time_ms=exposure, gain_mode=0.0),
        illumination_settings=IlluminationSettings(intensity=50.0),
    )


def test_save_record_zstack_yaml_writes_full_schema(tmp_path):
    """The file a Time at XY run (or Save Settings) leaves behind, key for key."""
    from control.core.record_zstack_controller import _save_record_zstack_yaml

    params = RecordZStackAcquisitionParameters(
        base_path=str(tmp_path),
        experiment_id="my_exp",
        Nt=3,
        dt_s=5.0,
        use_laser_af=True,
        use_contrast_af=True,
        use_focus_map=True,
        focus_map=object(),  # never serialised
        region_laser_af_offsets={"A1": 2.0},  # never serialised
        channels=[
            ChannelPlan(
                channel=_yaml_channel(),
                fps=15.0,
                duration_s=2.0,
                zi=True,
                r=True,
                zf=False,
                geometry=CameraGeometry(binning=2, crop_w=1024, crop_h=512, pixel_format="MONO8"),
            ),
            ChannelPlan(channel=_yaml_channel("Fluorescence 488 nm Ex"), zf=True),
        ],
        zstack=ZStackPlan(mode="Relative", dz_um=0.5, nz=9, z_range_um=4.0, z_min_mm=1.0, z_max_mm=1.2),
        record_slices=RecordSliceSelection(roles=["top", "bottom"], indices=[5, 2]),
        xy_mode="Select Wells",
        scan_size_mm=1.2,
        overlap_percent=10.0,
    )
    scan_coordinates = type(
        "FakeScanCoordinates", (), {"region_centers": {"A1": [1.0, 2.0, 0.1]}, "region_shapes": {"A1": "Square"}}
    )()
    yaml_path = tmp_path / "acquisition.yaml"
    objective_info = {"name": "20x", "magnification": 20.0, "pixel_size_um": 0.376, "camera_binning": [1, 1]}

    _save_record_zstack_yaml(params, str(yaml_path), scan_coordinates, objective_info)

    import yaml as pyyaml

    assert yaml_path.read_text().startswith("# Time at XY Acquisition Parameters - my_exp\n\n")
    data = pyyaml.safe_load(yaml_path.read_text())
    assert list(data) == [
        "acquisition",
        "objective",
        "time_series",
        "autofocus",
        "channels",
        "record_slices",
        "z_stack",
        "wellplate_scan",
    ]
    assert list(data["acquisition"]) == ["experiment_id", "start_time", "widget_type", "xy_mode"]
    assert data["acquisition"]["widget_type"] == "record_zstack"
    assert data["acquisition"]["xy_mode"] == "Select Wells"
    assert data["objective"] == objective_info
    assert data["time_series"] == {"nt": 3, "delta_t_s": 5.0}
    assert data["autofocus"] == {"laser_af": True, "contrast_af": True, "use_focus_map": True}
    first, second = data["channels"]
    assert list(first) == ["channel", "fps", "duration_s", "zi", "r", "zf", "camera"]
    assert first["channel"]["name"] == "BF LED matrix full"
    assert first["channel"]["camera_settings"]["exposure_time_ms"] == 50.0
    assert (first["fps"], first["duration_s"], first["zi"], first["r"], first["zf"]) == (15.0, 2.0, True, True, False)
    assert first["camera"] == {
        "binning": 2,
        "crop_width": 1024,
        "crop_height": 512,
        "pixel_format": "MONO8",
        "pixel_size_um": pytest.approx(0.752),  # the objective's pixel size at this row's binning
    }
    assert list(second) == ["channel", "fps", "duration_s", "zi", "r", "zf"]  # no camera key when nothing is pinned
    assert data["record_slices"] == {"roles": ["bottom", "top"], "indices": [2, 5]}
    assert data["z_stack"] == {
        "mode": "Relative",
        "dz_um": 0.5,
        "nz": 9,
        "z_range_um": 4.0,
        "z_min_mm": 1.0,
        "z_max_mm": 1.2,
    }
    assert data["wellplate_scan"]["scan_size_mm"] == 1.2
    assert data["wellplate_scan"]["regions"] == [{"name": "A1", "center_mm": [1.0, 2.0, 0.1], "shape": "Square"}]


def test_save_record_zstack_yaml_round_trips_through_the_loader(tmp_path):
    """Save -> parse -> rebuild gives back the same rows, z plan and record planes."""
    from control.acquisition_yaml_loader import parse_acquisition_yaml
    from control.core.record_zstack_controller import _save_record_zstack_yaml

    params = RecordZStackAcquisitionParameters(
        base_path=str(tmp_path),
        experiment_id="rt",
        Nt=2,
        dt_s=30.0,
        use_contrast_af=True,
        channels=[
            ChannelPlan(channel=_yaml_channel(), fps=40.0, duration_s=6.0, r=True, geometry=CameraGeometry(binning=2)),
            ChannelPlan(channel=_yaml_channel("Fluorescence 488 nm Ex", 80.0), zi=True, zf=True),
        ],
        zstack=ZStackPlan(mode="Set Range", dz_um=2.0, nz=6, z_range_um=4.0, z_min_mm=1.0, z_max_mm=1.01),
        record_slices=RecordSliceSelection(roles=["mid"], indices=[4]),
        xy_mode="Current Position",
    )
    path = tmp_path / "acquisition.yaml"
    _save_record_zstack_yaml(params, str(path), None, {"name": "20x", "pixel_size_um": 0.4, "camera_binning": [1, 1]})

    parsed = parse_acquisition_yaml(str(path))

    assert parsed.legacy_schema is False
    assert [ChannelPlan.from_yaml_dict(row) for row in parsed.channels] == params.channels
    assert (
        ZStackPlan(
            mode=parsed.z_mode,
            dz_um=parsed.z_dz_um,
            nz=parsed.z_nz,
            z_range_um=parsed.z_range_um,
            z_min_mm=parsed.z_min_mm,
            z_max_mm=parsed.z_max_mm,
        )
        == params.zstack
    )
    assert (parsed.record_slice_roles, parsed.record_slice_indices) == (["mid"], [4])
    assert (parsed.nt, parsed.delta_t_s) == (2, 30.0)
    assert (parsed.laser_af, parsed.contrast_af, parsed.use_focus_map) == (False, True, False)
    assert parsed.xy_mode == "Current Position" and parsed.wellplate_regions is None


def test_save_record_zstack_yaml_omits_wellplate_scan_for_current_position(tmp_path):
    from control.core.record_zstack_controller import _save_record_zstack_yaml

    params = RecordZStackAcquisitionParameters(
        base_path=str(tmp_path), experiment_id="my_exp", xy_mode="Current Position"
    )
    yaml_path = tmp_path / "acquisition.yaml"
    _save_record_zstack_yaml(params, str(yaml_path))

    import yaml as pyyaml

    data = pyyaml.safe_load(yaml_path.read_text())
    assert "wellplate_scan" not in data


def test_save_record_zstack_yaml_raises_on_write_failure(tmp_path):
    """Final-review Finding 3: _save_record_zstack_yaml() must let a real write
    failure (e.g. target directory doesn't exist) propagate rather than swallowing
    it internally. The two real call sites (run_acquisition()'s snapshot and the
    Save Settings button handler) each already have their own appropriate
    try/except one level up; swallowing here just made the button handler's
    warning dialog unreachable."""
    from control.core.record_zstack_controller import _save_record_zstack_yaml

    params = RecordZStackAcquisitionParameters(base_path=str(tmp_path), experiment_id="my_exp")
    # Parent directory doesn't exist -> open() raises FileNotFoundError (an OSError).
    yaml_path = tmp_path / "does_not_exist" / "acquisition.yaml"

    with pytest.raises(OSError):
        _save_record_zstack_yaml(params, str(yaml_path))


def test_run_acquisition_survives_snapshot_write_failure(tmp_path, monkeypatch):
    """Final-review Finding 3: run_acquisition()'s own try/except around
    _save_record_zstack_yaml (a real acquisition's settings snapshot) must still
    degrade gracefully -- i.e. NOT raise out of run_acquisition() -- now that the
    inner swallow inside _save_record_zstack_yaml itself has been removed."""
    from unittest.mock import MagicMock
    import control.core.record_zstack_controller as rzc

    class DummyWorker:
        def __init__(self, **kwargs):
            pass

        def run(self):
            pass

    monkeypatch.setattr("control.core.record_zstack_worker.RecordZStackWorker", DummyWorker)

    def _raise_on_save(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(rzc, "_save_record_zstack_yaml", _raise_on_save)

    def _write_channels_yaml(output_dir, **kw):
        (Path(output_dir) / "acquisition_channels.yaml").write_text("channels: []\n")

    microscope = MagicMock()
    microscope.config_repo.save_acquisition_output.side_effect = _write_channels_yaml
    microscope.camera.get_binning.return_value = (1, 1)
    microscope.camera.get_pixel_size_binned_um.return_value = 0.5
    objective_store = MagicMock()
    objective_store.current_objective = "20x"
    objective_store.objectives_dict = {}
    objective_store.get_pixel_size_factor.return_value = 1.0
    callbacks = MagicMock()

    controller = rzc.RecordZStackController(
        microscope=microscope,
        live_controller=MagicMock(),
        laser_autofocus_controller=None,
        objective_store=objective_store,
        scan_coordinates=None,
        callbacks=callbacks,
    )
    params = rzc.RecordZStackAcquisitionParameters(
        base_path=str(tmp_path), experiment_id="my_exp", channels=[ChannelPlan(channel=_yaml_channel(), zf=True)]
    )

    # Must not raise, even though _save_record_zstack_yaml raises internally.
    controller.run_acquisition(params)
    controller.join(timeout=5.0)

    experiment_dir = next(tmp_path.iterdir())
    assert (experiment_dir / "acquisition_channels.yaml").exists()
    # The failed snapshot must not have produced a (possibly partial) acquisition.yaml.
    assert not (experiment_dir / "acquisition.yaml").exists()


def test_run_acquisition_writes_both_yaml_files(tmp_path, monkeypatch):
    """acquisition_channels.yaml (existing) and acquisition.yaml (new) both land in the experiment dir.

    Stubs RecordZStackWorker entirely (a no-op .run()) so this test only exercises the
    synchronous setup code in run_acquisition() — the YAML-writing calls happen before the
    worker/thread are created. Exercising the real worker with mocked hardware would be slow
    and flaky; that's covered by this module's existing worker-level tests instead.
    """
    from unittest.mock import MagicMock
    import control.core.record_zstack_controller as rzc

    class DummyWorker:
        def __init__(self, **kwargs):
            pass

        def run(self):
            pass

    # run_acquisition() does `from control.core.record_zstack_worker import RecordZStackWorker`
    # as a local import at call time, so patching the source module's attribute (not
    # record_zstack_controller's namespace) is what actually intercepts it.
    monkeypatch.setattr("control.core.record_zstack_worker.RecordZStackWorker", DummyWorker)

    def _write_channels_yaml(output_dir, **kw):
        # microscope is a full MagicMock, so save_acquisition_output's real disk write
        # (which the existing acquisition_channels.yaml tests exercise for real) doesn't
        # happen here; write a stand-in so the "both files exist" assertion reflects the
        # real run_acquisition() call order rather than the (irrelevant) file contents.
        (Path(output_dir) / "acquisition_channels.yaml").write_text("channels: []\n")

    microscope = MagicMock()
    microscope.config_repo.save_acquisition_output.side_effect = _write_channels_yaml
    microscope.camera.get_binning.return_value = (1, 1)
    microscope.camera.get_pixel_size_binned_um.return_value = 0.5
    objective_store = MagicMock()
    objective_store.current_objective = "20x"
    objective_store.objectives_dict = {}
    objective_store.get_pixel_size_factor.return_value = 1.0
    callbacks = MagicMock()

    controller = rzc.RecordZStackController(
        microscope=microscope,
        live_controller=MagicMock(),
        laser_autofocus_controller=None,
        objective_store=objective_store,
        scan_coordinates=None,
        callbacks=callbacks,
    )
    params = rzc.RecordZStackAcquisitionParameters(
        base_path=str(tmp_path), experiment_id="my_exp", channels=[ChannelPlan(channel=_yaml_channel(), zf=True)]
    )
    controller.run_acquisition(params)
    controller.join(timeout=5.0)

    experiment_dir = next(tmp_path.iterdir())
    assert (experiment_dir / "acquisition_channels.yaml").exists()
    assert (experiment_dir / "acquisition.yaml").exists()


def test_record_routes_sequentially_when_camera_paces_and_downsamples_when_it_cannot(tmp_path, monkeypatch):
    """The router regime follows what set_frame_rate reports with the stream running: achievable <=
    requested (the camera paces itself, or the request is its maximum) -> every frame is wanted and
    slots are sequential; achievable > requested (no hardware pacing) -> downsample by arrival."""
    pytest.importorskip("tensorstore")
    import control.core.record_zstack_worker as rzw

    captured = {}

    class _SpyRouter(rzw.RecordingRouter):
        def __init__(self, fps, paced=False, **kw):
            captured["fps"] = fps
            super().__init__(fps, paced=paced, **kw)

        def set_paced_from_rate(self, running_fps):
            super().set_paced_from_rate(running_fps)
            captured["paced"] = self.paced

    monkeypatch.setattr(rzw, "RecordingRouter", _SpyRouter)

    # 1) camera honours the hint (simulated camera clamps to the request): paced.
    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path / "paced", recording_enabled=True, zstack_enabled=False
    )
    worker.run()
    assert not aborted["v"]
    assert captured["paced"] is True and captured["fps"] == 10.0

    # 2) camera reports it free-runs faster than requested: downsample regime.
    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path / "free", recording_enabled=True, zstack_enabled=False
    )
    monkeypatch.setattr(type(scope.camera), "set_frame_rate", lambda self, fps: 100.0)
    worker.run()
    assert not aborted["v"]
    assert captured["paced"] is False and captured["fps"] == 10.0

    # 3) camera is the limit (reports a lower achievable rate): paced at that rate.
    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path / "limited", recording_enabled=True, zstack_enabled=False
    )
    monkeypatch.setattr(type(scope.camera), "set_frame_rate", lambda self, fps: 5.0)
    worker.run()
    assert not aborted["v"]
    assert captured["paced"] is True and captured["fps"] == 5.0


# ---------------------------------------------------------------------------
# Task 3: live-preview tap threaded through the worker.
#
# The recording phase must invoke a plain (Qt-free) display callable, throttled
# by display_fps. The z-stack phase must invoke the per-plane signal_new_image
# callback once per plane x channel x FOV x timepoint.
# ---------------------------------------------------------------------------


def test_recording_phase_calls_display_fn(tmp_path):
    """The recording phase must invoke the plain display callable (throttled).
    display_fps=1000 -> effectively every frame; assert at least one call."""
    pytest.importorskip("tensorstore")
    import numpy as np

    shown = []
    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path,
        recording_enabled=True,
        zstack_enabled=False,
        display_frame_fn=shown.append,
        display_fps=1000.0,
        duration_s=0.3,
    )
    worker.run()
    assert len(shown) >= 1
    assert isinstance(shown[0], np.ndarray)


def test_zstack_phase_invokes_signal_new_image(tmp_path):
    """Pin the per-plane signal_new_image callback (was silently wired to a
    no-op before the preview feature): one call per plane x channel x FOV x t."""
    pytest.importorskip("tensorstore")
    import dataclasses
    from control.core.multi_point_controller import NoOpCallbacks

    new_image_calls = []
    callbacks = dataclasses.replace(
        NoOpCallbacks, signal_new_image=lambda frame, info: new_image_calls.append(frame.frame.shape)
    )
    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path,
        recording_enabled=False,
        zstack_enabled=True,
        zstack_channel_slice=slice(0, 2),
        callbacks=callbacks,
        z_min_um=-1.0,
        z_max_um=1.0,  # offsets -1, 0, 1 -> 3 planes
    )
    worker.run()
    assert len(new_image_calls) == 3 * 2  # planes x channels x 1 FOV x 1 timepoint


def test_controller_rejects_a_run_with_nothing_ticked():
    """A table with no Zi / R / Zf ticked would start an acquisition that visits every FOV and does nothing."""
    from control.core.record_zstack_controller import RecordZStackController

    params = RecordZStackAcquisitionParameters(
        base_path="/tmp", experiment_id="x", channels=[ChannelPlan(channel=_yaml_channel())]
    )
    with pytest.raises(ValueError, match="Zi, R, or Zf"):
        RecordZStackController.run_acquisition(object(), params)


def test_resolve_effective_fps_bounds_the_request_by_the_camera_maximum():
    from unittest.mock import MagicMock

    from control.core.record_zstack_controller import resolve_effective_fps

    cam = MagicMock()
    cam.get_max_frame_rate.return_value = 28.0
    assert resolve_effective_fps(cam, 30.0, 2.0) == 28.0
    assert resolve_effective_fps(cam, 10.0, 2.0) == 10.0
    cam.get_max_frame_rate.assert_called_with(2.0)
    # No query, or a failing one: the request stands.
    assert resolve_effective_fps(object(), 30.0, 2.0) == 30.0
    cam.get_max_frame_rate.side_effect = RuntimeError("no sdk")
    assert resolve_effective_fps(cam, 30.0, 2.0) == 30.0


def test_controller_resolves_effective_fps_before_the_run_and_records_it(tmp_path, monkeypatch):
    """The rate is fixed in run_acquisition, before the worker exists: params.effective_fps is
    set, acquisition.yaml carries it, and the worker sizes every store from it."""
    pytest.importorskip("tensorstore")
    import yaml
    import control._def
    import tests.control.test_stubs as ts
    from control.core.multi_point_controller import NoOpCallbacks
    from control.core.record_zstack_controller import RecordZStackController
    from control.core.scan_coordinates import ScanCoordinates

    control._def.FILE_SAVING_OPTION = control._def.FileSavingOption.ZARR_V3
    scope = _build_simulated_microscope(64, 48)
    live_controller = ts.get_test_live_controller(scope, scope.objective_store.default_objective)
    laser_af = ts.get_test_laser_autofocus_controller(scope)
    channels = live_controller.get_channels(scope.objective_store.default_objective)
    rec_channel = channels[0].model_copy(deep=True)
    rec_channel.camera_settings.exposure_time_ms = 200.0  # sim camera: max = 1000/(200+3) ~ 4.9 fps

    z_cfg = scope.stage.get_config().Z_AXIS
    scope.stage.move_z_to((z_cfg.MAX_POSITION + z_cfg.MIN_POSITION) / 2.0)
    x0 = scope.stage.get_config().X_AXIS.MIN_POSITION + 1.0
    y0 = scope.stage.get_config().Y_AXIS.MIN_POSITION + 1.0
    scan = ScanCoordinates(objectiveStore=scope.objective_store, stage=scope.stage, camera=scope.camera)
    scan.region_fov_coordinates = {"A1": [(x0, y0)]}

    controller = RecordZStackController(
        microscope=scope,
        live_controller=live_controller,
        laser_autofocus_controller=laser_af,
        objective_store=scope.objective_store,
        scan_coordinates=scan,
        callbacks=NoOpCallbacks,
    )
    fast_channel = channels[1].model_copy(deep=True)
    fast_channel.camera_settings.exposure_time_ms = 1.0
    params = RecordZStackAcquisitionParameters(
        base_path=str(tmp_path),
        experiment_id="resolved",
        channels=[
            ChannelPlan(channel=rec_channel, fps=30.0, duration_s=1.0, r=True),
            ChannelPlan(channel=fast_channel, fps=30.0, duration_s=0.2, r=True),  # within the camera's reach
        ],
    )
    expected = scope.camera.get_max_frame_rate(200.0)
    assert expected < 30.0  # precondition: the sim camera cannot do 30 fps at 200 ms
    controller.run_acquisition(params)
    # resolved per row, before the worker ran
    assert params.channels[0].effective_fps == pytest.approx(expected)
    assert params.channels[1].effective_fps == pytest.approx(30.0)
    controller.join(timeout=120)
    assert not controller.acquisition_in_progress()
    controller.close()

    exp_dir = next(d for d in tmp_path.iterdir() if d.is_dir())
    saved = yaml.safe_load(open(exp_dir / "acquisition.yaml"))
    assert saved["channels"][0]["fps"] == 30.0
    assert saved["channels"][0]["effective_fps"] == pytest.approx(expected)
    assert saved["channels"][1]["effective_fps"] == pytest.approx(30.0)

    slow_dir = exp_dir / "recording" / "t0" / "A1" / rec_channel.name.replace(" ", "_")
    sq = _recording_attrs(slow_dir / "fov_0_z0.ome.zarr")
    assert sq["shape"][0] == max(1, frame_count(expected, 1.0))
    assert sq["requested_fps"] == 30.0 and sq["effective_fps"] == pytest.approx(expected)
    # The store's spacing is the resolved one unless the simulated camera delivered more than 2%
    # off it, in which case the measured spacing is stamped and the resolved one kept alongside.
    assert sq.get("nominal_time_increment_s", sq["time_increment_s"]) == pytest.approx(1.0 / expected)


def _zstack_squid_attrs(root):
    """The _squid attributes of the one OME group zarr.json of the z-stack store under root."""
    import tests.control.test_stubs as ts

    (attrs,) = ts.zarr_squid_attrs(root)
    return attrs


def test_zstack_uses_the_in_process_runner_and_seals_the_store(tmp_path):
    """The z-stack's one runner is the in-process one; its store ends acquisition_complete=True."""
    pytest.importorskip("tensorstore")
    from control.core.in_process_zarr_runner import InProcessZarrRunner

    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=False, zstack_enabled=True
    )
    assert isinstance(worker._job_runners[0][1], InProcessZarrRunner)
    worker.run()
    assert _zstack_squid_attrs(Path(tmp_path) / "state_restore" / "zstack_final")["acquisition_complete"] is True


def test_abort_mid_zstack_seals_store_aborted(tmp_path):
    """Stop during the z-stack: the store says aborted, not complete."""
    pytest.importorskip("tensorstore")

    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=False, zstack_enabled=True, zstack_channel_slice=slice(0, 2)
    )
    original = worker.acquire_camera_image
    calls = []

    def abort_after_first(*args, **kwargs):
        original(*args, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            aborted["v"] = True

    worker.acquire_camera_image = abort_after_first
    worker.run()
    attrs = _zstack_squid_attrs(Path(tmp_path) / "state_restore" / "zstack_final")
    assert attrs["acquisition_complete"] is False and attrs.get("aborted") is True


def test_zarr_write_error_aborts_the_acquisition(tmp_path, monkeypatch, caplog):
    """A failed z-stack write must end the run as an error and seal the store incomplete, not finish quietly."""
    pytest.importorskip("tensorstore")
    import logging
    import tests.control.test_stubs as ts

    ts.make_zarr_writes_fail(monkeypatch)
    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=False, zstack_enabled=True, zstack_channel_slice=slice(0, 2)
    )
    with caplog.at_level(logging.ERROR):
        worker.run()
    assert aborted["v"] is True and worker._abort_cause == "error"
    assert any("disk full" in r.getMessage() for r in caplog.records)
    assert _zstack_squid_attrs(Path(tmp_path) / "state_restore" / "zstack_final")["acquisition_complete"] is False


def test_three_record_zstack_acquisitions_all_sealed(tmp_path, monkeypatch):
    """The data loss of PR 564: acquisition 3 used to lose its z-stack. No subprocess is started at all now."""
    pytest.importorskip("tensorstore")
    from control.core import job_processing

    started = []
    monkeypatch.setattr(job_processing.JobRunner, "start", lambda self: started.append(self))
    scope, live_controller, channels, controller = _build_controller_harness()
    try:
        for i in range(1, 4):
            base = tmp_path / f"acq{i}"
            base.mkdir()
            controller.run_acquisition(
                _params(
                    base, channels, experiment_id="b2b", duration_s=0.3, zstack_channels=channels[:2], z_min_um=-1.0
                )
            )
            controller.join(timeout=120)
            assert not controller.acquisition_in_progress(), f"acquisition {i} did not finish"
            (experiment_dir,) = [d for d in base.iterdir() if d.is_dir()]
            attrs = _zstack_squid_attrs(experiment_dir / "zstack_final")
            assert attrs["acquisition_complete"] is True, f"acquisition {i}"
    finally:
        controller.close()
    assert started == []


def test_recording_switches_to_paced_routing_when_the_camera_only_paces_once_streaming(tmp_path, monkeypatch, caplog):
    """The pacing decision is made before streaming.  A camera that rejects the stopped-state rate write
    (ToupTek after a binning change) reports its free-run maximum there and only paces itself once the
    stream runs; the recording must then route sequentially, or at-rate jitter seals it incomplete."""
    pytest.importorskip("tensorstore")
    import logging
    import tests.control.test_stubs as ts

    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=True, zstack_enabled=False
    )
    real_set_frame_rate = scope.camera.set_frame_rate

    def late_pacing_set_frame_rate(fps):
        real_set_frame_rate(fps)
        return fps if scope.camera.get_is_streaming() else 1000.0

    monkeypatch.setattr(scope.camera, "set_frame_rate", late_pacing_set_frame_rate)
    with caplog.at_level(logging.INFO):
        worker.run()
    assert not aborted["v"]
    assert any("routed camera-paced, sequential slots" in r.getMessage() for r in caplog.records)
    (attrs,) = ts.zarr_squid_attrs(worker.experiment_path)
    assert attrs["acquisition_complete"] is True


def _shorten_recording_finalize(monkeypatch, timeout_s):
    """Give every per-plane finalize() a short join budget, as a slow disk would exhaust the real one."""
    from control.core.streaming_capture import RecordingWriter

    real_finalize = RecordingWriter.finalize
    monkeypatch.setattr(RecordingWriter, "finalize", lambda self, timeout_s=timeout_s: real_finalize(self, timeout_s))


def test_late_recording_write_error_ends_the_run_as_error(tmp_path, monkeypatch, caplog):
    """A recording whose drain outlives finalize() still fails the run when its write errors surface
    afterwards, and .done is written only once the store is sealed."""
    pytest.importorskip("tensorstore")
    import logging
    import threading
    import tests.control.test_stubs as ts
    from control.core.record_zstack_worker import RecordZStackWorker
    from control.core.zarr_writer import ZarrWriter

    _shorten_recording_finalize(monkeypatch, 0.2)
    monkeypatch.setattr(RecordZStackWorker, "DRAIN_SETTLE_TIMEOUT_S", 5.0)
    release = threading.Event()  # the writes complete, failing, 0.6 s after the first one is submitted:
    timer = []  # after finalize() has given up its 0.2 s wait, before the end-of-run settle gives up

    def late_failing_submit(self, image, t, c, z, fov=None):
        if not timer:
            timer.append(threading.Timer(0.6, release.set))
            timer[0].start()
        return ts.GatedFuture(release, RuntimeError("disk error after finalize"))

    monkeypatch.setattr(ZarrWriter, "submit_frame", late_failing_submit)
    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=True, zstack_enabled=False
    )
    with caplog.at_level(logging.ERROR):
        worker.run()
    assert worker._abort_cause == "error"
    assert any("after finalize() returned" in r.getMessage() for r in caplog.records)
    assert os.path.exists(os.path.join(worker.experiment_path, ".done"))  # the directory is final: store sealed
    (attrs,) = ts.zarr_squid_attrs(worker.experiment_path)
    assert attrs["acquisition_complete"] is False


def test_run_withholds_done_while_a_recording_is_still_being_written(tmp_path, monkeypatch, caplog):
    """A store still being written when the run ends is not final: no .done, and the run ends as an error."""
    pytest.importorskip("tensorstore")
    import logging
    import threading
    import tests.control.test_stubs as ts
    from control.core.record_zstack_worker import RecordZStackWorker

    release = threading.Event()
    _shorten_recording_finalize(monkeypatch, 0.2)
    monkeypatch.setattr(RecordZStackWorker, "DRAIN_SETTLE_TIMEOUT_S", 0.3)
    ts.stub_zarr_writer_io(monkeypatch, lambda self, image, t, c, z, fov=None: ts.GatedFuture(release))
    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=True, zstack_enabled=False
    )
    try:
        with caplog.at_level(logging.ERROR):
            worker.run()
    finally:
        release.set()
    assert worker._abort_cause == "error"
    assert any("still being written" in r.getMessage() for r in caplog.records)
    assert not os.path.exists(os.path.join(worker.experiment_path, ".done"))


def test_zstack_stops_streaming_when_callback_registration_fails(tmp_path, monkeypatch):
    """start_streaming() succeeded but add_frame_callback() raised: the camera must not be left streaming."""
    pytest.importorskip("tensorstore")

    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=False, zstack_enabled=True
    )

    def refuse(cb):
        raise RuntimeError("no callback slot")

    monkeypatch.setattr(scope.camera, "add_frame_callback", refuse)
    with pytest.raises(RuntimeError, match="no callback slot"):
        worker.zstack(0, "A1", 0, scope.stage.get_pos().z_mm, phase="final")
    assert not scope.camera.get_is_streaming()


def test_record_restores_z_and_camera_mode_when_illumination_fails(tmp_path, monkeypatch):
    """Illumination raising after the first-plane Z move must still restore Z and software-trigger mode."""
    pytest.importorskip("tensorstore")
    from squid.abc import CameraAcquisitionMode

    scope, live_controller, channels, worker, aborted = _build_worker_harness(
        tmp_path, recording_enabled=True, zstack_enabled=False, recording_bottom_z_offset_um=5.0
    )
    real_move_z_to = scope.stage.move_z_to
    moves = []

    def move_z_to_spy(z_mm):
        moves.append(z_mm)
        return real_move_z_to(z_mm)

    scope.stage.move_z_to = move_z_to_spy

    def fail():
        raise RuntimeError("illumination")

    monkeypatch.setattr(live_controller, "turn_on_illumination", fail)
    z_ref = scope.stage.get_pos().z_mm
    with pytest.raises(RuntimeError, match="illumination"):
        _record(worker, z_ref)
    assert moves and moves[-1] == pytest.approx(z_ref), "stage-Z restore must run when illumination fails"
    assert scope.camera.get_acquisition_mode() == CameraAcquisitionMode.SOFTWARE_TRIGGER


def test_record_zstack_run_writes_the_watchdog_breadcrumb(tmp_path):
    """Time at XY leaves the run.json the multipoint acquisition does, started before the worker and
    ended with the run's reason, so the acquisition watchdog covers it."""
    pytest.importorskip("tensorstore")
    import squid.acquisition_state as ast

    scope, live_controller, channels, controller = _build_controller_harness()
    base = tmp_path / "acq"
    base.mkdir()
    try:
        controller.run_acquisition(
            _params(base, channels, experiment_id="wd", zstack_channels=channels[:2], z_min_um=-1.0)
        )
        controller.join(timeout=120)
    finally:
        controller.close()
    (experiment_dir,) = [d for d in base.iterdir() if d.is_dir()]
    rec = ast.read_run(os.environ["SQUID_WATCHDOG_STATE_DIR"])  # the autouse fixture's per-test state dir
    assert rec is not None, "no run.json written for a Time at XY run"
    assert rec["status"] == "ended" and rec["reason"] == "completed"
    assert Path(rec["output_path"]) == experiment_dir
    assert rec["expected"] == {"timepoints": 1, "regions": 1, "fovs": 1, "channels": 2, "z": 3}


# ---------------------------------------------------------------------------
# Time at XY: per-FOV order, per-channel camera settings, focus, frame-rate test
# ---------------------------------------------------------------------------


def _build_plan_worker(tmp_path, rows_fn, zstack=None, record_slices=None, n_fovs=1, **worker_kwargs):
    """A worker on the simulated setup for the rows rows_fn(channels) builds; returns
    (scope, live_controller, channels, worker, aborted)."""
    from control.core.multi_point_controller import NoOpCallbacks
    from control.core.record_zstack_worker import RecordZStackWorker

    param_fields = {
        k: worker_kwargs.pop(k) for k in list(worker_kwargs) if k in ("Nt", "use_laser_af", "use_contrast_af")
    }
    param_fields.update(worker_kwargs.pop("param_fields", {}))
    scope, live_controller, laser_af, channels, (x0, y0) = _build_simulated_setup()
    params = RecordZStackAcquisitionParameters(
        base_path=str(tmp_path),
        experiment_id="plan",
        channels=rows_fn(channels),
        zstack=zstack or ZStackPlan(nz=2, dz_um=1.0),
        record_slices=record_slices or RecordSliceSelection(roles=["bottom"]),
        **param_fields,
    )
    aborted = {"v": False}
    worker = RecordZStackWorker(
        scope=scope,
        live_controller=live_controller,
        laser_auto_focus_controller=laser_af,
        objective_store=scope.objective_store,
        params=params,
        callbacks=NoOpCallbacks,
        abort_requested_fn=lambda: aborted["v"],
        request_abort_fn=lambda: aborted.__setitem__("v", True),
        scan_region_fov_coords={"A1": [(x0 + 0.5 * i, y0) for i in range(n_fovs)]},
        **worker_kwargs,
    )
    return scope, live_controller, channels, worker, aborted


def test_per_fov_order_is_initial_stack_then_recordings_in_table_order_then_final_stack(tmp_path):
    """At every FOV of every timepoint: reference, Zi, one recording per R row in table order, Zf."""
    scope, live_controller, channels, worker, aborted = _build_plan_worker(
        tmp_path,
        lambda ch: [
            ChannelPlan(channel=ch[1], fps=20.0, duration_s=0.1, r=True),
            ChannelPlan(channel=ch[0], fps=10.0, duration_s=0.1, zi=True, r=True, zf=True),
            ChannelPlan(channel=ch[2], zi=True),
        ],
        n_fovs=2,
        Nt=2,
    )
    events = []
    worker.establish_reference = lambda region_id=None: events.append("ref") or scope.stage.get_pos().z_mm
    worker.zstack = lambda t, region, fov, z_ref, phase: events.append((t, fov, f"stack:{phase}"))
    worker.record = lambda t, region, fov, z_ref, plan, rec_idx: events.append((t, fov, f"rec{rec_idx}:{plan.name}"))

    worker.run()

    per_fov = lambda t, fov: [  # noqa: E731
        "ref",
        (t, fov, "stack:initial"),
        (t, fov, f"rec0:{channels[1].name}"),
        (t, fov, f"rec1:{channels[0].name}"),
        (t, fov, "stack:final"),
    ]
    assert events == per_fov(0, 0) + per_fov(0, 1) + per_fov(1, 0) + per_fov(1, 1)
    assert [c.name for c in worker.zi_channels] == [channels[0].name, channels[2].name]
    assert [c.name for c in worker.zf_channels] == [channels[0].name]
    assert worker.end_reason == "completed"


def test_a_camera_that_cannot_deliver_recording_frames_fails_the_run_before_anything_is_acquired(tmp_path):
    """The recordings are sized from a probe frame per camera setting at the start of the run: a colour
    frame (or none) must end the run there, not after the first FOV's initial z-stack."""
    scope, live_controller, channels, worker, aborted = _build_plan_worker(
        tmp_path, lambda ch: [ChannelPlan(channel=ch[0], fps=10.0, duration_s=0.1, zi=True, r=True)]
    )
    events = []
    worker.zstack = lambda *args, **kwargs: events.append("stack")
    real_move_xy = worker._move_xy
    worker._move_xy = lambda coord: events.append("move") or real_move_xy(coord)

    def colour_frame():
        raise ValueError("recording supports monochrome frames only")

    worker._probe_frame_shape = colour_frame
    worker.run()
    assert events == []
    assert aborted["v"] and worker.end_reason == "error"
    assert os.path.exists(os.path.join(worker.experiment_path, ".done")) is os.path.isdir(worker.experiment_path)


def test_recording_frames_are_probed_once_per_camera_setting(tmp_path):
    binned = CameraGeometry(binning=2)
    scope, live_controller, channels, worker, aborted = _build_plan_worker(
        tmp_path,
        lambda ch: [
            ChannelPlan(channel=ch[0], fps=10.0, duration_s=0.1, r=True),
            ChannelPlan(channel=ch[1], fps=10.0, duration_s=0.1, r=True, geometry=binned),
            ChannelPlan(channel=ch[2], fps=10.0, duration_s=0.1, r=True),  # same settings as the first row
        ],
        n_fovs=2,
    )
    probes = []
    real_probe = worker._probe_frame_shape
    worker._probe_frame_shape = lambda: probes.append(tuple(scope.camera.get_binning())) or real_probe()
    worker._record_one_plane = lambda *args, **kwargs: 0
    worker.run()
    assert probes == [(1, 1), (2, 2)]
    assert [shape[:2] for shape in worker._frame_shapes.values()] == [(48, 64), (24, 32)]


def test_abort_between_phases_stops_before_the_next_one(tmp_path):
    scope, live_controller, channels, worker, aborted = _build_plan_worker(
        tmp_path, lambda ch: [ChannelPlan(channel=ch[0], fps=10.0, duration_s=0.1, zi=True, r=True, zf=True)]
    )
    events = []
    worker.zstack = lambda t, region, fov, z_ref, phase: events.append(phase)

    def record_then_stop(*args):
        events.append("rec")
        aborted["v"] = True

    worker.record = record_then_stop
    worker.run()
    assert events == ["initial", "rec"]
    assert worker.end_reason == "user_abort"


def test_set_range_stack_visits_absolute_planes_whatever_the_reference(tmp_path):
    scope, live_controller, laser_af, channels, fov = _build_simulated_setup()
    z_mid = scope.stage.get_pos().z_mm
    scope, live_controller, channels, worker, aborted = _build_plan_worker(
        tmp_path,
        lambda ch: [ChannelPlan(channel=ch[0], zf=True)],
        zstack=ZStackPlan(mode="Set Range", dz_um=2.0, z_min_mm=z_mid + 0.010, z_max_mm=z_mid + 0.014),
    )
    z_ref = scope.stage.get_pos().z_mm  # not on any plane of the range
    moves = []
    real_move = worker.move_to_z_level
    worker.move_to_z_level = lambda z_mm: moves.append(z_mm) or real_move(z_mm)

    worker.zstack(0, "A1", 0, z_ref, phase="final")

    assert moves == pytest.approx([z_mid + 0.010, z_mid + 0.012, z_mid + 0.014, z_ref])


def test_stack_channels_are_grouped_by_camera_settings_into_separate_stores(tmp_path):
    """Rows with different binning / crop / pixel format can't share a (T, C, Z, Y, X) store: one store
    per group, under an extra <group> directory, only in a phase that has more than one group."""
    pytest.importorskip("tensorstore")
    import tensorstore as tstore

    binned = CameraGeometry(binning=2)
    scope, live_controller, channels, worker, aborted = _build_plan_worker(
        tmp_path,
        lambda ch: [
            ChannelPlan(channel=ch[0], zi=True),
            ChannelPlan(channel=ch[1], zi=True, zf=True, geometry=binned),
            ChannelPlan(channel=ch[2], zi=True),
        ],
    )
    from control.core.camera_geometry import resolve, snapshot_camera

    # A group's directory names its resolved settings: the run-start ROI for rows that pin nothing
    # ("roi"), and that ROI scaled to the new binning for a row that only pins binning.
    snapshot = snapshot_camera(scope.camera)
    fmt = snapshot.pixel_format.lower()
    native = f"bin1_roi_{fmt}"
    half = f"bin2_{snapshot.roi[2] // 2}x{snapshot.roi[3] // 2}_{fmt}"
    assert resolve(binned, snapshot).dir_name() == half
    assert [(g.subdir.replace(os.sep, "/"), [c.name for c in g.channels]) for g in worker._zi_groups] == [
        (f"zstack_initial/{native}", [channels[0].name, channels[2].name]),  # in order of first appearance
        (f"zstack_initial/{half}", [channels[1].name]),
    ]
    assert [(g.subdir, [c.name for c in g.channels]) for g in worker._zf_groups] == [
        ("zstack_final", [channels[1].name])  # a single group keeps the plain layout
    ]
    # The binned group's pixels are twice the size.
    assert worker._zi_groups[1].info.pixel_size_um == pytest.approx(2 * worker._zi_groups[0].info.pixel_size_um)

    worker.run()

    assert not aborted["v"] and worker.end_reason == "completed"
    base = Path(worker.experiment_path)
    assert _stack_dirs(base, "initial") == [
        f"zstack_initial/{native}/zarr/A1/fov_0.ome.zarr",
        f"zstack_initial/{half}/zarr/A1/fov_0.ome.zarr",
    ]
    assert _stack_dirs(base, "final") == ["zstack_final/zarr/A1/fov_0.ome.zarr"]

    def shape(store):
        spec = {"driver": "zarr3", "kvstore": {"driver": "file", "path": str(base / store / "0")}}
        return tuple(tstore.open(spec).result().shape)

    assert shape(f"zstack_initial/{native}/zarr/A1/fov_0.ome.zarr") == (1, 2, 2, 48, 64)
    assert shape(f"zstack_initial/{half}/zarr/A1/fov_0.ome.zarr") == (1, 1, 2, 24, 32)
    assert shape("zstack_final/zarr/A1/fov_0.ome.zarr") == (1, 1, 2, 24, 32)
    import tests.control.test_stubs as ts

    assert all(attrs["acquisition_complete"] is True for attrs in ts.zarr_squid_attrs(base))


@pytest.mark.parametrize("outcome", ["completed", "aborted", "failed"])
def test_camera_settings_are_restored_after_the_run(tmp_path, outcome):
    """A row's binning / crop / pixel format are applied for that row only; the camera is back the way
    the user had it when the run ends, however it ends."""
    pytest.importorskip("tensorstore")
    from control.core.camera_geometry import snapshot_camera

    pinned = CameraGeometry(binning=2, crop_w=16, crop_h=16)
    scope, live_controller, channels, worker, aborted = _build_plan_worker(
        tmp_path,
        lambda ch: [
            ChannelPlan(channel=ch[0], fps=10.0, duration_s=0.2, r=True, geometry=pinned),
            ChannelPlan(channel=ch[1], zf=True),
        ],
    )
    before = snapshot_camera(scope.camera)
    live_controller.set_microscope_mode(channels[2])  # what the user was looking at
    seen = []
    real = worker._record_one_plane

    def record_one_plane(*args, **kwargs):
        seen.append(snapshot_camera(scope.camera))
        if outcome == "failed":
            raise RuntimeError("simulated write failure")
        emitted = real(*args, **kwargs)
        if outcome == "aborted":
            aborted["v"] = True
        return emitted

    worker._record_one_plane = record_one_plane

    worker.run()

    assert seen and seen[0].binning == (2, 2) and seen[0].roi[2:] == (16, 16)  # the row did get its settings
    assert snapshot_camera(scope.camera) == before
    assert live_controller.currentConfiguration.name == channels[2].name
    assert worker.end_reason == {"completed": "completed", "aborted": "user_abort", "failed": "error"}[outcome]
    if outcome != "failed":
        (store,) = (Path(worker.experiment_path) / "recording").rglob("*.ome.zarr")
        attrs = _recording_attrs(store)
        # Sized from a frame delivered at the row's settings.  (The simulated camera bins its frames
        # but does not crop them to the hardware ROI, so the crop shows in the ROI readback above.)
        assert attrs["shape"][3:] == [24, 32]
        assert attrs["pixel_size_um"] == pytest.approx(2 * worker._pixel_size_um)
    if outcome == "completed":
        # The stack row inherits the user's settings, so its store has the full frame.
        assert _stack_dirs(worker.experiment_path, "final") == ["zstack_final/zarr/A1/fov_0.ome.zarr"]
        assert _zstack_squid_attrs(Path(worker.experiment_path) / "zstack_final")["shape"][3:] == [48, 64]


def test_a_row_with_its_own_camera_settings_resolves_its_rate_once_the_camera_is_in_them(tmp_path, monkeypatch):
    """get_max_frame_rate answers for the camera's current geometry, so such a row cannot be resolved
    before the run; the worker asks when the row first records, with the row's settings applied."""
    pytest.importorskip("tensorstore")
    pinned = CameraGeometry(binning=2)
    scope, live_controller, channels, worker, aborted = _build_plan_worker(
        tmp_path, lambda ch: [ChannelPlan(channel=ch[0], fps=30.0, duration_s=0.5, r=True, geometry=pinned)]
    )
    asked_at = []

    def max_rate(self, exposure_time_ms=None):
        asked_at.append(tuple(self.get_binning()))
        return 12.0 if tuple(self.get_binning()) == (2, 2) else 1000.0

    monkeypatch.setattr(type(scope.camera), "get_max_frame_rate", max_rate)
    worker.run()
    assert not aborted["v"]
    assert (2, 2) in asked_at
    (store,) = (Path(worker.experiment_path) / "recording").rglob("*.ome.zarr")
    attrs = _recording_attrs(store)
    assert attrs["requested_fps"] == 30.0 and attrs["effective_fps"] == pytest.approx(12.0)
    assert attrs["shape"][0] == 6


class _FakeLaserAF:
    def __init__(self, ok=True, has_reference=True):
        from types import SimpleNamespace

        self.laser_af_properties = SimpleNamespace(has_reference=has_reference)
        self.ok = ok
        self.calls = []

    def move_to_target(self, target_um):
        self.calls.append(("move_to_target", target_um))
        if isinstance(self.ok, Exception):
            raise self.ok
        return self.ok

    def apply_relative_offset_um(self, offset_um):
        self.calls.append(("offset", offset_um))


def test_laser_af_reference_applies_the_regions_offset(tmp_path):
    """Laser AF to the reference, then the region's captured offset as a relative move (the multipoint rule)."""
    scope, live_controller, channels, worker, aborted = _build_plan_worker(
        tmp_path,
        lambda ch: [ChannelPlan(channel=ch[0], zf=True)],
        use_laser_af=True,
        param_fields={"region_laser_af_offsets": {"A1": 1.5}},
    )
    worker.laser_af = _FakeLaserAF()
    z = worker.establish_reference("A1")
    assert worker.laser_af.calls == [("move_to_target", 0.0), ("offset", 1.5)]
    assert z == pytest.approx(scope.stage.get_pos().z_mm)
    worker.laser_af.calls.clear()
    worker.establish_reference("B7")  # no offset captured for this region
    assert worker.laser_af.calls == [("move_to_target", 0.0)]
    assert worker.laser_af_fallbacks == 0


@pytest.mark.parametrize("failure", [False, RuntimeError("no spot")])
def test_laser_af_failure_falls_back_to_the_current_z_and_is_counted(tmp_path, failure, caplog):
    import logging

    scope, live_controller, channels, worker, aborted = _build_plan_worker(
        tmp_path,
        lambda ch: [ChannelPlan(channel=ch[0], zf=True)],
        use_laser_af=True,
        param_fields={"region_laser_af_offsets": {"A1": 1.5}},
    )
    worker.laser_af = _FakeLaserAF(ok=failure)
    worker.zstack = lambda *args, **kwargs: None
    with caplog.at_level(logging.WARNING):
        worker.run()
    assert worker.laser_af.calls == [("move_to_target", 0.0)]  # no offset from an unanchored z
    assert worker.laser_af_fallbacks == 1
    assert any("fell back to the current Z at 1 FOV visit" in r.getMessage() for r in caplog.records)
    assert worker.end_reason == "completed"


def test_laser_af_without_a_reference_is_not_attempted(tmp_path):
    scope, live_controller, channels, worker, aborted = _build_plan_worker(
        tmp_path, lambda ch: [ChannelPlan(channel=ch[0], zf=True)], use_laser_af=True
    )
    worker.laser_af = _FakeLaserAF(has_reference=False)
    worker.establish_reference("A1")
    assert worker.laser_af.calls == []


class _FakeContrastAF:
    def __init__(self, camera):
        self.camera = camera
        self.calls = []

    def autofocus(self, focus_map_override=False):
        self.calls.append(("autofocus", focus_map_override, self.camera.get_is_streaming()))

    def wait_till_autofocus_has_completed(self):
        self.calls.append(("wait",))


@pytest.mark.parametrize(
    "zstack, use_laser_af, runs",
    [
        (ZStackPlan(nz=1), False, True),
        (ZStackPlan(mode="Relative", dz_um=1.0, z_range_um=2.0), False, True),
        (ZStackPlan(nz=3, dz_um=1.0), False, False),  # only with one plane or a centred stack
        (ZStackPlan(nz=1), True, False),  # laser AF takes precedence
    ],
)
def test_contrast_af_runs_only_when_it_applies(tmp_path, zstack, use_laser_af, runs):
    import control._def
    from control._def import TriggerMode

    scope, live_controller, laser_af, channels, fov = _build_simulated_setup()
    contrast_af = _FakeContrastAF(scope.camera)
    scope, live_controller, channels, worker, aborted = _build_plan_worker(
        tmp_path,
        lambda ch: [ChannelPlan(channel=ch[1], zf=True)],
        zstack=zstack,
        use_contrast_af=True,
        use_laser_af=use_laser_af,
        auto_focus_controller=None,
    )
    contrast_af.camera = scope.camera
    worker.autofocusController = contrast_af
    worker.laser_af = _FakeLaserAF()
    live_controller.set_trigger_mode(TriggerMode.CONTINUOUS)

    worker.establish_reference("A1")

    if runs:
        # The sweep runs on the AF channel, software-triggered, with the camera streaming; stopped again after.
        assert contrast_af.calls == [("autofocus", True, True), ("wait",)]
        assert live_controller.trigger_mode == TriggerMode.SOFTWARE
        assert live_controller.currentConfiguration.name == control._def.MULTIPOINT_AUTOFOCUS_CHANNEL
        assert not scope.camera.get_is_streaming()
    else:
        assert contrast_af.calls == []


def test_focus_map_z_is_put_into_the_fov_coordinates():
    """Use Focus Map: every FOV gets the fitted surface's z; a region the map does not know falls back
    to the global surface, and a FOV the map cannot answer for keeps its own coordinate."""
    scope, live_controller, channels, controller = _build_controller_harness()

    class _Map:
        def interpolate(self, x, y, region_id=None):
            if region_id == "current":
                raise ValueError("no fit for this region")
            if x > 100:
                raise RuntimeError("outside the fitted area")
            return 1.0 + 0.1 * x + (0.5 if region_id == "A1" else 0.0)

    coords = {"A1": [(1.0, 2.0), (2.0, 2.0, 9.9)], "current": [(3.0, 2.0)], "far": [(500.0, 2.0, 0.7)]}
    out = controller._apply_focus_map(_Map(), coords)

    assert out["A1"] == [(1.0, 2.0, pytest.approx(1.6)), (2.0, 2.0, pytest.approx(1.7))]
    assert out["current"] == [(3.0, 2.0, pytest.approx(1.3))]  # the global surface
    assert out["far"] == [(500.0, 2.0, 0.7)]
    # Only the acquisition's own copy is rewritten: what was passed in is untouched.
    assert coords["A1"] == [(1.0, 2.0), (2.0, 2.0, 9.9)]
    controller.close()


def test_run_acquisition_moves_each_fov_to_the_focus_map_z(tmp_path):
    pytest.importorskip("tensorstore")
    scope, live_controller, channels, controller = _build_controller_harness()
    z_mid = scope.stage.get_pos().z_mm

    class _Map:
        def interpolate(self, x, y, region_id=None):
            return z_mid + 0.02

    params = RecordZStackAcquisitionParameters(
        base_path=str(tmp_path),
        experiment_id="focus_map",
        use_focus_map=True,
        focus_map=_Map(),
        channels=[ChannelPlan(channel=channels[0], zf=True)],
    )
    z_moves = []
    real_move_z_to = scope.stage.move_z_to
    scope.stage.move_z_to = lambda z_mm, *a, **kw: z_moves.append(z_mm) or real_move_z_to(z_mm, *a, **kw)
    try:
        controller.run_acquisition(params)
        controller.join(timeout=120)
    finally:
        controller.close()
    assert controller.last_end_reason == "completed"
    assert z_moves[0] == pytest.approx(z_mid + 0.02)  # the surface z, before the reference is taken


def test_frame_rate_test_measures_the_free_run_rate_and_puts_the_camera_back():
    """Test FR: stream the channel free-run at the row's camera settings, report what was delivered,
    and restore the camera's settings, acquisition mode and channel."""
    from control.core.camera_geometry import snapshot_camera
    from squid.abc import CameraAcquisitionMode

    scope, live_controller, channels, controller = _build_controller_harness()
    live_controller.set_microscope_mode(channels[1])
    scope.camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    before = snapshot_camera(scope.camera)
    channel = channels[0].model_copy(deep=True)
    channel.exposure_time = 5.0
    results = []

    controller.run_frame_rate_test(channel, 0.5, on_finished=results.append, geometry=CameraGeometry(binning=2))
    assert controller.frame_rate_test_in_progress()
    with pytest.raises(RuntimeError, match="already running"):
        controller.run_frame_rate_test(channel, 0.5, on_finished=results.append)
    controller._fps_test_thread.join(timeout=30)

    (result,) = results
    assert result.error is None and result.channel_name == channel.name
    assert (result.exposure_ms, result.duration_s) == (5.0, 0.5)
    assert result.frame_count > 5 and result.mean_fps > 10 and 0 < result.min_fps <= result.mean_fps * 1.01
    # The rate the camera reported for the absurd request, read with the stream running.
    assert result.requested_fps == pytest.approx(scope.camera.get_max_frame_rate(5.0), rel=0.05)
    assert "fps (min" in result.summary()
    assert snapshot_camera(scope.camera) == before
    assert scope.camera.get_acquisition_mode() == CameraAcquisitionMode.SOFTWARE_TRIGGER
    assert not scope.camera.get_is_streaming()
    assert live_controller.currentConfiguration.name == channels[1].name
    controller.close()


def test_frame_rate_test_is_refused_while_acquiring(monkeypatch):
    scope, live_controller, channels, controller = _build_controller_harness()
    monkeypatch.setattr(controller, "acquisition_in_progress", lambda: True)
    with pytest.raises(RuntimeError, match="acquisition is running"):
        controller.run_frame_rate_test(channels[0], 0.5, on_finished=lambda result: None)
    controller.close()
