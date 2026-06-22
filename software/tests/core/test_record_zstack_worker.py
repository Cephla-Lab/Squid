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
# Also exercises Nt=2 with a short dt_s=0.1 (two time-points).
# ---------------------------------------------------------------------------


def test_record_zstack_controller_smoke(tmp_path):
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
        dt_s=0.1,  # short inter-timepoint delay
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
