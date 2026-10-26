"""Laser engine v2 lines in the illumination power calibration (spec §4, §6.4): which channels, getting a line ready
(the DF 560 nm shutter included), the sweep on the raw drive and the checks through the set-power call, the exact
set-point check, what stops a channel, Save after the engine changed, and what a run leaves behind. Every test runs
with the engine's poll and 560 source threads live, as in Squid, and the meter sees only what the simulated engine
emits through the simulated controller's TTL."""

import json

import pytest

from control._def import ILLUMINATION_CODE
from control.laser_engine_v2_calibration import EngineCalibration
from control.laser_engine_v2_link import EngineLink
from control.laser_engine_v2_sim import FakeEngine
from control.microcontroller import Microcontroller, SimSerial
from squid.intensity_calibration import EngineLineIdentity, load_calibration, write_calibration
from squid.intensity_calibration_run import CalibrationSession, ChannelResult, LightSourceStopped, calibrate_channel
from squid.power_meter import PowerMeterInfo, simulated_aom_mw, simulated_laser_mw
from tests.control.engine_calibration_fixtures import ENGINE_YAML, engine_instrument
from tests.squid.calibration_fixtures import FakeTime, make_engine_calibration


@pytest.fixture(autouse=True)
def _fast_resync(monkeypatch):
    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)


@pytest.fixture
def instrument(tmp_path):
    inst = engine_instrument(tmp_path)
    yield inst
    inst.engine.close()


def _target(side, name):
    return next(t for t in side.targets(side.illumination.config_repo.get_illumination_config()) if t.name == name)


class EngineLineMeter:
    """A meter at the sample plane of the simulated instrument: what the simulated engine emits through the simulated
    controller's TTL (EngineCalibration.simulated_light). `after` = (n, action) runs action() at the n-th lit reading.
    """

    def __init__(self, instrument, target, model, after=None):
        self.instrument = instrument
        self.target = target
        self.model = model
        self.after = after
        self.lit = 0
        self.side = EngineCalibration(instrument.illumination)
        self.info = PowerMeterInfo("Model meter", "Model sensor", 500.0, (350.0, 1100.0), 0.0, True)

    def set_wavelength(self, nm):
        pass

    def hold_range(self, max_mw):
        return 1197.0

    def release_range(self):
        pass

    def read_mw(self):
        on, x = self.side.simulated_light(self.instrument.mcu, self.target)
        if not on:
            return 0.002
        self.lit += 1
        if self.after is not None and self.lit == self.after[0]:
            self.after[1]()
        return float(self.model(x)) + 0.002

    def close(self):
        pass


def _calibrate(instrument, name, model=simulated_laser_mw, after=None):
    side = EngineCalibration(instrument.illumination)
    target = _target(side, name)
    side.prepare(target, lambda *a: None, lambda: None, lambda: False)
    output = side.output(instrument.mcu, target)
    meter = EngineLineMeter(instrument, target, model, after)
    time = FakeTime()
    call = dict(
        meter_info=meter.info,
        measured_in="widefield",
        sensor_limit_mw=500.0,
        settle_s=0.0,
        hold_s=1.0,
        rest_s=0.0,
        sleep=time.sleep,
        clock=time.clock,
    )
    return output, lambda: calibrate_channel(target, output, meter, 1.0, **call)


def _session(instrument):
    session = CalibrationSession(
        instrument.mcu, instrument.repo, dac_driven=False, engine=EngineCalibration(instrument.illumination)
    )
    session.settle_s, session.hold_s, session.rest_s = 0.0, 0.0, 0.0
    time = FakeTime()
    session.sleep, session.clock = time.sleep, time.clock
    return session


def _sets(instrument, line, since):
    return [c for c in instrument.fake.sent[since:] if c.startswith(f"LINE{line}:SET")]


def _with(tmp_path, fake):
    """An instrument on a misbehaving simulated engine; the caller closes it."""
    return engine_instrument(tmp_path, fake=fake)


class LaggingEngine(FakeEngine):
    """Reports L2 ON with its current short of the set-point."""

    def _tick(self):
        super()._tick()
        line = self.lines[1]
        if line["st"] == "ON" and line["target"] > 0:
            line["now"] = line["target"] * 0.5


class IgnoringSetEngine(FakeEngine):
    """Acknowledges LINE2:SET but keeps its old target once it holds one (a set-point the firmware did not take)."""

    def _reply(self, cmd):
        if cmd.upper().startswith("LINE2:SET") and self.lines[1]["target"] > 0:
            return "OK %.4f" % float(cmd.split()[1])
        return super()._reply(cmd)


