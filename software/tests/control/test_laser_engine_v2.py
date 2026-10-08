import time
from pathlib import Path

import pytest

from control.laser_engine_v2 import EngineOptions, LaserEngineV2, LaserEngineV2Error
from control.laser_engine_v2_link import EngineLink
from control.laser_engine_v2_sim import FakeEngine
from control.laser_engine_v2_status import LineState

NO_CALIBRATIONS = Path(__file__).parent / "no_such_calibration_dir"  # tests never read this machine's calibration CSVs


@pytest.fixture(autouse=True)
def _fast_resync(monkeypatch):
    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)


def _engine(fake=None, **kw):
    fake = fake or FakeEngine(tok_delay_polls=0)
    kw.setdefault("query_interval_s", 0.01)
    kw.setdefault("calibration_dir", NO_CALIBRATIONS)
    return LaserEngineV2(link_factory=lambda: EngineLink(fake), **kw), fake


def test_open_runs_the_startup_sequence_without_arming():
    engine, fake = _engine()
    engine.open()
    assert engine.variant == "DF"
    assert "HOST:TIMEOUT 5" in fake.sent
    assert fake.sent.count("FAULT:RESET") == 1
    assert "SHUT:SRC MCU" in fake.sent and "SHUT:SRC TTL" not in fake.sent  # the shutter is safety only
    assert all(f"LINE{n}:MOD INT" in fake.sent and f"LINE{n}:GATE 0" in fake.sent for n in range(1, 6))
    assert "ARM" not in fake.sent


def test_open_is_idempotent():
    engine, fake = _engine()
    engine.open()
    engine.open()
    assert fake.sent.count("*IDN?") == 1


def test_open_rejects_a_foreign_device():
    fake = FakeEngine()
    fake._reply = lambda cmd: "SomeOtherDevice,1.0"
    engine, _ = _engine(fake)
    with pytest.raises(RuntimeError, match="not a Cephla laser engine v2"):
        engine.open()


def test_open_waits_for_the_expanders_at_cold_power_up():
    fake = FakeEngine()
    fake.i2c_fail_count = 3
    engine, _ = _engine(fake)
    engine.open()
    assert fake.sent.count("LINE1:MOD INT") >= 2  # retried after "ERR I2C write failed"


def test_poll_once_publishes_status():
    engine, fake = _engine()
    engine.open()
    seen = []
    engine.status_updated.connect(seen.append)
    status = engine.poll_once()
    assert status is engine.get_latest_status() and seen == [status]
    assert status.channels["L1"].state == LineState.NOT_ARMED


def test_connection_lost_on_serial_error():
    engine, fake = _engine()
    engine.open()
    lost = []
    engine.connection_lost.connect(lost.append)
    fake.unplug()
    assert engine.poll_once() is None
    assert engine.poll_once() is None
    assert engine.is_connection_lost() and len(lost) == 1


def test_poll_thread_error_is_reported_as_connection_lost(monkeypatch, qtbot):
    import control.laser_engine_v2 as v2

    def unexpected_shape(*a, **kw):
        raise KeyError("lines")  # e.g. a STAT? reply of an unexpected shape

    engine, fake = _engine()
    lost = []
    engine.connection_lost.connect(lost.append)
    monkeypatch.setattr(v2, "parse_status", unexpected_shape)
    engine.start()
    qtbot.waitUntil(lambda: len(lost) > 0, timeout=2000)  # emitted on the poll thread, delivered by the Qt event loop
    qtbot.wait(50)  # nothing else queued behind it
    assert engine.is_connection_lost() and len(lost) == 1 and "poll thread error" in lost[0]
    engine.close()  # still closes cleanly
    assert "DISARM" in fake.sent


def test_close_disarms_and_is_safe_twice():
    engine, fake = _engine()
    engine.start()
    engine.close()
    engine.close()
    assert "DISARM" in fake.sent


def test_connect_reset_is_reported_with_the_engine_uptime():
    fake = FakeEngine()
    fake.faults, fake.last_event, fake.t_ms = ["OVERTEMP"], "OVERTEMP", 3 * 3600 * 1000
    engine, _ = _engine(fake)
    engine.open()
    assert fake.sent.count("FAULT:RESET") == 1
    assert engine.notices == [
        "cleared at connect: hardware fault latch; faults OVERTEMP; last event OVERTEMP, engine up 3.0 h"
    ]  # up 3 h: not the power-up latch - the operator can see that something happened before Squid connected


