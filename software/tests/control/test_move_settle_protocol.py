"""Move-and-settle on the wire (firmware >= 1.7): constants, command encoding, the status report.

The firmware side is firmware/controller/src/move_settle_policy.h (policy, native-tested) and move_settle.cpp;
constants_protocol.h is the contract both sides read. Every SET_MOVE_SETTLE_* field is literal - there is
no "0 = keep the current value" - so the encoders below must put exactly what was asked on the wire.
"""

import re
import time
from pathlib import Path

import pytest
from crc import CrcCalculator, Crc8

import control._def as _def
import control.microcontroller
from control.microcontroller import decode_move_settle_report


def _firmware_constants() -> dict:
    path = Path(__file__).parent.parent.parent.parent / "firmware" / "controller" / "src" / "constants_protocol.h"
    if not path.exists():
        pytest.skip(f"firmware constants not found: {path}")
    return {
        name: int(value)
        for name, value in re.findall(r"static\s+const\s+int\s+(\w+)\s*=\s*(-?\d+)\s*;", path.read_text())
    }


def _micro():
    return control.microcontroller.Microcontroller(
        serial_device=control.microcontroller.get_microcontroller_serial_device(simulated=True)
    )


def test_move_settle_constants_match_the_firmware():
    fw = _firmware_constants()
    expected = {
        "SET_LOOP_STRATEGY": _def.CMD_SET.SET_LOOP_STRATEGY,
        "SET_MOVE_SETTLE_MEASURE": _def.CMD_SET.SET_MOVE_SETTLE_MEASURE,
        "SET_MOVE_SETTLE_FEEDFORWARD": _def.CMD_SET.SET_MOVE_SETTLE_FEEDFORWARD,
        "SET_MOVE_SETTLE_SHAPER": _def.CMD_SET.SET_MOVE_SETTLE_SHAPER,
        "SET_MOVE_SETTLE_ACCEPT": _def.CMD_SET.SET_MOVE_SETTLE_ACCEPT,
        "SET_MOVE_SETTLE_MODEL": _def.CMD_SET.SET_MOVE_SETTLE_MODEL,
        "SET_MOVE_SETTLE_SCALE": _def.CMD_SET.SET_MOVE_SETTLE_SCALE,
        "SET_MOVE_SETTLE_FINISH": _def.CMD_SET.SET_MOVE_SETTLE_FINISH,
        "MOVE_Z_FROM_MEASURED": _def.MOVE_Z_FROM_MEASURED,
        "MOVE_Z_RETRY_LAST": _def.MOVE_Z_RETRY_LAST,
        "LOOP_STRATEGY_CHIP_PID": _def.LOOP_STRATEGY.CHIP_PID,
        "LOOP_STRATEGY_MOVE_SETTLE": _def.LOOP_STRATEGY.MOVE_SETTLE,
        "MOVE_SETTLE_APPROACH_MOVE_DIRECTION": _def.MOVE_SETTLE_APPROACH.MOVE_DIRECTION,
        "MOVE_SETTLE_APPROACH_POSITIVE": _def.MOVE_SETTLE_APPROACH.POSITIVE,
        "MOVE_SETTLE_APPROACH_NEGATIVE": _def.MOVE_SETTLE_APPROACH.NEGATIVE,
        "ENCODER_REPORT_MOVE_SETTLE": _def.ENCODER_REPORTING.MOVE_SETTLE,
        "MOVE_SETTLE_REPORT_TRIMS_MASK": _def.MOVE_SETTLE_REPORT.TRIMS_MASK,
        "MOVE_SETTLE_REPORT_BACKED_OFF": _def.MOVE_SETTLE_REPORT.BACKED_OFF,
        "MOVE_SETTLE_REPORT_MISSED": _def.MOVE_SETTLE_REPORT.MISSED,
        "MOVE_SETTLE_REPORT_LIMITED": _def.MOVE_SETTLE_REPORT.LIMITED,
        "MOVE_SETTLE_REPORT_BUSY": _def.MOVE_SETTLE_REPORT.BUSY,
    }
    assert {name: fw.get(name) for name in expected} == expected


