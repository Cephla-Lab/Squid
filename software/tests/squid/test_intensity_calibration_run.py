"""Running a calibration (design §5, §7, §8): sweep and verify through the simulated meter, the light always ends
off and every port is turned off, the ceiling and the sensor limits are never passed, the watchdog stays armed by
the code driving the light, bad readings are measured again, real kinks are verified, failures are contained per
channel, and saving is guarded."""

import datetime
import pathlib
from dataclasses import replace
from unittest.mock import MagicMock

import numpy as np
import pytest

from control.core.config import ConfigRepository
from squid.intensity_calibration import load_calibration, write_calibration
from squid.intensity_calibration_run import (
    CalibrationCancelled,
    CalibrationSession,
    ChannelResult,
    ChannelTarget,
    MicrocontrollerDacOutput,
    SensorLimitExceeded,
    WatchdogDeadline,
    WatchdogDeadlineMissed,
    calibrate_channel,
    measured_in_label,
)
from squid.power_meter import (
    PowerMeterError,
    PowerMeterInfo,
    PowerMeterOverrange,
    SimulatedPowerMeter,
    simulated_laser_mw,
    simulated_led_mw,
)
from tests.squid.calibration_fixtures import FakeMicrocontroller, FakeTime, bench_488_led_mw

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
        memory=None,
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
        self.memory = memory  # (fraction low, seconds) after a reading at DAC >= 90 %: a thermal memory
        self.last_top_s = None
        self.reads = 0
        self.info = PowerMeterInfo("Model meter", "Model sensor", 500.0, wavelength_range, 0.0, True)
        self.held_mw = None  # the range held, None while auto-ranging
        self.range_events = []  # ("hold", asked mW, held mW) / ("release",)
        self.lit_reads_by_range = []  # the range each reading with the light on was taken on

    def set_wavelength(self, nm):
        pass

    def hold_range(self, max_mw):
        # the PM16-121's ranges at 488 nm (bench 2026-10-09); it rounds up to the next one
        self.held_mw = next((r for r in (0.1193, 11.93, 1197.0) if r >= max_mw), 1197.0)
        self.range_events.append(("hold", max_mw, self.held_mw))
        return self.held_mw

    def release_range(self):
        self.held_mw = None
        self.range_events.append(("release",))

    def read_mw(self):
        self.reads += 1
        if self.fail_after is not None and self.reads > self.fail_after:
            raise PowerMeterError("USB disconnected")
        if not self.output.is_on:
            return 0.002
        self.lit_reads_by_range.append(self.held_mw)
        power = float(self.model(self.output.commanded_percent / 100.0 * FACTOR))
        if self.droop_per_s and self.mcu.on_since is not None:
            power *= 1.0 - self.droop_per_s * (self.time.clock() - self.mcu.on_since)
        held = self.held_mw is not None  # the spike lands in the sweep, not in the pre-scan
        if held and self.spike_at is not None and abs(self.output.commanded_percent - self.spike_at[0]) < 1e-9:
            power *= self.spike_at[1]
            self.spike_at = None
        power *= 1.0 + self.drift_per_read * self.reads
        if self.memory is not None:
            now = self.time.clock()
            if self.last_top_s is not None and now - self.last_top_s < self.memory[1]:
                power *= 1.0 - self.memory[0]
            if self.output.commanded_percent >= 90.0:
                self.last_top_s = now
        if self.alternate:
            power *= 1.0 + self.alternate * (-1) ** self.reads
        if self.held_mw is not None and power > self.held_mw:
            raise PowerMeterOverrange(f"{power:g} mW above the {self.held_mw:g} mW range")
        return power + 0.002

    def close(self):
        pass


def _run(
    target,
    model=simulated_laser_mw,
    sensor_limit_mw=500.0,
    hold_s=10.0,
    rest_s=None,
    cancelled=lambda: False,
    **meter_kwargs,
):
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
    if rest_s is not None:
        call["rest_s"] = rest_s
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
    # the bench 561 nm LED drifted 3.2 % and again after warming up: its output depends on its recent power
    assert any("drifted" in w and "if it repeats" in w for w in run().warnings)


