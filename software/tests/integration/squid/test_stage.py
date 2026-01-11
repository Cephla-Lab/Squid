"""
Tests for stage functionality including SimulatedStage and CephlaStage.
"""

import pytest
import tempfile

from squid.backend.drivers.stages.simulated import SimulatedStage
import squid.core.config
import squid.core.abc


# ============================================================================
# SimulatedStage Tests
# ============================================================================


@pytest.mark.integration
class TestSimulatedStage:
    """Test suite for SimulatedStage."""

    def test_initial_position_is_zero(self, simulated_stage):
        """Initial position should be at origin."""
        pos = simulated_stage.get_pos()
        assert pos.x_mm == 0.0
        assert pos.y_mm == 0.0
        assert pos.z_mm == 0.0
        assert pos.theta_rad == 0.0

    def test_move_x_relative(self, simulated_stage):
        """move_x should update X position relatively."""
        config = simulated_stage.get_config()
        # Move within limits
        target = min(10.0, config.X_AXIS.MAX_POSITION)
        simulated_stage.set_position(x_mm=config.X_AXIS.MIN_POSITION)
        initial = simulated_stage.get_pos().x_mm

        simulated_stage.move_x(target - initial)
        assert simulated_stage.get_pos().x_mm == pytest.approx(target)

    def test_move_x_to_absolute(self, simulated_stage):
        """move_x_to should set absolute X position."""
        config = simulated_stage.get_config()
        # Move to middle of range
        target = (config.X_AXIS.MIN_POSITION + config.X_AXIS.MAX_POSITION) / 2
        simulated_stage.move_x_to(target)
        assert simulated_stage.get_pos().x_mm == pytest.approx(target)

    def test_home_resets_position(self, simulated_stage):
        """home should reset specified axes to 0."""
        config = simulated_stage.get_config()
        # Set to valid position
        simulated_stage.set_position(
            x_mm=config.X_AXIS.MIN_POSITION,
            y_mm=config.Y_AXIS.MIN_POSITION,
            z_mm=config.Z_AXIS.MIN_POSITION,
        )

        simulated_stage.home(x=True, y=True, z=False, theta=False)

        pos = simulated_stage.get_pos()
        assert pos.x_mm == 0.0
        assert pos.y_mm == 0.0
        assert pos.z_mm == config.Z_AXIS.MIN_POSITION  # Z unchanged

    def test_zero_sets_current_as_origin(self, simulated_stage):
        """zero should set current position as origin."""
        config = simulated_stage.get_config()
        simulated_stage.set_position(x_mm=config.X_AXIS.MIN_POSITION + 50.0)

        simulated_stage.zero(x=True, y=False, z=False, theta=False)

        assert simulated_stage.get_pos().x_mm == 0.0

    def test_get_state_returns_busy_flag(self, simulated_stage):
        """get_state should return StageStage with busy flag."""
        state = simulated_stage.get_state()
        assert state.busy is False

        simulated_stage.set_busy(True)
        state = simulated_stage.get_state()
        assert state.busy is True

    def test_limits_are_enforced(self, simulated_stage):
        """Movements should be clamped to configured limits."""
        config = simulated_stage.get_config()

        # Try to move beyond positive limit
        simulated_stage.move_x_to(config.X_AXIS.MAX_POSITION + 100)
        assert simulated_stage.get_pos().x_mm == pytest.approx(
            config.X_AXIS.MAX_POSITION
        )

        # Try to move beyond negative limit
        simulated_stage.move_x_to(config.X_AXIS.MIN_POSITION - 100)
        assert simulated_stage.get_pos().x_mm == pytest.approx(
            config.X_AXIS.MIN_POSITION
        )

    def test_set_limits_updates_limits(self, simulated_stage):
        """set_limits should update software limits."""
        simulated_stage.set_limits(x_pos_mm=20.0, x_neg_mm=5.0)

        simulated_stage.move_x_to(100.0)
        assert simulated_stage.get_pos().x_mm == pytest.approx(20.0)

        simulated_stage.move_x_to(0.0)
        assert simulated_stage.get_pos().x_mm == pytest.approx(5.0)

    def test_set_position_helper(self, simulated_stage):
        """set_position test helper should directly set position."""
        simulated_stage.set_position(x_mm=100.0, y_mm=200.0, z_mm=50.0)

        pos = simulated_stage.get_pos()
        assert pos.x_mm == pytest.approx(100.0)
        assert pos.y_mm == pytest.approx(200.0)
        assert pos.z_mm == pytest.approx(50.0)

    def test_default_speed_is_realistic(self, stage_config):
        """Default stage speed should be 2.5 mm/s (typical hardware)."""
        stage = SimulatedStage(stage_config, simulate_delays=False)
        assert stage.DEFAULT_SPEED_MM_PER_S == 2.5
        assert stage._speed_mm_per_s == 2.5
        # 1/2.5 = 0.4 seconds per mm
        assert stage._move_delay_per_mm == pytest.approx(0.4)

    def test_simulate_delays_uses_realistic_timing(self, stage_config):
        """With simulate_delays=True, movement should take realistic time."""
        import time

        stage = SimulatedStage(stage_config, simulate_delays=True, speed_mm_per_s=100.0)
        # Start at middle of X range
        mid_x = (stage_config.X_AXIS.MIN_POSITION + stage_config.X_AXIS.MAX_POSITION) / 2
        stage.set_position(x_mm=mid_x)

        # Move 10mm at 100 mm/s should take ~0.1 seconds
        target_x = mid_x + 10.0
        start = time.monotonic()
        stage.move_x_to(target_x)
        elapsed = time.monotonic() - start

        # Should take at least 0.08s (allowing tolerance) but less than 0.3s
        assert elapsed >= 0.08
        assert elapsed < 0.3

    def test_set_speed_updates_delay(self, stage_config):
        """set_speed should update the movement delay calculation."""
        stage = SimulatedStage(stage_config, simulate_delays=True)

        # Default 2.5 mm/s
        assert stage._move_delay_per_mm == pytest.approx(0.4)

        # Change to 5 mm/s
        stage.set_speed(5.0)
        assert stage._speed_mm_per_s == 5.0
        assert stage._move_delay_per_mm == pytest.approx(0.2)  # 1/5 = 0.2 s/mm