def test_new_command_codes_do_not_collide_with_any_other_command():
    codes = [v for k, v in vars(_def.CMD_SET).items() if k.isupper() and isinstance(v, int)]
    assert len(codes) == len(set(codes))


def test_move_settle_commands_put_literal_values_on_the_wire():
    micro = _micro()
    try:
        micro.set_loop_strategy(_def.AXIS.Z, _def.LOOP_STRATEGY.MOVE_SETTLE)
        assert list(micro.last_command[1:4]) == [_def.CMD_SET.SET_LOOP_STRATEGY, _def.AXIS.Z, 1]

        micro.set_move_settle_measure(
            _def.AXIS.Z,
            window_ms=9.0,
            trim_gain=0.75,
            max_trims=6,
            max_reapproaches=2,
            approach=_def.MOVE_SETTLE_APPROACH.POSITIVE,
            wait_ms=5.0,
        )
        assert micro.last_command[1] == _def.CMD_SET.SET_MOVE_SETTLE_MEASURE
        assert micro.last_command[3] == 10  # settle, 0.5 ms units
        assert micro.last_command[4] == 18  # window, 0.5 ms units
        assert micro.last_command[5] == 12  # 1/16 units
        assert micro.last_command[6] == (2 << 6) | (1 << 4) | 6  # re-approaches | approach | trims

        micro.set_move_settle_feedforward(_def.AXIS.Z, lost_motion_um=0.8, bias_um=0.1, backoff_um=3.0)
        assert micro.last_command[1] == _def.CMD_SET.SET_MOVE_SETTLE_FEEDFORWARD
        assert (micro.last_command[3] << 8) + micro.last_command[4] == 80  # 0.01 um
        assert micro.last_command[5] == 10  # 0.01 um
        assert micro.last_command[6] == 30  # 0.1 um

        micro.set_move_settle_shaper(_def.AXIS.Z, half_period_ms=4.42, first_share=0.565, max_move_um=10)
        assert micro.last_command[1] == _def.CMD_SET.SET_MOVE_SETTLE_SHAPER
        assert (micro.last_command[3] << 8) + micro.last_command[4] == 442  # 10 us
        assert micro.last_command[5] == 145  # 0.565 x 256
        assert micro.last_command[6] == 10

        micro.set_move_settle_accept(_def.AXIS.Z, overshoot_tolerance_um=0.28, quiet_pp_um=0.2, quiet_windows=3)
        assert micro.last_command[1] == _def.CMD_SET.SET_MOVE_SETTLE_ACCEPT
        assert (micro.last_command[3] << 8) + micro.last_command[4] == 28
        assert micro.last_command[5] == 20
        assert micro.last_command[6] == 3

        micro.set_move_settle_model(_def.AXIS.Z, carry_um=0.47, full_push_um=0.94, learn_gain=0.25, bias_sigma=0.5)
        assert micro.last_command[1] == _def.CMD_SET.SET_MOVE_SETTLE_MODEL
        assert list(micro.last_command[3:7]) == [47, 94, 4, 8]  # 0.01 um, 0.01 um, 1/16, 1/16 sigma

        micro.set_move_settle_scale(_def.AXIS.Z, scale_ppm=-1100)
        assert micro.last_command[1] == _def.CMD_SET.SET_MOVE_SETTLE_SCALE
        assert int.from_bytes(bytes(micro.last_command[3:5]), "big", signed=True) == -1100
        micro.set_move_settle_scale(_def.AXIS.Z, scale_ppm=250)
        assert list(micro.last_command[3:5]) == [0, 250]

        # 50 um from 1 mm at 42.667 usteps/um (64 usteps/FS on a 0.3 mm screw): usteps, and 16-ustep units
        micro.set_move_settle_finish(_def.AXIS.Z, finish_um=50.0, from_um=1000.0, usteps_per_um=42.667)
        assert micro.last_command[1] == _def.CMD_SET.SET_MOVE_SETTLE_FINISH
        assert micro.last_command[2] == _def.AXIS.Z
        assert (micro.last_command[3] << 8) + micro.last_command[4] == 2133
        assert (micro.last_command[5] << 8) + micro.last_command[6] == 2667  # 42667 / 16
        micro.set_move_settle_finish(_def.AXIS.Z, finish_um=0.0, from_um=0.0, usteps_per_um=42.667)  # off is a value
        assert list(micro.last_command[3:7]) == [0, 0, 0, 0]

        micro.move_z_usteps_from_measured(-43)
        assert micro.last_command[1] == _def.CMD_SET.MOVE_Z
        assert int.from_bytes(bytes(micro.last_command[2:6]), "big", signed=True) == -43
        assert micro.last_command[6] == _def.MOVE_Z_FROM_MEASURED
        micro.wait_till_operation_is_completed()
        micro.move_z_usteps_retry_last(17)  # the retry of a missed one: the same command, another flag
        assert micro.last_command[1] == _def.CMD_SET.MOVE_Z
        assert int.from_bytes(bytes(micro.last_command[2:6]), "big", signed=True) == 17
        assert micro.last_command[6] == _def.MOVE_Z_RETRY_LAST
        micro.wait_till_operation_is_completed()
        micro.move_z_usteps(-43)
        assert micro.last_command[6] == 0  # the ordinary relative move says nothing in that byte
        micro.wait_till_operation_is_completed()

        # zeros are values, not "keep": they must be encodable
        micro.set_move_settle_feedforward(_def.AXIS.Z, lost_motion_um=0.0, bias_um=0.0, backoff_um=0.0)
        assert list(micro.last_command[3:7]) == [0, 0, 0, 0]
    finally:
        micro.close()