def test_connect_reset_refused_is_reported_and_never_retried():
    fake = FakeEngine()
    fake.reset_refusal = "OVERTEMP_N low - fix the cause first"
    engine, _ = _engine(fake)
    engine.open()
    for _ in range(3):
        engine.poll_once()
    assert fake.sent.count("FAULT:RESET") == 1
    assert engine.notices[0].startswith("NOT cleared at connect (OVERTEMP_N low")


def test_no_connect_reset_when_nothing_is_latched():
    fake = FakeEngine()
    fake.latch_ok = True
    engine, _ = _engine(fake)
    engine.open()
    assert "FAULT:RESET" not in fake.sent and engine.notices == []


def test_engine_options_are_validated():
    with pytest.raises(ValueError):
        EngineOptions(source_idle_off_min=-1)
    for bad in ("lots", float("nan"), True):
        with pytest.raises(ValueError, match="source_power_mw"):
            EngineOptions(source_power_mw=bad)
    assert EngineOptions().source_power_mw is None
    assert EngineOptions(source_power_mw=600).source_power_mw == 600.0


from control._def import ILLUMINATION_CODE


def _opened(tok_delay_polls=0):
    engine, fake = _engine(FakeEngine(tok_delay_polls=tok_delay_polls))
    engine.open()
    return engine, fake


def test_channel_keys_for_wavelengths_dedupes_and_skips_unknown():
    engine, _ = _opened()
    assert engine.channel_keys_for_wavelengths([488, 470, 532, 560, 640]) == ["L2", "L3", "L4"]


def test_line_for_wavelength_follows_the_ttl_map():
    engine, _ = _opened()
    assert [engine.line_for_wavelength(w) for w in (405, 488, 560, 638, 730)] == [
        1,
        2,
        3,
        4,
        5,
    ]  # DF on Squid's defaults
    engine.ttl_map_provider = lambda: {
        488: ILLUMINATION_CODE.ILLUMINATION_D4,  # code 13 = port D4 = engine TTL4
        640: ILLUMINATION_CODE.ILLUMINATION_D3,  # code 14 = port D3 = engine TTL3
        700: 20,  # not a D1-D5 port
    }
    assert engine.line_for_wavelength(488) == 4 and engine.line_for_wavelength(640) == 3
    assert engine.line_for_wavelength(700) is None and engine.line_for_wavelength(405) is None


def test_wavelengths_for_line_is_the_inverse_of_the_ttl_map():
    engine, _ = _opened()
    assert engine.wavelengths_for_line(1) == [405]
    assert engine.wavelengths_for_line(2) == [470, 488]  # Squid's default map: several wavelengths share a port
    assert engine.wavelengths_for_line(3) == [545, 550, 555, 560, 561]
    engine.ttl_map_provider = lambda: {
        488: ILLUMINATION_CODE.ILLUMINATION_D2,
        560: ILLUMINATION_CODE.ILLUMINATION_D3,
        640: ILLUMINATION_CODE.ILLUMINATION_D4,
        700: 20,  # not a D1-D5 port
    }
    assert [engine.wavelengths_for_line(n) for n in range(1, 6)] == [[], [488], [560], [640], []]
    assert all(engine.line_for_wavelength(w) == n for n in range(1, 6) for w in engine.wavelengths_for_line(n))


def test_on_startup_arms_and_brings_every_line_up():
    engine, fake = _opened()
    engine.on_startup()
    for _ in range(3):
        engine.poll_once()
    assert {"TEC1:OUT 1", "TEC2:OUT 1", "TEC4:OUT 1", "TEC5:OUT 1"} <= set(fake.sent) and "TEC3:OUT 1" not in fake.sent
    assert "ARM" in fake.sent
    assert all(f"LINE{n}:EN 1" in fake.sent for n in (1, 2, 4, 5))
    assert "LINE3:EN 1" not in fake.sent  # no 560 source configured in this test
    assert engine.bringup_state == "done"


