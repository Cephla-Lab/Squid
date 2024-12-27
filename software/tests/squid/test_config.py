import pytest

import squid.config
from squid.config import AxisConfig


def test_axis_config():
    stage_config = squid.config.get_stage_config()
    # micro step conversion round tripping
    trials = (1.0, 0.001, 2.2, 3.123456)

    # Test with easy to reason about axis config, then with real ones
    easy_config = stage_config.X_AXIS
    easy_config.ENCODER_SIGN = 1
    easy_config.USE_ENCODER = False
    # 400 steps -> 1 mm (2*200 = 1 rev, 1 rev == 1 mm)
    easy_config.SCREW_PITCH = 1.0
    easy_config.MICROSTEPS_PER_STEP = 2
    easy_config.FULL_STEPS_PER_REV = 200

    def round_trip_mm(config: AxisConfig, mm):
        # Round tripping should match within 1 ustep
        usteps = config.convert_real_units_to_ustep(mm)
        mm_round_tripped = config.convert_to_real_units(usteps)
        eps = abs(config.convert_to_real_units(1))
        assert mm_round_tripped == pytest.approx(mm, abs=eps)

    for trial in trials:
        round_trip_mm(easy_config, trial)

    for trial in trials:
        round_trip_mm(stage_config.X_AXIS, trial)
        round_trip_mm(stage_config.Y_AXIS, trial)
        round_trip_mm(stage_config.Z_AXIS, trial)
        round_trip_mm(stage_config.THETA_AXIS, trial)
