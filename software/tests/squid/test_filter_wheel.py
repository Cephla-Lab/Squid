import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import squid.config
import squid.filter_wheel_controller.utils
from control._def import SCREW_PITCH_W_MM
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


class TestSquidFilterWheelThreadSafety:
    """Tests that concurrent filter wheel operations don't corrupt position tracking."""

    @pytest.fixture
    def wheel(self):
        mcu = MagicMock()
        # Simulate real MCU latency so the critical section has a wide enough
        # window for threads to actually interleave without the lock.
        mcu.wait_till_operation_is_completed.side_effect = lambda *a, **kw: time.sleep(0.02)
        config = SquidFilterWheelConfig(
            max_index=8, min_index=1, offset=0.008, motor_slot_index=3, transitions_per_revolution=4000
        )
        return SquidFilterWheel(mcu, config, skip_init=True)

    def test_concurrent_moves_serialize(self, wheel):
        """Two threads calling set_filter_wheel_position must not corrupt tracking.

        Without the lock, both threads read current_pos=1, compute their deltas
        relative to 1, and issue overlapping moves that leave the physical wheel
        at the wrong position.  With the lock the second thread sees the updated
        position from the first and computes the correct delta relative to the
        actual current position.
        """
        move_deltas = []
        original_move_wheel = wheel._move_wheel

        def recording_move_wheel(wid, delta):
            move_deltas.append(delta)
            original_move_wheel(wid, delta)

        wheel._move_wheel = recording_move_wheel

        barrier = threading.Barrier(2)
        errors = []

        def move_to(pos):
            try:
                barrier.wait(timeout=2)
                wheel.set_filter_wheel_position({1: pos})
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=move_to, args=(5,))
        t2 = threading.Thread(target=move_to, args=(3,))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not t1.is_alive(), "Thread 1 did not finish (possible deadlock)"
        assert not t2.is_alive(), "Thread 2 did not finish (possible deadlock)"
        assert not errors, f"Threads raised: {errors}"
        # Final tracked position must equal the last physical move target
        final_pos = wheel.get_filter_wheel_position()[1]
        assert final_pos in (3, 5), f"Position tracking corrupted: {final_pos}"

        # With the lock, the second move should compute its delta from the first
        # move's result, not from the original position 1.  So the sum of deltas
        # must equal (final_pos - 1) * step_size, regardless of execution order.
        config = wheel._configs[1]
        step_size = SCREW_PITCH_W_MM / (config.max_index - config.min_index + 1)
        expected_total_delta = (final_pos - 1) * step_size
        actual_total_delta = sum(move_deltas)
        assert abs(actual_total_delta - expected_total_delta) < 1e-9, (
            f"Delta mismatch: moves summed to {actual_total_delta}, "
            f"but position {final_pos} requires {expected_total_delta}"
        )

    def test_home_during_move_serializes(self, wheel):
        """home() must not run concurrently with a move."""
        wheel._positions[1] = 4
        call_order = []

        original_home_w = wheel.microcontroller.home_w
        original_move_w = wheel.microcontroller.move_w_usteps

        def tracked_home_w(*a, **kw):
            call_order.append("home_start")
            original_home_w(*a, **kw)
            call_order.append("home_end")

        def tracked_move_w(usteps):
            call_order.append("move_start")
            original_move_w(usteps)
            call_order.append("move_end")

        wheel.microcontroller.home_w = tracked_home_w
        wheel.microcontroller.move_w_usteps = tracked_move_w

        barrier = threading.Barrier(2)

        def do_home():
            barrier.wait(timeout=2)
            wheel.home(1)

        def do_move():
            barrier.wait(timeout=2)
            wheel.set_filter_wheel_position({1: 6})

        t1 = threading.Thread(target=do_home)
        t2 = threading.Thread(target=do_move)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not t1.is_alive(), "Home thread did not finish (possible deadlock)"
        assert not t2.is_alive(), "Move thread did not finish (possible deadlock)"

        # Verify operations did not interleave.
        # home() calls home_w (home_start/home_end) then _move_wheel for offset (move_start/move_end).
        # set_filter_wheel_position calls _move_wheel (move_start/move_end).
        # With the lock, the two valid orderings are:
        #   home first: [home_start, home_end, move_start, move_end, move_start, move_end]
        #   move first: [move_start, move_end, home_start, home_end, move_start, move_end]
        assert call_order in (
            ["home_start", "home_end", "move_start", "move_end", "move_start", "move_end"],
            ["move_start", "move_end", "home_start", "home_end", "move_start", "move_end"],
        ), f"Operations interleaved: {call_order}"
        # home-first -> move sets final pos 6; move-first -> home resets to 1
        assert wheel.get_filter_wheel_position()[1] in (1, 6)
