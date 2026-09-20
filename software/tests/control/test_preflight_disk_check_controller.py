"""The Qt-free pre-flight disk check (control.core.disk_space.preflight_disk_check) against a real simulated
controller, as the TCP run commands use it: before the experiment folder exists."""


def test_shared_check_works_on_a_real_controller_before_the_experiment_folder_exists(tmp_path, monkeypatch):
    """The TCP run commands check before start_new_experiment(), so the estimate must not need the folder."""
    import control.microscope
    import control.utils
    import tests.control.test_stubs as ts
    from control.core.disk_space import PREFLIGHT_SAFETY_FACTOR, preflight_disk_check
    from tests.control.test_MultiPointController import select_some_configs

    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    cfg = mpc.stage.get_config()
    mpc.scanCoordinates.add_single_fov_region(
        "region_1",
        center_x=cfg.X_AXIS.MIN_POSITION + 1.0,
        center_y=cfg.Y_AXIS.MIN_POSITION + 1.0,
        center_z=cfg.Z_AXIS.MIN_POSITION + 1.0,
    )
    select_some_configs(mpc, scope.objective_store.current_objective)
    mpc.set_Nt(3)
    save_dir = tmp_path / "base" / "not_created_yet"
    mpc.set_base_path(str(save_dir))

    monkeypatch.setattr(control.utils, "get_available_disk_space", lambda d: 1)
    check = preflight_disk_check(mpc, str(save_dir))

    assert check.image_count == mpc.get_acquisition_image_count() > 0
    assert check.required_bytes == PREFLIGHT_SAFETY_FACTOR * mpc.get_estimated_acquisition_disk_storage() > 1
    assert not check.fits
    assert not save_dir.exists(), "checking must not create anything"
