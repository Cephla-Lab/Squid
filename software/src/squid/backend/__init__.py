# Backend: Hardware + orchestration layer
# Contains: drivers, services, controllers, managers, processing, io

from squid.backend.microscope import Microscope
from squid.backend.microcontroller import Microcontroller

__all__ = ["Microscope", "Microcontroller"]
