"""Running a calibration (design §5, §7, §8): sweep and verify through the simulated meter, the light always ends
off and every port is turned off, the ceiling and the sensor limits are never passed, the watchdog stays armed by
the code driving the light, bad readings are measured again, real kinks are verified, failures are contained per
channel, and saving is guarded."""

import datetime

import numpy as np
import pytest

from control.core.config import ConfigRepository
from squid.intensity_calibration import load_calibration
from squid.intensity_calibration_run import (
    CalibrationCancelled,
    CalibrationSession,
    ChannelResult,
    ChannelTarget,
    MicrocontrollerDacOutput,
    SensorLimitExceeded,
    calibrate_channel,
    measured_in_label,
)
from squid.power_meter import PowerMeterError, PowerMeterInfo, SimulatedPowerMeter, simulated_laser_mw, simulated_led_mw
from tests.squid.calibration_fixtures import FakeMicrocontroller, FakeTime

LASER = ChannelTarget("Fluorescence 405 nm Ex", 405, "D1", 11, 1.0)
LED = ChannelTarget("Fluorescence 730 nm Ex", 730, "D5", 15, 1.0)
FACTOR = 0.6

YAML = """\
version: 1
controller_port_mapping:
  D1: 11
  D5: 15
  USB1: 0
channels:
  - name: BF LED matrix full
    type: transillumination
    controller_port: USB1
    wavelength_nm: null
  - name: Fluorescence 405 nm Ex
    type: epi_illumination
    controller_port: D1
    wavelength_nm: 405
  - name: Fluorescence 730 nm Ex
    type: epi_illumination
    controller_port: D5
    wavelength_nm: 730
"""


class ModelMeter:
    """A meter reading power = model(x) from the commanded light, with optional faults; x = DAC fraction."""

    def __init__(
        self,
        output,
        model,
        mcu=None,
        time=None,
        droop_per_s=0.0,
        fail_after=None,
        spike_at=None,
        alternate=0.0,
        drift_per_read=0.0,
        wavelength_range=(350.0, 1100.0),
    ):
        self.output = output
        self.model = model
        self.mcu = mcu
        self.time = time
        self.droop_per_s = droop_per_s
        self.fail_after = fail_after
        self.spike_at = spike_at  # (commanded %, factor): the first reading at that DAC is multiplied
        self.alternate = alternate
        self.drift_per_read = drift_per_read
        self.reads = 0
        self.info = PowerMeterInfo("Model meter", "Model sensor", 500.0, wavelength_range, 0.0, True)

    def set_wavelength(self, nm):
        pass

    def read_mw(self):
        self.reads += 1
        if self.fail_after is not None and self.reads > self.fail_after:
            raise PowerMeterError("USB disconnected")
        if not self.output.is_on:
            return 0.002
        power = float(self.model(self.output.commanded_percent / 100.0 * FACTOR))
        if self.droop_per_s and self.mcu.on_since is not None:
            power *= 1.0 - self.droop_per_s * (self.time.clock() - self.mcu.on_since)
        if self.spike_at is not None and abs(self.output.commanded_percent - self.spike_at[0]) < 1e-9:
            power *= self.spike_at[1]
            self.spike_at = None
        power *= 1.0 + self.drift_per_read * self.reads
        if self.alternate:
            power *= 1.0 + self.alternate * (-1) ** self.reads
        return power + 0.002

    def close(self):
        pass


def _run(target, model=simulated_laser_mw, sensor_limit_mw=500.0, hold_s=10.0, cancelled=lambda: False, **meter_kwargs):
    time = FakeTime()
    mcu = FakeMicrocontroller(FACTOR, clock=time.clock)
    output = MicrocontrollerDacOutput(mcu, target.source_code)
    meter = ModelMeter(output, model, mcu=mcu, time=time, **meter_kwargs)
    call = dict(
        meter_info=meter.info,
        measured_in="widefield",
        sensor_limit_mw=sensor_limit_mw,
        settle_s=0.0,
        hold_s=hold_s,
        cancelled=cancelled,
        sleep=time.sleep,
        clock=time.clock,
    )
    return mcu, output, meter, lambda: calibrate_channel(target, output, meter, FACTOR, **call)


def _assert_off(mcu, output, source):
    assert not output.is_on and output.commanded_percent == 0.0
    assert mcu.calls[-2:] == [("off_all",), ("set", source, 0.0)]


def test_laser_calibrates_passes_and_ends_with_every_port_off():
    mcu, output, _, run = _run(LASER)
    result = run()
    assert isinstance(result, ChannelResult)
    c = result.calibration
    assert c.verification == "pass" and c.rollover is None and result.warnings == ()
    assert c.file_name == "405nm_D1.csv" and c.hold_s == 10.0 and abs(c.hold_droop_fraction) < 0.01
    assert ("off",) not in mcu.calls  # always all ports, never just the selected one
    _assert_off(mcu, output, 11)


def test_rolling_over_led_is_reported_and_passes():
    _, _, _, run = _run(LED, model=simulated_led_mw)
    calibration = run().calibration
    assert calibration.rollover is not None and calibration.top_dac_percent < 90
    assert calibration.verification == "pass"


