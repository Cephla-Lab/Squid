# squid/services/__init__.py
"""Service layer for hardware orchestration."""

from typing import Dict, Optional, TYPE_CHECKING

from squid.backend.services.base import BaseService
from squid.backend.services.peripheral_service import PeripheralService
from squid.backend.services.camera_service import CameraService
from squid.backend.services.stage_service import StageService
from squid.backend.services.illumination_service import IlluminationService
from squid.backend.services.filter_wheel_service import FilterWheelService
from squid.backend.services.piezo_service import PiezoService
from squid.backend.services.fluidics_service import FluidicsService
from squid.backend.services.objective_changer_service import ObjectiveChangerService
from squid.backend.services.spinning_disk_service import SpinningDiskService
from squid.backend.services.nl5_service import NL5Service
from squid.backend.services.movement_service import MovementService
from squid.core.events import EventBus

if TYPE_CHECKING:
    from squid.ui.ui_event_bus import UIEventBus


class ServiceRegistry:
    """
    Central registry for all services.

    Usage:
        from squid.core.events import event_bus
        from squid.backend.services import ServiceRegistry

        registry = ServiceRegistry(event_bus)
        registry.register('camera', CameraService(camera, event_bus))

        # Access services
        registry.camera.set_exposure_time(100)

        # For widgets, use ui_event_bus for thread-safe subscriptions
        if registry.ui_event_bus:
            registry.ui_event_bus.subscribe(SomeEvent, handler)
    """

    def __init__(self, event_bus: EventBus, ui_event_bus: Optional["UIEventBus"] = None):
        """
        Initialize registry.

        Args:
            event_bus: EventBus for service communication
            ui_event_bus: Optional UIEventBus for thread-safe widget subscriptions
        """
        self._event_bus = event_bus
        self._ui_event_bus = ui_event_bus
        self._services: Dict[str, BaseService] = {}

    def register(self, name: str, service: BaseService):
        """
        Register a service.

        Args:
            name: Service name (e.g., 'camera', 'stage')
            service: Service instance
        """
        self._services[name] = service

    def get(self, name: str) -> Optional[BaseService]:
        """
        Get a service by name.

        Args:
            name: Service name

        Returns:
            Service instance or None if not found
        """
        return self._services.get(name)

    @property
    def ui_event_bus(self) -> Optional["UIEventBus"]:
        """Get the UIEventBus for thread-safe widget subscriptions."""
        return self._ui_event_bus

    @ui_event_bus.setter
    def ui_event_bus(self, value: Optional["UIEventBus"]) -> None:
        """Set the UIEventBus (called by ApplicationContext after creation)."""
        self._ui_event_bus = value

    def shutdown(self):
        """Shutdown all services."""
        for service in self._services.values():
            service.shutdown()
        self._services.clear()


__all__ = [
    "BaseService",
    "ServiceRegistry",
    "PeripheralService",
    "CameraService",
    "StageService",
    "IlluminationService",
    "FilterWheelService",
    "PiezoService",
    "FluidicsService",
    "ObjectiveChangerService",
    "SpinningDiskService",
    "NL5Service",
    "MovementService",
]