# ============================================================================
# CephlaStage Tests (with simulated microcontroller)
# ============================================================================


@pytest.mark.integration
def test_create_simulated_cephla_stage(simulated_cephla_stage):
    """Test creating a simulated CephlaStage."""
    assert simulated_cephla_stage is not None


@pytest.mark.integration
def test_simulated_cephla_stage_ops(simulated_cephla_stage):
    """Test simulated CephlaStage operations."""
    assert simulated_cephla_stage.get_pos() == squid.core.abc.Pos(
        x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=0.0
    )


# ============================================================================
# Stage Factory Tests
# ============================================================================


@pytest.mark.integration
def test_get_stage_returns_simulated(stage_config):
    """get_stage with simulated=True should return SimulatedStage."""
    from squid.backend.drivers.stages.stage_utils import get_stage

    stage = get_stage(stage_config=stage_config, simulated=True)
    assert isinstance(stage, SimulatedStage)


# ============================================================================
# Position Caching Tests
# ============================================================================


def test_position_caching():
    """Test position caching and retrieval."""
    (unused_temp_fd, temp_cache_path) = tempfile.mkstemp(".cache", "squid_testing_")

    # Use 6 figures after the decimal so we test that we can capture nanometers
    p = squid.core.abc.Pos(x_mm=11.111111, y_mm=22.222222, z_mm=1.333333, theta_rad=None)
    from squid.backend.drivers.stages.stage_utils import cache_position, get_cached_position

    cache_position(pos=p, stage_config=squid.core.config.get_stage_config(), cache_path=temp_cache_path)

    p_read = get_cached_position(cache_path=temp_cache_path)

    assert p_read == p
