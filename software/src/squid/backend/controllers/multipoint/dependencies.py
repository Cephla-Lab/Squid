"""Service and controller dependency groupings for MultiPoint acquisition.

These dataclasses group related services and controllers to reduce the number
of constructor parameters in MultiPointWorker and MultiPointController.
Instead of 21+ individual parameters, we pass structured dependency objects.
"""

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from squid.backend.services import (
        CameraService,
        StageService,
        PeripheralService,
        PiezoService,
        FluidicsService,
        NL5Service,
        IlluminationService,
        FilterWheelService,
    )
    from squid.core.events import EventBus
    from squid.backend.controllers.autofocus import AutoFocusController
    from squid.backend.controllers.autofocus import LaserAutofocusController
    from squid.backend.controllers.autofocus.continuous_focus_lock import (
        ContinuousFocusLockController,
    )
    from squid.backend.controllers.autofocus.focus_lock_simulator import FocusLockSimulator


@dataclass
class AcquisitionServices:
    """Required and optional services for acquisition.

    Groups services used during acquisition to reduce constructor complexity.
    Required services must always be provided; optional services can be None.

    Attributes:
        camera: Camera service for image capture (required).
        stage: Stage service for XYZ positioning (required).
        peripheral: Peripheral service for hardware coordination (required).
        event_bus: Event bus for publishing acquisition events (required).
        illumination: Illumination control service.
        filter_wheel: Filter wheel positioning service.
        piezo: Piezo stage service for fine Z movement.
        fluidics: Fluidics system service for buffer changes.
        nl5: NL5 laser control service.
        stream_handler: Frame streaming handler for live display.
    """

    # Required services
    camera: "CameraService"
    stage: "StageService"
    peripheral: "PeripheralService"
    event_bus: "EventBus"

    # Optional services
    illumination: Optional["IlluminationService"] = None
    filter_wheel: Optional["FilterWheelService"] = None
    piezo: Optional["PiezoService"] = None
    fluidics: Optional["FluidicsService"] = None
    nl5: Optional["NL5Service"] = None
    stream_handler: Optional[object] = None

    def validate(self) -> None:
        """Validate that required services are present.

        Raises:
            ValueError: If any required service is None.
        """
        missing = []
        if self.camera is None:
            missing.append("camera")
        if self.stage is None:
            missing.append("stage")
        if self.peripheral is None:
            missing.append("peripheral")
        if self.event_bus is None:
            missing.append("event_bus")

        if missing:
            raise ValueError(f"Missing required services: {', '.join(missing)}")

    @property
    def has_illumination(self) -> bool:
        """Check if illumination service is available."""
        return self.illumination is not None

    @property
    def has_filter_wheel(self) -> bool:
        """Check if filter wheel service is available."""
        return self.filter_wheel is not None and self.filter_wheel.is_available()

    @property
    def has_piezo(self) -> bool:
        """Check if piezo service is available."""
        return self.piezo is not None and self.piezo.is_available

    @property
    def has_fluidics(self) -> bool:
        """Check if fluidics service is available."""
        return self.fluidics is not None

    @property
    def has_nl5(self) -> bool:
        """Check if NL5 service is available."""
        return self.nl5 is not None


@dataclass
class AcquisitionControllers:
    """Optional controllers for acquisition.

    Groups autofocus and focus lock controllers used during acquisition.
    All controllers are optional since not all systems have all capabilities.

    Attributes:
        autofocus: Software contrast-based autofocus controller.
        laser_autofocus: Hardware laser/reflection autofocus controller.
        focus_lock: Continuous focus lock controller (maintains focus in real-time).
    """

    autofocus: Optional["AutoFocusController"] = None
    laser_autofocus: Optional["LaserAutofocusController"] = None
    focus_lock: Optional["ContinuousFocusLockController | FocusLockSimulator"] = None

    def validate(self) -> None:
        """Validate controller configuration.

        Currently all controllers are optional, so validation is minimal.
        Override to add system-specific requirements.
        """
        pass

    @property
    def has_contrast_af(self) -> bool:
        """Check if contrast-based autofocus is available."""
        return self.autofocus is not None

    @property
    def has_laser_af(self) -> bool:
        """Check if laser autofocus is available."""
        return self.laser_autofocus is not None

    @property
    def has_focus_lock(self) -> bool:
        """Check if focus lock controller is available."""
        return self.focus_lock is not None

    @property
    def has_any_autofocus(self) -> bool:
        """Check if any autofocus capability is available."""
        return self.has_contrast_af or self.has_laser_af


@dataclass
class AcquisitionDependencies:
    """Complete set of dependencies for acquisition.

    Combines services and controllers into a single dependency container.
    This can be passed to MultiPointWorker instead of 21+ individual parameters.

    Attributes:
        services: Required and optional services.
        controllers: Optional autofocus controllers.
    """

    services: AcquisitionServices
    controllers: AcquisitionControllers

    def validate(self) -> None:
        """Validate all dependencies.

        Raises:
            ValueError: If validation fails.
        """
        self.services.validate()
        self.controllers.validate()

    @classmethod
    def create(
        cls,
        # Required
        camera: "CameraService",
        stage: "StageService",
        peripheral: "PeripheralService",
        event_bus: "EventBus",
        # Optional services
        illumination: Optional["IlluminationService"] = None,
        filter_wheel: Optional["FilterWheelService"] = None,
        piezo: Optional["PiezoService"] = None,
        fluidics: Optional["FluidicsService"] = None,
        nl5: Optional["NL5Service"] = None,
        stream_handler: Optional[object] = None,
        # Optional controllers
        autofocus: Optional["AutoFocusController"] = None,
        laser_autofocus: Optional["LaserAutofocusController"] = None,
        focus_lock: Optional["ContinuousFocusLockController | FocusLockSimulator"] = None,
    ) -> "AcquisitionDependencies":
        """Create dependencies from individual parameters.

        This factory method provides a convenient way to construct dependencies
        from the flat parameter list currently used by MultiPointWorker.

        Args:
            camera: Camera service (required).
            stage: Stage service (required).
            peripheral: Peripheral service (required).
            event_bus: Event bus (required).
            illumination: Illumination service (optional).
            filter_wheel: Filter wheel service (optional).
            piezo: Piezo service (optional).
            fluidics: Fluidics service (optional).
            nl5: NL5 service (optional).
            stream_handler: Stream handler (optional).
            autofocus: Contrast autofocus controller (optional).
            laser_autofocus: Laser autofocus controller (optional).
            focus_lock: Focus lock controller (optional).

        Returns:
            AcquisitionDependencies with services and controllers grouped.
        """
        services = AcquisitionServices(
            camera=camera,
            stage=stage,
            peripheral=peripheral,
            event_bus=event_bus,
            illumination=illumination,
            filter_wheel=filter_wheel,
            piezo=piezo,
            fluidics=fluidics,
            nl5=nl5,
            stream_handler=stream_handler,
        )
        controllers = AcquisitionControllers(
            autofocus=autofocus,
            laser_autofocus=laser_autofocus,
            focus_lock=focus_lock,
        )
        return cls(services=services, controllers=controllers)