def test_a_spiked_top_reading_is_measured_again_and_does_not_set_p_max():
    _, _, meter, run = _run(LASER, spike_at=(100.0, 1.5))  # 450 mW: under the sensor limit, so it reaches the fit
    calibration = run().calibration
    assert meter.spike_at is None  # the spiked reading was taken
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
    s.rest_s = 0.0
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


class OneShotWatchdog(FakeMicrocontroller):
    """The firmware's watchdog: armed by set_watchdog_timeout, fed by every message, and after it fires (all ports
    off) it stays off until armed again. Counts light turned on while it is not armed."""

    def __init__(self, time):
        super().__init__(FACTOR, heartbeat_interval_s=2.5, clock=time.clock)
        self.time = time
        self.armed = False
        self.fed_at = 0.0
        self.unprotected_on = 0
        self.stall_when = None  # a predicate on the last command: the wait for it to complete stalls 6 s

    def _fed(self):
        self.fed_at = self.time.clock()

    def wait_till_operation_is_completed(self, timeout_limit_s=5):
        super().wait_till_operation_is_completed(timeout_limit_s)
        if self.stall_when is not None and self.calls and self.stall_when(self.calls[-1]):
            self.stall(6.0)

    def set_watchdog_timeout(self, timeout_s):
        super().set_watchdog_timeout(timeout_s)
        self.armed = True
        self._fed()

    def send_heartbeat(self):
        super().send_heartbeat()
        self._fed()

    def set_illumination(self, source, percent):
        super().set_illumination(source, percent)
        self._fed()

    def turn_on_illumination(self):
        super().turn_on_illumination()
        self._fed()
        if not self.armed:
            self.unprotected_on += 1

    def turn_off_all_ports(self):
        super().turn_off_all_ports()
        self._fed()

    def stall(self, seconds):
        self.time.sleep(seconds)
        if self.armed and self.time.clock() - self.fed_at > 5.0:
            self.turn_off_all_ports()  # what the firmware does when it fires
            self.armed = False


class _StallingMeter(SimulatedPowerMeter):
    """Stalls once, for 6 s, at the first reading at or above 50 % DAC; then raises or answers normally."""

    def __init__(self, session, mcu, then_raise):
        super().__init__(session.source_state, noise_fraction=0.0, ambient_mw=0.0)
        self.session, self.mcu, self.then_raise, self.stalled = session, mcu, then_raise, False

    def read_mw(self):
        active = self.session._active
        if not self.stalled and active is not None and active.is_on and active.commanded_percent >= 50:
            self.stalled = True
            self.mcu.stall(6.0)
            if self.then_raise:
                raise PowerMeterError("read resumed after a stall")
        return super().read_mw()


@pytest.mark.parametrize("then_raise", [True, False])
def test_a_watchdog_that_fired_is_armed_again_before_the_next_channel(repo, then_raise):
    time = FakeTime()
    mcu = OneShotWatchdog(time)
    session = CalibrationSession(mcu, repo)
    session.settle_s, session.hold_s, session.sleep, session.clock = 0.0, 0.0, time.sleep, time.clock
    session.rest_s = 0.0
    session.meter = _StallingMeter(session, mcu, then_raise)
    results = session.run(session.targets(), measured_in="n/a", sensor_limit_mw=500.0)
    first, second = results["Fluorescence 405 nm Ex"], results["Fluorescence 730 nm Ex"]
    assert isinstance(first, Exception)  # the stalled channel cannot be trusted, whether the read raised or not
    if not then_raise:
        assert "watchdog" in str(first)
    assert isinstance(second, ChannelResult)
    assert mcu.unprotected_on == 0
    assert mcu.armed and mcu.calls[-1] == ("start_heartbeat", 2.5)


def _saved_once(session):
    calibration = _calibrated(session)
    session.save([calibration], now=datetime.datetime(2026, 10, 8, 14, 30, 12))
    return calibration, session.calibrations_dir() / "405nm_D1.csv"


def _unchanged(session, repo, path, before_bytes, before_png):
    assert path.read_bytes() == before_bytes and path.with_suffix(".png").read_bytes() == before_png
    assert repo.get_illumination_config().channels[1].intensity_calibration_file == "405nm_D1.csv"
    assert not list(session.calibrations_dir().glob(".*"))  # no staged files left behind
    assert not (session.calibrations_dir() / "backup").exists() or not list(
        (session.calibrations_dir() / "backup").iterdir()
    )


