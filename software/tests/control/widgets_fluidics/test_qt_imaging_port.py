import threading

import pytest

from control.core.fluidics_protocol.ports import ImagingRequest, ImagingStartError
from control.models.fluidics_protocol import CoordinatesBlock, SettingsBlock
from control.widgets_fluidics.qt_imaging_port import QtImagingPort


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


class _FakeLaserAF:
    def __init__(self, initialized=True, has_reference=True, reading=1.25):
        from types import SimpleNamespace

        self.is_initialized = initialized
        self.laser_af_properties = SimpleNamespace(has_reference=has_reference)
        self.reading = reading
        self.reads = 0

    def measure_displacement(self):
        self.reads += 1
        if isinstance(self.reading, Exception):
            raise self.reading
        return self.reading


def test_measure_focus_reads_the_laser_af_displacement_only_when_it_can(qtbot, qt_controller, monkeypatch):
    scope, controller = qt_controller
    port = QtImagingPort(controller, controller.scanCoordinates, scope)

    monkeypatch.setattr(controller, "laserAutoFocusController", None)
    assert port.measure_focus_um() is None
    for unusable in (_FakeLaserAF(initialized=False), _FakeLaserAF(has_reference=False)):
        monkeypatch.setattr(controller, "laserAutoFocusController", unusable)
        assert port.measure_focus_um() is None and unusable.reads == 0

    laf = _FakeLaserAF()
    monkeypatch.setattr(controller, "laserAutoFocusController", laf)
    assert port.measure_focus_um() == 1.25
    monkeypatch.setattr(controller, "acquisition_in_progress", lambda: True)
    assert port.measure_focus_um() is None and laf.reads == 1
    monkeypatch.setattr(controller, "acquisition_in_progress", lambda: False)
    laf.reading = float("nan")
    assert port.measure_focus_um() != port.measure_focus_um()  # nan: no spot this time
    laf.reading = TimeoutError("AF laser")
    value = port.measure_focus_um()
    assert value != value
