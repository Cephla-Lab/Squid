"""How the Microcontroller finds and keeps its place in the controller's status stream.

The controller sends a 24-byte status packet every 10 ms, with nothing marking where one starts. These tests cover
the connect handshake (the first packet the host uses is the answer to its own first command, wherever in the stream
it starts) and the framing after it (on firmware that sends checksums, a window is a packet only if its checksum and
firmware-version byte are right).
"""

import logging
import re
import threading

import pytest

from control._def import CMD_EXECUTION_STATUS, CMD_SET, LIMIT_SWITCH_CONFIG_MIN_FIRMWARE
from control.firmware_sim_serial import FirmwareSimSerial
from control.microcontroller import Microcontroller

FIRMWARE = LIMIT_SWITCH_CONFIG_MIN_FIRMWARE
# The version just before, as in test_microcontroller_limit_switches.py: it has no limit-switch commands.
OLDER_FIRMWARE = (FIRMWARE[0], FIRMWARE[1] - 1)
PACKET_LENGTH = 24
BYTE_BUTTONS = 18


def _sim(version=FIRMWARE):
    return FirmwareSimSerial(strict=True, firmware_version=version)


def _record_commands(sim):
    """The opcode of every command that reaches the sim, recorded before the sim handles it."""
    sent = []
    write = sim.write

    def recording_write(data, reconnect_tries=0):
        sent.append(data[1])
        return write(data, reconnect_tries)

    sim.write = recording_write
    return sent


def _queue(sim, data):
    """Bytes the controller sent before the host reads anything, or between two commands."""
    with sim._update_lock:
        sim.response_buffer.extend(data)
        sim._update_internal_state()


def _idle_packet(sim, cmd_id=0):
    return sim._build_response(cmd_id, CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS)


def _command_round_trip(mcu):
    """A command and its completion: only works while the host reads the stream at the right place."""
    mcu.turn_off_all_ports()
    mcu.wait_till_operation_is_completed(timeout_limit_s=2)
    assert not mcu.is_busy()


@pytest.mark.parametrize("offset", range(1, PACKET_LENGTH))
def test_connects_when_the_waiting_bytes_start_mid_packet(offset):
    # The bytes waiting when the port opens start `offset` bytes into a packet. An idle controller's packet is
    # mostly zeros, and its CRC-8 (start value 0) cannot tell such a packet from a shifted copy of itself, so
    # checksums alone do not find the packet boundary. Starting 4 or 5 bytes in puts the firmware-version or CRC byte
    # where the buttons byte belongs, which read as a joystick press nobody made.
    sim = _sim()
    packet = _idle_packet(sim)
    _queue(sim, packet[offset:] + packet * 2)
    sent = _record_commands(sim)

    mcu = Microcontroller(serial_device=sim, reset_and_initialize=False)

    assert mcu.firmware_version == FIRMWARE
    assert CMD_SET.ACK_JOYSTICK_BUTTON_PRESSED not in sent
    assert not mcu.joystick_button_pressed
    _command_round_trip(mcu)
    assert mcu.get_pos() == (0, 0, 0, 0)
    mcu.close()


def test_a_shifted_window_that_starts_with_the_command_id_is_not_the_answer():
    # The waiting bytes start mid-packet, at a stage position byte of 0x01 - the id of the host's first command.
    # The first 24 bytes read then look like an answer (first byte 0x01, last byte zero), and they come before
    # the real one, which has no whole packet before it to vouch for it. A second command settles it: the real
    # answers echo 1, then 2; the shifted window stays at 0x01.
    sim = _sim()
    sim.y = 70000  # 0x00011170: packet byte 7 is 0x01
    packet = _idle_packet(sim)
    assert packet[7] == 1
    _queue(sim, packet[7:])
    sent = _record_commands(sim)

    mcu = Microcontroller(serial_device=sim, reset_and_initialize=False)

    assert mcu.firmware_version == FIRMWARE
    assert sent[:2] == [CMD_SET.TURN_OFF_ALL_PORTS, CMD_SET.TURN_OFF_ALL_PORTS]
    assert CMD_SET.ACK_JOYSTICK_BUTTON_PRESSED not in sent
    _command_round_trip(mcu)
    assert mcu.get_pos() == (0, 70000, 0, 0)
    mcu.close()


def test_a_clean_start_needs_one_command():
    # Packets of a previous session wait in the port, each where the last one ended: the answer is taken at once.
    sim = _sim(OLDER_FIRMWARE)
    _queue(sim, _idle_packet(sim, cmd_id=37) * 3)
    sent = _record_commands(sim)

    mcu = Microcontroller(serial_device=sim, reset_and_initialize=False)

    assert mcu.firmware_version == OLDER_FIRMWARE
    assert sent == [CMD_SET.TURN_OFF_ALL_PORTS]
    mcu.close()


