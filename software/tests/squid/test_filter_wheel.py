from unittest.mock import MagicMock, patch

import pytest

import squid.config
import squid.filter_wheel_controller.utils
from squid.config import FilterWheelConfig, FilterWheelControllerVariant, SquidFilterWheelConfig
from squid.filter_wheel_controller.cephla import SquidFilterWheel


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


class TestSquidFilterWheelSkipInit:
    """Tests for SquidFilterWheel skip_init functionality."""

    @pytest.fixture
    def mock_microcontroller(self):
        """Create a mock microcontroller."""
        return MagicMock()

    @pytest.fixture
    def squid_config(self):
        """Create a SquidFilterWheelConfig for testing."""
        return SquidFilterWheelConfig(
            max_index=8,
            min_index=1,
            offset=0.008,
            homing_enabled=True,
            motor_slot_index=3,
            transitions_per_revolution=4000,
        )

    def test_skip_init_skips_mcu_initialization(self, mock_microcontroller, squid_config):
        """skip_init=True should skip init_filter_wheel and configure_squidfilter calls."""
        SquidFilterWheel(mock_microcontroller, squid_config, skip_init=True)

        mock_microcontroller.init_filter_wheel.assert_not_called()
        mock_microcontroller.configure_squidfilter.assert_not_called()

    @patch("squid.filter_wheel_controller.cephla.HAS_ENCODER_W", True)
    def test_skip_init_skips_encoder_pid_config(self, mock_microcontroller, squid_config):
        """skip_init=True should skip encoder PID configuration when HAS_ENCODER_W=True."""
        SquidFilterWheel(mock_microcontroller, squid_config, skip_init=True)

        mock_microcontroller.set_pid_arguments.assert_not_called()
        mock_microcontroller.configure_stage_pid.assert_not_called()
        mock_microcontroller.turn_on_stage_pid.assert_not_called()

    def test_normal_init_calls_mcu_initialization(self, mock_microcontroller, squid_config):
        """skip_init=False (default) should call init_filter_wheel and configure_squidfilter."""
        SquidFilterWheel(mock_microcontroller, squid_config, skip_init=False)

        mock_microcontroller.init_filter_wheel.assert_called_once()
        mock_microcontroller.configure_squidfilter.assert_called_once()

    @patch("squid.filter_wheel_controller.cephla.HAS_ENCODER_W", True)
    def test_normal_init_configures_encoder_pid(self, mock_microcontroller, squid_config):
        """skip_init=False with HAS_ENCODER_W=True should configure encoder PID."""
        SquidFilterWheel(mock_microcontroller, squid_config, skip_init=False)

        mock_microcontroller.set_pid_arguments.assert_called_once()
        mock_microcontroller.configure_stage_pid.assert_called_once()
        mock_microcontroller.turn_on_stage_pid.assert_called_once()
