from control._def import OBJECTIVES, DEFAULT_OBJECTIVE
from control.core.autofocus import AutoFocusController
from control.core.display import LiveController
from control.core.acquisition.multi_point_controller import (
    NoOpCallbacks,
    MultiPointController,
)
from control.core.acquisition.multi_point_utils import MultiPointControllerFunctions
from control.core.navigation import ObjectiveStore
from control.core.navigation import ScanCoordinates
from control.microcontroller import Microcontroller
from control.microscope import Microscope
import numpy as np
from squid.abc import AbstractStage, AbstractCamera
from squid.events import event_bus
from squid.services import (
    ServiceRegistry,
    CameraService,
    StageService,
    PeripheralService,
    IlluminationService,
    FilterWheelService,
    PiezoService,
)


class _FakeLaserAutofocusProperties:
    """Lightweight stub that mimics LaserAF properties without hardware."""

    def __init__(self) -> None:
        self.has_reference: bool = False
        self.reference_image = None

    def set_reference_image(self, image) -> None:
        self.reference_image = image
        self.has_reference = True


class FakeLaserAutofocusController:
    """Fast, hardware-free laser autofocus stand-in for tests."""

    def __init__(self) -> None:
        self.laser_af_properties = _FakeLaserAutofocusProperties()
        self.characterization_mode = False
        # Provide a deterministic image payload for any logging/save paths
        self.image = np.zeros((10, 10), dtype=np.uint8)

    def move_to_target(self, displacement_um: float) -> None:  # noqa: ARG002
        return None

    def get_image(self):
        return self.image


def _build_test_services(microscope: Microscope) -> ServiceRegistry:
    """Create a ServiceRegistry for tests with the simulated microscope."""
    services = ServiceRegistry(event_bus)
    services.register("camera", CameraService(microscope.camera, event_bus))
    services.register("stage", StageService(microscope.stage, event_bus))
    services.register(
        "peripheral",
        PeripheralService(microscope.low_level_drivers.microcontroller, event_bus),
    )
    services.register(
        "illumination",
        IlluminationService(microscope.illumination_controller, event_bus),
    )
    filter_wheel = getattr(microscope.addons, "emission_filter_wheel", None)
    services.register("filter_wheel", FilterWheelService(filter_wheel, event_bus))
    piezo = getattr(microscope.addons, "piezo_stage", None)
    services.register("piezo", PiezoService(piezo, event_bus))
    return services


def get_test_live_controller(
    microscope: Microscope, starting_objective, services: ServiceRegistry | None = None
) -> LiveController:
    services = services or _build_test_services(microscope)
    controller = LiveController(
        microscope=microscope,
        camera=microscope.camera,
        event_bus=event_bus,
        camera_service=services.get("camera"),
        illumination_service=services.get("illumination"),
        peripheral_service=services.get("peripheral"),
        filter_wheel_service=services.get("filter_wheel"),
    )
    # Avoid emission filter calls when there is no hardware in simulation.
    controller.enable_channel_auto_filter_switching = False

    controller.set_microscope_mode(
        microscope.configuration_manager.channel_manager.get_configurations(
            objective=starting_objective
        )[0]
    )
    return controller


def get_test_autofocus_controller(
    camera,
    stage: AbstractStage,
    live_controller: LiveController,
    microcontroller: Microcontroller,
    services: ServiceRegistry | None = None,
):
    services = services or _build_test_services(live_controller.microscope)
    return AutoFocusController(
        camera=camera,
        stage=stage,
        liveController=live_controller,
        microcontroller=microcontroller,
        nl5=None,
        finished_fn=lambda: None,
        image_to_display_fn=lambda image: None,
        camera_service=services.get("camera"),
        stage_service=services.get("stage"),
        peripheral_service=services.get("peripheral"),
        event_bus=event_bus,
    )


def get_test_scan_coordinates(
    objective_store: ObjectiveStore,
    stage: AbstractStage,
    camera: AbstractCamera,
):
    return ScanCoordinates(objectiveStore=objective_store, stage=stage, camera=camera)


def get_test_objective_store():
    return ObjectiveStore(
        objectives_dict=OBJECTIVES, default_objective=DEFAULT_OBJECTIVE
    )


def get_test_laser_autofocus_controller(microscope: Microscope):
    # Use a fake implementation to keep integration tests fast and hardware-free.
    return FakeLaserAutofocusController()


def get_test_multi_point_controller(
    microscope: Microscope,
    callbacks: MultiPointControllerFunctions = NoOpCallbacks,
) -> MultiPointController:
    services = _build_test_services(microscope)
    live_controller = get_test_live_controller(
        microscope=microscope,
        starting_objective=microscope.objective_store.default_objective,
        services=services,
    )

    multi_point_controller = MultiPointController(
        microscope=microscope,
        live_controller=live_controller,
        autofocus_controller=get_test_autofocus_controller(
            microscope.camera,
            microscope.stage,
            live_controller,
            microscope.low_level_drivers.microcontroller,
            services=services,
        ),
        channel_configuration_manager=microscope.channel_configuration_manager,
        scan_coordinates=get_test_scan_coordinates(
            objective_store=microscope.objective_store,
            stage=microscope.stage,
            camera=microscope.camera,
        ),
        callbacks=callbacks,
        objective_store=microscope.objective_store,
        laser_autofocus_controller=get_test_laser_autofocus_controller(microscope),
        camera_service=services.get("camera"),
        stage_service=services.get("stage"),
        peripheral_service=services.get("peripheral"),
        piezo_service=services.get("piezo"),
        event_bus=event_bus,
    )
    # Keep simulated acquisition fast and avoid long waits for callbacks.
    multi_point_controller.frame_wait_timeout_override_s = 0.2

    multi_point_controller.set_base_path("/tmp/")
    multi_point_controller.start_new_experiment("unit test experiment")

    return multi_point_controller
