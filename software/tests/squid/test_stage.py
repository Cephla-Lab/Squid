import pytest
import tempfile

import squid.stage.cephla
import squid.stage.prior
import squid.stage.utils
import squid.stage.pi
import squid.config
import squid.abc
from tests.control.test_microcontroller import get_test_micro


def test_create_simulated_stages():
    microcontroller = get_test_micro()
    cephla_stage = squid.stage.cephla.CephlaStage(microcontroller, squid.config.get_stage_config())


def test_simulated_cephla_stage_ops():
    microcontroller = get_test_micro()
    stage: squid.stage.cephla.CephlaStage = squid.stage.cephla.CephlaStage(
        microcontroller, squid.config.get_stage_config()
    )

    assert stage.get_pos() == squid.abc.Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=0.0)


def test_position_caching():
    (unused_temp_fd, temp_cache_path) = tempfile.mkstemp(".cache", "squid_testing_")

    # Use 6 figures after the decimal so we test that we can capture nanometers
    p = squid.abc.Pos(x_mm=11.111111, y_mm=22.222222, z_mm=1.333333, theta_rad=None)
    squid.stage.utils.cache_position(pos=p, stage_config=squid.config.get_stage_config(), cache_path=temp_cache_path)

    p_read = squid.stage.utils.get_cached_position(cache_path=temp_cache_path)

    assert p_read == p


# --- PI V-308 / C-414 focus stage --------------------------------------------


def test_simulated_c414_move_and_clamp():
    sim = squid.stage.pi._SimulatedC414(axis="1")
    sim.initialize(reference=True)
    assert sim.is_referenced() is True
    assert sim.move_to(1.0) == 1.0
    assert sim.get_position_mm() == 1.0
    assert sim.move_relative(-0.25) == 0.75
    assert sim.is_moving() is False
    sim.set_travel_limits(-1.0, 1.0)
    assert sim.move_to(5.0) == 1.0  # clamped to travel limit, like the controller