class StuckShutterEngine(FakeEngine):
    """Refuses to open the DF shutter while `stuck`."""

    stuck = True

    def _reply(self, cmd):
        if self.stuck and cmd.upper().replace(" ", "") == "SHUT:OPEN1":
            return "ERR shutter feedback fault"
        return super()._reply(cmd)


class RoundingEngine(FakeEngine):
    """Reports set-points as the firmware prints them: 3 decimals."""

    def _stat(self):
        raw = json.loads(super()._stat())
        for line in raw["lines"]:
            line["target"], line["now"] = round(line["target"], 3), round(line["now"], 3)
        return json.dumps(raw)


# ---- which channels --------------------------------------------------------------------------------------------------
def test_engine_channels_are_the_epi_channels_on_engine_lines(instrument):
    targets = EngineCalibration(instrument.illumination).targets(instrument.repo.get_illumination_config())
    assert [(t.name, t.engine_line, t.max_output) for t in targets] == [
        ("Fluorescence 405 nm Ex", 1, 1.0),
        ("Fluorescence 488 nm Ex", 2, 1.0),  # Max Output 0.5 in the config: not used on engine lines
        ("Fluorescence 560 nm Ex", 3, 1.0),
        ("Fluorescence 638 nm Ex", 4, 1.0),
    ]


def test_a_wavelength_on_two_ports_lists_only_the_channel_whose_port_drives_it(tmp_path):
    yaml = (
        ENGINE_YAML
        + "  - name: Second 488\n    type: epi_illumination\n    controller_port: D5\n    wavelength_nm: 488\n"
    )
    inst = engine_instrument(tmp_path, yaml=yaml)
    try:
        names = [t.name for t in EngineCalibration(inst.illumination).targets(inst.repo.get_illumination_config())]
        assert "Second 488" in names and "Fluorescence 488 nm Ex" not in names  # the TTL map drives 488 on D5
    finally:
        inst.engine.close()


def test_a_disconnected_engine_offers_no_channels(instrument):
    instrument.fake.unplug()
    instrument.engine.poll_once()
    assert EngineCalibration(instrument.illumination).targets(instrument.repo.get_illumination_config()) == []


def test_ceiling_and_run_notes(instrument):
    instrument.engine.set_source_power_mw(300.0)
    side = EngineCalibration(instrument.illumination)
    assert side.ceiling_text(_target(side, "Fluorescence 488 nm Ex")) == "2.045 A"
    assert side.ceiling_text(_target(side, "Fluorescence 560 nm Ex")) == "5 V"
    notes = side.run_notes([_target(side, "Fluorescence 488 nm Ex"), _target(side, "Fluorescence 560 nm Ex")])
    assert notes == [
        "Fluorescence 560 nm Ex is measured at the 560 nm laser power set in the Laser Engine tab (300 mW); its "
        "calibration holds at any laser power."
    ]


# ---- getting a line ready ------------------------------------------------------------------------------------------
def test_waiting_for_a_line_reports_why_and_keeps_the_watchdog_fed(tmp_path):
    inst = _with(tmp_path, FakeEngine(tok_delay_polls=20))  # the TEC takes ~20 polls
    try:
        side = EngineCalibration(inst.illumination)
        messages, feeds = [], []
        side.prepare(
            _target(side, "Fluorescence 488 nm Ex"),
            lambda message, *a: messages.append(message),
            lambda: feeds.append(1),
            lambda: False,
        )
        assert any("waiting for L2 (L2 TEC not in window yet)" in m for m in messages) and feeds
        assert inst.engine.get_latest_status().channels["L2"].is_ready
    finally:
        inst.engine.close()


def test_the_meter_reading_stays_on_show_while_a_line_comes_up(tmp_path):
    """A line can take minutes to come up (TEC, the 560 source): the run keeps reporting what the meter reads, the
    light off, instead of the dialog showing the previous channel's last reading all that time."""
    inst = _with(tmp_path, FakeEngine(tok_delay_polls=50))  # the TEC takes ~50 polls
    try:
        session = _session(inst)
        session.connect()
        while_not_ready = []

        def note(channel, mw, level):
            status = inst.engine.get_latest_status()
            if status is None or not status.channels["L2"].is_ready:
                while_not_ready.append((channel, level))

        target = next(t for t in session.targets() if t.name == "Fluorescence 488 nm Ex")
        session.run([target], measured_in="widefield", sensor_limit_mw=500.0, on_reading=note)
        assert while_not_ready and all(entry == (target.name, None) for entry in while_not_ready)
    finally:
        inst.engine.close()


