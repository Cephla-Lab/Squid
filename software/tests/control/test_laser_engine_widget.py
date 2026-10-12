"""Unit tests for the Laser Engine tab's display rules."""

from control.laser_engine_widget import _is_engine_measured
from control.squid_laser_engine import LaserChannelState, TcmModuleInfo


def _module(state, setpoint_diff_c, temperature_c=25.0):
    return TcmModuleInfo(
        module_index=0,
        state=state,
        temperature_c=temperature_c,
        setpoint_c=temperature_c - setpoint_diff_c,
        setpoint_diff_c=setpoint_diff_c,
        tec_voltage=0.0,
        tec_current=0.0,
        hi_temp_setpoint_c=30.0,
    )


class TestIsEngineMeasured:
    def test_active_at_setpoint_is_measured(self):
        assert _is_engine_measured(_module(LaserChannelState.ACTIVE, 0.0))

    def test_active_within_band_is_measured(self):
        # Firmware tolerates ACTIVE anywhere in (-0.5, +5.0).
        assert _is_engine_measured(_module(LaserChannelState.ACTIVE, -0.4))
        assert _is_engine_measured(_module(LaserChannelState.ACTIVE, 4.9))

    def test_active_far_below_setpoint_is_not_measured(self):
        # A channel the engine regulates cannot hold ACTIVE here; it would have
        # dropped to WARMING_UP. So this reading is a floor value, not a
        # measurement. Matches 638/730 on hardware: 0.4 C with dT -24.6.
        assert not _is_engine_measured(_module(LaserChannelState.ACTIVE, -24.6, temperature_c=0.4))

    def test_active_at_error_threshold_is_not_measured(self):
        assert not _is_engine_measured(_module(LaserChannelState.ACTIVE, 5.0))

    def test_warming_up_far_from_setpoint_is_measured(self):
        # Genuinely warming: far from setpoint is expected and the numbers are
        # real, so they must still be shown.
        assert _is_engine_measured(_module(LaserChannelState.WARMING_UP, -66.9, temperature_c=27.2))

    def test_sleep_is_measured(self):
        # A sleeping channel drifts away from setpoint; still a real reading.
        assert _is_engine_measured(_module(LaserChannelState.SLEEP, -42.3, temperature_c=51.8))
