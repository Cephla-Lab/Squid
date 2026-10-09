"""The host knows the illumination intensity factor the firmware runs (design §6.2): calibrated intensities are
converted through it."""

import pytest

import control._def
from control.microcontroller import Microcontroller, SimSerial


def _as_firmware_gets_it(factor):
    return int(round(factor, 2) * 100) / 100


@pytest.mark.parametrize("reset_and_initialize", [True, False])
def test_factor_known_from_construction(reset_and_initialize):
    mcu = Microcontroller(SimSerial(), reset_and_initialize=reset_and_initialize)
    assert mcu.illumination_intensity_factor == _as_firmware_gets_it(control._def.ILLUMINATION_INTENSITY_FACTOR)


@pytest.mark.parametrize("sent, recorded", [(0.8, 0.8), (1.7, 1.0), (-1.0, 0.01), (0.57, 0.56)])
def test_factor_recorded_as_the_firmware_receives_it(sent, recorded):
    mcu = Microcontroller(SimSerial(), reset_and_initialize=False)
    mcu.set_dac80508_scaling_factor_for_illumination(sent)
    assert mcu.illumination_intensity_factor == pytest.approx(recorded)  # 0.57 -> 0.56: master's int() truncation, kept


def test_heartbeat_interval_is_reported_while_it_runs():
    mcu = Microcontroller(SimSerial(), reset_and_initialize=False)
    assert mcu.heartbeat_interval_s is None
    mcu.start_heartbeat(interval_s=2.5)
    try:
        assert mcu.heartbeat_interval_s == 2.5
    finally:
        mcu.stop_heartbeat()
    assert mcu.heartbeat_interval_s is None
