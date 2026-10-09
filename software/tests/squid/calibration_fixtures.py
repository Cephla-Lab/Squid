"""Synthetic sources, calibrations and a call-recording controller for the illumination power calibration tests."""

from dataclasses import replace

import numpy as np
import pandas as pd

from squid.intensity_calibration import IntensityCalibration
from squid.power_meter import simulated_laser_mw

DAC = np.linspace(0.0, 100.0, 201)


def bench_488_laser_mw(x):
    """The bench 488 nm laser (2026-10-09): nothing up to DAC 19.0 %, 2.12 mW at 19.5 % (x 0.117), 31.7 mW at the top:
    it jumps on to 6.7 % of its maximum."""
    x = np.asarray(x, dtype=float)
    return np.where(x < 0.117 - 1e-9, 0.0, 2.12 + (31.7 - 2.12) * (x - 0.117) / (0.6 - 0.117))


def make_calibration(
    model=simulated_laser_mw,
    factor=0.6,
    max_output=1.0,
    wavelength_nm=405,
    port="D1",
    channel="Fluorescence 405 nm Ex",
    noise=0.0,
    seed=0,
    verification="pass",
) -> IntensityCalibration:
    """A calibration as a sweep of `model` would make it (dark already subtracted)."""
    dac = DAC * max_output
    x = dac / 100.0 * factor
    rng = np.random.default_rng(seed)
    power = np.asarray(model(x), dtype=float) * (1.0 + noise * rng.standard_normal(dac.size))
    calibration = IntensityCalibration.from_sweep(
        channel=channel,
        controller_port=port,
        wavelength_nm=wavelength_nm,
        calibrated_at="2026-10-08T14:30:12",
        software_commit="test",
        meter="Simulated power meter",
        sensor="Simulated sensor",
        factor=factor,
        max_output=max_output,
        measured_in="widefield",
        dark_mw=(0.0, 0.0),
        drift_fraction=0.0,
        dac_percent=dac,
        power_raw_mw=power,
        power_mw=power,
        sigma_dark=0.0,
        pulse_on_s=0.1,
    )
    return replace(calibration, verification=verification, verification_points=((1.0, 0.5), (10.0, -0.4), (100.0, 0.1)))


def write_legacy_csv(path, dac, power):
    """A file as tools/generate_intensity_calibrations.py wrote it before 2026-10."""
    pd.DataFrame({"DAC Percent": dac, "Optical Power (mW)": power}).to_csv(path, index=False)


def master_lookup(path, intensity):
    """Frozen copy of master f453dc18: IlluminationController._load_intensity_calibrations + _apply_lut."""
    calibration_data = pd.read_csv(path)
    max_power = calibration_data["Optical Power (mW)"].max()
    normalized_power = calibration_data["Optical Power (mW)"] / max_power * 100
    dac_percent = np.clip(calibration_data["DAC Percent"].values, 0, 100)
    lut = {"power_percent": normalized_power.values, "dac_percent": dac_percent}
    intensity_percent = np.clip(intensity, 0, 100)
    dac = np.interp(intensity_percent, lut["power_percent"], lut["dac_percent"])
    return np.clip(dac, 0, 100)


class FakeMicrocontroller:
    """Records illumination and heartbeat commands and answers at once (a run drives ~1500 commands per channel)."""

    is_simulated = True

    def __init__(self, factor=0.6, heartbeat_interval_s=None, clock=None):
        self.illumination_intensity_factor = factor
        self.heartbeat_interval_s = heartbeat_interval_s
        self.calls = []
        self._clock = clock
        self.on_since = None  # clock() when the light last came on; None while off

    def supports_multi_port(self):
        return True

    def set_illumination(self, source, percent):
        self.calls.append(("set", source, percent))

    def turn_on_illumination(self):
        self.calls.append(("on",))
        self.on_since = self._clock() if self._clock else 0.0

    def turn_off_illumination(self):
        self.calls.append(("off",))
        self.on_since = None

    def turn_off_all_ports(self):
        self.calls.append(("off_all",))
        self.on_since = None

    def wait_till_operation_is_completed(self, timeout_limit_s=5):
        pass

    def send_heartbeat(self):
        self.calls.append(("heartbeat",))

    def set_watchdog_timeout(self, timeout_s):
        self.calls.append(("set_watchdog_timeout", timeout_s))

    def stop_heartbeat(self):
        self.calls.append(("stop_heartbeat",))
        self.heartbeat_interval_s = None

    def start_heartbeat(self, interval_s=None):
        self.calls.append(("start_heartbeat", interval_s))
        self.heartbeat_interval_s = interval_s

    def commanded(self):
        return [call[2] for call in self.calls if call[0] == "set"]


class FakeTime:
    """sleep() advances clock(): runs with settle times and a 10 s hold finish at once."""

    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def clock(self):
        return self.now
