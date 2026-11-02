import json
import os
import shutil
import tempfile
import threading
import warnings
from pathlib import Path
from typing import Optional

from control._def import OBJECTIVES, DEFAULT_OBJECTIVE
from control.core.auto_focus_controller import AutoFocusController
from control.core.laser_auto_focus_controller import LaserAutofocusController
from control.core.live_controller import LiveController
from control.core.multi_point_controller import NoOpCallbacks, MultiPointController
from control.core.multi_point_utils import MultiPointControllerFunctions
from control.core.objective_store import ObjectiveStore
from control.core.scan_coordinates import ScanCoordinates
from control.microcontroller import Microcontroller
from control.microscope import Microscope
from squid.abc import AbstractStage, AbstractCamera

# Simulated acquisitions write real image data (~180 MB per run). Every base dir a
# stub controller writes into is registered here so the autouse fixture in
# tests/conftest.py can delete it at test teardown - pointing them at a literal
# "/tmp/" used to accumulate output across runs until the disk filled.
_acquisition_output_dirs: list[str] = []


def new_acquisition_base_path() -> str:
    base_path = tempfile.mkdtemp(prefix="squid_unit_test_acquisition_")
    _acquisition_output_dirs.append(base_path)
    return base_path


def cleanup_stub_acquisition_dirs() -> None:
    """Delete every registered acquisition output dir.

    A dir that cannot be deleted (e.g. a writer still holds a file open) stays
    registered so the next teardown retries it, and the failure surfaces as a
    pytest warning instead of being swallowed.
    """
    kept: list[str] = []
    for base_path in _acquisition_output_dirs:
        try:
            shutil.rmtree(base_path)
        except OSError as e:
            if os.path.exists(base_path):  # not merely already gone: keep it for a later retry
                warnings.warn(
                    f"Could not delete simulated-acquisition output {base_path} ({e}); kept registered for retry",
                    RuntimeWarning,
                )
                kept.append(base_path)
    _acquisition_output_dirs[:] = kept


def get_test_live_controller(microscope: Microscope, starting_objective) -> LiveController:
    controller = LiveController(microscope=microscope, camera=microscope.camera)

    channels = controller.get_channels(objective=starting_objective)
    if channels:
        controller.set_microscope_mode(channels[0])
    return controller


def get_test_autofocus_controller(
    camera,
    stage: AbstractStage,
    live_controller: LiveController,
    microcontroller: Microcontroller,
):
    return AutoFocusController(
        camera=camera,
        stage=stage,
        liveController=live_controller,
        microcontroller=microcontroller,
        nl5=None,
        finished_fn=lambda: None,
        image_to_display_fn=lambda image: None,
    )


def get_test_scan_coordinates(
    objective_store: ObjectiveStore,
    stage: AbstractStage,
    camera: AbstractCamera,
):
    return ScanCoordinates(objectiveStore=objective_store, stage=stage, camera=camera)


def zarr_squid_attrs(root) -> list[dict]:
    """The _squid attributes of every OME group zarr.json under root (array zarr.json files carry none)."""
    out = []
    for p in Path(root).rglob("zarr.json"):
        with open(p) as f:
            attrs = json.load(f).get("attributes", {}).get("_squid")
        if attrs is not None:
            out.append(attrs)
    return out


class GatedFuture:
    """A write future that completes once `release` is set, raising `error` then if given."""

    def __init__(self, release: threading.Event, error: Optional[BaseException] = None):
        self._release = release
        self._error = error

    def done(self):
        return self._release.is_set()

    def result(self):
        self._release.wait()
        if self._error is not None:
            raise self._error


def stub_zarr_writer_io(monkeypatch, submit_frame):
    """A ZarrWriter that touches no disk: open/seal are no-ops and `submit_frame(self, image, t, c, z, fov=None)`
    stands in for the write (return a future with done()/result())."""
    from control.core.zarr_writer import ZarrWriter

    monkeypatch.setattr(ZarrWriter, "initialize", lambda self: None)
    monkeypatch.setattr(ZarrWriter, "submit_frame", submit_frame)
    monkeypatch.setattr(ZarrWriter, "finalize", lambda self, *a, **k: None)
    monkeypatch.setattr(ZarrWriter, "abort", lambda self, *a, **k: None)


def make_zarr_writes_fail(monkeypatch, message="disk full"):
    """Every SaveZarrJob opens its store as usual (so there is a zarr.json to inspect) but its write fails."""
    import concurrent.futures
    from control.core.job_processing import SaveZarrJob

    real_submit = SaveZarrJob.submit

    def failing_submit(self):
        future, result = real_submit(self)
        if future is not None:
            future.result()
        failed = concurrent.futures.Future()
        failed.set_exception(RuntimeError(message))
        return failed, result

    monkeypatch.setattr(SaveZarrJob, "submit", failing_submit)


def get_test_objective_store():
    return ObjectiveStore(objectives_dict=OBJECTIVES, default_objective=DEFAULT_OBJECTIVE)


def get_test_laser_autofocus_controller(microscope: Microscope):
    return LaserAutofocusController(
        microcontroller=microscope.low_level_drivers.microcontroller,
        camera=microscope.addons.camera_focus,
        liveController=LiveController(microscope=microscope, camera=microscope.addons.camera_focus),
        stage=microscope.stage,
        piezo=microscope.addons.piezo_stage,
        objectiveStore=microscope.objective_store,
    )


def get_test_multi_point_controller(
    microscope: Microscope,
    callbacks: MultiPointControllerFunctions = NoOpCallbacks,
) -> MultiPointController:
    live_controller = get_test_live_controller(
        microscope=microscope, starting_objective=microscope.objective_store.default_objective
    )

    multi_point_controller = MultiPointController(
        microscope=microscope,
        live_controller=live_controller,
        autofocus_controller=get_test_autofocus_controller(
            microscope.camera,
            microscope.stage,
            live_controller,
            microscope.low_level_drivers.microcontroller,
        ),
        scan_coordinates=get_test_scan_coordinates(
            objective_store=microscope.objective_store, stage=microscope.stage, camera=microscope.camera
        ),
        callbacks=callbacks,
        objective_store=microscope.objective_store,
        laser_autofocus_controller=get_test_laser_autofocus_controller(microscope),
    )

    multi_point_controller.set_base_path(new_acquisition_base_path())
    multi_point_controller.start_new_experiment("unit test experiment")

    return multi_point_controller
