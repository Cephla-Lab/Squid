"""
Application context for dependency management.

Centralizes creation of microscope and controllers, replacing the
pattern where GUI creates and owns everything.

Usage:
    context = ApplicationContext(simulation=True)
    gui = context.create_gui()
    gui.show()

    # Later:
    context.shutdown()
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

import squid.logging

from control.microscope import Microscope
from control.core.display import LiveController
from control.core.display import StreamHandler
from control.core.acquisition import MultiPointController
from control.core.configuration import ChannelConfigurationManager
from control.core.navigation import ObjectiveStore
from control.gui_hcs import HighContentScreeningGui
from squid.services import ServiceRegistry
from squid.controllers import MicroscopeModeController, PeripheralsController
from squid.events import event_bus

if TYPE_CHECKING:
    from squid.qt_event_dispatcher import QtEventDispatcher
    from squid.ui_event_bus import UIEventBus


@dataclass
class Controllers:
    """
    Container for all controllers.

    This replaces the pattern where GUI has 20+ instance variables
    for different controllers.
    """

    live: "LiveController"
    stream_handler: "StreamHandler"
    microscope_mode: Optional["MicroscopeModeController"] = None
    peripherals: Optional["PeripheralsController"] = None
    multipoint: Optional["MultiPointController"] = None
    channel_config_manager: Optional["ChannelConfigurationManager"] = None
    objective_store: Optional["ObjectiveStore"] = None


class ApplicationContext:
    """
    Application-level context that owns all components.

    This replaces the pattern where GUI creates everything.
    Now: Application creates everything, GUI just displays.

    Example:
        # Create context
        context = ApplicationContext(simulation=True)

        # Create and show GUI
        gui = context.create_gui()
        gui.show()

        # When done
        context.shutdown()
    """

    def __init__(
        self, simulation: bool = False, external_controller_creation: bool = False
    ):
        """
        Initialize the application context.

        Args:
            simulation: If True, use simulated hardware
            external_controller_creation: If True, create controllers in ApplicationContext
                instead of letting Microscope create them internally. This enables
                better dependency injection and testability.
        """
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._simulation = simulation
        self._external_controller_creation = external_controller_creation
        self._microscope: Optional["Microscope"] = None
        self._controllers: Optional[Controllers] = None
        self._services: Optional["ServiceRegistry"] = None
        self._gui: Optional["HighContentScreeningGui"] = None

        # Qt/UI event handling - created lazily via create_ui_event_bus()
        self._qt_dispatcher: Optional["QtEventDispatcher"] = None
        self._ui_event_bus: Optional["UIEventBus"] = None

        self._log.info(
            f"Creating ApplicationContext (simulation={simulation}, "
            f"external_controller_creation={external_controller_creation})"
        )

        # Build components
        self._build_microscope()
        # Inject event_bus into ObjectiveStore for event publishing
        if self._microscope and self._microscope.objective_store:
            self._microscope.objective_store._event_bus = event_bus
        # Build services before controllers so controllers can receive them
        self._build_services()
        self._build_controllers()
        # Subscribe to objective changes to refresh channel configs
        from squid.events import ObjectiveChanged

        event_bus.subscribe(ObjectiveChanged, self._on_objective_changed)

    def _build_microscope(self) -> None:
        """Build the microscope from configuration."""
        from control.microscope import Microscope

        self._log.info("Building microscope...")
        self._microscope = Microscope.build_from_global_config(
            simulated=self._simulation,
            skip_controller_creation=self._external_controller_creation,
        )
        self._log.info("Microscope built successfully")

    def _build_controllers(self) -> None:
        """
        Build controllers container.

        If external_controller_creation is True, creates controllers here with
        explicit dependency injection. Otherwise, wraps controllers that
        Microscope created internally.
        """
        self._log.info("Building controllers...")

        assert self._microscope is not None, (
            "Microscope must be built before controllers"
        )
        microscope_mode_controller: Optional[MicroscopeModeController] = None
        peripherals_controller: Optional[PeripheralsController] = None

        if self._external_controller_creation:
            self._create_controllers_externally()
            self._log.info("Controllers built successfully")
            return
        else:
            # Wrap controllers that Microscope created internally
            assert self._microscope.live_controller is not None, (
                "LiveController not created by Microscope"
            )
            assert self._microscope.stream_handler is not None, (
                "StreamHandler not created by Microscope"
            )
            # Ensure LiveController is bus-enabled
            self._microscope.live_controller.attach_event_bus(event_bus)

            # Create new controllers that manage mode and peripherals
            microscope_mode_controller = self._create_microscope_mode_controller()
            peripherals_controller = self._create_peripherals_controller()
            # Inject services into LiveController for service-based operations
            if self._services:
                self._microscope.live_controller._camera_service = self._services.get("camera")
                self._microscope.live_controller._illumination_service = self._services.get("illumination")  # type: ignore[attr-defined]
                self._microscope.live_controller._peripheral_service = self._services.get("peripheral")  # type: ignore[attr-defined]
                self._microscope.live_controller._filter_wheel_service = self._services.get("filter_wheel")  # type: ignore[attr-defined]
                self._microscope.live_controller._nl5_service = self._services.get("nl5")  # type: ignore[attr-defined]
        # Refresh channel configs now that controller exists
        self._refresh_channel_configs(microscope_mode_controller)

        self._controllers = Controllers(
            live=self._microscope.live_controller,
            stream_handler=self._microscope.stream_handler,
            microscope_mode=microscope_mode_controller,
            peripherals=peripherals_controller,
            channel_config_manager=self._microscope.channel_configuration_manager,
            objective_store=self._microscope.objective_store,
        )

        self._log.info("Controllers built successfully")

    def _create_controllers_externally(self) -> None:
        """Create controllers with explicit dependency injection."""
        from control.core.display import LiveController
        from control.core.display import StreamHandler, NoOpStreamHandlerFunctions

        assert self._microscope is not None, (
            "Microscope must be built before creating controllers"
        )

        # Create StreamHandler
        stream_handler = StreamHandler(handler_functions=NoOpStreamHandlerFunctions)

        # Create LiveController with EventBus for event-driven communication
        live_controller = LiveController(
            microscope=self._microscope,
            camera=self._microscope.camera,
            event_bus=event_bus,
            camera_service=self._services.get("camera") if self._services else None,
            illumination_service=self._services.get("illumination") if self._services else None,
            peripheral_service=self._services.get("peripheral") if self._services else None,
            filter_wheel_service=self._services.get("filter_wheel") if self._services else None,
            nl5_service=self._services.get("nl5") if self._services else None,
        )

        # Assign controllers to Microscope (it expects these to exist)
        self._microscope.stream_handler = stream_handler
        self._microscope.live_controller = live_controller

        # Handle focus camera if present
        if self._microscope.addons.camera_focus:
            stream_handler_focus = StreamHandler(
                handler_functions=NoOpStreamHandlerFunctions
            )
            live_controller_focus = LiveController(
                microscope=self._microscope,
                camera=self._microscope.addons.camera_focus,
                event_bus=event_bus,
                control_illumination=False,
                for_displacement_measurement=True,
            )
            self._microscope.stream_handler_focus = stream_handler_focus
            self._microscope.live_controller_focus = live_controller_focus

        # Create new controllers that manage mode and peripherals
        microscope_mode_controller = self._create_microscope_mode_controller()
        peripherals_controller = self._create_peripherals_controller()
        self._refresh_channel_configs(microscope_mode_controller)

        # Create Controllers container
        self._controllers = Controllers(
            live=live_controller,
            stream_handler=stream_handler,
            microscope_mode=microscope_mode_controller,
            peripherals=peripherals_controller,
            channel_config_manager=self._microscope.channel_configuration_manager,
            objective_store=self._microscope.objective_store,
        )

    def _create_microscope_mode_controller(self) -> MicroscopeModeController:
        """Create MicroscopeModeController with dependencies."""
        assert self._microscope is not None

        channel_configs = self._get_channel_configs_for_current_objective()
        camera_service = (
            self._services.get("camera") if self._services is not None else None
        )
        illumination_service = (
            self._services.get("illumination") if self._services is not None else None
        )
        filter_wheel_service = (
            self._services.get("filter_wheel") if self._services is not None else None
        )

        return MicroscopeModeController(
            camera_service=camera_service,
            illumination_service=illumination_service,
            filter_wheel_service=filter_wheel_service,
            channel_configs=channel_configs,
            event_bus=event_bus,
        )

    def _refresh_channel_configs(
        self, controller: Optional[MicroscopeModeController]
    ) -> None:
        """Update channel configs on the controller from the current objective."""
        if controller is None:
            return
        channel_configs = self._get_channel_configs_for_current_objective()
        if channel_configs:
            controller.update_channel_configs(channel_configs)

    def _create_peripherals_controller(self) -> PeripheralsController:
        """Create PeripheralsController with dependencies."""
        assert self._microscope is not None

        # Get services for optional peripherals
        objective_service = self._services.get("objective_changer") if self._services else None
        spinning_disk_service = self._services.get("spinning_disk") if self._services else None
        piezo_service = self._services.get("piezo") if self._services else None

        return PeripheralsController(
            objective_service=objective_service,
            spinning_disk_service=spinning_disk_service,
            piezo_service=piezo_service,
            objective_store=self._microscope.objective_store,
            event_bus=event_bus,
        )

    def _get_channel_configs_for_current_objective(self) -> dict:
        """Return channel config mapping for the current objective."""
        assert self._microscope is not None
        manager = self._microscope.channel_configuration_manager
        objective_store = self._microscope.objective_store
        if manager is None or objective_store is None:
            return {}
        current_obj = getattr(objective_store, "current_objective", None)
        if not current_obj:
            return {}
        try:
            configs = manager.get_configurations(current_obj)
        except Exception:
            return {}
        return {mode.name: mode for mode in configs}

    # Event handlers
    def _on_objective_changed(self, event) -> None:
        """Refresh channel configs when objective changes."""
        if self._controllers and self._controllers.microscope_mode:
            self._refresh_channel_configs(self._controllers.microscope_mode)

    def _build_services(self) -> None:
        """Build service layer."""
        from squid.services import (
            ServiceRegistry,
            CameraService,
            StageService,
            PeripheralService,
            IlluminationService,
            FilterWheelService,
            PiezoService,
            FluidicsService,
            ObjectiveChangerService,
            SpinningDiskService,
            NL5Service,
            MovementService,
        )
        from squid.events import event_bus

        assert self._microscope is not None, "Microscope must be built before services"

        self._log.info("Building services...")

        self._services = ServiceRegistry(event_bus)

        self._services.register(
            "camera", CameraService(self._microscope.camera, event_bus)
        )

        # Focus camera service for laser autofocus
        if self._microscope.addons.camera_focus:
            self._services.register(
                "camera_focus",
                CameraService(self._microscope.addons.camera_focus, event_bus),
            )

        self._services.register(
            "stage", StageService(self._microscope.stage, event_bus)
        )

        self._services.register(
            "peripheral",
            PeripheralService(
                self._microscope.low_level_drivers.microcontroller, event_bus
            ),
        )

        if getattr(self._microscope, "illumination_controller", None):
            self._services.register(
                "illumination",
                IlluminationService(
                    self._microscope.illumination_controller,
                    event_bus,
                ),
            )

        filter_wheel = getattr(self._microscope.addons, "emission_filter_wheel", None)
        self._services.register(
            "filter_wheel",
            FilterWheelService(filter_wheel, event_bus),
        )

        # Piezo service (integral to Z-stack acquisition and focus locking)
        piezo = getattr(self._microscope.addons, "piezo_stage", None)
        self._services.register(
            "piezo",
            PiezoService(piezo, event_bus),
        )

        # Fluidics service (for MERFISH and other fluidics-based protocols)
        fluidics = getattr(self._microscope.addons, "fluidics", None)
        if fluidics:
            self._services.register(
                "fluidics",
                FluidicsService(fluidics, event_bus),
            )

        objective_changer = getattr(self._microscope.addons, "objective_changer", None)
        if objective_changer:
            self._services.register(
                "objective_changer",
                ObjectiveChangerService(objective_changer, event_bus),
            )

        spinning_disk = getattr(self._microscope.addons, "xlight", None)
        if spinning_disk:
            self._services.register(
                "spinning_disk",
                SpinningDiskService(spinning_disk, event_bus),
            )

        nl5 = getattr(self._microscope.addons, "nl5", None)
        if nl5:
            self._services.register(
                "nl5",
                NL5Service(nl5, event_bus),
            )

        # Movement service for stage/piezo position polling
        # This replaces MovementUpdater from qt_controllers.py
        piezo = getattr(self._microscope.addons, "piezo_stage", None)
        movement_service = MovementService(
            self._microscope.stage,
            piezo,
            event_bus,
        )
        self._services.register("movement", movement_service)
        movement_service.start()  # Start polling immediately

        self._log.info("Services built successfully")

    @property
    def microscope(self) -> "Microscope":
        """Get the microscope instance."""
        if self._microscope is None:
            raise RuntimeError("Microscope not initialized")
        return self._microscope

    @property
    def controllers(self) -> Controllers:
        """Get the controllers container."""
        if self._controllers is None:
            raise RuntimeError("Controllers not initialized")
        return self._controllers

    @property
    def services(self) -> "ServiceRegistry":
        """Get the service registry."""
        if self._services is None:
            raise RuntimeError("Services not initialized")
        return self._services

    @property
    def is_simulation(self) -> bool:
        """Check if running in simulation mode."""
        return self._simulation

    def create_ui_event_bus(self) -> "UIEventBus":
        """Create UIEventBus for widget subscriptions.

        Must be called from Qt main thread after QApplication is created.
        Returns the UIEventBus that widgets should use for subscriptions.

        This ensures widget event handlers run on the Qt main thread,
        preventing GUI crashes from worker-thread events.
        """
        if self._ui_event_bus is None:
            from squid.qt_event_dispatcher import QtEventDispatcher
            from squid.ui_event_bus import UIEventBus

            self._qt_dispatcher = QtEventDispatcher()
            self._ui_event_bus = UIEventBus(event_bus, self._qt_dispatcher)
            self._log.info("Created UIEventBus for thread-safe widget updates")

            # Wire to services so they can expose it
            if self._services is not None:
                self._services.ui_event_bus = self._ui_event_bus

        return self._ui_event_bus

    @property
    def ui_event_bus(self) -> Optional["UIEventBus"]:
        """Get the UIEventBus, or None if not yet created."""
        return self._ui_event_bus

    def create_gui(self) -> "HighContentScreeningGui":
        """
        Create the GUI with pre-built controllers.

        Returns:
            HighContentScreeningGui instance
        """
        # Import here to avoid circular imports
        from control.gui_hcs import HighContentScreeningGui

        assert self._microscope is not None, "Microscope must be built before GUI"

        self._log.info("Creating GUI...")
        # For now, pass microscope directly - GUI still creates some things
        # Future: pass Controllers dataclass only
        gui = HighContentScreeningGui(
            microscope=self._microscope,
            services=self._services,
            is_simulation=self._simulation,
        )
        self._gui = gui
        self._log.info("GUI created successfully")
        return gui

    def shutdown(self) -> None:
        """Clean shutdown of all components."""
        self._log.info("Shutting down application...")

        if self._gui:
            self._gui.close()
            self._gui = None

        # Shutdown controllers
        if self._controllers:
            if self._controllers.live:
                self._controllers.live.stop_live()
            # StreamHandler doesn't have a stop method currently

        # Shutdown services
        if self._services:
            self._services.shutdown()
            self._services = None

        # Shutdown microscope
        if self._microscope:
            self._microscope.close()
            self._microscope = None

        self._controllers = None

        self._log.info("Application shutdown complete")
