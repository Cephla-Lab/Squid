"""Phase-2 exit criterion: a 3-round protocol runs through the REAL Qt imaging path.

Simulated microscope + QtMultiPointController + QtImagingPort + FluidicsService(instant) +
LibraryFluidicsPort. R01/R02 image from header blocks; R03's coordinates come from a saved
acquisition folder produced earlier in the test. Mid-run, R02's acquisition is aborted and
the HELD step restarted, which must land in an `_attempt2` session folder.
"""

import pathlib

import pytest

pytest.importorskip("fluidics")

import control._def
import control.microscope
from control.core.fluidics_protocol import manifest as manifest_io
from control.core.fluidics_protocol.events import RunnerState
from control.core.fluidics_protocol.library_port import LibraryFluidicsPort
from control.core.fluidics_protocol.ports import ImagingRequest
from control.core.fluidics_protocol.resolve import resolve_protocol
from control.core.fluidics_protocol.runner import HoldAction, ProtocolRunner
from control.fluidics_system import FluidicsService
from control.models.fluidics_protocol import CoordinatesBlock, ProtocolFile, SettingsBlock
from control.widgets_fluidics.qt_imaging_port import QtImagingPort

EXAMPLE_CONFIG = pathlib.Path(__file__).resolve().parents[3] / "machine_configs" / "fluidics_config.yaml.example"


@pytest.fixture
def qt_controller(qtbot):
    control._def.MERGE_CHANNELS = False
    import tests.control.test_stubs as ts
    from control.gui_hcs import QtMultiPointController

    scope = control.microscope.Microscope.build_from_global_config(True)
    live = ts.get_test_live_controller(microscope=scope, starting_objective=scope.objective_store.default_objective)
    controller = QtMultiPointController(
        scope,
        live,
        ts.get_test_autofocus_controller(scope.camera, scope.stage, live, scope.low_level_drivers.microcontroller),
        scope.objective_store,
        scan_coordinates=ts.get_test_scan_coordinates(
            objective_store=scope.objective_store, stage=scope.stage, camera=scope.camera
        ),
        laser_autofocus_controller=ts.get_test_laser_autofocus_controller(scope),
    )
    yield scope, controller
    controller.close()
    scope.close()


def _fovs(scope, count):
    limits = scope.stage.get_config()
    x0, y0 = limits.X_AXIS.MIN_POSITION + 1.0, limits.Y_AXIS.MIN_POSITION + 1.0
    z = limits.Z_AXIS.MIN_POSITION + 0.5
    return [[x0 + 0.05 * i, y0, z] for i in range(count)]


def _flow(round_name, port):
    return {
        "type": "flow_reagent",
        "round": round_name,
        "name": "probe",
        "fluidic_port": port,
        "flow_rate": 2000,
        "volume": 100,
    }


def _image(round_name, source_key):
    return {
        "type": "imaging",
        "round": round_name,
        "name": "image",
        "folder": f"{round_name}_image",
        "settings": "cur",
        "coordinates": source_key,
    }


def test_three_round_gui_run_with_abort_and_restart(qtbot, qt_controller, tmp_path):
    scope, controller = qt_controller
    port = QtImagingPort(controller, controller.scanCoordinates, scope)
    channel = [m.name for m in controller.liveController.get_channels(scope.objective_store.current_objective)][:1]
    settings = {"channels": channel, "z_stack": {"nz": 1}}

    # 1) Produce a saved acquisition folder to use as R03's coordinates source.
    seed_dir = tmp_path / "seed_runs"
    seed_dir.mkdir()
    handle = port.start(
        ImagingRequest(
            folder="seed",
            run_dir=str(seed_dir),
            settings=SettingsBlock.model_validate(settings),
            coordinates=CoordinatesBlock.model_validate({"regions": [{"name": "A1", "fovs": _fovs(scope, 2)}]}),
            step_index=0,
            attempt=1,
            protocol={"round": "seed", "run_name": "seed"},
        )
    )
    box = {}

    def seed_done():
        result = handle.wait(0.05)
        if result is None:
            return False
        box["seed"] = result
        return True

    qtbot.waitUntil(seed_done, timeout=60000)
    assert box["seed"].end_reason == "completed"
    seed_session = seed_dir / "seed"
    assert (seed_session / "acquisition.yaml").exists() and (seed_session / "coordinates.csv").exists()

    # 2) A 3-round protocol: R01/R02 from header blocks, R03's coordinates from the saved folder.
    protocol = ProtocolFile(
        name="e2e_gui",
        imaging={
            "settings": {"cur": settings},
            "coordinates": {
                "cur": {"regions": [{"name": "A1", "fovs": _fovs(scope, 2)}]},
                "wide": {"regions": [{"name": "A1", "fovs": _fovs(scope, 8)}]},
            },
        },
        sequences=[
            _flow("R01", 1),
            _image("R01", "cur"),
            _flow("R02", 2),
            {**_image("R02", "wide")},
            _flow("R03", 3),
            _image("R03", str(seed_session)),
        ],
    )

    service = FluidicsService(default_config_path=str(EXAMPLE_CONFIG), simulated=True)
    service.initialize(report_dir=str(tmp_path / "reports"), instant=True)
    try:
        fluidics_port = LibraryFluidicsPort(service.system)
        resolved = resolve_protocol(protocol, tmp_path, fluidics=fluidics_port)
        run_dir = manifest_io.create_run_dir(tmp_path, "e2e")
        runner = ProtocolRunner(resolved, run_dir, port, fluidics_port, run_name="e2e", poll_s=0.05)
        runner.start()

        # 3) Abort R02's acquisition while it runs, then Restart from the HELD panel's action.
        qtbot.waitUntil(lambda: (run_dir / "R02_image").is_dir() or runner.outcome is not None, timeout=120000)
        assert runner.outcome is None, f"run ended early: {runner.snapshot()}"
        runner.abort_step()
        qtbot.waitUntil(lambda: runner.state == RunnerState.HELD or runner.outcome is not None, timeout=60000)
        assert runner.state == RunnerState.HELD, runner.snapshot()
        assert (run_dir / "R02_image" / "aborted.json").exists()
        runner.hold_action(HoldAction.RESTART)

        qtbot.waitUntil(lambda: runner.outcome is not None, timeout=180000)
        assert runner.outcome == "finished", runner.snapshot()
        assert runner.wait(10)  # join the runner thread so the final manifest write has landed

        man = manifest_io.read_manifest(run_dir)
        assert man.status == "finished"
        assert [s.kind for s in man.steps] == ["fluidics", "imaging"] * 3
        for folder in ("R01_image", "R02_image_attempt2", "R03_image"):
            assert (run_dir / folder / ".done").exists(), folder
        r02 = man.steps[3]
        assert [a.outcome for a in r02.attempts] == ["user_abort", "completed"]
        assert [a.folder for a in r02.attempts] == ["R02_image", "R02_image_attempt2"]
        r03 = man.steps[5]
        assert r03.attempts[0].outcome == "completed" and r03.attempts[0].images == 2
    finally:
        if runner := locals().get("runner"):
            runner.wait(5)
        assert service.close() == []