def test_connects_after_text_on_the_status_port():
    # Text has reached this port before (a library's logging, a bench build's report); it shifts every packet
    # after it.
    sim = _sim()
    _queue(sim, b"[TMC] X: TMC2660 probe_raw=0x40000000\r\n" + _idle_packet(sim) * 2)
    sent = _record_commands(sim)

    mcu = Microcontroller(serial_device=sim, reset_and_initialize=False)

    assert mcu.firmware_version == FIRMWARE
    assert CMD_SET.ACK_JOYSTICK_BUTTON_PRESSED not in sent
    _command_round_trip(mcu)
    mcu.close()


def test_a_joystick_press_held_at_connect_is_acknowledged_once_after_the_handshake():
    # The button is reserved: a press the controller really reports is still acknowledged, and it cannot disturb
    # the handshake, because nothing is acted on before the handshake's answer.
    sim = _sim()
    sim.joystick_button = True
    sent = _record_commands(sim)

    mcu = Microcontroller(serial_device=sim, reset_and_initialize=False)

    assert mcu.firmware_version == FIRMWARE
    assert sent[:2] == [CMD_SET.TURN_OFF_ALL_PORTS, CMD_SET.ACK_JOYSTICK_BUTTON_PRESSED]
    assert sent.count(CMD_SET.ACK_JOYSTICK_BUTTON_PRESSED) == 1
    assert mcu.joystick_button_pressed
    mcu.close()


def test_a_packet_queued_before_the_handshake_is_neither_acted_on_nor_used():
    # A previous session's packet (older firmware, joystick pressed) is queued before the host's first command
    # (id 1) and happens to echo id 2. It is not the answer, so its press is not acknowledged - an acknowledgement
    # would be command 2, which would make this very packet look like an answer - and its firmware version is not
    # taken. The real answer is held back until detection returns (or 1 s).
    sim = _sim()
    stale_sim = _sim(OLDER_FIRMWARE)
    stale_sim.joystick_button = True
    stale = stale_sim._build_response(2, CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS)
    _queue(sim, stale)
    sent = _record_commands(sim)

    written = threading.Event()
    detection_returned = threading.Event()
    reads = []
    bytes_available, write, read = sim.bytes_available, sim.write, sim.read

    def bytes_available_after_the_write():
        return bytes_available() if written.is_set() else 0

    def write_and_signal(data, reconnect_tries=0):
        n = write(data, reconnect_tries)
        written.set()
        return n

    def read_holding_the_answer(count=1, reconnect_tries=0):
        reads.append(count)
        if len(reads) == len(stale) + 1:
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

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Microcontroller, "_detect_firmware_version", detect_and_signal)
        mcu = Microcontroller(serial_device=sim, reset_and_initialize=False)

    assert detected == [FIRMWARE]
    assert CMD_SET.ACK_JOYSTICK_BUTTON_PRESSED not in sent
    mcu.close()


def test_the_handshake_accepts_the_echo_of_a_command_sent_after_its_first():
    # Another command follows the first before the controller answers, and the controller handles both in one status
    # cycle: no packet echoes the first command's id, only the second's. That packet is still an answer.
    sim = _sim()
    build = sim._build_response
    answers = []

    def one_answer_for_both(cmd_id, status):
        answers.append(cmd_id)
        return b"" if len(answers) == 1 else build(cmd_id, status)

    sim._build_response = one_answer_for_both
    probe = Microcontroller.turn_off_all_ports
    probes = []

    def probe_then_another_command(self):
        probe(self)
        probes.append(None)
        if len(probes) == 1:
            self.ack_joystick_button_pressed()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Microcontroller, "turn_off_all_ports", probe_then_another_command)
        mcu = Microcontroller(serial_device=sim, reset_and_initialize=False)

    assert answers[:2] == [1, 2]
    assert mcu.firmware_version == FIRMWARE
    mcu.close()


def test_a_window_that_only_ends_in_zero_is_not_a_packet_on_firmware_that_sends_checksums():
    # 24 stray bytes that end in zero, with the joystick bit set where the buttons byte would be. Firmware from
    # before checksums ended every packet in zero, so such a window used to count as a packet on any firmware.
    sim = _sim()
    sent = _record_commands(sim)
    mcu = Microcontroller(serial_device=sim, reset_and_initialize=False)
    stray = bytearray(PACKET_LENGTH)
    stray[BYTE_BUTTONS] = 1

    _queue(sim, stray)
    _command_round_trip(mcu)

    assert CMD_SET.ACK_JOYSTICK_BUTTON_PRESSED not in sent
    assert not mcu.joystick_button_pressed
    mcu.close()