def test_move_settle_commands_refuse_what_the_wire_cannot_carry():
    micro = _micro()
    try:
        with pytest.raises(ValueError):
            micro.set_loop_strategy(_def.AXIS.Z, 7)
        with pytest.raises(ValueError):
            micro.set_move_settle_measure(_def.AXIS.Z, window_ms=0.0)  # a window of nothing averages nothing
        with pytest.raises(ValueError):
            micro.set_move_settle_measure(_def.AXIS.Z, window_ms=200.0)
        with pytest.raises(ValueError):
            micro.set_move_settle_measure(_def.AXIS.Z, trim_gain=0.0)
        with pytest.raises(ValueError):
            micro.set_move_settle_measure(_def.AXIS.Z, max_reapproaches=4)  # two bits on the wire
        with pytest.raises(ValueError):
            micro.set_move_settle_measure(_def.AXIS.Z, max_trims=16)  # four bits on the wire
        with pytest.raises(ValueError):
            micro.set_move_settle_measure(_def.AXIS.Z, wait_ms=200.0)
        micro.set_move_settle_measure(_def.AXIS.Z, wait_ms=0.0)  # no settle is a value, not an error
        assert micro.last_command[3] == 0
        with pytest.raises(ValueError):
            micro.set_move_settle_feedforward(_def.AXIS.Z, bias_um=3.0)
        with pytest.raises(ValueError):
            micro.set_move_settle_shaper(_def.AXIS.Z, first_share=1.0)
        with pytest.raises(ValueError):
            micro.set_move_settle_model(_def.AXIS.Z, full_push_um=0.0)  # a carry that is full from nothing is no model
        with pytest.raises(ValueError):
            micro.set_move_settle_model(_def.AXIS.Z, learn_gain=1.5)
        with pytest.raises(ValueError):
            micro.set_move_settle_model(_def.AXIS.Z, bias_sigma=2.5)
        with pytest.raises(ValueError):
            micro.set_move_settle_scale(_def.AXIS.Z, scale_ppm=6000)
        with pytest.raises(ValueError):  # a finishing leg not shorter than the length it is used from
            micro.set_move_settle_finish(_def.AXIS.Z, finish_um=1000.0, from_um=50.0, usteps_per_um=42.667)
        with pytest.raises(ValueError):  # 2 mm at 42.667 usteps/um does not fit a uint16
            micro.set_move_settle_finish(_def.AXIS.Z, finish_um=2000.0, from_um=5000.0, usteps_per_um=42.667)
        micro.set_move_settle_finish(_def.AXIS.Z, finish_um=50.0, from_um=0.0, usteps_per_um=42.667)  # from 0: a value
        assert list(micro.last_command[5:7]) == [0, 0]
        micro.set_move_settle_model(_def.AXIS.Z, bias_sigma=0.0)  # no learned bias is a value
        assert micro.last_command[6] == 0
    finally:
        micro.close()


