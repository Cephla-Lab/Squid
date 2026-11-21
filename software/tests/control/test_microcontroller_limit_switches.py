"""[LIMIT_SWITCHES] reaches the controller on every connect (SET_LIMIT_SWITCH_CONFIG, opcode 64), or the
Microcontroller refuses to start when the firmware cannot apply it; Z limit state comes back in status byte 18."""

import logging
import threading

import pytest

import control._def
import control.microcontroller
from control._def import AXIS, CMD_EXECUTION_STATUS, CMD_SET, LIMIT_SWITCH_CONFIG_MIN_FIRMWARE, LIMIT_SWITCHES
from control.firmware_sim_serial import FirmwareSimSerial
from control.microcontroller import CommandAborted, LimitSwitchConfigError, Microcontroller, SimSerial, decode_z_limits

# The version just before the first one that ships SET_LIMIT_SWITCH_CONFIG.
OLD_FIRMWARE = (LIMIT_SWITCH_CONFIG_MIN_FIRMWARE[0], LIMIT_SWITCH_CONFIG_MIN_FIRMWARE[1] - 1)


def _sim(version):
    if version == "old":
        version = OLD_FIRMWARE
    return FirmwareSimSerial(strict=True, firmware_version=version)


def _record_commands(sim):
    """Every command that reaches the sim, recorded before the sim handles it."""
    sent = []
    write = sim.write

    def recording_write(data, reconnect_tries=0):
        sent.append(bytes(data))
        return write(data, reconnect_tries)

    sim.write = recording_write
    return sent


def _z_both_active_high(monkeypatch):
    monkeypatch.setattr(LIMIT_SWITCHES, "Z_LEFT_ACTIVE_HIGH", True)
    monkeypatch.setattr(LIMIT_SWITCHES, "Z_RIGHT_ACTIVE_HIGH", True)


def _refuse_z_current_above(sim, limit_mA):
    """Answer CMD_EXECUTION_ERROR to a Z CONFIGURE_STEPPER_DRIVER above limit_mA, as firmware that cannot drive
    that current does."""
    process = sim._process_command

    def refusing_process(cmd):
        if cmd[1] == CMD_SET.CONFIGURE_STEPPER_DRIVER and cmd[2] == AXIS.Z and (cmd[4] << 8 | cmd[5]) > limit_mA:
            sim.response_buffer.extend(sim._build_response(cmd[0], CMD_EXECUTION_STATUS.CMD_EXECUTION_ERROR))
            sim._update_internal_state()
            return
        process(cmd)

    sim._process_command = refusing_process


def _stuck_in_progress_until_reset(sim):
    """Answer IN_PROGRESS to everything until RESET, as firmware still busy with a move from a previous session
    does (callback_reset clears it). Returns a dict whose "stuck" entry RESET sets to False."""
    state = {"stuck": True}
    build = sim._build_response
    process = sim._process_command

    def stuck_build(cmd_id, status):
        return build(cmd_id, CMD_EXECUTION_STATUS.IN_PROGRESS if state["stuck"] else status)

    def process_and_reset(cmd):
        if cmd[1] == CMD_SET.RESET:
            state["stuck"] = False
        process(cmd)

    sim._build_response = stuck_build
    sim._process_command = process_and_reset
    return state


def test_set_limit_switch_config_encodes_opcode_axis_and_nibble(monkeypatch):
    mcu = Microcontroller(serial_device=_sim(LIMIT_SWITCH_CONFIG_MIN_FIRMWARE), reset_and_initialize=False)
    sent = []
    monkeypatch.setattr(mcu, "send_command", sent.append)

    mcu.set_limit_switch_config(AXIS.Z, 0x0F)

    assert len(sent) == 1
    assert sent[0][1:4] == bytes([64, AXIS.Z, 0x0F])
    mcu.close()


@pytest.mark.parametrize("reset_and_initialize", [True, False])
def test_config_is_sent_on_both_startup_paths(reset_and_initialize, monkeypatch):
    _z_both_active_high(monkeypatch)
    sim = _sim(LIMIT_SWITCH_CONFIG_MIN_FIRMWARE)
    sent = _record_commands(sim)

    mcu = Microcontroller(serial_device=sim, reset_and_initialize=reset_and_initialize)

    assert sim.limit_switch_config == {AXIS.X: 0x03, AXIS.Y: 0x03, AXIS.Z: 0x0F}
    # All three axes, defaults included, after any reset/initialize/actuator setup.
    assert [cmd[1:4] for cmd in sent[-3:]] == [
        bytes([CMD_SET.SET_LIMIT_SWITCH_CONFIG, AXIS.X, 0x03]),
        bytes([CMD_SET.SET_LIMIT_SWITCH_CONFIG, AXIS.Y, 0x03]),
        bytes([CMD_SET.SET_LIMIT_SWITCH_CONFIG, AXIS.Z, 0x0F]),
    ]
    assert (CMD_SET.RESET in [cmd[1] for cmd in sent]) == reset_and_initialize
    mcu.close()


