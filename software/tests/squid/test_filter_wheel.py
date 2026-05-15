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


class TestSquidFilterWheelWPosVerification:
    """Tests for the post-move W position verification path (firmware >= v1.2)."""

    @pytest.fixture
    def squid_config(self):
        return SquidFilterWheelConfig(
            max_index=8,
            min_index=1,
            offset=0.008,
            motor_slot_index=3,
            transitions_per_revolution=4000,
        )

    def _build_wheel(self, supports_broadcast: bool, w_pos_after_move: int):
        """Construct a SquidFilterWheel whose mocked microcontroller advances
        `w_pos` to `w_pos_after_move` whenever `move_w_usteps` is called.
        """
        mc = MagicMock()
        mc.supports_w_pos_broadcast.return_value = supports_broadcast
        mc.w_pos = 0

        def fake_move_w_usteps(_usteps):
            mc.w_pos = w_pos_after_move

        mc.move_w_usteps.side_effect = fake_move_w_usteps
        return mc

    def test_verify_passes_when_motor_moves_as_commanded(self, squid_config):
        """Move 1 -> 2 (delta = +1600 usteps) and have the broadcast match."""
        mc = self._build_wheel(supports_broadcast=True, w_pos_after_move=1600)
        wheel = SquidFilterWheel(mc, squid_config, skip_init=True)
        wheel.initialize([1])

        wheel.set_filter_wheel_position({1: 2})

        assert wheel.get_filter_wheel_position()[1] == 2
        mc.home_w.assert_not_called()

    def test_verify_triggers_rehome_when_motor_did_not_move(self, squid_config):
        """If broadcast W position doesn't change, we should re-home + retry."""
        mc = self._build_wheel(supports_broadcast=True, w_pos_after_move=0)
        wheel = SquidFilterWheel(mc, squid_config, skip_init=True)
        wheel.initialize([1])

        with pytest.raises(TimeoutError):
            wheel.set_filter_wheel_position({1: 2})

        # First attempt fails verification -> re-home -> retry still fails.
        assert mc.home_w.call_count == 1
        assert mc.move_w_usteps.call_count >= 2

    def test_verification_skipped_when_firmware_does_not_broadcast(self, squid_config):
        """Old firmware: w_pos is unreliable, verification must be skipped
        (otherwise the move would falsely look like a silent failure)."""
        mc = self._build_wheel(supports_broadcast=False, w_pos_after_move=0)
        wheel = SquidFilterWheel(mc, squid_config, skip_init=True)
        wheel.initialize([1])

        wheel.set_filter_wheel_position({1: 2})

        assert wheel.get_filter_wheel_position()[1] == 2
        mc.home_w.assert_not_called()

    def test_verification_skipped_for_w2_axis(self):
        """W2 (motor_slot 4) isn't broadcast yet; verification must be skipped."""
        w2_config = SquidFilterWheelConfig(
            max_index=8,
            min_index=1,
            offset=0.008,
            motor_slot_index=4,
            transitions_per_revolution=4000,
        )
        mc = MagicMock()
        mc.supports_w_pos_broadcast.return_value = True
        mc.w_pos = 0  # Never advances; would trip verification if it ran.

        wheel = SquidFilterWheel(mc, w2_config, skip_init=True)
        wheel.initialize([1])
        wheel.set_filter_wheel_position({1: 2})

        assert wheel.get_filter_wheel_position()[1] == 2
        mc.home_w2.assert_not_called()
