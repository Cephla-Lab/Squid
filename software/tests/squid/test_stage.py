import pytest
import tempfile

import squid.stage.cephla
import squid.stage.prior
import squid.stage.utils
import squid.config
from control.microcontroller import Microcontroller, SimSerial
import squid.abc


def test_create_simulated_stages():
    microcontroller = Microcontroller(existing_serial=SimSerial())
    cephla_stage = squid.stage.cephla.CephlaStage(microcontroller, squid.config.get_stage_config())

    with pytest.raises(NotImplementedError):
        prior_stage = squid.stage.prior.PriorStage(squid.config.get_stage_config())


def test_simulated_cephla_stage_ops():
    microcontroller = Microcontroller(existing_serial=SimSerial())
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
