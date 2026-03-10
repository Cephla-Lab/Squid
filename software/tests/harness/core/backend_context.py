"""
Backend context for test setup.

The BackendContext class provides a context manager that sets up a simulated
microscope and all associated services/controllers for testing. It handles
proper initialization and cleanup.
"""

from __future__ import annotations

import tempfile
from typing import Optional, TYPE_CHECKING

import _def
import squid.backend.microscope as microscope
from squid.core.events import event_bus, EventBus
from squid.backend.services import (
    ServiceRegistry,
    CameraService,
    StageService,
    PeripheralService,
    IlluminationService,
    FilterWheelService,
    PiezoService,
)
from squid.backend.controllers.autofocus import AutoFocusController
from squid.backend.controllers.live_controller import LiveController
from squid.backend.controllers.multipoint import MultiPointController
from squid.backend.managers import ScanCoordinates

from tests.harness.core.event_monitor import EventMonitor

if TYPE_CHECKING:
    from squid.backend.managers import ObjectiveStore, ChannelConfigurationManager


class BackendContext:
    """
    Context manager for setting up a simulated backend for tests.

    This class provides:
    - A simulated microscope with all hardware
    - Service registry with all services
    - Controllers (created on demand)
    - Event monitoring

    Usage:
        with BackendContext() as ctx:
            # Access microscope
            scope = ctx.microscope

            # Access services
            camera = ctx.camera_service
            stage = ctx.stage_service

            # Access controllers (created on demand)
            mpc = ctx.multipoint_controller
            live = ctx.live_controller

            # Access event monitor
            monitor = ctx.event_monitor
            monitor.subscribe(AcquisitionFinished)

            # ... run test ...

    The context manager ensures proper cleanup on exit.
    """

    def __init__(
        self,
        simulation: bool = True,
        merge_channels: bool = False,
        base_path: Optional[str] = None,
    ):
        """
        Initialize the backend context.

        Args:
            simulation: If True, use simulated hardware
            merge_channels: Value for _def.MERGE_CHANNELS
            base_path: Base path for acquisitions (uses temp dir if None)
        """
        self._simulation = simulation
        self._merge_channels = merge_channels
        self._base_path = base_path or tempfile.mkdtemp(prefix="squid_test_")

        # These are created on __enter__
        self._microscope: Optional[microscope.Microscope] = None
        self._services: Optional[ServiceRegistry] = None
        self._event_monitor: Optional[EventMonitor] = None

        # Controllers are created on demand
        self._live_controller: Optional[LiveController] = None
        self._autofocus_controller: Optional[AutoFocusController] = None
        self._multipoint_controller: Optional[MultiPointController] = None
        self._scan_coordinates: Optional[ScanCoordinates] = None

    def __enter__(self) -> "BackendContext":
        """Set up the backend context."""
        # Set global config
        _def.MERGE_CHANNELS = self._merge_channels

        # Create simulated microscope
        self._microscope = microscope.Microscope.build_from_global_config(self._simulation)

        # Build services
        self._services = self._build_services()

        # Start event bus and create monitor
        event_bus.start()
        self._event_monitor = EventMonitor(event_bus)

        return self

    def __exit__(self, *args) -> None:
        """Clean up the backend context."""
        # Stop any running acquisition
        if self._multipoint_controller:
            try:
                self._multipoint_controller.request_abort_aquisition()
                # Wait briefly for acquisition to stop
                import time
                for _ in range(20):  # Up to 2 seconds
                    if not self._multipoint_controller.acquisition_in_progress:
                        break
                    time.sleep(0.1)
            except Exception:
                pass  # Ignore cleanup errors

            # Unsubscribe multipoint controller from event bus to prevent stale handlers
            try:
                self._unsubscribe_multipoint_controller()
            except Exception:
                pass

        # Clear scan coordinates
        if self._scan_coordinates:
            try:
                self._scan_coordinates.clear_regions()
            except Exception:
                pass

        # Unsubscribe event monitor
        if self._event_monitor:
            self._event_monitor.unsubscribe_all()

        # Ensure stale subscribers are cleared between tests
        event_bus.clear()

        # Close microscope
        if self._microscope:
            try:
                self._microscope.close()
            except Exception:
                pass  # Ignore cleanup errors

    def _unsubscribe_multipoint_controller(self) -> None:
        """Unsubscribe the multipoint controller from event bus commands."""
        from squid.core.events import (
            SetFluidicsRoundsCommand,
            SetAcquisitionParametersCommand,
            SetAcquisitionPathCommand,
            SetAcquisitionChannelsCommand,
            StartNewExperimentCommand,
            StartAcquisitionCommand,
            StopAcquisitionCommand,
            AcquisitionWorkerFinished,
            AcquisitionWorkerProgress,
        )

        mpc = self._multipoint_controller
        commands = [
            (SetFluidicsRoundsCommand, mpc._on_set_fluidics_rounds),
            (SetAcquisitionParametersCommand, mpc._on_set_acquisition_parameters),
            (SetAcquisitionPathCommand, mpc._on_set_acquisition_path),
            (SetAcquisitionChannelsCommand, mpc._on_set_acquisition_channels),
            (StartNewExperimentCommand, mpc._on_start_new_experiment),
            (StartAcquisitionCommand, mpc._on_start_acquisition),
            (StopAcquisitionCommand, mpc._on_stop_acquisition),
            (AcquisitionWorkerFinished, mpc._on_worker_finished),
            (AcquisitionWorkerProgress, mpc._on_worker_progress),
        ]

        for event_type, handler in commands:
            try:
                event_bus.unsubscribe(event_type, handler)
            except Exception:
                pass  # Ignore if not subscribed

    def _build_services(self) -> ServiceRegistry:
        """Create the service registry with all services."""
        services = ServiceRegistry(event_bus)

        services.register("camera", CameraService(self._microscope.camera, event_bus))
        services.register("stage", StageService(self._microscope.stage, event_bus))
        services.register(
            "peripheral",
            PeripheralService(self._microscope.low_level_drivers.microcontroller, event_bus),
        )
        services.register(
            "illumination",
            IlluminationService(self._microscope.illumination_controller, event_bus),
        )

        # Optional services
        filter_wheel = getattr(self._microscope.addons, "emission_filter_wheel", None)
        services.register("filter_wheel", FilterWheelService(filter_wheel, event_bus))

        piezo = getattr(self._microscope.addons, "piezo_stage", None)
        services.register("piezo", PiezoService(piezo, event_bus))

        return services

    # =========================================================================
    # Properties - Core Components
    # =========================================================================

    @property
    def microscope(self) -> microscope.Microscope:
        """Get the microscope instance."""
        if self._microscope is None:
            raise RuntimeError("BackendContext not entered. Use 'with BackendContext() as ctx:'")
        return self._microscope

    @property
    def event_bus(self) -> EventBus:
        """Get the event bus."""
        return event_bus

    @property
    def event_monitor(self) -> EventMonitor:
        """Get the event monitor."""
        if self._event_monitor is None:
            raise RuntimeError("BackendContext not entered. Use 'with BackendContext() as ctx:'")
        return self._event_monitor

    @property
    def services(self) -> ServiceRegistry:
        """Get the service registry."""
        if self._services is None:
            raise RuntimeError("BackendContext not entered. Use 'with BackendContext() as ctx:'")
        return self._services

    @property
    def base_path(self) -> str:
        """Get the base path for acquisitions."""
        return self._base_path

    # =========================================================================
    # Properties - Services
    # =========================================================================

    @property
    def camera_service(self) -> CameraService:
        """Get the camera service."""
        return self.services.get("camera")

    @property
    def stage_service(self) -> StageService:
        """Get the stage service."""
        return self.services.get("stage")

    @property
    def peripheral_service(self) -> PeripheralService:
        """Get the peripheral service."""
        return self.services.get("peripheral")

    @property
    def illumination_service(self) -> IlluminationService:
        """Get the illumination service."""
        return self.services.get("illumination")

    @property
    def filter_wheel_service(self) -> FilterWheelService:
        """Get the filter wheel service."""
        return self.services.get("filter_wheel")

    @property
    def piezo_service(self) -> PiezoService:
        """Get the piezo service."""
        return self.services.get("piezo")

    # =========================================================================
    # Properties - Managers
    # =========================================================================

    @property
    def objective_store(self) -> "ObjectiveStore":
        """Get the objective store."""
        return self.microscope.objective_store

    @property
    def channel_config_manager(self) -> "ChannelConfigurationManager":
        """Get the channel configuration manager."""
        return self.microscope.channel_configuration_manager

    @property
    def scan_coordinates(self) -> ScanCoordinates:
        """Get or create scan coordinates manager."""
        if self._scan_coordinates is None:
            self._scan_coordinates = ScanCoordinates(
                objectiveStore=self.objective_store,
                stage=self.microscope.stage,
                camera=self.microscope.camera,
                event_bus=event_bus,
            )
        return self._scan_coordinates

    # =========================================================================
    # Properties - Controllers (created on demand)
    # =========================================================================

    @property
    def live_controller(self) -> LiveController:
        """Get or create the live controller."""
        if self._live_controller is None:
            self._live_controller = LiveController(
                camera_service=self.camera_service,
                event_bus=event_bus,
                illumination_service=self.illumination_service,
                peripheral_service=self.peripheral_service,
                filter_wheel_service=self.filter_wheel_service,
            )
            # Avoid emission filter calls in simulation
            self._live_controller.enable_channel_auto_filter_switching = False

            # Set initial mode
            configs = self.channel_config_manager.get_configurations(
                objective=self.objective_store.current_objective
            )
            if configs:
                self._live_controller.set_microscope_mode(configs[0])

        return self._live_controller

    @property
    def autofocus_controller(self) -> AutoFocusController:
        """Get or create the autofocus controller."""
        if self._autofocus_controller is None:
            self._autofocus_controller = AutoFocusController(
                liveController=self.live_controller,
                camera_service=self.camera_service,
                stage_service=self.stage_service,
                peripheral_service=self.peripheral_service,
                event_bus=event_bus,
            )
        return self._autofocus_controller

    @property
    def multipoint_controller(self) -> MultiPointController:
        """Get or create the multipoint controller."""
        if self._multipoint_controller is None:
            # Get or create fake laser AF controller
            laser_af = self._get_fake_laser_af_controller()
            focus_lock = self._get_focus_lock_simulator(laser_af)

            self._multipoint_controller = MultiPointController(
                live_controller=self.live_controller,
                autofocus_controller=self.autofocus_controller,
                channel_configuration_manager=self.channel_config_manager,
                scan_coordinates=self.scan_coordinates,
                objective_store=self.objective_store,
                laser_autofocus_controller=laser_af,
                focus_lock_controller=focus_lock,
                camera_service=self.camera_service,
                stage_service=self.stage_service,
                peripheral_service=self.peripheral_service,
                event_bus=event_bus,
                piezo_service=self.piezo_service,
                illumination_service=self.illumination_service,
                filter_wheel_service=self.filter_wheel_service,
            )

        return self._multipoint_controller

    def _get_fake_laser_af_controller(self):
        """Get a fake laser AF controller for testing.

        Sets a dummy reference so protocols with laser AF enabled can run
        without the 'Laser Autofocus Not Ready' validation error.
        """
        from tests.unit.control.test_stubs import FakeLaserAutofocusController
        import numpy as np

        controller = FakeLaserAutofocusController()
        controller.laser_af_properties.set_reference_image(
            np.zeros((10, 10), dtype=np.uint8)
        )
        return controller

    def _get_focus_lock_simulator(self, laser_af=None):
        """Get a FocusLockSimulator for testing focus lock protocols.

        Uses a FakeLaserAF that provides measure_displacement_continuous()
        so the simulator's control loop can actually achieve lock.
        """
        from squid.core.config.focus_lock import FocusLockConfig
        from squid.core.events import LaserAFInitialized
        from squid.backend.controllers.autofocus.focus_lock_simulator import FocusLockSimulator
        from tests.e2e.harness.focus_lock_context import FakeLaserAF

        # Create a FakeLaserAF that the simulator can use for measurements
        fake_laser_af = FakeLaserAF()
        fake_laser_af.is_initialized = True

        simulator = FocusLockSimulator(
            event_bus=event_bus,
            config=FocusLockConfig(),
            laser_autofocus=fake_laser_af,
            piezo_service=self.piezo_service,
        )

        # Mark laser AF as initialized directly (event subscription may
        # not be wired yet when publishing during construction)
        simulator._laser_af_initialized = True

        return simulator

    # =========================================================================
    # Convenience Methods
    # =========================================================================

    def get_stage_limits(self) -> dict:
        """Get stage movement limits as a dict."""
        cfg = self.microscope.stage.get_config()
        return {
            "x": (cfg.X_AXIS.MIN_POSITION, cfg.X_AXIS.MAX_POSITION),
            "y": (cfg.Y_AXIS.MIN_POSITION, cfg.Y_AXIS.MAX_POSITION),
            "z": (cfg.Z_AXIS.MIN_POSITION, cfg.Z_AXIS.MAX_POSITION),
        }

    def get_stage_center(self) -> tuple:
        """Get the center position of the stage."""
        limits = self.get_stage_limits()
        return (
            (limits["x"][0] + limits["x"][1]) / 2,
            (limits["y"][0] + limits["y"][1]) / 2,
            (limits["z"][0] + limits["z"][1]) / 2,
        )

    def get_available_channels(self) -> list:
        """Get list of available channel names."""
        configs = self.channel_config_manager.get_configurations(
            objective=self.objective_store.current_objective
        )
        return [c.name for c in configs]
