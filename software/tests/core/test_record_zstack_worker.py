from pathlib import Path

import pytest

from control.core.record_zstack_controller import frame_count, zstack_plane_count, zstack_offsets_um


def test_frame_count():
    assert frame_count(10.0, 30.0) == 300
    assert frame_count(7.5, 2.0) == 15


def test_zstack_plane_count_and_offsets():
    assert zstack_plane_count(-3.0, 3.0, 1.0) == 7
    assert zstack_offsets_um(-3.0, 3.0, 1.0) == [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0]
    assert zstack_plane_count(0.0, 5.0, 2.0) == 3  # 0,2,4
    assert zstack_offsets_um(0.0, 5.0, 2.0) == [0.0, 2.0, 4.0]


def test_zstack_plane_count_validation():
    with pytest.raises(ValueError):
        zstack_plane_count(3.0, -3.0, 1.0)  # z_max < z_min
    with pytest.raises(ValueError):
        zstack_plane_count(0.0, 3.0, 0.0)  # step == 0
    with pytest.raises(ValueError):
        zstack_plane_count(0.0, 3.0, -1.0)  # step < 0


# ---------------------------------------------------------------------------
# Task D2: RecordZStackWorker smoke test (simulated microscope + JobRunner)
#
# 2 wells x 2 FOV x 2 time points, both phases (recording + z-stack) enabled.
# Asserts:
#   - one recording .ome.zarr per (t, well, fov) with shape (T,1,1,Y,X)
#   - the z-stack per-FOV zarr datasets exist
# ---------------------------------------------------------------------------


def _build_simulated_microscope(crop_w: int, crop_h: int):
    """Build a simulated microscope and shrink the camera frame for a fast test.

    The simulated camera reports its resolution from crop_width/crop_height, so
    mutating the (loaded) config shrinks every captured/recorded frame.
    """
    import control.microscope

    scope = control.microscope.Microscope.build_from_global_config(True)
    scope.camera._config.crop_width = crop_w
    scope.camera._config.crop_height = crop_h
    return scope


def test_record_zstack_worker_smoke(tmp_path):
    pytest.importorskip("tensorstore")  # optional dep; the worker writes real Zarr
    import control._def
    import tests.control.test_stubs as ts
    from control.core.multi_point_controller import NoOpCallbacks
    from control.core.record_zstack_controller import RecordZStackAcquisitionParameters, frame_count
    from control.core.record_zstack_worker import RecordZStackWorker

    # Use real Zarr v3 saving for the z-stack phase (the path under test).
    control._def.FILE_SAVING_OPTION = control._def.FileSavingOption.ZARR_V3

    crop_w, crop_h = 64, 48
    scope = _build_simulated_microscope(crop_w, crop_h)
    live_controller = ts.get_test_live_controller(scope, scope.objective_store.default_objective)
    laser_af = ts.get_test_laser_autofocus_controller(scope)

    channels = live_controller.get_channels(scope.objective_store.default_objective)
    assert len(channels) >= 2
    recording_channel = channels[0]
    zstack_channels = channels[:2]

    # Keep exposure short so capture/recording is fast.
    scope.camera.set_exposure_time(1)

    # Move the stage to a safe mid-Z so z_ref +/- offsets stay in range.
    z_cfg = scope.stage.get_config().Z_AXIS
    z_mid = (z_cfg.MAX_POSITION + z_cfg.MIN_POSITION) / 2.0
    scope.stage.move_z_to(z_mid)
    scope.low_level_drivers.microcontroller.wait_till_operation_is_completed()

    x_cfg = scope.stage.get_config().X_AXIS
    y_cfg = scope.stage.get_config().Y_AXIS
    x0 = x_cfg.MIN_POSITION + 1.0
    y0 = y_cfg.MIN_POSITION + 1.0
    # 2 wells x 2 FOV
    scan_region_fov_coords = {
        "A1": [(x0, y0), (x0 + 0.5, y0)],
        "A2": [(x0 + 1.0, y0), (x0 + 1.5, y0)],
    }
    n_wells = len(scan_region_fov_coords)
    n_fov = 2
    Nt = 2

    fps = 10.0
    duration_s = 0.3
    T = frame_count(fps, duration_s)
    assert T >= 1

    params = RecordZStackAcquisitionParameters(
        base_path=str(tmp_path),
        experiment_id="rec_zstack_smoke",
        Nt=Nt,
        dt_s=0.0,
        use_laser_af=False,  # no reference set -> would fall back anyway
        recording_enabled=True,
        recording_channel=recording_channel,
        fps=fps,
        duration_s=duration_s,
        recording_z_offset_um=0.0,
        zstack_enabled=True,
        zstack_channels=zstack_channels,
        z_min_um=-1.0,
        z_max_um=1.0,
        z_step_um=1.0,  # 3 planes
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
        scan_region_fov_coords=scan_region_fov_coords,
    )

    worker.run()

    assert not aborted["v"], "worker requested abort during the smoke run"

    base = Path(params.base_path) / params.experiment_id

    # Recording: one dataset per (t, well, fov).
    rec = sorted((base / "recording").rglob("*.ome.zarr"))
    assert len(rec) == Nt * n_wells * n_fov, f"expected {Nt * n_wells * n_fov} recordings, got {len(rec)}: {rec}"

    # Each recording dataset has shape (T, 1, 1, Y, X).
    import tensorstore as tstore

    for path in rec:
        ds = tstore.open({"driver": "zarr3", "kvstore": {"driver": "file", "path": str(path)}}).result()
        assert tuple(ds.shape) == (T, 1, 1, crop_h, crop_w), f"bad recording shape {ds.shape} for {path}"

    # Z-stack: per-FOV zarr datasets exist under {experiment}/zarr (non-HCS per-FOV layout).
    zstack_zarrs = list((base / "zarr").rglob("fov_*.ome.zarr"))
    assert zstack_zarrs, f"no z-stack zarr datasets found under {base / 'zarr'}"
    # One per (well, fov) — written across all time points/z/channels.
    assert len(zstack_zarrs) == n_wells * n_fov, f"expected {n_wells * n_fov} z-stack fovs, got {len(zstack_zarrs)}"

    # Verify z-stack shape (T, C, Z, Y, X).
    n_z = len(zstack_offsets_um(params.z_min_um, params.z_max_um, params.z_step_um))
    for path in zstack_zarrs:
        ds = tstore.open({"driver": "zarr3", "kvstore": {"driver": "file", "path": str(path / "0")}}).result()
        assert tuple(ds.shape) == (Nt, len(zstack_channels), n_z, crop_h, crop_w), f"bad z-stack shape {ds.shape}"


