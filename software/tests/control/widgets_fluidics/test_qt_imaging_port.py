import threading

import pytest

import control._def
import control.microscope
from control.core.fluidics_protocol.ports import ImagingRequest, ImagingStartError
from control.models.fluidics_protocol import CoordinatesBlock, SettingsBlock
from control.widgets_fluidics.qt_imaging_port import QtImagingPort


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


def _request(scope, controller, run_dir, folder="R01_image"):
    channel = [m.name for m in controller.liveController.get_channels(scope.objective_store.current_objective)][:1]
    limits = controller.stage.get_config()
    fov = [limits.X_AXIS.MIN_POSITION + 1.0, limits.Y_AXIS.MIN_POSITION + 1.0, limits.Z_AXIS.MIN_POSITION + 0.5]
    return ImagingRequest(
        folder=folder,
        run_dir=str(run_dir),
        settings=SettingsBlock.model_validate({"channels": channel, "z_stack": {"nz": 1}}),
        coordinates=CoordinatesBlock.model_validate({"regions": [{"name": "A1", "fovs": [fov]}]}),
        step_index=0,
        attempt=1,
        protocol={"round": "R01", "run_name": "t"},
    )


def _wait_result(qtbot, handle, timeout=30000):
    box = {}

    def done():
        result = handle.wait(0.05)
        if result is None:
            return False
        box["result"] = result
        return True

    qtbot.waitUntil(done, timeout=timeout)
    return box["result"]


def test_start_runs_a_named_acquisition_to_completion(qtbot, qt_controller, tmp_path):
    scope, controller = qt_controller
    port = QtImagingPort(controller, controller.scanCoordinates, scope)
    channels_seen = []
    port.signal_acquisition_channels.connect(channels_seen.append)

    handle = port.start(_request(scope, controller, tmp_path))
    result = _wait_result(qtbot, handle)

    assert result.end_reason == "completed" and result.folder == "R01_image"
    assert result.image_count > 0
    assert (tmp_path / "R01_image" / ".done").exists()
    assert len(channels_seen) == 1 and len(channels_seen[0]) == 1
    import yaml

    data = yaml.safe_load((tmp_path / "R01_image" / "acquisition.yaml").read_text())
    assert data["protocol"]["round"] == "R01"


def test_start_from_a_worker_thread_like_the_runner(qtbot, qt_controller, tmp_path):
    scope, controller = qt_controller
    port = QtImagingPort(controller, controller.scanCoordinates, scope)
    box = {}

    def runner_side():
        try:
            handle = port.start(_request(scope, controller, tmp_path, folder="R02_image"))
            while True:
                result = handle.wait(0.05)
                if result is not None:
                    box["result"] = result
                    return
        except Exception as e:  # pragma: no cover - surfaced by the assertion below
            box["error"] = e

    thread = threading.Thread(target=runner_side, daemon=True)
    thread.start()
    qtbot.waitUntil(lambda: "result" in box or "error" in box, timeout=30000)
    thread.join(5)
    assert box.get("error") is None
    assert box["result"].end_reason == "completed"


def test_abort_reports_user_abort(qtbot, qt_controller, tmp_path):
    scope, controller = qt_controller
    port = QtImagingPort(controller, controller.scanCoordinates, scope)
    handle = port.start(_request(scope, controller, tmp_path, folder="R03_image"))
    handle.abort()
    result = _wait_result(qtbot, handle)
    assert result.end_reason == "user_abort"


def test_start_refusals_raise_imaging_start_error(qtbot, qt_controller, tmp_path):
    scope, controller = qt_controller
    port = QtImagingPort(controller, controller.scanCoordinates, scope)
    request = _request(scope, controller, tmp_path, folder="R04_image")
    (tmp_path / "R04_image").mkdir()
    with pytest.raises(ImagingStartError, match="R04_image"):
        port.start(request)

    bad = _request(scope, controller, tmp_path, folder="R05_image")
    bad.settings.channels = ["No Such Channel"]
    with pytest.raises(ImagingStartError, match="Invalid channels"):
        port.start(bad)
    assert not (tmp_path / "R05_image").exists()
