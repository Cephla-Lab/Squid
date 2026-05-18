from unittest.mock import MagicMock, patch

import pytest

import squid.config
import squid.filter_wheel_controller.utils
from control._def import AXIS
from squid.config import FilterWheelConfig, FilterWheelControllerVariant, SquidFilterWheelConfig
from squid.filter_wheel_controller.cephla import SquidFilterWheel


def _make_squid_config(motor_slot: int = 3) -> SquidFilterWheelConfig:
    """Default 8-slot SquidFilterWheelConfig used across the test module."""
    return SquidFilterWheelConfig(
        max_index=8,
        min_index=1,
        offset=0.008,
        motor_slot_index=motor_slot,
        transitions_per_revolution=4000,
    )


def _make_mock_mc(firmware_version=(1, 2)) -> MagicMock:
    """MagicMock microcontroller with a configured firmware_version."""
    mc = MagicMock()
    mc.firmware_version = firmware_version
    return mc


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
        return _make_mock_mc()

    @pytest.fixture
    def squid_config(self):
        return _make_squid_config()

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
    @patch("squid.filter_wheel_controller.cephla.ENABLE_PID_W", True)
    def test_normal_init_configures_encoder_pid(self, mock_microcontroller, squid_config):
        """HAS_ENCODER_W=True + ENABLE_PID_W=True should configure and engage PID."""
        SquidFilterWheel(mock_microcontroller, squid_config, skip_init=False)

        mock_microcontroller.set_pid_arguments.assert_called_once()
        mock_microcontroller.configure_stage_pid.assert_called_once()
        # turn_on_stage_pid takes axis only — passing extra args would TypeError.
        mock_microcontroller.turn_on_stage_pid.assert_called_once_with(AXIS.W)

    @patch("squid.filter_wheel_controller.cephla.HAS_ENCODER_W", True)
    @patch("squid.filter_wheel_controller.cephla.ENABLE_PID_W", False)
    def test_encoder_without_pid_skips_turn_on(self, mock_microcontroller, squid_config):
        """HAS_ENCODER_W=True + ENABLE_PID_W=False configures encoder but leaves PID off."""
        SquidFilterWheel(mock_microcontroller, squid_config, skip_init=False)

        mock_microcontroller.set_pid_arguments.assert_called_once()
        mock_microcontroller.configure_stage_pid.assert_called_once()
        mock_microcontroller.turn_on_stage_pid.assert_not_called()


