import time
from typing import List, Dict, Optional
import squid.logging
from squid.abc import AbstractFilterWheelController, FilterWheelInfo
from squid.config import FilterWheelConfig, FilterWheelControllerVariant


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
        self._delay_ms: float = 50.0
        self._delay_offset_ms: float = 0.0

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
                # Homing takes longer than normal movement
                homing_delay_s = (self._delay_ms + self._delay_offset_ms) * 5 / 1000
                time.sleep(max(0, homing_delay_s))

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
                    delay_s = (self._delay_ms + self._delay_offset_ms) / 1000
                    time.sleep(max(0, delay_s))

                self._positions[wheel_index] = position

    def get_filter_wheel_position(self) -> Dict[int, int]:
        """Get current positions of all filter wheels."""
        return self._positions.copy()

    def close(self):
        """Close the controller."""
        self.log.info("Closing simulated filter wheel controller")
        self._positions.clear()
        self._available_filter_wheels = []

    def set_delay_offset_ms(self, delay_offset_ms: float):
        """Set the delay offset in milliseconds."""
        self._delay_offset_ms = delay_offset_ms
        self.log.debug(f"Set delay offset to {delay_offset_ms} ms")

    def get_delay_offset_ms(self) -> Optional[float]:
        """Get the current delay offset in milliseconds."""
        return self._delay_offset_ms

    def set_delay_ms(self, delay_ms: float):
        """Set the base delay in milliseconds."""
        self._delay_ms = delay_ms
        self.log.debug(f"Set base delay to {delay_ms} ms")

    def get_delay_ms(self) -> Optional[float]:
        """Get the base delay in milliseconds."""
        return self._delay_ms


def get_filter_wheel_controller(
    config: FilterWheelConfig,
    microcontroller=None,  # Type hint would create circular dependency
    simulated: bool = False,
    skip_init: bool = False,
) -> AbstractFilterWheelController:
    """
    Factory function to create the appropriate filter wheel controller based on configuration.

    Args:
        config: FilterWheelConfig containing controller type and settings
        microcontroller: Microcontroller instance (required for SQUID filter wheel)
        simulated: If True, return a simulated controller regardless of config
        skip_init: If True, skip hardware initialization (for restart after settings change)

    Returns:
        AbstractFilterWheelController instance

    Raises:
        ValueError: If controller type is unknown or required dependencies are missing
    """
    if simulated:
        # Create simulated controller with correct number of wheels
        num_wheels = len(config.indices) if config else 1
        return SimulatedFilterWheelController(number_of_wheels=num_wheels)

    # Import here to avoid circular dependencies
    from squid.filter_wheel_controller.cephla import SquidFilterWheel
    from squid.filter_wheel_controller.optospin import Optospin
    from squid.filter_wheel_controller.zaber import ZaberFilterController

    if config.controller_type == FilterWheelControllerVariant.SQUID:
        if microcontroller is None:
            raise ValueError("SquidFilterWheel requires a microcontroller instance")
        # Use multi-wheel configs if explicitly set, otherwise fall back to single config
        configs = config.squid_wheel_configs if config.squid_wheel_configs is not None else config.controller_config
        return SquidFilterWheel(microcontroller=microcontroller, configs=configs, skip_init=skip_init)

    elif config.controller_type == FilterWheelControllerVariant.ZABER:
        return ZaberFilterController(config=config.controller_config)

    elif config.controller_type == FilterWheelControllerVariant.OPTOSPIN:
        return Optospin(config=config.controller_config)

    else:
        raise ValueError(f"Unknown or unsupported filter wheel controller type: {config.controller_type}")