def test_startup_bring_up_waits_for_cover_and_transient_refusals():
    engine, fake = _opened()
    fake.open_cover()
    fake.arm_refusals = ["WDOG_5V low (watchdog in reset)"]
    engine.on_startup()
    for _ in range(3):
        engine.poll_once()
    assert "ARM" not in fake.sent and engine.bringup_state == "running"  # cover open: every line reads PAUSED
    fake.close_cover()
    engine.poll_once()
    assert "WDOG_5V low" in engine.bringup_state  # says why it is waiting
    for _ in range(3):
        engine.poll_once()
    assert fake.sent.count("ARM") == 2  # one transient refusal, then armed
    assert engine.bringup_state == "done"


def test_startup_key_off_gives_up_then_arms_on_use():
    engine, fake = _opened()
    fake.key_on = False
    engine.on_startup()  # ARM refused: key off -> the bring-up ends
    assert fake.sent.count("ARM") == 1 and engine.bringup_state.startswith("cancelled")
    assert any("key switch off" in n for n in engine.notices)
    fake.key_on = True  # turning the key on alone brings nothing back
    for _ in range(3):
        engine.poll_once()
    assert fake.sent.count("ARM") == 1
    engine.wake_up("L1")  # the next use arms
    assert fake.sent.count("ARM") == 2 and fake.armed


def test_startup_with_an_engine_fault_is_cancelled():
    engine, fake = _opened()
    fake.faults = ["OVERTEMP"]  # latched after the connect reset
    engine.on_startup()
    assert "ARM" not in fake.sent and "OVERTEMP" in engine.bringup_state


def test_disarm_after_startup_is_not_undone_automatically():
    engine, fake = _opened(tok_delay_polls=10_000)  # TECs still warming up: the bring-up is still running
    engine.on_startup()
    engine.poll_once()
    assert fake.armed and engine.bringup_state == "running"
    fake.armed, fake.last_event = False, "HOST_LOST"  # the engine disarmed itself (host timeout, key cycle)
    for _ in range(3):
        engine.poll_once()
    assert fake.sent.count("ARM") == 1 and "HOST_LOST" in engine.bringup_state


def test_key_cycle_right_after_the_startup_arm_is_not_undone():
    engine, fake = _opened(tok_delay_polls=10_000)
    engine.on_startup()  # ARM accepted
    fake.armed = False  # key off and on again before the next poll
    for _ in range(3):
        engine.poll_once()
    assert fake.sent.count("ARM") == 1 and engine.bringup_state.startswith("cancelled")


def test_wait_until_ready_arms_enables_and_waits_for_tok_on_use():
    engine, fake = _opened(tok_delay_polls=3)
    assert engine.wait_until_ready(["L1"], timeout_s=2.0) is True
    assert "ARM" in fake.sent and "TEC1:OUT 1" in fake.sent and "LINE1:EN 1" in fake.sent
    assert engine.get_latest_status().channels["L1"].state == LineState.READY


def test_warming_up_resends_tec_on_once():
    engine, fake = _opened(tok_delay_polls=10_000)
    engine.on_startup()
    assert engine.wait_until_ready(["L1"], timeout_s=0.3) is False
    assert fake.sent.count("TEC1:OUT 1") == 2  # on_startup + one resend by the wait (the bring-up does not resend)


def test_key_off_raises_with_the_firmware_reason():
    engine, fake = _opened()
    fake.key_on = False
    with pytest.raises(LaserEngineV2Error, match="key switch off"):
        engine.wait_until_ready(["L1"], timeout_s=1.0)


def test_transient_arm_refusal_waits():
    engine, fake = _opened()
    fake.arm_refusals = ["WDOG_5V low (watchdog in reset)", "expanders not responding"]
    assert engine.wait_until_ready(["L1"], timeout_s=2.0) is True
    assert fake.sent.count("ARM") == 3


def test_unused_line_raises_at_once():
    fake = FakeEngine(tok_delay_polls=0)
    fake.lines[4]["kind"] = "NONE"  # a variant with nothing on line 5
    engine, _ = _engine(fake)
    engine.open()
    t0 = time.monotonic()
    with pytest.raises(LaserEngineV2Error, match="nothing on this line"):
        engine.wait_until_ready(["L5"], timeout_s=5.0)
    assert time.monotonic() - t0 < 1.0  # refused at once, not after the timeout


