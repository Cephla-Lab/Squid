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

import squid.core.logging

from squid.backend.microscope import Microscope
from squid.backend.controllers.live_controller import LiveController
from squid.backend.io.stream_handler import StreamHandler
from squid.backend.controllers.multipoint import MultiPointController
from squid.backend.managers import ChannelConfigurationManager
from squid.backend.managers import ObjectiveStore
from squid.backend.managers.scan_coordinates import ScanCoordinates
from squid.backend.managers.navigation_state_service import NavigationViewerStateService
from squid.backend.services import ServiceRegistry
from squid.backend.controllers import MicroscopeModeController, PeripheralsController, ImageClickController
from squid.core.events import event_bus
from squid.core.mode_gate import GlobalModeGate
from squid.backend.controllers.autofocus import AutoFocusController, LaserAutofocusController

if TYPE_CHECKING:
    from squid.ui.qt_event_dispatcher import QtEventDispatcher
    from squid.ui.ui_event_bus import UIEventBus
    from squid.backend.controllers.tracking_controller import TrackingControllerCore


@dataclass
class Controllers:
    """
    Container for all controllers.

    This replaces the pattern where GUI has 20+ instance variables
    for different controllers.
    """

    live: "LiveController"
    stream_handler: "StreamHandler"
    stream_handler_focus: Optional["StreamHandler"] = None
    microscope_mode: Optional["MicroscopeModeController"] = None
    peripherals: Optional["PeripheralsController"] = None
    multipoint: Optional["MultiPointController"] = None
    autofocus: Optional["AutoFocusController"] = None
    laser_autofocus: Optional["LaserAutofocusController"] = None
    live_focus: Optional["LiveController"] = None
    channel_config_manager: Optional["ChannelConfigurationManager"] = None
    objective_store: Optional["ObjectiveStore"] = None
    scan_coordinates: Optional["ScanCoordinates"] = None
    image_click: Optional["ImageClickController"] = None
    tracking: Optional["TrackingControllerCore"] = None


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
        self, simulation: bool = False
    ):
        """
        Initialize the application context.

        Args:
            simulation: If True, use simulated hardware
        """
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._simulation = simulation
        self._microscope: Optional["Microscope"] = None
        self._controllers: Optional[Controllers] = None
        self._services: Optional["ServiceRegistry"] = None
        self._gui: Optional["HighContentScreeningGui"] = None

        # Qt/UI event handling - created lazily via create_ui_event_bus()
        self._qt_dispatcher: Optional["QtEventDispatcher"] = None
        self._ui_event_bus: Optional["UIEventBus"] = None

        self._mode_gate: Optional[GlobalModeGate] = None
        self._navigation_state_service: Optional[NavigationViewerStateService] = None
        self._camera_frame_callback_id: Optional[str] = None
        self._camera_focus_frame_callback_id: Optional[str] = None

        self._log.info(
            f"Creating ApplicationContext (simulation={simulation})"
        )

        # Ensure the core EventBus dispatch thread is running early
        event_bus.start()
        self._mode_gate = GlobalModeGate(event_bus)

        # Build components
        self._build_microscope()
        # Inject event_bus into ObjectiveStore for event publishing
        if self._microscope and self._microscope.objective_store:
            self._microscope.objective_store._event_bus = event_bus
        # Build services before controllers so controllers can receive them
        self._build_services()
        self._build_controllers()
        self._initialize_hardware()
        # Subscribe to objective changes to refresh channel configs
        from squid.core.events import ObjectiveChanged

        event_bus.subscribe(ObjectiveChanged, self._on_objective_changed)

    def _initialize_hardware(self) -> None:
        """Backend-owned one-time hardware initialization.

        Keeps main_window free of hardware setup and callback wiring.
        """
        if self._microscope is None or self._services is None or self._controllers is None:
            return

        try:
            import _def as _config
        except Exception:
            _config = None

        # Stage limits + home
        stage_service = self._services.get("stage")
        if stage_service is not None:
            try:
                stage_config = stage_service.get_config()
                x_config = stage_config.X_AXIS
                y_config = stage_config.Y_AXIS
                z_config = stage_config.Z_AXIS
                stage_service.set_limits(
                    x_pos_mm=x_config.MAX_POSITION,
                    x_neg_mm=x_config.MIN_POSITION,
                    y_pos_mm=y_config.MAX_POSITION,
                    y_neg_mm=y_config.MIN_POSITION,
                    z_pos_mm=z_config.MAX_POSITION,
                    z_neg_mm=z_config.MIN_POSITION,
                )
            except Exception:
                self._log.exception("Failed to set stage limits")

            try:
                x_home = True
                y_home = True
                z_home = True
                if _config is not None:
                    x_home = bool(getattr(_config, "HOMING_ENABLED_X", True))
                    y_home = bool(getattr(_config, "HOMING_ENABLED_Y", True))
                    z_home = bool(getattr(_config, "HOMING_ENABLED_Z", True))
                if x_home or y_home or z_home:
                    stage_service.home(x=x_home, y=y_home, z=z_home, theta=False)
                else:
                    self._log.info("Skipping stage homing; disabled in config")
            except Exception:
                self._log.exception("Failed to home stage")

            # Restore cached position (previously done in main_window)
            try:
                if _config is not None and all(
                    [
                        getattr(_config, "HOMING_ENABLED_X", False),
                        getattr(_config, "HOMING_ENABLED_Y", False),
                        getattr(_config, "HOMING_ENABLED_Z", False),
                    ]
                ):
                    import squid.backend.drivers.stages.stage_utils as stage_utils

                    cached_pos = stage_utils.get_cached_position()
                    safety_z = float(getattr(_config, "Z_HOME_SAFETY_POINT", 0)) / 1000.0
                    if cached_pos is not None:
                        stage_service.move_to(
                            x_mm=float(cached_pos.x_mm),
                            y_mm=float(cached_pos.y_mm),
                            blocking=True,
                        )
                        target_z = float(cached_pos.z_mm) if safety_z < float(cached_pos.z_mm) else safety_z
                        stage_service.move_to(z_mm=target_z, blocking=True)
                    else:
                        stage_service.move_to(z_mm=safety_z, blocking=True)
            except Exception:
                self._log.exception("Failed to restore cached stage position")

        # Camera callback wiring (live display path)
        camera_service = self._services.get("camera")
        if camera_service is not None and self._camera_frame_callback_id is None:
            try:
                if _config is not None and getattr(_config, "DEFAULT_TRIGGER_MODE", None) is not None:
                    from squid.core.abc import CameraAcquisitionMode
                    from _def import TriggerMode

                    if getattr(_config, "DEFAULT_TRIGGER_MODE") == TriggerMode.HARDWARE:
                        camera_service.set_acquisition_mode(CameraAcquisitionMode.HARDWARE_TRIGGER)
                    else:
                        camera_service.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
                self._camera_frame_callback_id = camera_service.add_frame_callback(
                    self._controllers.stream_handler.on_new_frame
                )
                camera_service.enable_callbacks(enabled=True)
            except Exception:
                self._log.exception("Failed to initialize camera callbacks")

        # Focus camera callback wiring (laser autofocus)
        focus_camera_service = self._services.get("camera_focus")
        if (
            focus_camera_service is not None
            and self._controllers.stream_handler_focus is not None
            and self._camera_focus_frame_callback_id is None
        ):
            try:
                from squid.core.abc import CameraAcquisitionMode

                focus_camera_service.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
                self._camera_focus_frame_callback_id = focus_camera_service.add_frame_callback(
                    self._controllers.stream_handler_focus.on_new_frame
                )
                focus_camera_service.enable_callbacks(enabled=True)
                focus_camera_service.start_streaming()
            except Exception:
                self._log.exception("Failed to initialize focus camera callbacks")

        # Objective changer home (best-effort)
        objective_service = self._services.get("objective_changer")
        if objective_service is not None:
            try:
                objective_service.home()
            except Exception:
                self._log.debug("Objective changer home not supported", exc_info=True)

    def _build_microscope(self) -> None:
        """Build the microscope from configuration."""
        from squid.backend.microscope import Microscope

        self._log.info("Building microscope...")
        self._microscope = Microscope.build_from_global_config(
            simulated=self._simulation,
            skip_controller_creation=True,
        )
        self._log.info("Microscope built successfully")

    def _build_controllers(self) -> None:
        """
        Build controllers container.

        Controllers are created here with explicit dependency injection.
        """
        self._log.info("Building controllers...")

        assert self._microscope is not None, (
            "Microscope must be built before controllers"
        )
        self._create_controllers_externally()
        self._log.info("Controllers built successfully")

    def _create_controllers_externally(self) -> None:
        """Create controllers with explicit dependency injection."""
        from squid.backend.controllers.live_controller import LiveController
        from squid.backend.io.stream_handler import (
            StreamHandler,
            StreamHandlerFunctions,
        )

        assert self._microscope is not None, (
            "Microscope must be built before creating controllers"
        )
        assert self._services is not None, (
            "Services must be built before creating controllers"
        )

        # Create LiveController with EventBus for event-driven communication
        camera_service = self._services.get("camera")
        if camera_service is None:
            raise RuntimeError("CameraService not available")
        illumination_service = self._services.get("illumination")
        live_controller = LiveController(
            camera_service=camera_service,
            event_bus=event_bus,
            illumination_service=illumination_service,
            peripheral_service=self._services.get("peripheral"),
            filter_wheel_service=self._services.get("filter_wheel"),
            nl5_service=self._services.get("nl5"),
            mode_gate=self.mode_gate,
            control_illumination=illumination_service is not None,
            camera="main",
        )

        # Create StreamHandler with backend hooks preserved across Qt wiring.
        stream_handler = StreamHandler(
            handler_functions=StreamHandlerFunctions(
                image_to_display=lambda _img: None,
                packet_image_to_write=lambda _img, _fid, _ts: None,
                signal_new_frame_received=live_controller.on_new_frame,
                accept_new_frame=lambda: bool(getattr(live_controller, "is_live", False)),
            )
        )
        stream_handler_focus: Optional[StreamHandler] = None
        live_controller_focus: Optional[LiveController] = None

        # Assign controllers to Microscope (it expects these to exist)
        self._microscope.stream_handler = stream_handler
        self._microscope.live_controller = live_controller

        # Handle focus camera if present
        if self._microscope.addons.camera_focus:
            focus_camera_service = self._services.get("camera_focus")
            if focus_camera_service is None:
                raise RuntimeError("Focus CameraService not available")
            live_controller_focus = LiveController(
                camera_service=focus_camera_service,
                event_bus=event_bus,
                control_illumination=False,
                for_displacement_measurement=True,
                mode_gate=self.mode_gate,
                camera="focus",
            )
            stream_handler_focus = StreamHandler(
                handler_functions=StreamHandlerFunctions(
                    image_to_display=lambda _img: None,
                    packet_image_to_write=lambda _img, _fid, _ts: None,
                    signal_new_frame_received=live_controller_focus.on_new_frame,
                    accept_new_frame=lambda: True,
                )
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
            stream_handler_focus=stream_handler_focus if self._microscope.addons.camera_focus else None,
            live_focus=live_controller_focus if self._microscope.addons.camera_focus else None,
            microscope_mode=microscope_mode_controller,
            peripherals=peripherals_controller,
            channel_config_manager=self._microscope.channel_configuration_manager,
            objective_store=self._microscope.objective_store,
        )
        self._controllers.autofocus = self._build_autofocus_controller()
        self._controllers.laser_autofocus = self._build_laser_autofocus_controller()
        self._controllers.scan_coordinates = self._build_scan_coordinates()
        self._controllers.multipoint = self._build_multipoint_controller(
            autofocus=self._controllers.autofocus,
            laser_autofocus=self._controllers.laser_autofocus,
            scan_coordinates=self._controllers.scan_coordinates,
        )
        # Phase 8: ImageClickController for click-to-move
        self._controllers.image_click = self._build_image_click_controller()
        self._controllers.tracking = self._build_tracking_controller()

        # Backend navigation state publisher (UI can subscribe via UIEventBus).
        try:
            camera_service = self._services.get("camera") if self._services else None
            if camera_service is not None:
                self._navigation_state_service = NavigationViewerStateService(
                    objective_store=self._microscope.objective_store,
                    camera_service=camera_service,
                    event_bus=event_bus,
                )
        except Exception:
            self._log.exception("Failed to initialize NavigationViewerStateService")

    def _build_tracking_controller(self) -> Optional["TrackingControllerCore"]:
        """Create TrackingControllerCore (backend-only, services-only)."""
        try:
            import _def as _config  # Local import to avoid circularity at module import time
        except Exception:
            _config = None

        if self._microscope is None or self._services is None or _config is None:
            return None
        if not getattr(_config, "ENABLE_TRACKING", False):
            return None

        from squid.backend.controllers.tracking_controller import TrackingControllerCore

        camera_service = self._services.get("camera")
        stage_service = self._services.get("stage")
        if camera_service is None or stage_service is None:
            raise RuntimeError("Required services missing for TrackingControllerCore")

        return TrackingControllerCore(
            event_bus=event_bus,
            camera_service=camera_service,
            stage_service=stage_service,
            live_controller=self._microscope.live_controller,
            peripheral_service=self._services.get("peripheral"),
            channel_config_manager=self._microscope.channel_configuration_manager,
            objective_store=self._microscope.objective_store,
            mode_gate=self.mode_gate,
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
            objective_store=self._microscope.objective_store,
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

    def _build_scan_coordinates(self) -> Optional[ScanCoordinates]:
        """Create scan coordinates model without UI callbacks."""
        if self._microscope is None:
            return None
        return ScanCoordinates(
            objectiveStore=self._microscope.objective_store,
            stage=self._microscope.stage,
            camera=self._microscope.camera,
            event_bus=event_bus,
        )

    def _build_autofocus_controller(self) -> Optional[AutoFocusController]:
        """Create AutoFocusController without Qt dependencies."""
        if self._microscope is None or self._services is None:
            return None
        live_controller = self._microscope.live_controller
        if live_controller is None:
            raise RuntimeError("LiveController must be created before AutoFocusController")
        camera_service = self._services.get("camera")
        stage_service = self._services.get("stage")
        peripheral_service = self._services.get("peripheral")
        if camera_service is None or stage_service is None or peripheral_service is None:
            raise RuntimeError("Required services missing for AutoFocusController")
        return AutoFocusController(
            liveController=live_controller,
            camera_service=camera_service,
            stage_service=stage_service,
            peripheral_service=peripheral_service,
            nl5_service=self._services.get("nl5"),
            illumination_service=self._services.get("illumination"),
            stream_handler=getattr(self._microscope, "stream_handler", None),
            event_bus=event_bus,
            mode_gate=self.mode_gate,
        )

    def _build_laser_autofocus_controller(self) -> Optional[LaserAutofocusController]:
        """Create LaserAutofocusController core without Qt dependencies."""
        try:
            import _def as _config  # Local import to avoid circularity at module import time
        except Exception:
            _config = None

        if self._microscope is None or _config is None:
            return None
        if not getattr(_config, "SUPPORT_LASER_AUTOFOCUS", False):
            return None
        if self._services is None:
            return None
        camera_focus_service = self._services.get("camera_focus")
        stage_service = self._services.get("stage")
        peripheral_service = self._services.get("peripheral")
        if camera_focus_service is None or stage_service is None or peripheral_service is None:
            raise RuntimeError("Required services missing for LaserAutofocusController")
        return LaserAutofocusController(
            camera_service=camera_focus_service,
            stage_service=stage_service,
            peripheral_service=peripheral_service,
            piezo_service=self._services.get("piezo"),
            objectiveStore=self._microscope.objective_store,
            laserAFSettingManager=getattr(self._microscope, "laser_af_settings_manager", None),
            event_bus=event_bus,
            stream_handler=getattr(self._microscope, "stream_handler_focus", None),
        )

    def _build_multipoint_controller(
        self,
        autofocus: Optional[AutoFocusController],
        laser_autofocus: Optional[LaserAutofocusController],
        scan_coordinates: Optional[ScanCoordinates],
    ) -> Optional[MultiPointController]:
        """Create MultiPointController using services and EventBus callbacks."""
        if self._microscope is None or self._services is None:
            return None
        if autofocus is None:
            raise RuntimeError("MultiPointController requires an AutoFocusController")
        if scan_coordinates is None:
            scan_coordinates = self._build_scan_coordinates()
        if self._microscope.live_controller is None:
            raise RuntimeError("LiveController must be created before MultiPointController")
        camera_service = self._services.get("camera")
        stage_service = self._services.get("stage")
        peripheral_service = self._services.get("peripheral")
        if camera_service is None or stage_service is None or peripheral_service is None:
            raise RuntimeError("Required services missing for MultiPointController")
        return MultiPointController(
            self._microscope.live_controller,
            autofocus,
            self._microscope.objective_store,
            self._microscope.channel_configuration_manager,
            scan_coordinates=scan_coordinates,
            laser_autofocus_controller=laser_autofocus,
            camera_service=camera_service,
            stage_service=stage_service,
            peripheral_service=peripheral_service,
            piezo_service=self._services.get("piezo"),
            fluidics_service=self._services.get("fluidics"),
            nl5_service=self._services.get("nl5"),
            illumination_service=self._services.get("illumination"),
            filter_wheel_service=self._services.get("filter_wheel"),
            event_bus=event_bus,
            mode_gate=self.mode_gate,
            stream_handler=getattr(self._microscope, "stream_handler", None),
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

    def _build_image_click_controller(self) -> Optional[ImageClickController]:
        """Create ImageClickController for click-to-move functionality."""
        if self._microscope is None:
            return None
        if self._services is None:
            return None

        camera_service = self._services.get("camera")
        if camera_service is None:
            self._log.warning("No camera service available for ImageClickController")
            return None
        stage_service = self._services.get("stage")
        if stage_service is None:
            self._log.warning("No stage service available for ImageClickController")
            return None

        # Check for INVERTED_OBJECTIVE config
        try:
            import _def as _config
            inverted = getattr(_config, "INVERTED_OBJECTIVE", False)
        except Exception:
            inverted = False

        return ImageClickController(
            objective_store=self._microscope.objective_store,
            camera_service=camera_service,
            stage_service=stage_service,
            event_bus=event_bus,
            inverted_objective=inverted,
        )

    # Event handlers
    def _on_objective_changed(self, event) -> None:
        """Refresh channel configs when objective changes."""
        if self._controllers and self._controllers.microscope_mode:
            self._refresh_channel_configs(self._controllers.microscope_mode)

    def _build_services(self) -> None:
        """Build service layer."""
        from squid.backend.services import (
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
        from squid.core.events import event_bus

        assert self._microscope is not None, "Microscope must be built before services"

        self._log.info("Building services...")

        self._services = ServiceRegistry(event_bus)

        self._services.register(
            "camera", CameraService(self._microscope.camera, event_bus, mode_gate=self.mode_gate)
        )

        # Focus camera service for laser autofocus
        if self._microscope.addons.camera_focus:
            self._services.register(
                "camera_focus",
                CameraService(self._microscope.addons.camera_focus, event_bus, mode_gate=self.mode_gate),
            )

        self._services.register(
            "stage", StageService(self._microscope.stage, event_bus, mode_gate=self.mode_gate)
        )

        self._services.register(
            "peripheral",
            PeripheralService(
                self._microscope.low_level_drivers.microcontroller, event_bus, mode_gate=self.mode_gate
            ),
        )

        if getattr(self._microscope, "illumination_controller", None):
            self._services.register(
                "illumination",
                IlluminationService(
                    self._microscope.illumination_controller,
                    event_bus,
                    mode_gate=self.mode_gate,
                ),
            )

        filter_wheel = getattr(self._microscope.addons, "emission_filter_wheel", None)
        self._services.register(
            "filter_wheel",
            FilterWheelService(filter_wheel, event_bus, mode_gate=self.mode_gate),
        )

        # Piezo service (integral to Z-stack acquisition and focus locking)
        piezo = getattr(self._microscope.addons, "piezo_stage", None)
        self._services.register(
            "piezo",
            PiezoService(piezo, event_bus, mode_gate=self.mode_gate),
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

        # Wire piezo service to focus camera for simulation mode
        # This allows the camera to read piezo position directly for immediate updates
        camera_focus = getattr(self._microscope.addons, "camera_focus", None)
        piezo_service = self._services.get("piezo")
        if camera_focus is not None and piezo_service is not None:
            if hasattr(camera_focus, 'set_piezo_service'):
                camera_focus.set_piezo_service(piezo_service)

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

    @property
    def mode_gate(self) -> GlobalModeGate:
        """Get the global mode gate."""
        if self._mode_gate is None:
            raise RuntimeError("Mode gate not initialized")
        return self._mode_gate

    def create_ui_event_bus(self) -> "UIEventBus":
        """Create UIEventBus for widget subscriptions.

        Must be called from Qt main thread after QApplication is created.
        Returns the UIEventBus that widgets should use for subscriptions.

        This ensures widget event handlers run on the Qt main thread,
        preventing GUI crashes from worker-thread events.
        """
        if self._ui_event_bus is None:
            from squid.ui.qt_event_dispatcher import QtEventDispatcher
            from squid.ui.ui_event_bus import UIEventBus

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
        from squid.ui.main_window import HighContentScreeningGui

        assert self._microscope is not None, "Microscope must be built before GUI"

        self._log.info("Creating GUI...")
        # For now, pass microscope directly - GUI still creates some things
        # Future: pass Controllers dataclass only
        gui = HighContentScreeningGui(
            microscope=self._microscope,
            controllers=self._controllers,
            services=self._services,
            is_simulation=self._simulation,
        )
        self._gui = gui
        self._log.info("GUI created successfully")
        return gui

    def shutdown(self) -> None:
        """Clean shutdown of all components."""
        self._log.info("Shutting down application...")

        if self._gui is not None:
            try:
                setattr(self._gui, "_skip_close_confirmation", True)
            except Exception:
                pass
            try:
                self._gui.close()
            except Exception:
                self._log.exception("Failed to close GUI during shutdown")
            finally:
                self._gui = None

        # Shutdown controllers
        if self._controllers:
            if self._controllers.live:
                self._controllers.live.stop_live()
            if getattr(self._controllers, "live_focus", None):
                try:
                    self._controllers.live_focus.stop_live()  # type: ignore[union-attr]
                except Exception:
                    self._log.exception("Failed to stop focus LiveController during shutdown")
            # StreamHandler doesn't have a stop method currently

        self._shutdown_hardware()

        # Shutdown services
        if self._services:
            self._services.shutdown()
            self._services = None

        # Shutdown microscope
        if self._microscope:
            self._microscope.close()
            self._microscope = None

        self._controllers = None

        # Stop the global EventBus dispatch thread
        event_bus.stop()
        # Clear subscribers to avoid leaking old controller/service handlers across tests/runs.
        event_bus.clear()

        self._log.info("Application shutdown complete")

    def _shutdown_hardware(self) -> None:
        """Best-effort hardware reset that must not raise.

        The goal is to keep UI code free of hardware orchestration, and
        centralize shutdown behavior here.
        """
        if self._services is None:
            return
        if self._microscope is None:
            return

        try:
            import _def as _def  # Local import to avoid circulars at import time
        except Exception:
            _def = None  # type: ignore[assignment]

        stage_service = self._services.get("stage")
        if stage_service is not None:
            try:
                import squid.backend.drivers.stages.stage_utils as stage_utils

                stage_utils.cache_position(
                    pos=stage_service.get_position(),
                    stage_config=stage_service.get_config(),
                )
            except Exception:
                self._log.exception("Failed to cache stage position during shutdown")

            if _def is not None:
                try:
                    stage_service.move_to(z_mm=float(_def.OBJECTIVE_RETRACTED_POS_MM), blocking=True)
                except Exception:
                    self._log.exception("Failed to retract Z during shutdown")

        filter_service = self._services.get("filter_wheel")
        if filter_service is not None:
            try:
                filter_service.set_filter_wheel_position({1: 1})
            except Exception:
                self._log.exception("Failed to reset emission filter wheel during shutdown")

        camera_service = self._services.get("camera")
        if camera_service is not None:
            try:
                camera_service.stop_streaming()
            except Exception:
                self._log.exception("Failed to stop camera streaming during shutdown")

        focus_camera_service = self._services.get("camera_focus")
        if focus_camera_service is not None:
            try:
                focus_camera_service.stop_streaming()
            except Exception:
                self._log.exception("Failed to stop focus camera streaming during shutdown")

        if _def is not None and getattr(_def, "USE_XERYON", False):
            objective_service = self._services.get("objective_changer")
            if objective_service is not None:
                try:
                    objective_service.set_position(0)
                except Exception:
                    self._log.exception("Failed to reset objective changer during shutdown")

        try:
            self._microscope.low_level_drivers.microcontroller.turn_off_all_pid()
        except Exception:
            self._log.exception("Failed to turn off microcontroller PID during shutdown")

        if _def is not None and getattr(_def, "ENABLE_CELLX", False):
            try:
                cellx = getattr(self._microscope.addons, "cellx", None)
                if cellx is not None:
                    for channel in [1, 2, 3, 4]:
                        try:
                            cellx.turn_off(channel)
                        except Exception:
                            pass
                    try:
                        cellx.close()
                    except Exception:
                        pass
            except Exception:
                self._log.exception("Failed to shut down CellX during shutdown")

        if _def is not None and getattr(_def, "RUN_FLUIDICS", False):
            try:
                fluidics = getattr(self._microscope.addons, "fluidics", None)
                if fluidics is not None:
                    fluidics.close()
            except Exception:
                self._log.exception("Failed to shut down fluidics during shutdown")
