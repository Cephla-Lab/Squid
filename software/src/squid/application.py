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

from squid.mcs.microscope import Microscope
from squid.mcs.controllers.live_controller import LiveController
from squid.storage.stream_handler import StreamHandler
from squid.ops.acquisition import MultiPointController
from squid.ops.configuration import ChannelConfigurationManager
from squid.ops.navigation import ObjectiveStore
from squid.ops.navigation.scan_coordinates import ScanCoordinates
from squid.mcs.services import ServiceRegistry
from squid.mcs.controllers import MicroscopeModeController, PeripheralsController, ImageClickController
from squid.core.events import event_bus
from squid.core.actor import BackendActor, BackendCommandRouter
from squid.core.coordinator import ResourceCoordinator, GlobalMode, Resource
from squid.mcs.controllers.autofocus import AutoFocusController, LaserAutofocusController

if TYPE_CHECKING:
    from squid.ui.qt_event_dispatcher import QtEventDispatcher
    from squid.ui.ui_event_bus import UIEventBus


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
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._simulation = simulation
        self._external_controller_creation = external_controller_creation
        self._microscope: Optional["Microscope"] = None
        self._controllers: Optional[Controllers] = None
        self._services: Optional["ServiceRegistry"] = None
        self._gui: Optional["HighContentScreeningGui"] = None

        # Qt/UI event handling - created lazily via create_ui_event_bus()
        self._qt_dispatcher: Optional["QtEventDispatcher"] = None
        self._ui_event_bus: Optional["UIEventBus"] = None

        # Backend actor for command processing
        self._backend_actor: Optional[BackendActor] = None
        self._command_router: Optional[BackendCommandRouter] = None

        # Resource coordinator for managing shared resources
        self._coordinator: Optional[ResourceCoordinator] = None

        self._log.info(
            f"Creating ApplicationContext (simulation={simulation}, "
            f"external_controller_creation={external_controller_creation})"
        )

        # Ensure the core EventBus dispatch thread is running early
        event_bus.start()

        # Build components
        self._build_microscope()
        # Inject event_bus into ObjectiveStore for event publishing
        if self._microscope and self._microscope.objective_store:
            self._microscope.objective_store._event_bus = event_bus
        # Build services before controllers so controllers can receive them
        self._build_services()
        self._build_controllers()
        # Build the resource coordinator for managing shared resources
        self._build_coordinator()
        # Build and start the backend actor for command processing
        self._build_backend_actor()
        # Subscribe to objective changes to refresh channel configs
        from squid.core.events import ObjectiveChanged

        event_bus.subscribe(ObjectiveChanged, self._on_objective_changed)

    def _build_microscope(self) -> None:
        """Build the microscope from configuration."""
        from squid.mcs.microscope import Microscope

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
            stream_handler_focus=getattr(self._microscope, "stream_handler_focus", None),
            live_focus=getattr(self._microscope, "live_controller_focus", None),
            microscope_mode=microscope_mode_controller,
            peripherals=peripherals_controller,
            channel_config_manager=self._microscope.channel_configuration_manager,
            objective_store=self._microscope.objective_store,
        )
        # Build higher-level controllers that should be UI-agnostic
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

        self._log.info("Controllers built successfully")

    def _create_controllers_externally(self) -> None:
        """Create controllers with explicit dependency injection."""
        from squid.mcs.controllers.live_controller import LiveController
        from squid.storage.stream_handler import StreamHandler, NoOpStreamHandlerFunctions

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
            subscribe_to_bus=False,
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
                subscribe_to_bus=False,
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
            subscribe_to_bus=False,
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
            subscribe_to_bus=False,
        )

    def _build_scan_coordinates(self) -> Optional[ScanCoordinates]:
        """Create scan coordinates model without UI callbacks."""
        if self._microscope is None:
            return None
        return ScanCoordinates(
            objectiveStore=self._microscope.objective_store,
            stage=self._microscope.stage,
            camera=self._microscope.camera,
            update_callback=None,
            # Event bus not passed - UI wires it via main_window
        )

    def _build_autofocus_controller(self) -> Optional[AutoFocusController]:
        """Create AutoFocusController without Qt dependencies."""
        if self._microscope is None:
            return None
        try:
            controller = AutoFocusController(
                self._microscope.camera,
                self._microscope.stage,
                self._microscope.live_controller,
                self._microscope.low_level_drivers.microcontroller,
                finished_fn=None,
                image_to_display_fn=None,
                nl5=self._microscope.addons.nl5,
                camera_service=self._services.get("camera") if self._services else None,
                stage_service=self._services.get("stage") if self._services else None,
                peripheral_service=self._services.get("peripheral") if self._services else None,
                event_bus=event_bus,
                subscribe_to_bus=False,
            )
            return controller
        except Exception as exc:  # pragma: no cover - defensive
            self._log.error(f"Failed to build AutoFocusController: {exc}")
            return None

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
        focus_camera = getattr(self._microscope.addons, "camera_focus", None)
        if focus_camera is None:
            return None

        focus_live = getattr(self._microscope, "live_controller_focus", None)
        controller = LaserAutofocusController(
            self._microscope.low_level_drivers.microcontroller,
            focus_camera,
            focus_live or self._microscope.live_controller,
            self._microscope.stage,
            getattr(self._microscope.addons, "piezo_stage", None),
            self._microscope.objective_store,
            getattr(self._microscope, "laser_af_settings_manager", None),
            camera_service=self._services.get("camera_focus") if self._services else None,
            stage_service=self._services.get("stage") if self._services else None,
            peripheral_service=self._services.get("peripheral") if self._services else None,
            piezo_service=self._services.get("piezo") if self._services else None,
            event_bus=event_bus,
            subscribe_to_bus=False,
        )
        return controller

    def _build_multipoint_controller(
        self,
        autofocus: Optional[AutoFocusController],
        laser_autofocus: Optional[LaserAutofocusController],
        scan_coordinates: Optional[ScanCoordinates],
    ) -> Optional[MultiPointController]:
        """Create MultiPointController using services and EventBus callbacks."""
        if self._microscope is None:
            return None
        autofocus_controller = autofocus or getattr(self._microscope, "autofocus_controller", None)
        if autofocus_controller is None:
            raise RuntimeError("MultiPointController requires an AutoFocusController")
        if scan_coordinates is None:
            scan_coordinates = self._build_scan_coordinates()
        return MultiPointController(
            self._microscope,
            self._microscope.live_controller,
            autofocus_controller,
            self._microscope.objective_store,
            self._microscope.channel_configuration_manager,
            scan_coordinates=scan_coordinates,
            laser_autofocus_controller=laser_autofocus,
            camera_service=self._services.get("camera") if self._services else None,
            stage_service=self._services.get("stage") if self._services else None,
            peripheral_service=self._services.get("peripheral") if self._services else None,
            piezo_service=self._services.get("piezo") if self._services else None,
            fluidics_service=self._services.get("fluidics") if self._services else None,
            nl5_service=self._services.get("nl5") if self._services else None,
            event_bus=event_bus,
            subscribe_to_bus=False,
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

        # Check for INVERTED_OBJECTIVE config
        try:
            import _def as _config
            inverted = getattr(_config, "INVERTED_OBJECTIVE", False)
        except Exception:
            inverted = False

        return ImageClickController(
            objective_store=self._microscope.objective_store,
            camera_service=camera_service,
            event_bus=event_bus,
            inverted_objective=inverted,
            subscribe_to_bus=False,  # Will be routed via BackendActor
        )

    # Event handlers
    def _on_objective_changed(self, event) -> None:
        """Refresh channel configs when objective changes."""
        if self._controllers and self._controllers.microscope_mode:
            self._refresh_channel_configs(self._controllers.microscope_mode)

    def _build_coordinator(self) -> None:
        """Build the ResourceCoordinator for managing shared resources.

        The coordinator:
        1. Manages leases on shared resources (camera, stage, illumination)
        2. Tracks global mode based on active leases
        3. Publishes events when mode or leases change
        """
        from squid.core.events import (
            GlobalModeChanged,
            LeaseAcquired,
            LeaseReleased,
            LeaseRevoked,
        )

        self._log.info("Building resource coordinator...")

        self._coordinator = ResourceCoordinator(
            watchdog_interval_s=1.0,
            default_lease_timeout_s=None,  # No automatic timeout by default
        )

        # Wire coordinator callbacks to EventBus events
        def on_mode_change(old_mode: GlobalMode, new_mode: GlobalMode) -> None:
            event_bus.publish(GlobalModeChanged(
                old_mode=old_mode.name,
                new_mode=new_mode.name,
            ))

        def on_lease_acquired(lease) -> None:
            event_bus.publish(LeaseAcquired(
                lease_id=lease.lease_id,
                owner=lease.owner,
                resources=[r.name for r in lease.resources],
            ))

        def on_lease_released(lease) -> None:
            event_bus.publish(LeaseReleased(
                lease_id=lease.lease_id,
                owner=lease.owner,
            ))

        def on_lease_revoked(lease, reason: str) -> None:
            event_bus.publish(LeaseRevoked(
                lease_id=lease.lease_id,
                owner=lease.owner,
                reason=reason,
            ))

        self._coordinator.on_mode_change(on_mode_change)
        self._coordinator.on_lease_acquired(on_lease_acquired)
        self._coordinator.on_lease_released(on_lease_released)
        self._coordinator.on_lease_revoked(on_lease_revoked)

        # Start the coordinator (watchdog thread)
        self._coordinator.start()

        # Inject coordinator into controllers that support resource leasing
        if self._controllers:
            if getattr(self._controllers, "live", None) is not None:
                setattr(self._controllers.live, "_coordinator", self._coordinator)
            if getattr(self._controllers, "autofocus", None) is not None:
                setattr(self._controllers.autofocus, "_coordinator", self._coordinator)
            if getattr(self._controllers, "multipoint", None) is not None:
                setattr(self._controllers.multipoint, "_coordinator", self._coordinator)

        self._log.info("Resource coordinator started")

    def _build_backend_actor(self) -> None:
        """Build the backend actor and command router.

        The BackendActor processes commands on a dedicated thread,
        ensuring all controller logic runs in a predictable context.
        The BackendCommandRouter subscribes to command events and
        routes them to the actor's priority queue.
        """
        from squid.core.events import (
            # Live commands
            StartLiveCommand,
            StopLiveCommand,
            SetTriggerModeCommand,
            SetTriggerFPSCommand,
            SetFilterAutoSwitchCommand,
            UpdateIlluminationCommand,
            SetDisplayResolutionScalingCommand,
            # Mode commands
            SetMicroscopeModeCommand,
            UpdateChannelConfigurationCommand,
            # Peripheral commands
            SetObjectiveCommand,
            SetSpinningDiskPositionCommand,
            SetSpinningDiskSpinningCommand,
            SetDiskDichroicCommand,
            SetDiskEmissionFilterCommand,
            SetPiezoPositionCommand,
            MovePiezoRelativeCommand,
            # Autofocus commands
            StartAutofocusCommand,
            StopAutofocusCommand,
            SetAutofocusParamsCommand,
            # Acquisition commands
            StartAcquisitionCommand,
            StopAcquisitionCommand,
            PauseAcquisitionCommand,
            ResumeAcquisitionCommand,
            SetFluidicsRoundsCommand,
            SetAcquisitionParametersCommand,
            SetAcquisitionPathCommand,
            SetAcquisitionChannelsCommand,
            StartNewExperimentCommand,
            # Laser autofocus commands
            SetLaserAFPropertiesCommand,
            InitializeLaserAFCommand,
            SetLaserAFCharacterizationModeCommand,
            UpdateLaserAFThresholdCommand,
            MoveToLaserAFTargetCommand,
            SetLaserAFReferenceCommand,
            MeasureLaserAFDisplacementCommand,
            CaptureLaserAFFrameCommand,
        )

        self._log.info("Building backend actor...")

        self._backend_actor = BackendActor()
        self._command_router = BackendCommandRouter(event_bus, self._backend_actor)

        # Register command types that have backend handlers wired
        command_types = []

        # LiveController command handling via backend actor
        if self._controllers and self._controllers.live:
            live = self._controllers.live
            # Ensure we don't double-handle via direct EventBus subscriptions
            if hasattr(live, "detach_event_bus_commands"):
                live.detach_event_bus_commands()
            self._backend_actor.register_handler(StartLiveCommand, live._on_start_live_command)
            self._backend_actor.register_handler(StopLiveCommand, live._on_stop_live_command)
            self._backend_actor.register_handler(SetTriggerModeCommand, live._on_set_trigger_mode_command)
            self._backend_actor.register_handler(SetTriggerFPSCommand, live._on_set_trigger_fps_command)
            self._backend_actor.register_handler(SetFilterAutoSwitchCommand, live._on_set_filter_auto_switch)
            self._backend_actor.register_handler(UpdateIlluminationCommand, live._on_update_illumination)
            self._backend_actor.register_handler(
                SetDisplayResolutionScalingCommand, live._on_set_display_resolution_scaling
            )
            command_types.extend(
                [
                    StartLiveCommand,
                    StopLiveCommand,
                    SetTriggerModeCommand,
                    SetTriggerFPSCommand,
                    SetFilterAutoSwitchCommand,
                    UpdateIlluminationCommand,
                    SetDisplayResolutionScalingCommand,
                ]
            )

        # MicroscopeModeController
        if self._controllers and self._controllers.microscope_mode:
            mode_controller = self._controllers.microscope_mode
            if hasattr(mode_controller, "detach_event_bus_commands"):
                mode_controller.detach_event_bus_commands()
            self._backend_actor.register_handler(SetMicroscopeModeCommand, mode_controller._on_set_mode)
            self._backend_actor.register_handler(UpdateChannelConfigurationCommand, mode_controller._on_update_config)
            command_types.extend([SetMicroscopeModeCommand, UpdateChannelConfigurationCommand])

        # PeripheralsController
        if self._controllers and self._controllers.peripherals:
            peripherals = self._controllers.peripherals
            if hasattr(peripherals, "detach_event_bus_commands"):
                peripherals.detach_event_bus_commands()
            self._backend_actor.register_handler(SetObjectiveCommand, peripherals._on_set_objective)
            self._backend_actor.register_handler(SetSpinningDiskPositionCommand, peripherals._on_set_disk_position)
            self._backend_actor.register_handler(SetSpinningDiskSpinningCommand, peripherals._on_set_spinning)
            self._backend_actor.register_handler(SetDiskDichroicCommand, peripherals._on_set_dichroic)
            self._backend_actor.register_handler(SetDiskEmissionFilterCommand, peripherals._on_set_emission)
            self._backend_actor.register_handler(SetPiezoPositionCommand, peripherals._on_set_piezo)
            self._backend_actor.register_handler(MovePiezoRelativeCommand, peripherals._on_move_piezo_relative)
            command_types.extend(
                [
                    SetObjectiveCommand,
                    SetSpinningDiskPositionCommand,
                    SetSpinningDiskSpinningCommand,
                    SetDiskDichroicCommand,
                    SetDiskEmissionFilterCommand,
                    SetPiezoPositionCommand,
                    MovePiezoRelativeCommand,
                ]
            )

        # AutoFocusController
        if self._controllers and self._controllers.autofocus:
            autofocus = self._controllers.autofocus
            if hasattr(autofocus, "detach_event_bus_commands"):
                autofocus.detach_event_bus_commands()
            self._backend_actor.register_handler(StartAutofocusCommand, autofocus._on_start_command)
            self._backend_actor.register_handler(StopAutofocusCommand, autofocus._on_stop_command)
            self._backend_actor.register_handler(SetAutofocusParamsCommand, autofocus._on_set_params_command)
            command_types.extend(
                [
                    StartAutofocusCommand,
                    StopAutofocusCommand,
                    SetAutofocusParamsCommand,
                ]
            )

        # LaserAutofocusController (core)
        if self._controllers and self._controllers.laser_autofocus:
            laser_af = self._controllers.laser_autofocus
            if hasattr(laser_af, "detach_event_bus_commands"):
                laser_af.detach_event_bus_commands()
            self._backend_actor.register_handler(SetLaserAFPropertiesCommand, laser_af._on_set_properties)
            self._backend_actor.register_handler(InitializeLaserAFCommand, laser_af._on_initialize)
            self._backend_actor.register_handler(SetLaserAFCharacterizationModeCommand, laser_af._on_set_characterization_mode)
            self._backend_actor.register_handler(UpdateLaserAFThresholdCommand, laser_af._on_update_threshold)
            self._backend_actor.register_handler(MoveToLaserAFTargetCommand, laser_af._on_move_to_target)
            self._backend_actor.register_handler(SetLaserAFReferenceCommand, laser_af._on_set_reference)
            self._backend_actor.register_handler(MeasureLaserAFDisplacementCommand, laser_af._on_measure_displacement)
            self._backend_actor.register_handler(CaptureLaserAFFrameCommand, laser_af._on_capture_frame)
            command_types.extend(
                [
                    SetLaserAFPropertiesCommand,
                    InitializeLaserAFCommand,
                    SetLaserAFCharacterizationModeCommand,
                    UpdateLaserAFThresholdCommand,
                    MoveToLaserAFTargetCommand,
                    SetLaserAFReferenceCommand,
                    MeasureLaserAFDisplacementCommand,
                    CaptureLaserAFFrameCommand,
                ]
            )

        # MultiPointController
        if self._controllers and self._controllers.multipoint:
            multipoint = self._controllers.multipoint
            if hasattr(multipoint, "detach_event_bus_commands"):
                multipoint.detach_event_bus_commands()
            self._backend_actor.register_handler(SetFluidicsRoundsCommand, multipoint._on_set_fluidics_rounds)
            self._backend_actor.register_handler(SetAcquisitionParametersCommand, multipoint._on_set_acquisition_parameters)
            self._backend_actor.register_handler(SetAcquisitionPathCommand, multipoint._on_set_acquisition_path)
            self._backend_actor.register_handler(SetAcquisitionChannelsCommand, multipoint._on_set_acquisition_channels)
            self._backend_actor.register_handler(StartNewExperimentCommand, multipoint._on_start_new_experiment)
            self._backend_actor.register_handler(StartAcquisitionCommand, multipoint._on_start_acquisition)
            self._backend_actor.register_handler(StopAcquisitionCommand, multipoint._on_stop_acquisition)
            command_types.extend(
                [
                    SetFluidicsRoundsCommand,
                    SetAcquisitionParametersCommand,
                    SetAcquisitionPathCommand,
                    SetAcquisitionChannelsCommand,
                    StartNewExperimentCommand,
                    StartAcquisitionCommand,
                    StopAcquisitionCommand,
                ]
            )

        # ImageClickController (Phase 8)
        if self._controllers and self._controllers.image_click:
            from squid.core.events import ImageCoordinateClickedCommand, ClickToMoveEnabledChanged
            image_click = self._controllers.image_click
            if hasattr(image_click, "detach_event_bus_commands"):
                image_click.detach_event_bus_commands()
            self._backend_actor.register_handler(
                ImageCoordinateClickedCommand, image_click._on_image_clicked
            )
            self._backend_actor.register_handler(
                ClickToMoveEnabledChanged, image_click._on_click_to_move_changed
            )
            command_types.extend([ImageCoordinateClickedCommand, ClickToMoveEnabledChanged])

        # Register command types to route through the backend actor
        if command_types:
            self._command_router.register_commands(command_types)

        # Start the backend actor
        self._backend_actor.start()

        self._log.info("Backend actor started")

    def _build_services(self) -> None:
        """Build service layer."""
        from squid.mcs.services import (
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

    @property
    def coordinator(self) -> Optional[ResourceCoordinator]:
        """Get the resource coordinator.

        Returns None if not yet built.
        """
        return self._coordinator

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

        if self._gui:
            self._gui.close()
            self._gui = None

        # Shutdown controllers
        if self._controllers:
            if self._controllers.live:
                self._controllers.live.stop_live()
            # StreamHandler doesn't have a stop method currently

        # Stop the backend actor before services/microscope
        if self._backend_actor:
            self._backend_actor.stop()
            self._backend_actor = None
        if self._command_router:
            self._command_router.unregister_all()
            self._command_router = None

        # Stop the resource coordinator
        if self._coordinator:
            self._coordinator.stop()
            self._coordinator = None

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

        self._log.info("Application shutdown complete")