def test_move_settle_report_decoding():
    r = decode_move_settle_report(0xFD, 0b1101_0010)  # -3 usteps; busy, limited, backed off, 2 trims
    assert r == {
        "first_landing_usteps": -3,
        "trims": 2,
        "backed_off": True,
        "missed": False,
        "limited": True,
        "busy": True,
    }
    r = decode_move_settle_report(5, 0b0010_0110)  # a missed move: 6 trims spent, never backed off
    assert r == {
        "first_landing_usteps": 5,
        "trims": 6,
        "backed_off": False,
        "missed": True,
        "limited": False,
        "busy": False,
    }
    r = decode_move_settle_report(5, 0)
    assert r["trims"] == 0 and not (r["backed_off"] or r["missed"] or r["limited"] or r["busy"])


def _feed(micro, msg):
    deadline = time.time() + 5.0
    while micro._serial.bytes_available() and time.time() < deadline:
        time.sleep(0.005)
    with micro._received_packet_cv:
        with micro._serial._update_lock:
            micro._serial.response_buffer.extend(bytes(msg))
        assert micro._received_packet_cv.wait(timeout=5.0), "read thread never parsed the injected packet"


def test_status_bytes_20_21_are_read_as_the_report_only_in_move_settle_reporting():
    """The two layouts cannot be told apart on the wire: the mode this host asked for decides. In the
    move-and-settle layout the deviation still has to be right - it comes from ENC_POS and the counter."""
    crc = CrcCalculator(Crc8.CCITT, table_based=True)

    def packet(z_pos, enc_pos, b20, b21):
        msg = bytearray(24)
        msg[1] = _def.CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS
        msg[10:14] = int(z_pos).to_bytes(4, "big", signed=True)
        msg[14:18] = int(enc_pos).to_bytes(4, "big", signed=True)
        msg[19] = (
            (1 << _def.ENC_FLAG.REPORTING)
            | (1 << _def.ENC_FLAG.PID_ENABLED)
            | (_def.AXIS.Z << _def.ENC_FLAG.AXIS_SHIFT)
        )
        msg[20] = b20
        msg[21] = b21
        msg[22] = (1 << 4) | 7
        msg[23] = crc.calculate_checksum(msg[:23])
        return msg

    micro = _micro()
    try:
        micro.set_encoder_reporting(_def.AXIS.Z, _def.ENCODER_REPORTING.MOVE_SETTLE)
        micro.wait_till_operation_is_completed()
        _feed(micro, packet(z_pos=26000, enc_pos=26001, b20=2, b21=1))
        assert micro.move_settle_report == {
            "first_landing_usteps": 2,
            "trims": 1,
            "backed_off": False,
            "missed": False,
            "limited": False,
            "busy": False,
        }
        assert micro.encoder_dev32 == 1
        assert micro.encoder_deviation == 1  # not 0x0201

        micro.set_encoder_reporting(_def.AXIS.Z, _def.ENCODER_REPORTING.ENC_IN_THETA)
        micro.wait_till_operation_is_completed()
        _feed(micro, packet(z_pos=26000, enc_pos=25997, b20=0xFF, b21=0xFD))
        assert micro.encoder_deviation == -3  # the firmware's clipped int16, as before
        assert micro.move_settle_report["trims"] == 1  # untouched by a packet in the other layout
    finally:
        micro.close()
