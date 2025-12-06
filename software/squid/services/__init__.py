# squid/services/__init__.py
"""Service layer for hardware orchestration."""

from typing import Dict, Optional

from squid.services.base import BaseService
from squid.services.peripheral_service import PeripheralService
from squid.services.camera_service import CameraService
from squid.services.stage_service import StageService
from squid.events import EventBus


class ServiceRegistry:
    """
    Central registry for all services.

    Usage:
        from squid.events import event_bus
        from squid.services import ServiceRegistry

        registry = ServiceRegistry(event_bus)
        registry.register('camera', CameraService(camera, event_bus))

        # Access services
        registry.camera.set_exposure_time(100)
    """

    def __init__(self, event_bus: EventBus):
        """
        Initialize registry.

        Args:
            event_bus: EventBus for service communication
        """
        self._event_bus = event_bus
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
]