def test_blocked_line_raises():
    engine, fake = _opened()
    engine.on_startup()
    assert engine.wait_until_ready(["L1"], timeout_s=2.0)
    fake.set_tok(1, False)
    with pytest.raises(LaserEngineV2Error, match="FAULT:RESET"):
        engine.wait_until_ready(["L1"], timeout_s=1.0)


def test_no_enable_while_paused():
    engine, fake = _opened()
    engine.on_startup()
    assert engine.wait_until_ready(["L1"], timeout_s=2.0)
    fake.open_cover()
    engine.poll_once()
    n_en = fake.sent.count("LINE1:EN 1")
    assert engine.wait_until_ready(["L1"], timeout_s=0.3) is False  # paused: wait, not an error
    assert fake.sent.count("LINE1:EN 1") == n_en
    fake.close_cover()
    assert engine.wait_until_ready(["L1"], timeout_s=2.0) is True
    assert fake.sent.count("LINE1:EN 1") == n_en  # the firmware resumed it, the driver did not re-enable


def test_cover_open_before_arm_waits():
    engine, fake = _opened()
    fake.open_cover()
    engine.on_startup()
    assert engine.wait_until_ready(["L1"], timeout_s=0.3) is False
    assert "ARM" not in fake.sent
    fake.close_cover()
    assert engine.wait_until_ready(["L1"], timeout_s=2.0) is True


def test_cover_open_with_nothing_on_does_not_enable():
    engine, fake = _opened()
    engine.on_startup()  # the first bring-up step arms
    assert fake.armed
    fake.open_cover()  # armed, nothing on: the firmware does not pause, only interlock_ok = 0
    assert engine.wait_until_ready(["L1"], timeout_s=0.3) is False
    assert "LINE1:EN 1" not in fake.sent


def test_cancel_and_connection_loss():
    engine, fake = _opened(tok_delay_polls=10_000)
    engine.on_startup()
    assert engine.wait_until_ready(["L1"], timeout_s=5.0, cancel_fn=lambda: True) is False
    fake.unplug()
    assert engine.wait_until_ready(["L1"], timeout_s=5.0) is False
    assert engine.is_connection_lost()


def test_wake_up_never_raises_and_goes_all_the_way():
    engine, fake = _opened()
    fake.tok = [True] * 5  # TECs already in window
    engine.wake_up("L1")
    assert "ARM" in fake.sent and "LINE1:EN 1" in fake.sent
    fake.key_on = False
    engine.disarm()
    engine.wake_up("L1")  # refused ARM: logged, not raised


def test_sleep_disables_the_line():
    engine, fake = _opened()
    engine.on_startup()
    assert engine.wait_until_ready(["L1"], timeout_s=2.0)
    engine.put_to_sleep("L1")
    assert "LINE1:EN 0" in fake.sent


from unittest.mock import MagicMock

from control.lighting import IlluminationController, IntensityControlMode, LightSourceType, ShutterControlMode


def test_set_line_intensity_scales_to_the_line_ceiling_and_reads_back():
    engine, fake = _opened()
    engine.set_line_intensity(2, 50.0)
    assert fake.sent[-1] == "LINE2:SET %.4f" % (0.5 * 2.045)
    assert engine.get_line_intensity(2) == pytest.approx(50.0)


def test_set_intensity_waits_for_ramp():
    engine, fake = _opened()
    engine.on_startup()
    assert engine.wait_until_ready(["L1"], timeout_s=2.0)
    engine.set_line_intensity(1, 80.0)  # line ON -> RAMP -> ON at the next STAT?
    assert fake.lines[0]["st"] == "ON" and fake.lines[0]["now"] == pytest.approx(0.8 * 0.541)


def test_set_intensity_after_connection_loss_raises_and_signals():
    engine, fake = _opened()
    lost = []
    engine.connection_lost.connect(lost.append)
    fake.unplug()
    with pytest.raises(LaserEngineV2Error, match="connection lost"):
        engine.set_line_intensity(1, 10.0)
    assert len(lost) == 1