def test_a_line_that_does_not_become_ready_fails_with_the_engines_reason(tmp_path):
    inst = _with(tmp_path, FakeEngine(tok_delay_polls=10**6))  # the TEC never settles
    try:
        side = EngineCalibration(inst.illumination)
        side.ready_timeout_s = 0.3
        with pytest.raises(LightSourceStopped, match=r"L2 not ready within 0.3 s \(L2 TEC not in window yet\)"):
            side.prepare(_target(side, "Fluorescence 488 nm Ex"), lambda *a: None, lambda: None, lambda: False)
    finally:
        inst.engine.close()


def test_the_560_key_not_cycled_fails_the_channel(instrument):
    instrument.source.needs_key = True
    side = EngineCalibration(instrument.illumination)
    with pytest.raises(LightSourceStopped, match="560 key OFF then ON"):
        side.prepare(_target(side, "Fluorescence 560 nm Ex"), lambda *a: None, lambda: None, lambda: False)


def test_a_560_line_whose_shutter_does_not_open_is_refused(tmp_path):
    inst = _with(tmp_path, StuckShutterEngine(tok_delay_polls=0))
    try:
        side = EngineCalibration(inst.illumination)
        side.shutter_open_timeout_s = 0.3
        target = _target(side, "Fluorescence 560 nm Ex")
        with pytest.raises(LightSourceStopped, match="shutter did not open"):
            side.prepare(target, lambda *a: None, lambda: None, lambda: False)
        assert inst.engine.get_latest_status().channels["L3"].is_ready  # READY alone is not enough
        session = _session(inst)
        with pytest.raises(LightSourceStopped, match="shutter is not open"):
            session.test_beam_on(target, 30.0)
        assert ("on",) not in inst.mcu.calls
    finally:
        inst.engine.close()


# ---- one channel ---------------------------------------------------------------------------------------------------
def test_a_diode_line_calibrates_to_its_ceiling_and_passes_through_the_set_power_call(instrument):
    output, run = _calibrate(instrument, "Fluorescence 488 nm Ex")
    mark = len(instrument.fake.sent)
    c = run().calibration
    assert c.verification == "pass" and c.engine == EngineLineIdentity("SN-1", "DF", 2, 2.045, "A")
    assert c.illumination_intensity_factor == 1.0 and c.max_output == 1.0
    sets = _sets(instrument, 2, mark)
    assert max(float(cmd.split()[1]) for cmd in sets) == pytest.approx(2.045)  # swept to the firmware ceiling
    assert sets[-1] == "LINE2:SET 0.0000" and not output.is_on  # ends dark, at 0
    assert instrument.mcu.calls[-1] == ("set", 12, 0.0)
    assert instrument.illumination.lookup_calibration(488).calibration is None  # the candidate is gone, nothing saved


def test_the_560_line_finds_the_aom_peak_and_records_the_laser_power(instrument):
    instrument.engine.set_source_power_mw(300.0)
    output, run = _calibrate(instrument, "Fluorescence 560 nm Ex", model=simulated_aom_mw)
    c = run().calibration
    assert output.is_aom and c.verification == "pass"
    assert c.rollover is not None and c.top_dac_percent == pytest.approx(90.0, abs=1.0)
    assert c.engine.ceiling_unit == "V" and c.source_power_mw[0] == 300.0


def test_a_line_reporting_set_points_at_three_decimals_still_calibrates(tmp_path):
    inst = _with(tmp_path, RoundingEngine(tok_delay_polls=0))
    try:
        _, run = _calibrate(inst, "Fluorescence 488 nm Ex")
        assert run().calibration.verification == "pass"
    finally:
        inst.engine.close()


def test_a_line_that_leaves_ready_mid_sweep_stops_the_channel_with_the_light_off(instrument):
    output, run = _calibrate(instrument, "Fluorescence 488 nm Ex", after=(50, instrument.fake.open_cover))
    with pytest.raises(LightSourceStopped, match="cover open"):
        run()
    assert not output.is_on and instrument.mcu.calls[-1] == ("set", 12, 0.0)


def test_a_set_point_the_engine_acknowledges_but_does_not_take_stops_the_channel(tmp_path):
    inst = _with(tmp_path, IgnoringSetEngine(tok_delay_polls=0))
    try:
        output, run = _calibrate(inst, "Fluorescence 488 nm Ex")
        with pytest.raises(LightSourceStopped, match="the set-point was not taken"):
            run()
        assert not output.is_on
    finally:
        inst.engine.close()