class TestSquidFilterWheelAbsoluteMove:
    """Tests for the absolute-MOVETO move path on the filter wheel.

    Verifies the wheel issues MOVETO_W / MOVETO_W2 with absolute microstep
    targets computed against a home-anchored coordinate frame, and that
    error recovery splits cleanly: CommandAborted → cheap resend,
    TimeoutError → re-home + retry.
    """

    @pytest.fixture
    def w_config(self):
        return _make_squid_config(motor_slot=3)

    @pytest.fixture
    def w2_config(self):
        return _make_squid_config(motor_slot=4)

    # (motor_slot, move_to_attr, move_rel_attr, home_attr) for each wheel axis.
    AXIS_PARAMS = [
        pytest.param(3, "move_w_to_usteps", "move_w_usteps", "home_w", id="W"),
        pytest.param(4, "move_w2_to_usteps", "move_w2_usteps", "home_w2", id="W2"),
    ]

    @staticmethod
    def _build_wheel(motor_slot):
        config = _make_squid_config(motor_slot=motor_slot)
        mc = _make_mock_mc()
        return SquidFilterWheel(mc, config, skip_init=True), mc, config

    @pytest.fixture
    def wheel(self, w_config):
        mc = _make_mock_mc()
        return SquidFilterWheel(mc, w_config, skip_init=True), mc

    def test_move_to_position_uses_absolute_moveto_for_w(self, wheel, w_config):
        """Moving slot 1 → slot 5 issues MOVETO_W with absolute target usteps."""
        wheel_inst, mc = wheel

        expected_usteps = SquidFilterWheel._target_pos_to_usteps(w_config, 5)
        wheel_inst._move_to_position(1, 5)

        mc.move_w_to_usteps.assert_called_once_with(expected_usteps)
        mc.move_w_usteps.assert_not_called()
        assert wheel_inst._positions[1] == 5

    def test_move_to_position_uses_absolute_moveto_for_w2(self, w2_config):
        """Wheel on W2 axis routes to MOVETO_W2, not MOVE_W2."""
        wheel_inst, mc, _ = self._build_wheel(motor_slot=4)

        expected_usteps = SquidFilterWheel._target_pos_to_usteps(w2_config, 3)
        wheel_inst._move_to_position(1, 3)

        mc.move_w2_to_usteps.assert_called_once_with(expected_usteps)
        mc.move_w2_usteps.assert_not_called()
        assert wheel_inst._positions[1] == 3

    def test_move_to_same_position_is_noop(self, wheel):
        """Asking for the current slot issues no MCU command."""
        wheel_inst, mc = wheel
        wheel_inst._move_to_position(1, 1)
        mc.move_w_to_usteps.assert_not_called()

    def test_target_usteps_advances_monotonically_with_slot(self, w_config):
        """Each slot is one step_size further from home along the absolute frame."""
        u1 = SquidFilterWheel._target_pos_to_usteps(w_config, 1)
        u2 = SquidFilterWheel._target_pos_to_usteps(w_config, 2)
        u8 = SquidFilterWheel._target_pos_to_usteps(w_config, 8)
        step = u2 - u1
        assert step > 0
        # 7 step_sizes between slot 1 and slot 8
        assert u8 - u1 == 7 * step

    @pytest.mark.parametrize("motor_slot,move_to_attr,move_rel_attr,home_attr", AXIS_PARAMS)
    def test_command_aborted_triggers_software_resend_not_rehome(
        self, motor_slot, move_to_attr, move_rel_attr, home_attr
    ):
        """CMD_EXECUTION_ERROR → resend the same MOVETO; do NOT re-home."""
        from control.microcontroller import CommandAborted

        wheel_inst, mc, _ = self._build_wheel(motor_slot)

        # First wait raises CommandAborted, second succeeds.
        mc.wait_till_operation_is_completed.side_effect = [
            CommandAborted(reason="firmware reported CMD_EXECUTION_ERROR", command_id=1),
            None,
        ]

        wheel_inst._move_to_position(1, 4)

        assert getattr(mc, move_to_attr).call_count == 2
        getattr(mc, home_attr).assert_not_called()
        # Absolute-MOVETO path must not fall back to relative MOVE.
        getattr(mc, move_rel_attr).assert_not_called()
        assert wheel_inst._positions[1] == 4

    @pytest.mark.parametrize("motor_slot,move_to_attr,move_rel_attr,home_attr", AXIS_PARAMS)
    def test_timeout_skips_resend_and_goes_straight_to_rehome(self, motor_slot, move_to_attr, move_rel_attr, home_attr):
        """Ack timeout → re-home + retry (no cheap resend, motor state is uncertain)."""
        wheel_inst, mc, _ = self._build_wheel(motor_slot)

        # First move times out; home succeeds; retry succeeds.
        mc.wait_till_operation_is_completed.side_effect = [
            TimeoutError("ack timeout"),
            None,  # home wait
            None,  # home offset move wait
            None,  # retry MOVETO wait
        ]

        wheel_inst._move_to_position(1, 4)

        getattr(mc, home_attr).assert_called_once()
        # Three MOVETO calls: the failed initial attempt, the home-offset
        # move inside _home_wheel, and the post-home retry to slot 4.
        assert getattr(mc, move_to_attr).call_count == 3
        # Absolute-MOVETO path must not fall back to relative MOVE.
        getattr(mc, move_rel_attr).assert_not_called()
        assert wheel_inst._positions[1] == 4

    def test_home_uses_absolute_moveto_for_offset(self, wheel, w_config):
        """After firmware home, host drives to the offset slot via MOVETO_W (absolute)."""
        wheel_inst, mc = wheel

        wheel_inst._home_wheel(1)

        expected_offset_usteps = SquidFilterWheel._delta_to_usteps(w_config.offset)
        mc.home_w.assert_called_once()
        mc.move_w_to_usteps.assert_called_once_with(expected_offset_usteps)
        mc.move_w_usteps.assert_not_called()
        assert wheel_inst._positions[1] == w_config.min_index

    @pytest.mark.parametrize("motor_slot,move_to_attr,move_rel_attr,home_attr", AXIS_PARAMS)
    def test_rehome_retry_failure_propagates(self, motor_slot, move_to_attr, move_rel_attr, home_attr):
        """When the post-rehome retry also fails, _move_to_position must re-raise."""
        wheel_inst, mc, _ = self._build_wheel(motor_slot)

        # Initial move times out → re-home → offset move succeeds → retry times out → raise.
        mc.wait_till_operation_is_completed.side_effect = [
            TimeoutError("initial move ack timeout"),
            None,  # home wait
            None,  # home offset move wait
            TimeoutError("post-rehome retry ack timeout"),
        ]

        with pytest.raises(TimeoutError, match="post-rehome retry"):
            wheel_inst._move_to_position(1, 4)

        getattr(mc, home_attr).assert_called_once()
        # Position must not be updated to target on failure.
        assert wheel_inst._positions[1] != 4


class TestSquidFilterWheelFirmwareVersionGate:
    """Tests for the firmware version requirement on SquidFilterWheel."""

    @pytest.fixture
    def squid_config(self):
        return _make_squid_config()

    @pytest.mark.parametrize("firmware_version", [(0, 0), (1, 0), (1, 1)])
    def test_init_raises_on_pre_v1_2_firmware(self, squid_config, firmware_version):
        """Constructing SquidFilterWheel must reject firmware older than v1.2."""
        mc = _make_mock_mc(firmware_version=firmware_version)
        with pytest.raises(RuntimeError, match="firmware >= v1.2"):
            SquidFilterWheel(mc, squid_config)

    @pytest.mark.parametrize("firmware_version", [(1, 2), (2, 0)])
    def test_init_succeeds_on_supported_firmware(self, squid_config, firmware_version):
        """v1.2 (the minimum) and any newer version should be accepted."""
        mc = _make_mock_mc(firmware_version=firmware_version)
        SquidFilterWheel(mc, squid_config)  # no raise

    def test_skip_init_also_checks_version(self, squid_config):
        """Version check runs in __init__ so it fires regardless of skip_init.

        Restart flows could be running against a re-flashed (possibly downgraded)
        firmware, so the check must not be bypassed.
        """
        mc = _make_mock_mc(firmware_version=(1, 1))
        with pytest.raises(RuntimeError, match="firmware >= v1.2"):
            SquidFilterWheel(mc, squid_config, skip_init=True)
