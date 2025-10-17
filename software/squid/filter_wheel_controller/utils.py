import time
from typing import List, Dict, Optional
import squid.logging
from squid.abc import AbstractFilterWheelController, FilterWheelInfo


class SimulatedFilterWheelController(AbstractFilterWheelController):
    """Simulated filter wheel controller for testing purposes."""

    def __init__(self, number_of_wheels: int = 1, slots_per_wheel: int = 8, simulate_delays: bool = True):
        """
        Initialize the simulated filter wheel controller.

        Args:
            number_of_wheels: Number of filter wheels to simulate
            slots_per_wheel: Number of slots per wheel
            simulate_delays: Whether to simulate realistic timing delays
        """
        self.log = squid.logging.get_logger(self.__class__.__name__)
        self._available_filter_wheels = []
        self.number_of_wheels = number_of_wheels
        self.slots_per_wheel = slots_per_wheel
        self.simulate_delays = simulate_delays
        self._positions: Dict[int, int] = {}

    def initialize(self, filter_wheel_indices: List[int]):
        """Initialize the filter wheels."""
        if len(filter_wheel_indices) > self.number_of_wheels:
            raise ValueError(
                f"Cannot initialize {len(filter_wheel_indices)} wheels. "
                f"Only {self.number_of_wheels} wheel(s) configured."
            )

        self._available_filter_wheels = filter_wheel_indices

        for index in filter_wheel_indices:
            self._positions[index] = 1

        self.log.info(f"Initialized filter wheels: {filter_wheel_indices}")

    @property
    def available_filter_wheels(self) -> List[int]:
        """List of available filter wheel indices."""
        return self._available_filter_wheels

    def get_filter_wheel_info(self, index: int) -> FilterWheelInfo:
        """Get information about a specific filter wheel."""
        if index not in self._available_filter_wheels:
            raise ValueError(f"Filter wheel index {index} not found")

        return FilterWheelInfo(
            index=index,
            number_of_slots=self.slots_per_wheel,
            slot_names=[str(i) for i in range(1, self.slots_per_wheel + 1)],
        )

    def home(self, index: int = None):
        """Home the filter wheel(s)."""
        wheels_to_home = [index] if index is not None else self._available_filter_wheels

        for wheel_index in wheels_to_home:
            if wheel_index not in self._available_filter_wheels:
                raise ValueError(f"Filter wheel index {wheel_index} not found")

            self.log.info(f"Homing filter wheel {wheel_index}...")

            if self.simulate_delays:
                time.sleep(0.5)

            self._positions[wheel_index] = 1
            self.log.info(f"Filter wheel {wheel_index} homed successfully")

    def set_filter_wheel_position(self, positions: Dict[int, int]):
        """Set filter wheel positions."""
        for wheel_index, position in positions.items():
            if wheel_index not in self._available_filter_wheels:
                raise ValueError(f"Filter wheel index {wheel_index} not found")

            if position < 1 or position > self.slots_per_wheel:
                raise ValueError(
                    f"Invalid position {position} for wheel {wheel_index}. " f"Valid range: 1-{self.slots_per_wheel}"
                )

            current_pos = self._positions.get(wheel_index, 1)

            if position != current_pos:
                self.log.info(f"Moving filter wheel {wheel_index} from position {current_pos} to {position}")

                if self.simulate_delays:
                    time.sleep(0.1)

                self._positions[wheel_index] = position

    def get_filter_wheel_position(self) -> Dict[int, int]:
        """Get current positions of all filter wheels."""
        return self._positions.copy()

    def close(self):
        """Close the controller."""
        self.log.info("Closing simulated filter wheel controller")
        self._positions.clear()
        self._available_filter_wheels = []