@pytest.mark.parametrize("lost", [1, 10, 23])
def test_finds_the_stream_again_after_bytes_are_lost(lost):
    sim = _sim()
    mcu = Microcontroller(serial_device=sim, reset_and_initialize=False)
    mcu.move_x_usteps(1234)
    mcu.wait_till_operation_is_completed(timeout_limit_s=2)
    position = mcu.get_pos()
    assert position[0] == 1234

    _queue(sim, _idle_packet(sim, cmd_id=mcu._cmd_id)[lost:])  # a packet whose first bytes never arrived
    _command_round_trip(mcu)

    assert mcu.get_pos() == position
    assert mcu.firmware_version == FIRMWARE
    mcu.close()


def test_a_firmware_change_on_the_same_connection_is_followed_and_reported(caplog):
    # The controller was reflashed while Squid stayed connected. Its packets verify, but carry another version byte:
    # two in a row are taken as the new firmware (one alone could be chance), with a warning to restart Squid.
    sim = _sim()
    mcu = Microcontroller(serial_device=sim, reset_and_initialize=False)
    sim.firmware_version = OLDER_FIRMWARE

    with caplog.at_level(logging.WARNING):
        _queue(sim, _idle_packet(sim, cmd_id=mcu._cmd_id))
        _command_round_trip(mcu)

    assert mcu.firmware_version == OLDER_FIRMWARE
    assert [r for r in caplog.records if "firmware changed" in r.getMessage()]
    _command_round_trip(mcu)
    mcu.close()


def test_firmware_without_checksums_still_connects():
    # Firmware from before April 2025 ends every packet in a zero instead of a checksum, and reports no version.
    sim = _sim((0, 0))
    build = sim._build_response
    sim._build_response = lambda cmd_id, status: build(cmd_id, status)[:-1] + b"\x00"

    sent = _record_commands(sim)

    mcu = Microcontroller(serial_device=sim, reset_and_initialize=False)

    assert mcu.firmware_version == (0, 0)
    # Nothing verifies such a packet, so the handshake takes two commands: the echoed id has to follow them.
    assert sent == [CMD_SET.TURN_OFF_ALL_PORTS, CMD_SET.TURN_OFF_ALL_PORTS]
    mcu.move_x_usteps(1234)
    mcu.wait_till_operation_is_completed(timeout_limit_s=2)
    assert mcu.get_pos()[0] == 1234
    mcu.close()


def test_firmware_without_checksums_connects_when_the_waiting_bytes_start_mid_packet():
    # Without checksums only the echoed id following the host's commands can show where packets start.
    sim = _sim((0, 0))
    build = sim._build_response
    sim._build_response = lambda cmd_id, status: build(cmd_id, status)[:-1] + b"\x00"
    sim.x = 96000  # 0x00017700: a 0x01 byte to mistake for the first command's id
    packet = sim._build_response(37, CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS)
    _queue(sim, packet[3:] + packet * 2)

    mcu = Microcontroller(serial_device=sim, reset_and_initialize=False)

    assert mcu.firmware_version == (0, 0)
    _command_round_trip(mcu)
    assert mcu.get_pos() == (96000, 0, 0, 0)
    mcu.close()


def test_stray_bytes_and_a_position_byte_do_not_pass_for_two_answers_without_checksums():
    # Without checksums a window is "packet-shaped" if it only ends in zero. Here stray bytes hold a 0x01 exactly
    # 24 bytes before a 0x02 of the Y position, both in windows that end in zero: first bytes 1 then 2, like answers
    # to the first two commands, and ahead of the real second answer. They are not alike as two packets of one
    # stream are, so they are not taken.
    sim = _sim((0, 0))
    build = sim._build_response
    sim._build_response = lambda cmd_id, status: build(cmd_id, status)[:-1] + b"\x00"
    sim.x, sim.y, sim.z = -23305, 192184, 131071
    assert sim._build_response(1, CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS)[7] == 2
    stray = bytes.fromhex(
        "29153c9a39573b29eb531f14843bf91cc1e34f86dbbc882854c53b9a46fc0ebaf88dd4f1d9563667bbbcf401942eee95c8917e9714c190570a9b5213"
    )
    assert stray[43] == 1
    _queue(sim, stray)

    mcu = Microcontroller(serial_device=sim, reset_and_initialize=False)

    assert mcu.firmware_version == (0, 0)
    _command_round_trip(mcu)
    assert mcu.get_pos() == (-23305, 192184, 131071, 0)
    mcu.close()


def test_a_controller_that_never_answers_is_reported_with_what_was_read():
    # The controller keeps sending packets but never takes the host's command (they all echo command 0).
    sim = _sim()
    _queue(sim, _idle_packet(sim) * 3)
    sim._process_command = lambda cmd: None
    detect = Microcontroller._detect_firmware_version

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Microcontroller, "_detect_firmware_version", lambda self: detect(self, timeout_s=0.3))
        with pytest.raises(TimeoutError) as error:
            Microcontroller(serial_device=sim, reset_and_initialize=False)

    message = str(error.value)
    assert re.search(r"asking for the firmware version \(\d+ time\(s\)\)", message)
    assert "72 byte" in message and "echoed cmd 0" in message and "did not take the command" in message