def test_the_ceiling_is_never_exceeded():
    target = ChannelTarget("Fluorescence 405 nm Ex", 405, "D1", 11, 0.5)
    mcu, _, _, run = _run(target)
    assert run().calibration.verification == "pass"
    assert max(mcu.commanded()) <= 50.0 + 1e-9


def test_sensor_limit_stops_at_the_first_reading_over_it_with_the_light_off():
    mcu, output, _, run = _run(LASER, sensor_limit_mw=100.0)
    with pytest.raises(SensorLimitExceeded, match="sensor limit"):
        run()
    # the sweep only steps upward: the last DAC lit is one step past the 100 mW point
    assert max(mcu.commanded()) == pytest.approx(30.0 + 100.0 / 300.0 * 70.0, abs=0.6)
    _assert_off(mcu, output, 11)


def test_a_wavelength_outside_the_sensor_range_is_refused_before_any_light():
    mcu, _, _, run = _run(LASER, wavelength_range=(500.0, 1100.0))
    with pytest.raises(PowerMeterError, match="outside the sensor's range"):
        run()
    assert mcu.calls == []


def test_meter_error_mid_sweep_leaves_light_off():
    mcu, output, _, run = _run(LASER, fail_after=50)
    with pytest.raises(PowerMeterError, match="USB disconnected"):
        run()
    _assert_off(mcu, output, 11)


def test_cancel_stops_with_the_light_off():
    calls = []
    mcu, output, _, run = _run(LASER, cancelled=lambda: len(calls) > 100)
    calls = mcu.calls
    with pytest.raises(CalibrationCancelled):
        run()
    _assert_off(mcu, output, 11)


def test_drift_is_warned():
    _, _, _, run = _run(LASER, drift_per_read=0.0002)
    assert any("drifted" in warning for warning in run().warnings)


def test_a_spiked_top_reading_is_measured_again_and_does_not_set_p_max():
    _, _, _, run = _run(LASER, spike_at=(100.0, 1.5))  # 450 mW: under the sensor limit, so it reaches the fit
    calibration = run().calibration
    assert calibration.p_max_mw == pytest.approx(300.0, rel=0.01)
    assert calibration.verification == "pass"


