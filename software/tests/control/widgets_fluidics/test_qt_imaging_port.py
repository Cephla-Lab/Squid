import threading

import pytest

from control.core.fluidics_protocol import manifest as manifest_io
from control.core.fluidics_protocol.events import HoldAction, RunnerState
from control.core.fluidics_protocol.ports import ImagingRequest, ImagingStartError
from control.core.fluidics_protocol.resolve import resolve_protocol
from control.core.fluidics_protocol.runner import ProtocolRunner
from control.models.fluidics_protocol import CoordinatesBlock, ProtocolFile, SettingsBlock
from control.widgets_fluidics.qt_imaging_port import QtImagingPort
from tests.control.core.fluidics_protocol.fakes import FakeFluidicsPort, FakeImagingPort


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
    controller.set_base_path(str(tmp_path / "manual"))  # what the multipoint UI had set
    port = QtImagingPort(controller, controller.scanCoordinates, scope)
    channels_seen = []
    port.signal_acquisition_channels.connect(channels_seen.append)

    handle = port.start(_request(scope, controller, tmp_path))
    result = _wait_result(qtbot, handle)

    assert result.end_reason == "completed" and result.folder == "R01_image"
    assert controller.base_path == str(tmp_path / "manual")  # the operator's path came back
    assert result.image_count > 0
    assert (tmp_path / "R01_image" / ".done").exists()
    assert len(channels_seen) == 1 and len(channels_seen[0]) == 1
    import yaml

    data = yaml.safe_load((tmp_path / "R01_image" / "acquisition.yaml").read_text())
    assert data["protocol"]["round"] == "R01"


def test_a_resumed_request_images_from_its_start_fov_and_reports_the_end_fov(qtbot, qt_controller, tmp_path):
    scope, controller = qt_controller
    port = QtImagingPort(controller, controller.scanCoordinates, scope)
    request = _request(scope, controller, tmp_path, folder="R01_image_attempt2")
    x, y, z = request.coordinates.regions[0].fovs[0]
    request.coordinates.regions[0].fovs = [[x + 0.5 * i, y, z] for i in range(3)]
    request.start_fov = 2

    handle = port.start(request)
    result = _wait_result(qtbot, handle)

    assert result.end_reason == "completed"
    assert result.end_fov == 3
    assert result.image_count == 1  # one channel, one plane, the last FOV only
    assert controller.start_fov == 0


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
    controller.set_base_path(str(tmp_path / "manual"))
    request = _request(scope, controller, tmp_path, folder="R04_image")
    (tmp_path / "R04_image").mkdir()
    with pytest.raises(ImagingStartError, match="R04_image"):
        port.start(request)
    assert controller.base_path == str(tmp_path / "manual")  # restored on the failure path

    bad = _request(scope, controller, tmp_path, folder="R05_image")
    bad.settings.channels = ["No Such Channel"]
    with pytest.raises(ImagingStartError, match="Invalid channels"):
        port.start(bad)
    assert not (tmp_path / "R05_image").exists()


def test_a_refused_start_of_a_resumed_attempt_keeps_its_resume_point(qtbot, qt_controller, tmp_path):
    """The runner resumes a failed imaging step at FOV 2 through the real controller, whose start is refused (the
    microcontroller still busy): run_acquisition() returns False, so the attempt ends failed_to_start with nothing
    imaged. The next hold must still offer Resume from FOV 2, and that resume images the rest."""
    scope, controller = qt_controller
    qt_port = QtImagingPort(controller, controller.scanCoordinates, scope)
    first_attempt = FakeImagingPort(script=[("error", 2)])  # FOVs 0-1 done, then a fault

    class Imaging:  # attempt 1 from the fake, every later attempt through the Qt port
        def start(self, request):
            return (first_attempt if request.attempt == 1 else qt_port).start(request)

    base = _request(scope, controller, tmp_path)
    x, y, z = base.coordinates.regions[0].fovs[0]
    protocol = ProtocolFile(
        name="demo",
        imaging={
            "settings": {"cur": base.settings.model_dump()},
            "coordinates": {"cur": {"regions": [{"name": "A1", "fovs": [[x + 0.5 * i, y, z] for i in range(3)]}]}},
        },
        sequences=[
            {
                "type": "imaging",
                "round": "R01",
                "name": "image",
                "folder": "image",
                "settings": "cur",
                "coordinates": "cur",
            }
        ],
    )
    fluidics = FakeFluidicsPort()
    resolved = resolve_protocol(protocol, tmp_path, fluidics=fluidics)
    run_dir = manifest_io.create_run_dir(tmp_path, "resume")
    runner = ProtocolRunner(resolved, run_dir, Imaging(), fluidics, run_name="resume", poll_s=0.01)

    def held(attempt):
        hold = runner.hold
        return runner.state == RunnerState.HELD and hold is not None and hold.attempt == attempt

    microcontroller = controller.microcontroller
    real_wait = microcontroller.wait_till_operation_is_completed

    def still_busy(*args, **kwargs):
        if kwargs.get("timeout_limit_s") == controller._MCU_IDLE_WAIT_S:
            raise TimeoutError("still busy")
        return real_wait(*args, **kwargs)

    microcontroller.wait_till_operation_is_completed = still_busy
    try:
        runner.start()
        qtbot.waitUntil(lambda: held(1), timeout=10000)
        assert runner.hold.resume_position == 2
        runner.hold_action(HoldAction.RESUME)
        qtbot.waitUntil(lambda: held(2), timeout=30000)
    finally:
        microcontroller.wait_till_operation_is_completed = real_wait

    hold = runner.hold
    assert hold.reason == "failed_to_start"
    assert hold.can_resume and hold.resume_position == 2
    attempt = manifest_io.read_manifest(run_dir).steps[0].attempts[1]
    assert (attempt.folder, attempt.start_fov, attempt.end_fov) == ("R01_image_attempt2", 2, 2)

    runner.hold_action(HoldAction.RESUME)
    qtbot.waitUntil(lambda: runner.state == RunnerState.ENDED, timeout=30000)
    assert runner.outcome == "finished"
    attempts = manifest_io.read_manifest(run_dir).steps[0].attempts
    assert [(a.start_fov, a.end_fov, a.outcome) for a in attempts] == [
        (0, 2, "error"),
        (2, 2, "failed_to_start"),
        (2, 3, "completed"),
    ]
