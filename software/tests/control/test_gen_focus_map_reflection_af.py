import dataclasses
import threading

import pytest

import control._def
import control.microscope
import tests.control.test_stubs as ts
from control.core.multi_point_controller import NoOpCallbacks


@pytest.fixture(scope="module")
def sim_scope():
    scope = control.microscope.Microscope.build_from_global_config(True)
    yield scope
    scope.close()


def test_gen_focus_map_with_reflection_af_bakes_plane_into_fovs(sim_scope, monkeypatch):
    """gen_focus_map + reflection AF: run_acquisition must bake the generated focus
    plane into every FOV coordinate (z = plane(x, y)) instead of enabling the contrast
    focus-map. The contrast focus-map interpolation never runs in the worker's laser-AF
    branch, so set_focus_map_use(True) must NOT be called; each FOV is pre-positioned on
    the tilted plane instead. The 3-corner measurement is stubbed so the plane is known
    and no real contrast/laser AF math executes (fully deterministic).
    """
    control._def.MERGE_CHANNELS = False
    control._def.SUPPORT_LASER_AUTOFOCUS = True

    started = threading.Event()
    finished = threading.Event()
    callbacks = dataclasses.replace(
        NoOpCallbacks,
        signal_acquisition_start=lambda *a, **kw: started.set(),
        signal_acquisition_finished=lambda *a, **kw: finished.set(),
    )
    mpc = ts.get_test_multi_point_controller(sim_scope, callbacks=callbacks)

    # One well A1 as a 2x2 grid -> four FOVs at distinct x positions on the tilted plane.
    cfg = sim_scope.stage.get_config()
    center_x = cfg.X_AXIS.MIN_POSITION + 1.0
    center_y = cfg.Y_AXIS.MIN_POSITION + 1.0
    center_z = (cfg.Z_AXIS.MAX_POSITION - cfg.Z_AXIS.MIN_POSITION) / 2.0
    mpc.scanCoordinates.clear_regions()
    mpc.scanCoordinates.add_flexible_region("A1", center_x, center_y, center_z, 2, 2, 0)

    channel_names = [c.name for c in mpc.liveController.get_channels(mpc.objectiveStore.current_objective)]
    mpc.set_selected_configurations(channel_names[0:1])

    mpc.set_reflection_af_flag(True)
    mpc.set_gen_focus_map_flag(True)

    # Known tilted plane: z = 2.0 + 0.01*x (pure x tilt). For the A1 FOV x values
    # (~5.6-6.4 mm) the baked z (~2.06 mm) stays well within the sim Z limits [0.05, 6.0].
    def plane_z(x):
        return 2.0 + 0.01 * x

    # Stub the 3-corner contrast-AF measurement: no stage moves / no real autofocus,
    # just install a known non-colinear focus map that defines the plane above.
    def fake_gen_focus_map(coord1, coord2, coord3):
        mpc.autofocusController.focus_map_coords = [
            (0.0, 0.0, plane_z(0.0)),
            (100.0, 0.0, plane_z(100.0)),
            (0.0, 100.0, plane_z(0.0)),
        ]

    monkeypatch.setattr(mpc.autofocusController, "gen_focus_map", fake_gen_focus_map)

    # Spy on set_focus_map_use: the reflection-AF path must never enable the contrast focus map.
    focus_map_use_calls = []
    real_set_focus_map_use = mpc.autofocusController.set_focus_map_use

    def spy_set_focus_map_use(enable):
        focus_map_use_calls.append(enable)
        return real_set_focus_map_use(enable)

    monkeypatch.setattr(mpc.autofocusController, "set_focus_map_use", spy_set_focus_map_use)

    # Satisfy validate_acquisition_settings (laser AF requires a stored reference)...
    sim_scope.addons.camera_focus.send_trigger()
    ref_image = sim_scope.addons.camera_focus.read_frame()
    assert ref_image is not None
    mpc.laserAutoFocusController.laser_af_properties.set_reference_image(ref_image)

    # ...and neutralize the sim's flaky laser-AF closed-loop move so the run can't fail on it.
    monkeypatch.setattr(mpc.laserAutoFocusController, "move_to_target", lambda *a, **kw: True)

    mpc.run_acquisition()

    assert started.wait(60), "acquisition never started"
    assert finished.wait(60), "acquisition never finished"

    # (1) Every FOV coordinate is a 3-tuple whose z equals the known plane at its (x, y).
    fovs = mpc.scanCoordinates.region_fov_coordinates["A1"]
    assert len(fovs) == 4
    for coord in fovs:
        assert len(coord) == 3, f"FOV was not baked to (x, y, z): {coord}"
        x, y, z = coord
        assert z == pytest.approx(plane_z(x), abs=1e-6)
    # The plane actually tilts (larger x -> larger z); the z's are not a single constant.
    assert max(f[2] for f in fovs) > min(f[2] for f in fovs)

    # (2) The contrast focus-map was never enabled in the reflection-AF path.
    assert True not in focus_map_use_calls

    # (3) The run completed, not aborted.
    assert mpc.abort_acqusition_requested is False