def test_a_failed_plot_leaves_the_active_calibration_untouched(repo, monkeypatch):
    session = _session(repo)
    calibration, path = _saved_once(session)
    before, before_png = path.read_bytes(), path.with_suffix(".png").read_bytes()
    monkeypatch.setattr("squid.intensity_calibration_run.write_plot", MagicMock(side_effect=OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        session.save([replace(calibration, calibrated_at="2026-10-09T00:20:00")])
    _unchanged(session, repo, path, before, before_png)


def test_a_failure_on_the_second_channel_publishes_nothing(repo, monkeypatch):
    session = _session(repo)
    calibration, path = _saved_once(session)
    before, before_png = path.read_bytes(), path.with_suffix(".png").read_bytes()
    session.connect()
    led = session.run([session.targets()[1]], measured_in="n/a", sensor_limit_mw=500.0)["Fluorescence 730 nm Ex"]
    from squid import intensity_calibration_run as run_module

    real_plot = run_module.write_plot

    def plot_fails_for_730(c, p):
        if c.wavelength_nm == 730:
            raise OSError("disk full")
        real_plot(c, p)

    monkeypatch.setattr(run_module, "write_plot", plot_fails_for_730)
    with pytest.raises(OSError):
        session.save([replace(calibration, calibrated_at="2026-10-09T00:20:00"), led.calibration])
    _unchanged(session, repo, path, before, before_png)
    assert not (session.calibrations_dir() / "730nm_D5.csv").exists()


def test_a_failed_config_save_restores_the_previous_calibration(repo, monkeypatch):
    session = _session(repo)
    calibration, path = _saved_once(session)
    before, before_png = path.read_bytes(), path.with_suffix(".png").read_bytes()
    monkeypatch.setattr(repo, "save_illumination_config", MagicMock(side_effect=OSError("read-only file system")))
    with pytest.raises(OSError, match="read-only"):
        session.save([replace(calibration, calibrated_at="2026-10-09T00:20:00")])
    _unchanged(session, repo, path, before, before_png)


@pytest.mark.parametrize("where", ["settle", "hold"])
def test_a_pause_outside_a_read_that_outlasts_the_watchdog_fails_the_channel(repo, where):
    # the watchdog can fire during any gap in messages, not only during a meter read
    time = FakeTime()
    mcu = OneShotWatchdog(time)
    session = CalibrationSession(mcu, repo)
    session.settle_s, session.clock = 0.01, time.clock
    session.hold_s = 10.0 if where == "hold" else 0.0
    session.rest_s = 0.0
    stalled = []

    def sleep(seconds):
        time.sleep(seconds)
        active = session._active
        mid_sweep = active is not None and active.is_on and active.commanded_percent >= 50
        at_settle = where == "settle" and seconds == session.settle_s and mid_sweep
        in_hold = where == "hold" and seconds == 0.5
        if not stalled and (at_settle or in_hold):
            stalled.append(True)
            mcu.stall(6.0)

    session.sleep = sleep
    session.connect()
    results = session.run(session.targets(), measured_in="n/a", sensor_limit_mw=500.0)
    first, second = results["Fluorescence 405 nm Ex"], results["Fluorescence 730 nm Ex"]
    assert stalled and isinstance(first, WatchdogDeadlineMissed) and "watchdog" in str(first)
    assert isinstance(second, ChannelResult)
    assert mcu.unprotected_on == 0


def test_a_gap_any_message_sees_fails_every_later_check():
    # the watchdog is one-shot: once a gap let it fire, a later message (a feed, light off) does not arm it again
    time = FakeTime()
    deadline = WatchdogDeadline(5.0, time.clock)
    time.sleep(4.0)
    deadline.check()
    deadline.sent()
    time.sleep(6.0)
    deadline.sent()  # light off, or a feed: never refused, but the gap is remembered
    with pytest.raises(WatchdogDeadlineMissed, match="6.0 s"):
        deadline.check()
    deadline.sent()
    with pytest.raises(WatchdogDeadlineMissed):
        deadline.check()
    WatchdogDeadline(None, time.clock).check()  # no watchdog of ours to keep: nothing to miss


def test_a_failed_move_of_the_old_plot_to_backup_puts_the_old_calibration_back(repo, monkeypatch):
    # the CSV is already in backup/ when moving its PNG fails: undone move by move, not per calibration
    session = _session(repo)
    calibration, path = _saved_once(session)
    before, before_png = path.read_bytes(), path.with_suffix(".png").read_bytes()
    real_replace = pathlib.Path.replace

    def replace_fails_for_the_png_backup(source, target):
        if pathlib.Path(target).parent.name == "backup" and pathlib.Path(target).suffix == ".png":
            raise OSError("device busy")
        return real_replace(source, target)

    monkeypatch.setattr(pathlib.Path, "replace", replace_fails_for_the_png_backup)
    with pytest.raises(OSError, match="device busy"):
        session.save([replace(calibration, calibrated_at="2026-10-09T00:20:00")])
    monkeypatch.undo()
    _unchanged(session, repo, path, before, before_png)


def test_a_partly_written_config_is_put_back(repo, monkeypatch):
    session = _session(repo)
    calibration, path = _saved_once(session)
    before, before_png = path.read_bytes(), path.with_suffix(".png").read_bytes()
    config_path = repo.machine_configs_path / "illumination_channel_config.yaml"
    config_before = config_path.read_bytes()

    def write_half_then_fail(target, model):
        target.write_text("version: 1\nchannels:\n  - name: Fluoresc")
        raise OSError("disk full")

    monkeypatch.setattr(repo, "_save_yaml", write_half_then_fail)
    with pytest.raises(OSError, match="disk full"):
        session.save([replace(calibration, calibrated_at="2026-10-09T00:20:00")])
    assert config_path.read_bytes() == config_before
    _unchanged(session, repo, path, before, before_png)
    reloaded = ConfigRepository(base_path=repo.machine_configs_path.parent).get_illumination_config()
    assert reloaded.channels[1].intensity_calibration_file == "405nm_D1.csv"


def test_a_config_that_cannot_be_put_back_does_not_stop_the_files_being_put_back(repo, monkeypatch):
    session = _session(repo)
    calibration, path = _saved_once(session)
    before, before_png = path.read_bytes(), path.with_suffix(".png").read_bytes()

    def write_half_then_lock(target, model):
        target.write_text("version: 1\nchannels:\n  - name: Fluoresc")
        target.chmod(0o444)
        raise OSError("disk full")

    monkeypatch.setattr(repo, "_save_yaml", write_half_then_lock)
    with pytest.raises(OSError, match="disk full"):
        session.save([replace(calibration, calibrated_at="2026-10-09T00:20:00")])
    assert path.read_bytes() == before and path.with_suffix(".png").read_bytes() == before_png
    assert not list(session.calibrations_dir().glob(".*"))


def test_a_channel_is_measured_on_the_one_meter_range_that_holds_the_sensor_limit():
    # auto-ranging stitched the PM16-121's 11.9 mW and 1.2 W ranges, which disagree by 5-7 %, into the 488 nm curve
    mcu, output, meter, run = _run(LASER, sensor_limit_mw=500.0)
    calibration = run().calibration
    assert meter.range_events == [("hold", 500.0, 1197.0), ("release",)]
    assert meter.lit_reads_by_range and set(meter.lit_reads_by_range) == {1197.0}  # not one reading auto-ranged
    assert calibration.meter_range_mw == 1197.0
    assert meter.held_mw is None  # auto-ranging again afterwards


def test_a_lower_sensor_limit_picks_a_lower_range():
    _, _, meter, run = _run(LASER, model=lambda x: simulated_laser_mw(x) / 100.0, sensor_limit_mw=10.0)
    assert run().calibration.meter_range_mw == 11.93
    assert meter.range_events[0] == ("hold", 10.0, 11.93)


def test_the_meter_goes_back_to_auto_range_when_a_channel_fails():
    mcu, output, meter, run = _run(LASER, fail_after=60)
    with pytest.raises(PowerMeterError):
        run()
    assert meter.range_events[-1] == ("release",) and meter.held_mw is None
    _assert_off(mcu, output, LASER.source_code)


def test_unsettled_sweep_readings_are_counted_and_saved(tmp_path):
    clean = _run(LASER)[3]().calibration
    assert clean.unsettled_readings == 0
    result = _run(LASER, alternate=0.05)[3]()
    noisy = result.calibration
    assert noisy.unsettled_readings > 0
    assert any("did not settle" in w for w in result.warnings)  # some stayed unsettled when measured again
    path = tmp_path / noisy.file_name
    write_calibration(noisy, path)
    again = load_calibration(path)
    assert (again.unsettled_readings, again.meter_range_mw) == (noisy.unsettled_readings, noisy.meter_range_mw)


def test_a_source_that_jumps_on_is_reported_after_the_run():
    result = _run(LASER, model=bench_488_led_mw)[3]()
    assert result.calibration.lowest_percent > 0
    lowest = f"{result.calibration.lowest_percent:.1f} %"
    assert any(f"lowest non-zero power is {lowest} of max" in w and f"get {lowest}" in w for w in result.warnings)
    assert not any("lowest non-zero" in w for w in _run(LASER)[3]().warnings)  # a smooth source: nothing to say


def test_the_checks_after_the_sweep_wait_out_a_thermal_memory():
    # the bench 561 nm LED reads 2-3 % low for several seconds after full power (time constant 5-10 s); the drift
    # check and the verification came right after the sweep's top end and failed it, twice
    memory = (0.03, 10.0)
    hot = _run(LASER, hold_s=0.0, rest_s=0.0, memory=memory)[3]()
    assert any("drifted" in w for w in hot.warnings)
    rested = _run(LASER, hold_s=0.0, memory=memory)[3]()  # the default rest
    assert not any("drifted" in w for w in rested.warnings)
    assert rested.calibration.verification == "pass"


def test_the_rest_keeps_the_watchdog_fed(repo):
    # 30 s with the light off is six watchdog periods: unfed, the one-shot watchdog would fire and disarm
    time = FakeTime()
    mcu = OneShotWatchdog(time)
    session = CalibrationSession(mcu, repo)
    session.settle_s, session.hold_s, session.sleep, session.clock = 0.0, 0.0, time.sleep, time.clock
    assert session.rest_s == 30.0
    session.connect()
    result = session.run([session.targets()[0]], measured_in="n/a", sensor_limit_mw=500.0)["Fluorescence 405 nm Ex"]
    assert isinstance(result, ChannelResult)
    assert mcu.armed and mcu.unprotected_on == 0


def _owned_session(repo, time, mcu):
    session = CalibrationSession(mcu, repo)
    session.settle_s, session.hold_s, session.rest_s = 0.0, 0.0, 0.0
    session.sleep, session.clock = time.sleep, time.clock
    session.connect()
    return session


def test_a_stall_while_arming_the_watchdog_fails_the_channel(repo):
    # review 4: the deadline started after the arming wait, so a 6 s stall in it - the firmware already counting from
    # the arming message - let the watchdog fire unseen, and the channel ran with 220 unprotected light-on commands
    time = FakeTime()
    mcu = OneShotWatchdog(time)
    mcu.stall_when = lambda call: call[0] == "set_watchdog_timeout"
    session = _owned_session(repo, time, mcu)
    results = session.run(session.targets(), measured_in="n/a", sensor_limit_mw=500.0)
    assert all(isinstance(r, WatchdogDeadlineMissed) for r in results.values())
    assert mcu.unprotected_on == 0


def test_a_stall_while_setting_up_the_test_beam_keeps_it_off(repo):
    # review 4: the test beam had no deadline; a 6 s stall setting the DAC fired the watchdog, then the beam came on
    time = FakeTime()
    mcu = OneShotWatchdog(time)
    session = _owned_session(repo, time, mcu)
    mcu.stall_when = lambda call: call[0] == "set" and call[2] > 0
    with pytest.raises(WatchdogDeadlineMissed):
        session.test_beam_on(session.targets()[0], 10.0)
    assert mcu.unprotected_on == 0
    assert session.source_state() == (False, 0.0)
    assert mcu.calls[-1] == ("start_heartbeat", 2.5)  # the heartbeat is back


def test_save_refuses_when_the_port_was_remapped_since_the_run(repo):
    session = _session(repo)
    calibration = _calibrated(session)
    assert calibration.source_code == 11  # D1 in this config
    config = repo.get_illumination_config(for_edit=True)
    config.controller_port_mapping["D1"] = 12
    repo.save_illumination_config(config)
    with pytest.raises(ValueError, match="run the calibration again"):
        session.save([calibration])
    assert not (session.calibrations_dir() / "405nm_D1.csv").exists()