@pytest.mark.parametrize("version", [(0, 0), "old"])
@pytest.mark.parametrize("reset_and_initialize", [True, False])
def test_incompatible_config_refuses_before_any_command(version, reset_and_initialize, monkeypatch):
    _z_both_active_high(monkeypatch)
    sim = _sim(version)
    sent = _record_commands(sim)

    with pytest.raises(LimitSwitchConfigError) as e:
        Microcontroller(serial_device=sim, reset_and_initialize=reset_and_initialize)

    message = str(e.value)
    assert "LIMIT_SWITCHES" in message and "%d.%d" % LIMIT_SWITCH_CONFIG_MIN_FIRMWARE in message
    # Every non-default setting is named, and only those.
    assert "z_left_active_high" in message and "z_right_active_high" in message
    for default_key in ("x_left", "x_right", "y_left", "y_right", "z_left_enabled", "z_right_enabled"):
        assert default_key not in message
    opcodes = [cmd[1] for cmd in sent]
    # Only _detect_firmware_version's harmless command reached the controller: no reset, initialize, actuator
    # setup, motion or limit-switch command before the compatibility check passed.
    assert set(opcodes) == {CMD_SET.TURN_OFF_ALL_PORTS}, opcodes


def test_incompatible_config_takes_precedence_over_an_unsupported_current(monkeypatch):
    monkeypatch.setattr(control._def, "Z_MOTOR_RMS_CURRENT_mA", 1000)  # configure_actuators reads it there

    # The modelled old firmware does refuse that current: with default limit switches, startup ends there.
    sim = _sim("old")
    _refuse_z_current_above(sim, 692)
    with pytest.raises(CommandAborted):
        Microcontroller(serial_device=sim, reset_and_initialize=True)

    _z_both_active_high(monkeypatch)
    sim = _sim("old")
    _refuse_z_current_above(sim, 692)
    with pytest.raises(LimitSwitchConfigError):
        Microcontroller(serial_device=sim, reset_and_initialize=True)


def test_failed_constructor_releases_the_link(monkeypatch):
    _z_both_active_high(monkeypatch)
    sim = _sim("old")
    threads_before = set(threading.enumerate())

    with pytest.raises(LimitSwitchConfigError):
        Microcontroller(serial_device=sim, reset_and_initialize=False)

    assert not sim.is_open()
    assert [t for t in threading.enumerate() if t not in threads_before] == []


@pytest.mark.parametrize("reset_and_initialize", [True, False])
def test_old_firmware_defaults_start_normally(reset_and_initialize):
    sim = _sim("old")
    sent = _record_commands(sim)

    mcu = Microcontroller(serial_device=sim, reset_and_initialize=reset_and_initialize)

    opcodes = [cmd[1] for cmd in sent]
    assert mcu.firmware_version == OLD_FIRMWARE
    assert CMD_SET.SET_LIMIT_SWITCH_CONFIG not in opcodes
    if reset_and_initialize:
        assert {CMD_SET.RESET, CMD_SET.INITIALIZE, CMD_SET.CONFIGURE_STEPPER_DRIVER} <= set(opcodes)
    else:
        assert opcodes == [CMD_SET.TURN_OFF_ALL_PORTS]
    mcu.close()


# Invalid [LIMIT_SWITCHES] content: (ini text, the key the error must name, what it must say about it).
INVALID_LIMIT_SWITCHES = {
    "unknown key": ("[LIMIT_SWITCHES]\nz_left_activ_high = True\n", "z_left_activ_high", "unknown key"),
    "non-bool value": ("[LIMIT_SWITCHES]\nz_right_active_high = no\n", "z_right_active_high", "True or False"),
}


@pytest.mark.parametrize("device", ["supported", "old", "SimSerial"])
@pytest.mark.parametrize("problem", sorted(INVALID_LIMIT_SWITCHES))
@pytest.mark.parametrize("reset_and_initialize", [True, False])
def test_invalid_config_refuses_on_every_serial_device(device, problem, reset_and_initialize, load_limit_switches):
    text, key, says = INVALID_LIMIT_SWITCHES[problem]
    load_limit_switches(text)
    if device == "SimSerial":
        sim = SimSerial()
    else:
        sim = _sim(LIMIT_SWITCH_CONFIG_MIN_FIRMWARE if device == "supported" else "old")
    sent = _record_commands(sim)

    with pytest.raises(LimitSwitchConfigError) as e:
        Microcontroller(serial_device=sim, reset_and_initialize=reset_and_initialize)

    message = str(e.value)
    assert "LIMIT_SWITCHES" in message and key in message and says in message, message
    # The content check comes first: not the firmware-version refusal, even on old firmware.
    assert "%d.%d" % LIMIT_SWITCH_CONFIG_MIN_FIRMWARE not in message, message
    # Nothing beyond version detection reached the controller.
    assert [cmd[1] for cmd in sent] == [CMD_SET.TURN_OFF_ALL_PORTS]
    assert not sim.is_open()


