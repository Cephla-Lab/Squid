import shutil
import tempfile

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
_acquisition_output_dirs: list = []


def new_acquisition_base_path() -> str:
    base_path = tempfile.mkdtemp(prefix="squid_unit_test_acquisition_")
    _acquisition_output_dirs.append(base_path)
    return base_path


def cleanup_stub_acquisition_dirs():
    while _acquisition_output_dirs:
        shutil.rmtree(_acquisition_output_dirs.pop(), ignore_errors=True)


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
