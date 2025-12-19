from _def import OBJECTIVES, DEFAULT_OBJECTIVE
from squid.backend.controllers.autofocus import AutoFocusController
from squid.backend.controllers.live_controller import LiveController
from squid.backend.controllers.multipoint.multi_point_controller import MultiPointController
from squid.backend.managers import ObjectiveStore
from squid.backend.managers import ScanCoordinates
from squid.backend.microcontroller import Microcontroller
from squid.backend.microscope import Microscope
import numpy as np
from squid.core.abc import AbstractStage, AbstractCamera
from squid.core.events import event_bus
from squid.backend.services import (
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
        camera_service=services.get("camera"),
        event_bus=event_bus,
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
    live_controller: LiveController,
    services: ServiceRegistry,
):
    return AutoFocusController(
        liveController=live_controller,
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
) -> MultiPointController:
    services = _build_test_services(microscope)
    live_controller = get_test_live_controller(
        microscope=microscope,
        starting_objective=microscope.objective_store.default_objective,
        services=services,
    )

    multi_point_controller = MultiPointController(
        live_controller=live_controller,
        autofocus_controller=get_test_autofocus_controller(
            live_controller,
            services,
        ),
        channel_configuration_manager=microscope.channel_configuration_manager,
        scan_coordinates=get_test_scan_coordinates(
            objective_store=microscope.objective_store,
            stage=microscope.stage,
            camera=microscope.camera,
        ),
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