def test_a_target_changed_after_a_good_set_point_stops_the_channel_at_the_next_reading(instrument):
    # the set-point was taken and reached, then something else moved the line before a reading
    def change_the_target():
        line = instrument.fake.lines[1]
        line["target"] = line["now"] = line["target"] / 2

    output, run = _calibrate(instrument, "Fluorescence 488 nm Ex", after=(50, change_the_target))
    with pytest.raises(LightSourceStopped, match="was changed"):
        run()
    assert not output.is_on


def test_a_level_the_line_does_not_reach_stops_the_channel(tmp_path):
    inst = _with(tmp_path, LaggingEngine(tok_delay_polls=0))
    try:
        output, run = _calibrate(inst, "Fluorescence 488 nm Ex")
        with pytest.raises(LightSourceStopped, match="did not reach its set-point"):
            run()
        assert not output.is_on
    finally:
        inst.engine.close()


def test_the_560_shutter_closing_mid_run_stops_the_channel(tmp_path):
    fake = StuckShutterEngine(tok_delay_polls=0)
    fake.stuck = False
    inst = _with(tmp_path, fake)
    try:

        def close_the_shutter():
            fake.stuck = True
            fake.shut_open = False

        output, run = _calibrate(inst, "Fluorescence 560 nm Ex", model=simulated_aom_mw, after=(50, close_the_shutter))
        with pytest.raises(LightSourceStopped, match="shutter is not open"):
            run()
        assert not output.is_on
    finally:
        inst.engine.close()


def test_a_lost_engine_connection_stops_the_channel(instrument):
    output, run = _calibrate(instrument, "Fluorescence 488 nm Ex", after=(50, instrument.fake.unplug))
    with pytest.raises(LightSourceStopped, match="connection lost"):
        run()
    assert not output.is_on


# ---- simulation ------------------------------------------------------------------------------------------------------
def test_the_simulated_meter_sees_the_engines_actual_output_through_the_controller_ttl(instrument):
    side = EngineCalibration(instrument.illumination)
    target = _target(side, "Fluorescence 488 nm Ex")
    side.prepare(target, lambda *a: None, lambda: None, lambda: False)
    instrument.engine.set_line_intensity(2, 50.0)
    assert side.simulated_light(instrument.mcu, target) == (False, 0.0)  # the TTL is off
    instrument.mcu.set_illumination(target.source_code, 0.0)
    instrument.mcu.turn_on_illumination()
    assert side.simulated_light(instrument.mcu, target) == (True, pytest.approx(0.5, abs=1e-4))
    instrument.fake.lines[1]["now"] = 0.25 * 2.045  # what the engine holds, not what was asked
    assert side.simulated_light(instrument.mcu, target)[1] == pytest.approx(0.25)


def test_the_simulated_controller_reports_its_port_ttl():
    mcu = Microcontroller(serial_device=SimSerial())
    mcu.set_illumination(ILLUMINATION_CODE.ILLUMINATION_D2, 0.0)
    mcu.wait_till_operation_is_completed()
    mcu.turn_on_illumination()
    mcu.wait_till_operation_is_completed()
    assert mcu.simulated_port_is_on(1) and not mcu.simulated_port_is_on(0)
    mcu.turn_off_all_ports()
    mcu.wait_till_operation_is_completed()
    assert not mcu.simulated_port_is_on(1)


# ---- the session -----------------------------------------------------------------------------------------------------
def test_a_run_and_a_save_then_live_view_sends_the_same_percent_through_the_new_file(instrument):
    assert instrument.engine.wait_until_ready(["L2"], timeout_s=10.0)
    instrument.illumination.set_intensity(488, 50.0)  # live view had 488 nm at 50 % before the calibration
    session = _session(instrument)
    session.connect()  # the simulated meter (the controller is simulated): reads the simulated engine
    targets = [t for t in session.targets() if t.name in ("Fluorescence 488 nm Ex", "Fluorescence 560 nm Ex")]
    results = session.run(targets, measured_in="widefield", sensor_limit_mw=500.0)
    assert all(isinstance(r, ChannelResult) and r.calibration.verification == "pass" for r in results.values())
    session.save([r.calibration for r in results.values()])
    saved = load_calibration(instrument.calibrations / "488nm_D2.csv")
    assert saved.engine == EngineLineIdentity("SN-1", "DF", 2, 2.045, "A")
    mark = len(instrument.fake.sent)
    instrument.illumination.set_intensity(488, 50.0)  # the same % as before the run: sent again, through the file
    drive = saved.commanded_percent(50.0, 1.0, 1.0)[0]
    assert _sets(instrument, 2, mark) == ["LINE2:SET %.4f" % (drive / 100.0 * 2.045)]


