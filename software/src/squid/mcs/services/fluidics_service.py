#* **FluidicsService** (wrapper around `fluidics_v2`)

#  * `run_protocol(proto: FluidicsProtocol) -> None`
#  * `abort_protocol() -> None`
#  * Optional: `get_status() -> FluidicsStatus`

from __future__ import annotations
import threading
from typing import TYPE_CHECKING
from squid.mcs.services.base import BaseService
from squid.core.events import EventBus

if TYPE_CHECKING:
    from squid.mcs.drivers.fluidics.fluidics import Fluidics

class FluidicsService(BaseService):
    """Service for fluidics operations.

    Currently provides direct method access for fluidics control.
    Event subscriptions can be added when fluidics command events are defined.
    """
    def __init__(self, fluidics: Fluidics, event_bus: EventBus):
        super().__init__(event_bus)
        self._fluidics = fluidics
        self._lock = threading.RLock()

    def run_protocol(self, proto: FluidicsProtocol) -> None:
        """Run a fluidics protocol."""
        with self._lock:
            self._fluidics.run_protocol(proto)

    def abort_protocol(self) -> None:
        """Abort a fluidics protocol."""
        with self._lock:
            self._fluidics.abort_protocol()

    def get_status(self) -> FluidicsStatus:
        """Get the status of the fluidics system."""
        with self._lock:
            return self._fluidics.get_status()

    def set_valve_position(self, valve: int, position: int) -> None:
        """Set the position of a valve."""
        with self._lock:
            self._fluidics.set_valve_position(valve, position)

    def get_valve_position(self, valve: int) -> int:
        """Get the position of a valve."""
        with self._lock:
            return self._fluidics.get_valve_position(valve)

    def get_valve_positions(self) -> list[int]:
        """Get the positions of all valves."""
        with self._lock:
            return self._fluidics.get_valve_positions()

    def set_flow_rate(self, flow_rate: float) -> None:
        """Set the flow rate of the fluidics system."""
        with self._lock:
            self._fluidics.set_flow_rate(flow_rate)

    def get_flow_rate(self) -> float:
        """Get the flow rate of the fluidics system."""
        with self._lock:
            return self._fluidics.get_flow_rate()

    def set_volume(self, volume: float) -> None:
        """Set the volume of the fluidics system."""
        with self._lock:
            self._fluidics.set_volume(volume)

    def get_volume(self) -> float:
        """Get the volume of the fluidics system."""
        with self._lock:
            return self._fluidics.get_volume()

    def set_incubation_time(self, incubation_time: float) -> None:
        """Set the incubation time of the fluidics system."""
        with self._lock:
            self._fluidics.set_incubation_time(incubation_time)

    def get_incubation_time(self) -> float:
        """Get the incubation time of the fluidics system."""
        with self._lock:
            return self._fluidics.get_incubation_time()

    def set_repeat(self, repeat: int) -> None:
        """Set the repeat of the fluidics system."""
        with self._lock:
            self._fluidics.set_repeat(repeat)

    def get_repeat(self) -> int:
        """Get the repeat of the fluidics system."""
        with self._lock:
            return self._fluidics.get_repeat()

    def set_fill_tubing_with(self, fill_tubing_with: int) -> None:
        """Set the fill tubing with of the fluidics system."""
        with self._lock:
            self._fluidics.set_fill_tubing_with(fill_tubing_with)

    def get_fill_tubing_with(self) -> int:
        """Get the fill tubing with of the fluidics system."""
        with self._lock:
            return self._fluidics.get_fill_tubing_with()

    # Methods used by MultiPointWorker for acquisition
    def update_port(self, time_point: int) -> None:
        """Update the port based on time point (for PORT_LIST cycling)."""
        with self._lock:
            self._fluidics.update_port(time_point)

    def run_before_imaging(self) -> None:
        """Run fluidics sequences before imaging (e.g., add probe, wash, imaging buffer)."""
        with self._lock:
            self._fluidics.run_before_imaging()

    def run_after_imaging(self) -> None:
        """Run fluidics sequences after imaging (e.g., cleavage buffer, rinse)."""
        with self._lock:
            self._fluidics.run_after_imaging()

    def wait_for_completion(self) -> None:
        """Wait for current fluidics operation to complete."""
        with self._lock:
            self._fluidics.wait_for_completion()