def test_calibration_csv_maps_optical_percent_to_drive_percent(tmp_path):
    (tmp_path / "488.csv").write_text("DAC Percent,Optical Power (mW)\n0,0\n50,80\n100,100\n")
    (tmp_path / "560_aom.csv").write_text("AOM Volts,Transmission\n0,0\n5,1\n")  # not a wavelength file: ignored here
    engine, fake = _engine(calibration_dir=tmp_path)
    engine.open()
    engine.set_wavelength_intensity(488, 50.0)  # 50 % of the optical maximum = 31.25 % of the current ceiling
    assert fake.sent[-1] == "LINE2:SET %.4f" % (0.3125 * 2.045)
    assert engine.get_wavelength_intensity(488) == pytest.approx(50.0)


def test_no_calibration_is_linear_and_logged_once():
    engine, fake = _opened()
    engine._log = MagicMock()
    engine.set_wavelength_intensity(405, 40.0)
    engine.set_wavelength_intensity(405, 60.0)
    assert fake.sent[-1] == "LINE1:SET %.4f" % (0.6 * 0.541)
    assert sum("no intensity calibration" in str(c) for c in engine._log.info.call_args_list) == 1


def test_intensity_follows_the_live_ttl_map():
    engine, fake = _opened()
    ports = {488: ILLUMINATION_CODE.ILLUMINATION_D2}
    engine.ttl_map_provider = lambda: dict(ports)
    engine.light_source.set_intensity(488, 50.0)
    assert "LINE2:SET %.4f" % (0.5 * 2.045) in fake.sent
    ports[488] = ILLUMINATION_CODE.ILLUMINATION_D4  # remapped in Squid's port map: the set-point moves with the TTL
    engine.light_source.set_intensity(488, 50.0)
    assert "LINE4:SET %.4f" % (0.5 * 1.196) in fake.sent


def test_wavelength_not_on_an_engine_port_is_left_to_the_controller():
    engine, fake = _opened()
    engine.ttl_map_provider = lambda: {730: 20}  # a port beyond D5
    n = len(fake.sent)
    engine.light_source.set_intensity(730, 50.0)  # no engine command; IlluminationController still selects the port
    assert len(fake.sent) == n


def test_light_source_rejects_software_shutter():
    engine, _ = _opened()
    with pytest.raises(ValueError):
        engine.light_source.set_shutter_control_mode(ShutterControlMode.Software)


def test_illumination_controller_software_intensity_ttl_shutter_and_wake():
    engine, fake = _opened()
    mcu = MagicMock()
    ctrl = IlluminationController(
        mcu,
        IntensityControlMode.Software,
        ShutterControlMode.TTL,
        LightSourceType.CephlaLaserEngineV2,
        engine.light_source,
        config_repo=MagicMock(
            get_illumination_config=lambda: None
        ),  # Squid's default TTL map, whatever YAML is on this machine
    )
    engine.ttl_map_provider = lambda: ctrl.channel_mappings_TTL  # as Microscope wires it
    assert "ARM" not in fake.sent  # building the controller only opens the engine
    fake.tok = [True] * 5
    ctrl.set_intensity(488, 25.0)
    assert "LINE2:SET %.4f" % (0.25 * 2.045) in fake.sent
    assert (
        "ARM" in fake.sent and "LINE2:EN 1" in fake.sent
    )  # using the line wakes it first (re-arm on use, no dark frames)
    mcu.set_illumination.assert_called_with(
        ILLUMINATION_CODE.ILLUMINATION_D2, 25.0
    )  # the controller selects D2 for 488
    ctrl.turn_on_illumination(488)
    mcu.turn_on_illumination.assert_called_once()


def test_the_variant_is_read_again_once_the_expanders_answer():
    from control.laser_engine_v2_sim import FakeSource

    fake = FakeEngine(tok_delay_polls=0)
    fake.i2c_fail_count = 1  # cold power-up: VAR? reads UNPROGRAMMED until the expanders answer (firmware readStraps)
    opened = []
    engine, _ = _engine(fake, source_factory=lambda: opened.append(1) or FakeSource())
    engine.open()
    try:
        assert engine.variant == "DF" and "SHUT:SRC MCU" in fake.sent and opened == [1]
        assert engine.poll_once().channels["L3"].state != LineState.NOT_CONFIGURED
    finally:
        engine.close()