def test_after_a_run_without_save_live_view_sends_again(instrument):
    assert instrument.engine.wait_until_ready(["L2"], timeout_s=10.0)
    instrument.illumination.set_intensity(488, 50.0)
    session = _session(instrument)
    session.connect()
    session.run([t for t in session.targets() if t.engine_line == 2], measured_in="widefield", sensor_limit_mw=500.0)
    mark = len(instrument.fake.sent)
    instrument.illumination.set_intensity(488, 50.0)  # the run left L2 at 0: sent again, still linear
    assert _sets(instrument, 2, mark) == ["LINE2:SET %.4f" % (0.5 * 2.045)]


def test_save_refuses_a_calibration_from_an_engine_that_changed_since_the_run(instrument):
    assert instrument.engine.wait_until_ready(["L2"], timeout_s=10.0)
    session = _session(instrument)
    session.connect()
    results = session.run(
        [t for t in session.targets() if t.engine_line == 2], measured_in="widefield", sensor_limit_mw=500.0
    )
    instrument.engine.serial_number = "SN-2"  # another engine connected between the run and Save
    with pytest.raises(ValueError, match="laser engine changed since the run.*SN-2"):
        session.save([r.calibration for r in results.values()])
    assert not (instrument.calibrations / "488nm_D2.csv").exists()


def test_the_current_status_checks_the_engine(instrument):
    c = make_engine_calibration(serial="SN-OTHER")
    write_calibration(c, instrument.calibrations / c.file_name)
    config = instrument.repo.get_illumination_config(for_edit=True)
    next(ch for ch in config.channels if ch.name == c.channel).intensity_calibration_file = c.file_name
    instrument.repo.save_illumination_config(config)
    session = _session(instrument)
    status = session.current_status(next(t for t in session.targets() if t.engine_line == 2))
    assert status.startswith("488nm_D2.csv: not applied: ") and "SN-OTHER" in status


def test_the_test_beam_is_refused_while_the_line_is_not_ready(tmp_path):
    inst = _with(tmp_path, FakeEngine(tok_delay_polls=10**6))
    try:
        session = _session(inst)
        target = next(t for t in session.targets() if t.engine_line == 2)
        with pytest.raises(LightSourceStopped, match="not ready"):
            session.test_beam_on(target, 30.0)
        assert ("on",) not in inst.mcu.calls
    finally:
        inst.engine.close()


# ---- what a raw drive leaves behind (final review C1) ------------------------------------------------------------------
def test_after_a_test_beam_live_view_at_the_same_percent_sends_again(instrument):
    assert instrument.engine.wait_until_ready(["L2"], timeout_s=10.0)
    instrument.illumination.set_intensity(488, 50.0)  # live view had 488 nm at 50 %
    session = _session(instrument)
    target = next(t for t in session.targets() if t.engine_line == 2)
    session.test_beam_on(target, 30.0)  # centring the sensor: the line goes to 30 % of its ceiling, then to 0
    session.test_beam_off(target)
    mark = len(instrument.fake.sent)
    instrument.illumination.set_intensity(488, 50.0)  # the dialog closes and the live channel is re-sent
    assert _sets(instrument, 2, mark) == ["LINE2:SET %.4f" % (0.5 * 2.045)]


def test_a_run_of_one_wavelength_resends_the_other_wavelength_on_its_line(tmp_path):
    yaml = ENGINE_YAML + (
        "  - name: Fluorescence 470 nm Ex\n    type: epi_illumination\n    controller_port: D2\n    wavelength_nm: 470\n"
    )
    inst = engine_instrument(tmp_path, yaml=yaml)
    try:
        assert inst.engine.wait_until_ready(["L2"], timeout_s=10.0)
        inst.illumination.set_intensity(470, 50.0)  # live view had the other channel on L2 at 50 %
        session = _session(inst)
        session.connect()
        session.run(
            [t for t in session.targets() if t.wavelength_nm == 488], measured_in="widefield", sensor_limit_mw=500.0
        )
        mark = len(inst.fake.sent)
        inst.illumination.set_intensity(470, 50.0)  # the run left L2 at 0: sent again
        assert _sets(inst, 2, mark) == ["LINE2:SET %.4f" % (0.5 * 2.045)]
    finally:
        inst.engine.close()