# ---------------------------------------------------------------------------
# Task D3: RecordZStackController smoke test
#
# Same 2-well x 2-FOV geometry as the D2 test but driven through
# RecordZStackController.run_acquisition() + join().
# Also exercises Nt=2 with dt_s=0.0 (continuous: no grid pacing, so both
# time-points run even though the per-timepoint work exceeds any short dt —
# with dt>0 the worker now SKIPS missed slots, mirroring MultiPointWorker).
# ---------------------------------------------------------------------------


def test_record_zstack_controller_smoke(tmp_path):
    pytest.importorskip("tensorstore")  # optional dep; the worker writes real Zarr
    import control._def
    import tests.control.test_stubs as ts
    from control.core.multi_point_controller import NoOpCallbacks
    from control.core.record_zstack_controller import (
        RecordZStackAcquisitionParameters,
        RecordZStackController,
        frame_count,
    )
    from control.core.scan_coordinates import ScanCoordinates

    control._def.FILE_SAVING_OPTION = control._def.FileSavingOption.ZARR_V3

    crop_w, crop_h = 64, 48
    scope = _build_simulated_microscope(crop_w, crop_h)
    live_controller = ts.get_test_live_controller(scope, scope.objective_store.default_objective)
    laser_af = ts.get_test_laser_autofocus_controller(scope)

    channels = live_controller.get_channels(scope.objective_store.default_objective)
    assert len(channels) >= 2
    recording_channel = channels[0]
    zstack_channels = channels[:2]

    scope.camera.set_exposure_time(1)

    z_cfg = scope.stage.get_config().Z_AXIS
    z_mid = (z_cfg.MAX_POSITION + z_cfg.MIN_POSITION) / 2.0
    scope.stage.move_z_to(z_mid)
    scope.low_level_drivers.microcontroller.wait_till_operation_is_completed()

    x_cfg = scope.stage.get_config().X_AXIS
    y_cfg = scope.stage.get_config().Y_AXIS
    x0 = x_cfg.MIN_POSITION + 1.0
    y0 = y_cfg.MIN_POSITION + 1.0

    # Build a minimal ScanCoordinates with two regions, two FOVs each.
    scan_coords = ScanCoordinates(
        objectiveStore=scope.objective_store,
        stage=scope.stage,
        camera=scope.camera,
    )
    scan_coords.region_fov_coordinates = {
        "A1": [(x0, y0), (x0 + 0.5, y0)],
        "A2": [(x0 + 1.0, y0), (x0 + 1.5, y0)],
    }
    n_wells = 2
    n_fov = 2
    Nt = 2

    fps = 10.0
    duration_s = 0.3
    T = frame_count(fps, duration_s)
    assert T >= 1

    controller = RecordZStackController(
        microscope=scope,
        live_controller=live_controller,
        laser_autofocus_controller=laser_af,
        objective_store=scope.objective_store,
        scan_coordinates=scan_coords,
        callbacks=NoOpCallbacks,
    )

    # Build params directly and pass to run_acquisition (same path as the widget).
    params = RecordZStackAcquisitionParameters(
        base_path=str(tmp_path),
        experiment_id="ctrl_smoke",
        Nt=Nt,
        dt_s=0.0,  # continuous: dt>0 would skip slots missed while working
        use_laser_af=False,
        recording_enabled=True,
        recording_channel=recording_channel,
        fps=fps,
        duration_s=duration_s,
        recording_z_offset_um=0.0,
        zstack_enabled=True,
        zstack_channels=zstack_channels,
        z_min_um=-1.0,
        z_max_um=1.0,
        z_step_um=1.0,  # 3 planes
    )

    controller.run_acquisition(params)
    # Wait up to 120 s for the worker thread to finish.
    controller.join(timeout=120)
    assert not controller.acquisition_in_progress(), "worker thread did not finish in time"

    # Determine the resolved experiment_id (has a timestamp appended).
    subdirs = [d for d in Path(tmp_path).iterdir() if d.is_dir()]
    assert len(subdirs) == 1, f"expected exactly one experiment dir, got {subdirs}"
    base = subdirs[0]

    # Recording: one dataset per (t, well, fov).
    rec = sorted((base / "recording").rglob("*.ome.zarr"))
    assert len(rec) == Nt * n_wells * n_fov, f"expected {Nt * n_wells * n_fov} recordings, got {len(rec)}: {rec}"

    import tensorstore as tstore

    for path in rec:
        ds = tstore.open({"driver": "zarr3", "kvstore": {"driver": "file", "path": str(path)}}).result()
        assert tuple(ds.shape) == (T, 1, 1, crop_h, crop_w), f"bad recording shape {ds.shape} for {path}"

    # Z-stack: per-FOV zarr datasets exist.
    zstack_zarrs = list((base / "zarr").rglob("fov_*.ome.zarr"))
    assert zstack_zarrs, f"no z-stack zarr datasets found under {base / 'zarr'}"
    assert len(zstack_zarrs) == n_wells * n_fov, f"expected {n_wells * n_fov} z-stack fovs, got {len(zstack_zarrs)}"

    n_z = len(zstack_offsets_um(-1.0, 1.0, 1.0))
    for path in zstack_zarrs:
        ds = tstore.open({"driver": "zarr3", "kvstore": {"driver": "file", "path": str(path / "0")}}).result()
        assert tuple(ds.shape) == (Nt, len(zstack_channels), n_z, crop_h, crop_w), f"bad z-stack shape {ds.shape}"

    controller.close()


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
    y, x, dtype = RecordZStackWorker._probe_frame_shape(fake_self)

    assert (y, x) == (50, 60), f"expected processed-frame shape, got ({y}, {x})"
    assert dtype == np.uint8