def test_a_real_narrow_dip_gets_verified_and_fails():
    # external review finding 6: the fixed grid steps over a narrow dip; the dip must be verified where the lookup
    # lands on it (a dip of 20 % of full scale, sigma 0.5 % of the DAC range, at DAC 46 %)
    def dipped(x):
        d = x / FACTOR
        return 300.0 * max(d - 0.2 * np.exp(-0.5 * ((d - 0.46) / 0.005) ** 2), 0.0)

    _, _, _, run = _run(LASER, model=dipped)
    calibration = run().calibration
    extra = [
        r for r, _ in calibration.verification_points if r not in (1, 2, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
    ]
    assert extra, "no verification point was added at the dip"
    assert calibration.verification == "fail"
    assert max(abs(e) for r, e in calibration.verification_points if r in extra) > 5.0


def test_readings_that_do_not_settle_are_flagged():
    _, _, _, run = _run(LASER, alternate=0.05)
    assert any("did not settle" in warning for warning in run().warnings)


def test_continuous_hold_records_the_droop_and_warns():
    _, _, _, run = _run(LASER, droop_per_s=0.006)  # LED-like heating: -6 % after 10 s on
    result = run()
    assert result.calibration.hold_droop_fraction == pytest.approx(-0.06, abs=0.01)
    assert result.calibration.verification == "pass"  # pulsed readings barely heat
    assert any("continuous light" in warning for warning in result.warnings)


def test_measured_in_label():
    assert measured_in_label(False, True) == "n/a"
    assert measured_in_label(True, True) == "confocal"
    assert measured_in_label(True, False) == "widefield"


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(YAML)
    return ConfigRepository(base_path=tmp_path)


def _session(repo, heartbeat_interval_s=None):
    s = CalibrationSession(FakeMicrocontroller(FACTOR, heartbeat_interval_s=heartbeat_interval_s), repo)
    s.settle_s = 0.0
    s.hold_s = 0.0
    return s


def test_targets_are_dac_driven_epi_channels(repo):
    assert [t.name for t in _session(repo).targets()] == ["Fluorescence 405 nm Ex", "Fluorescence 730 nm Ex"]
    assert CalibrationSession(FakeMicrocontroller(), repo, dac_driven=False).targets() == []


def test_the_run_owns_the_watchdog_and_gives_it_back(repo):
    session = _session(repo, heartbeat_interval_s=2.5)
    session.connect()
    assert session.watchdog_protected
    session.run([session.targets()[0]], measured_in="n/a", sensor_limit_mw=500.0)
    calls = session.microcontroller.calls
    # the watchdog is one-shot: it is re-armed before the heartbeat comes back, in case it fired during the run
    assert calls[0] == ("stop_heartbeat",) and calls[-2:] == [("set_watchdog_timeout", 5.0), ("start_heartbeat", 2.5)]
    assert calls.count(("heartbeat",)) > 200  # fed before every reading


def test_the_watchdog_is_given_back_after_a_failure(repo):
    session = _session(repo, heartbeat_interval_s=2.5)
    session.connect()
    session.meter = ModelMeter(session._output_for(LASER), simulated_laser_mw, fail_after=3)
    results = session.run([session.targets()[0]], measured_in="n/a", sensor_limit_mw=500.0)
    assert isinstance(results["Fluorescence 405 nm Ex"], PowerMeterError)
    assert session.microcontroller.calls[-1] == ("start_heartbeat", 2.5)


def test_the_test_beam_owns_the_watchdog_while_on(repo):
    session = _session(repo, heartbeat_interval_s=2.5)
    assert session.connect().meter == "Simulated power meter"
    session.test_beam_on(LASER, 50.0)
    assert session.source_state() == (True, pytest.approx(0.3))
    assert session.read_mw() > 50.0
    session.feed_watchdog()
    assert session.microcontroller.calls[-1] == ("heartbeat",)
    session.test_beam_off(LASER)
    assert session.source_state() == (False, 0.0)
    assert session.microcontroller.calls[-2:] == [("set_watchdog_timeout", 5.0), ("start_heartbeat", 2.5)]
    assert session.hardware_touched


def test_without_a_watchdog_nothing_is_paused_or_fed(repo):
    session = _session(repo)
    session.connect()
    session.run([session.targets()[0]], measured_in="n/a", sensor_limit_mw=500.0)
    names = {call[0] for call in session.microcontroller.calls}
    assert not session.watchdog_protected and not names & {"heartbeat", "stop_heartbeat", "start_heartbeat"}


class _FailingFor405(SimulatedPowerMeter):
    def set_wavelength(self, nm):
        super().set_wavelength(nm)
        self.fail = nm == 405

    def read_mw(self):
        if getattr(self, "fail", False):
            raise PowerMeterError("sensor not responding")
        return super().read_mw()


def test_session_run_continues_after_a_failed_channel(repo):
    session = _session(repo)
    session.connect()
    session.meter = _FailingFor405(session.source_state, seed=0)
    done = []
    results = session.run(
        session.targets(), measured_in="n/a", sensor_limit_mw=500.0, on_channel_done=lambda name, r: done.append(name)
    )
    assert isinstance(results["Fluorescence 405 nm Ex"], PowerMeterError)
    assert isinstance(results["Fluorescence 730 nm Ex"], ChannelResult)
    assert done == ["Fluorescence 405 nm Ex", "Fluorescence 730 nm Ex"]
    assert session.microcontroller.calls[-2:] == [("off_all",), ("set", 15, 0.0)]


def _calibrated(session):
    session.connect()
    return session.run([session.targets()[0]], measured_in="n/a", sensor_limit_mw=500.0)[
        "Fluorescence 405 nm Ex"
    ].calibration


def test_save_writes_files_points_the_config_and_backs_up(repo):
    session = _session(repo)
    calibration = _calibrated(session)
    first = datetime.datetime(2026, 10, 8, 14, 30, 12)
    [(path, backup)] = session.save([calibration], now=first)
    assert backup is None and path.name == "405nm_D1.csv" and path.with_suffix(".png").is_file()
    assert repo.get_illumination_config().channels[1].intensity_calibration_file == "405nm_D1.csv"
    assert load_calibration(path).verification == "pass"

    [(path, backup)] = session.save([calibration], now=first + datetime.timedelta(minutes=1))
    assert backup == path.parent / "backup" / "405nm_D1.20261008T143112.csv"
    assert backup.is_file() and (path.parent / "backup" / "405nm_D1.20261008T143112.png").is_file()


def test_save_refuses_when_the_channel_changed_since_the_run(repo):
    session = _session(repo)
    calibration = _calibrated(session)
    config = repo.get_illumination_config(for_edit=True)
    config.channels[1].name = "405 renamed"
    repo.save_illumination_config(config)
    with pytest.raises(ValueError, match="run the calibration again"):
        session.save([calibration])
    assert not (session.calibrations_dir() / "405nm_D1.csv").exists()


class _ClosingFails:
    info = None

    def close(self):
        raise PowerMeterError("closing the power meter failed: unplugged")


def test_disconnect_never_raises(repo):
    session = _session(repo)
    session.meter = _ClosingFails()
    session.disconnect()
    assert session.meter is None


class _WavelengthFailsFor405(SimulatedPowerMeter):
    def set_wavelength(self, nm):
        if nm == 405:
            raise PowerMeterError("power meter did not take 'SENS:CORR:WAV 405'")
        super().set_wavelength(nm)


def test_a_meter_error_on_set_wavelength_fails_only_that_channel(repo):
    session = _session(repo)
    session.connect()
    session.meter = _WavelengthFailsFor405(session.source_state, seed=0)
    results = session.run(session.targets(), measured_in="n/a", sensor_limit_mw=500.0)
    assert isinstance(results["Fluorescence 405 nm Ex"], PowerMeterError)
    assert isinstance(results["Fluorescence 730 nm Ex"], ChannelResult)