def test_ini_z_both_active_high_reaches_the_controller(load_limit_switches):
    load_limit_switches("[LIMIT_SWITCHES]\nz_left_active_high = True\nz_right_active_high = True\n")
    sim = _sim(LIMIT_SWITCH_CONFIG_MIN_FIRMWARE)

    mcu = Microcontroller(serial_device=sim, reset_and_initialize=False)

    assert sim.limit_switch_config == {AXIS.X: 0x03, AXIS.Y: 0x03, AXIS.Z: 0x0F}
    mcu.close()


def test_controller_stuck_in_progress_recovers_through_reset(monkeypatch):
    # Detection must not wait for its command to complete: this controller answers IN_PROGRESS until RESET.
    _z_both_active_high(monkeypatch)
    sim = _sim(LIMIT_SWITCH_CONFIG_MIN_FIRMWARE)
    state = _stuck_in_progress_until_reset(sim)
    sent = _record_commands(sim)

    mcu = Microcontroller(serial_device=sim, reset_and_initialize=True)

    assert mcu.firmware_version == LIMIT_SWITCH_CONFIG_MIN_FIRMWARE
    assert [cmd[1] for cmd in sent[:2]] == [CMD_SET.TURN_OFF_ALL_PORTS, CMD_SET.RESET]
    assert not state["stuck"]
    assert not mcu.is_busy()
    # The check passed on the detected version and the config went out after the reset.
    assert sim.limit_switch_config[AXIS.Z] == 0x0F
    mcu.close()


def test_detection_never_uses_a_packet_queued_before_its_command(monkeypatch):
    # A previous session's last packet (cmd id 7, completed, older firmware) is fully queued when detection writes
    # its command (id 1); the reader does not touch it until the write, so it begins reading it afterwards. Only the
    # echoed command id tells it apart. The probe's own answer is held back until detection returns (or 1 s).
    _z_both_active_high(monkeypatch)
    sim = _sim(LIMIT_SWITCH_CONFIG_MIN_FIRMWARE)
    stale = _sim("old")._build_response(7, CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS)
    sim.response_buffer.extend(stale)
    sim._update_internal_state()

    written = threading.Event()
    detection_returned = threading.Event()
    bytes_read = []
    bytes_available, write, read = sim.bytes_available, sim.write, sim.read

    def bytes_available_after_the_write():
        return bytes_available() if written.is_set() else 0

    def write_and_signal(data, reconnect_tries=0):
        n = write(data, reconnect_tries)
        written.set()
        return n

    def read_holding_the_answer(count=1, reconnect_tries=0):
        bytes_read.append(count)
        if len(bytes_read) == len(stale) + 1:  # the probe's answer waits for detection
            detection_returned.wait(1)
        return read(count, reconnect_tries)

    sim.bytes_available = bytes_available_after_the_write
    sim.write = write_and_signal
    sim.read = read_holding_the_answer

    detect = Microcontroller._detect_firmware_version
    detected = []

    def detect_and_signal(self):
        detect(self)
        detected.append(self.firmware_version)
        detection_returned.set()

    monkeypatch.setattr(Microcontroller, "_detect_firmware_version", detect_and_signal)

    mcu = Microcontroller(serial_device=sim, reset_and_initialize=False)

    assert detected == [LIMIT_SWITCH_CONFIG_MIN_FIRMWARE]
    assert sim.limit_switch_config[AXIS.Z] == 0x0F
    mcu.close()


@pytest.mark.parametrize("reset_and_initialize", [True, False])
def test_simulated_controller_skips_the_firmware_check(reset_and_initialize, monkeypatch, caplog):
    # SimSerial reports firmware 1.3 and models no limit switches; --simulation must start with any ini.
    _z_both_active_high(monkeypatch)
    sim = SimSerial()
    sent = _record_commands(sim)

    with caplog.at_level(logging.INFO):
        mcu = Microcontroller(serial_device=sim, reset_and_initialize=reset_and_initialize)

    assert CMD_SET.SET_LIMIT_SWITCH_CONFIG not in [cmd[1] for cmd in sent]
    skip_lines = [r for r in caplog.records if r.levelno == logging.INFO and "LIMIT_SWITCHES" in r.getMessage()]
    assert len(skip_lines) == 1
    mcu.close()


def test_z_limit_bits_reach_the_microcontroller_through_the_reader():
    sim = _sim(LIMIT_SWITCH_CONFIG_MIN_FIRMWARE)
    mcu = Microcontroller(serial_device=sim, reset_and_initialize=False)
    assert (mcu.z_limit_left_triggered, mcu.z_limit_right_triggered) == (False, False)

    sim.z_limit_right = True
    mcu.turn_off_all_ports()
    mcu.wait_till_operation_is_completed()

    assert (mcu.z_limit_left_triggered, mcu.z_limit_right_triggered) == (False, True)
    mcu.close()


def test_decode_z_limits():
    assert decode_z_limits(0x00) == (False, False)
    assert decode_z_limits(0x04) == (True, False)
    assert decode_z_limits(0x08) == (False, True)
    assert decode_z_limits(0x0C) == (True, True)
    assert decode_z_limits(0xF3) == (False, False)  # other bits ignored