# ---------------------------------------------------------------------------
# Review-fix tests: post-run hardware state restore (F8, F9, F10)
# ---------------------------------------------------------------------------


def _build_worker_harness(tmp_path, recording_enabled, zstack_enabled, zstack_channel_slice=slice(1, 2)):
    """Simulated scope + live controller + a 1-FOV worker with tiny params."""
    import control._def
    import tests.control.test_stubs as ts
    from control.core.multi_point_controller import NoOpCallbacks
    from control.core.record_zstack_controller import RecordZStackAcquisitionParameters
    from control.core.record_zstack_worker import RecordZStackWorker

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

    params = RecordZStackAcquisitionParameters(
        base_path=str(tmp_path),
        experiment_id="state_restore",
        Nt=1,
        dt_s=0.0,
        use_laser_af=False,
        recording_enabled=recording_enabled,
        recording_channel=channels[0],
        fps=10.0,
        duration_s=0.2,
        recording_z_offset_um=0.0,
        zstack_enabled=zstack_enabled,
        zstack_channels=list(channels[zstack_channel_slice]) if zstack_enabled else [],
        z_min_um=0.0,
        z_max_um=1.0,
        z_step_um=1.0,
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
        scan_region_fov_coords={"A1": [(x0, y0)]},
    )
    return scope, live_controller, channels, worker, aborted


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
    assert squid_attrs["shape"][0] == expected_T, (
        f"dataset sized for the requested fps (T={squid_attrs['shape'][0]}), "
        f"expected achievable-rate T={expected_T}"
    )
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
    ok = worker._wait_for_dt(1)
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
    from control.core.record_zstack_controller import (
        RecordZStackAcquisitionParameters,
        RecordZStackController,
    )

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
        recording_enabled=True,
        recording_channel=channels[0],
        fps=10.0,
        duration_s=0.2,
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
        RecordZStackWorker._probe_frame_shape(fake_self)


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
