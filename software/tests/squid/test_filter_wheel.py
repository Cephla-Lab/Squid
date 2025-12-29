import pytest

import squid.config
import squid.filter_wheel_controller.utils
from squid.config import FilterWheelConfig, FilterWheelControllerVariant, SquidFilterWheelConfig


def test_create_simulated_filter_wheel():
    """Test that we can create a simulated filter wheel controller."""
    controller = squid.filter_wheel_controller.utils.SimulatedFilterWheelController(
        number_of_wheels=1, slots_per_wheel=8, simulate_delays=False
    )
    controller.initialize([1])

    assert controller.available_filter_wheels == [1]


def test_simulated_filter_wheel_position():
    """Test setting and getting filter wheel positions."""
    controller = squid.filter_wheel_controller.utils.SimulatedFilterWheelController(
        number_of_wheels=1, slots_per_wheel=8, simulate_delays=False
    )
    controller.initialize([1])

    # Set position
    controller.set_filter_wheel_position({1: 5})

    # Verify position
    assert controller.get_filter_wheel_position()[1] == 5


def test_simulated_filter_wheel_homing():
    """Test homing filter wheels."""
    controller = squid.filter_wheel_controller.utils.SimulatedFilterWheelController(
        number_of_wheels=1, slots_per_wheel=8, simulate_delays=False
    )
    controller.initialize([1])

    # Move to a different position
    controller.set_filter_wheel_position({1: 5})

    # Home the wheel
    controller.home(1)

    assert controller.get_filter_wheel_position()[1] == 1


def test_filter_wheel_config_creation():
    """Test that filter wheel config models can be created."""
    squid_config = SquidFilterWheelConfig(
        max_index=8,
        min_index=1,
        offset=0.008,
        homing_enabled=True,
        motor_slot_index=3,
        transitions_per_revolution=4000,
    )

    config = FilterWheelConfig(
        controller_type=FilterWheelControllerVariant.SQUID,
        indices=[1],
        controller_config=squid_config,
    )

    assert config.controller_type == FilterWheelControllerVariant.SQUID
    assert config.indices == [1]
