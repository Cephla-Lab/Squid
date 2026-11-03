"""Tests for Microcontroller.seq_* — the wire layer that speaks to the hardware sequencer.

These use a recording serial stub rather than the sequencer simulator: the point here is
exactly what goes on the wire, the probe that decides whether the firmware has a sequencer,
and how a failed SEQ command is surfaced.  End-to-end running lives in
test_sequencer_simulation.py.
"""

import struct
import threading

import pytest

import control.sequencer_program as sp
from control._def import CMD_EXECUTION_STATUS, CMD_SET, ENC_FLAG, SEQ_FLAG_STATUS
from control.microcontroller import (
    AbstractCephlaMicroSerial,
    CommandAborted,
    Microcontroller,
    SequencerNotSupportedError,
    SimSerial,
)

from tests.control.test_sequencer_program import golden_program, minimal_program


def status_as_theta(state: sp.SeqState, error: sp.SeqError, detail: int = 0, frames_fired: int = 0) -> int:
    """Sequencer status bytes 14..17, expressed as the int32 SimSerial writes there.

    Bytes 14..17 are the theta field, which the sequencer takes over (flagged in byte 19 by
    SEQ_FLAG_STATUS) to carry its status (seq_wire.h).  Reusing the existing response builder
    keeps this test honest about the packet layout.
    """
    packed = bytes(
        sp.SequencerStatus(state=state, error=error, detail=detail, frames_fired=frames_fired).to_response_bytes()
    )
    return int.from_bytes(packed, "big", signed=True)


def is_probe(command: bytes) -> bool:
    """The host's "is there a sequencer?" question: a SEQ_COMMIT of length 0 with CRC 0."""
    return command[1] == CMD_SET.SEQ_COMMIT and command[2:6] == bytes(4)


class RecordingSerial(AbstractCephlaMicroSerial):
    """Answers every command immediately, records it, and reports a chosen firmware version.

    `sequencer` says which firmware this is.  With the sequencer, the host's probe (an empty
    SEQ_COMMIT) is refused and bytes 14..17 are flagged as the sequencer's status.  Without
    it, every SEQ_* opcode completes (callback_default) and bytes 14..17 are theta.  Either
    way `status` answers everything else, and `flags` is byte 19 of every packet.
    """

    def __init__(self, firmware_version=(1, 8), status=CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS, sequencer=True):
        super().__init__()
        self._lock = threading.Lock()
        self.firmware_version = firmware_version
        self.status = status
        self.sequencer = sequencer
        self.flags = (1 << SEQ_FLAG_STATUS) if sequencer else 0
        self.theta = 0
        self.commands = []
        self._buffer = bytearray()
        self._closed = False

    def commands_of_type(self, command_code):
        return [c for c in self.commands if c[1] == command_code]

    def write(self, data, reconnect_tries: int = 0) -> int:
        with self._lock:
            command = bytes(data)
            self.commands.append(command)
            status = self.status
            if self.sequencer and is_probe(command):
                status = CMD_EXECUTION_STATUS.CMD_EXECUTION_ERROR  # Staging::commit() refuses a zero-length program
            self._buffer.extend(
                SimSerial.response_bytes_for(
                    data[0],
                    status,
                    0,
                    0,
                    0,
                    self.theta,
                    False,
                    False,
                    firmware_version=self.firmware_version,
                    flags=self.flags,
                )
            )
        return len(data)

    def read(self, count: int = 1, reconnect_tries: int = 0) -> bytes:
        with self._lock:
            out = self._buffer[:count]
            del self._buffer[:count]
            return bytes(out)

    def bytes_available(self) -> int:
        with self._lock:
            return len(self._buffer)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._buffer.clear()

    def reset_input_buffer(self) -> bool:
        with self._lock:
            self._buffer.clear()
        return True

    def is_open(self) -> bool:
        return not self._closed

    def reconnect(self, attempts: int) -> bool:
        return True


@pytest.fixture
def mcu_and_serial():
    serial = RecordingSerial()
    mcu = Microcontroller(serial, reset_and_initialize=False)
    yield mcu, serial
    mcu.close()


@pytest.fixture
def no_sequencer_mcu_and_serial():
    """Firmware without the sequencer - master's, which reports the same version number."""
    serial = RecordingSerial(sequencer=False)
    mcu = Microcontroller(serial, reset_and_initialize=False)
    yield mcu, serial
    mcu.close()


def probes_sent(serial) -> list:
    return [c for c in serial.commands if is_probe(c)]


def program_commits(serial) -> list:
    """The SEQ_COMMITs that sealed a program, as opposed to the probe."""
    return [c for c in serial.commands_of_type(CMD_SET.SEQ_COMMIT) if not is_probe(c)]


class TestSequencerProbe:
    def test_nothing_is_sent_until_asked(self, mcu_and_serial):
        _, serial = mcu_and_serial
        assert serial.commands_of_type(CMD_SET.SEQ_COMMIT) == []

    def test_firmware_that_refuses_an_empty_commit_has_the_sequencer(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        assert mcu.supports_hardware_sequencer()
        assert len(probes_sent(serial)) == 1

    def test_firmware_that_completes_an_empty_commit_has_no_sequencer(self, no_sequencer_mcu_and_serial):
        mcu, serial = no_sequencer_mcu_and_serial
        assert not mcu.supports_hardware_sequencer()
        assert len(probes_sent(serial)) == 1

    def test_the_version_number_does_not_decide(self):
        # Master firmware 1.8 and sequencer firmware report the same version; only the probe can tell.
        serial = RecordingSerial(firmware_version=(1, 8), sequencer=False)
        mcu = Microcontroller(serial, reset_and_initialize=False)
        try:
            assert mcu.firmware_version == (1, 8)
            assert not mcu.supports_hardware_sequencer()
        finally:
            mcu.close()

    def test_asks_once_per_connection(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        assert mcu.supports_hardware_sequencer()
        assert mcu.supports_hardware_sequencer()
        assert len(probes_sent(serial)) == 1

    def test_the_refusal_leaves_no_aborted_command_behind(self, mcu_and_serial):
        mcu, _ = mcu_and_serial
        mcu.supports_hardware_sequencer()
        assert mcu.last_command_aborted_error is None
        mcu.turn_off_all_ports()
        mcu.wait_till_operation_is_completed()  # the next command is unaffected

    def test_seq_upload_raises_without_the_sequencer(self, no_sequencer_mcu_and_serial):
        mcu, serial = no_sequencer_mcu_and_serial
        with pytest.raises(SequencerNotSupportedError, match="completed an empty SEQ_COMMIT"):
            mcu.seq_upload(minimal_program())
        assert serial.commands_of_type(CMD_SET.SEQ_WRITE) == []

    def test_seq_run_raises_without_the_sequencer(self, no_sequencer_mcu_and_serial):
        mcu, serial = no_sequencer_mcu_and_serial
        with pytest.raises(SequencerNotSupportedError):
            mcu.seq_run(0)
        assert serial.commands_of_type(CMD_SET.SEQ_RUN) == []

    def test_seq_cancel_raises_without_the_sequencer(self, no_sequencer_mcu_and_serial):
        mcu, serial = no_sequencer_mcu_and_serial
        with pytest.raises(SequencerNotSupportedError):
            mcu.seq_cancel()
        assert serial.commands_of_type(CMD_SET.SEQ_CANCEL) == []


class TestSeqUpload:
    def test_writes_every_word_then_commits(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        program = golden_program()
        mcu.seq_upload(program)

        writes = serial.commands_of_type(CMD_SET.SEQ_WRITE)
        assert len(writes) == len(program.words()) == 22
        assert len(program_commits(serial)) == 1
        # commit comes last
        assert serial.commands[-1][1] == CMD_SET.SEQ_COMMIT

    def test_write_payload_is_word_index_plus_four_bytes(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        program = golden_program()
        mcu.seq_upload(program)

        for command, (index, data) in zip(serial.commands_of_type(CMD_SET.SEQ_WRITE), program.words()):
            assert command[2] == index
            assert command[3:7] == data

    def test_writes_use_absolute_offsets_so_a_resend_is_idempotent(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        packed = golden_program().pack()
        mcu.seq_upload(golden_program())

        for command in serial.commands_of_type(CMD_SET.SEQ_WRITE):
            offset = command[2] * sp.WORD_BYTES
            assert command[3:7] == packed[offset : offset + sp.WORD_BYTES]

    def test_commit_carries_unpadded_length_and_crc_big_endian(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        program = golden_program()
        mcu.seq_upload(program)

        [commit] = program_commits(serial)
        assert struct.unpack(">H", commit[2:4])[0] == len(program.pack()) == 88
        assert struct.unpack(">H", commit[4:6])[0] == program.crc16()

    def test_commit_crc_is_the_ccitt_false_value(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        program = golden_program()
        mcu.seq_upload(program)

        [commit] = program_commits(serial)
        assert struct.unpack(">H", commit[4:6])[0] == sp.crc16_ccitt_false(program.pack())

    def test_one_pending_command_at_a_time(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        mcu.seq_upload(golden_program())
        # Every staged command was acked before the next went out, so nothing is pending.
        assert not mcu.is_busy()

    def test_rejects_an_invalid_program_before_sending_anything(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        with pytest.raises(sp.ProgramValidationError):
            mcu.seq_upload(minimal_program(channels=()))
        assert serial.commands_of_type(CMD_SET.SEQ_WRITE) == []
        assert serial.commands_of_type(CMD_SET.SEQ_COMMIT) == []  # not even the probe


class TestSeqRunAndCancel:
    @pytest.mark.parametrize("stack_start", [0, 1, 32768, 65535, -1, -2147483648, 2147483647])
    def test_run_carries_int32_stack_start_big_endian(self, mcu_and_serial, stack_start):
        mcu, serial = mcu_and_serial
        mcu.seq_run(stack_start)

        run = serial.commands_of_type(CMD_SET.SEQ_RUN)[0]
        assert struct.unpack(">i", run[2:6])[0] == stack_start

    def test_run_does_not_wait(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        mcu.seq_run(0)
        # The caller waits with wait_till_operation_is_completed(); seq_run only sends.
        assert len(serial.commands_of_type(CMD_SET.SEQ_RUN)) == 1

    def test_cancel_has_no_payload(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        mcu.seq_cancel()

        cancel = serial.commands_of_type(CMD_SET.SEQ_CANCEL)[0]
        assert cancel[2:7] == b"\x00" * 5


class TestSeqStatus:
    def test_is_none_until_the_firmware_flags_the_field(self, no_sequencer_mcu_and_serial):
        mcu, serial = no_sequencer_mcu_and_serial
        # Bytes that would decode as a status are still theta while byte 19 does not say otherwise.
        serial.theta = status_as_theta(sp.SeqState.EXPOSING, sp.SeqError.NONE, frames_fired=1234)
        mcu.turn_off_all_ports()
        mcu.wait_till_operation_is_completed()
        assert mcu.seq_status is None

    def test_an_encoder_report_is_not_a_sequencer_status(self, no_sequencer_mcu_and_serial):
        mcu, serial = no_sequencer_mcu_and_serial
        serial.flags = 1 << ENC_FLAG.REPORTING
        serial.theta = 777
        mcu.turn_off_all_ports()
        mcu.wait_till_operation_is_completed()
        assert mcu.seq_status is None
        assert mcu.encoder_pos == 777

    def test_keeps_the_last_report_once_the_flag_clears(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        serial.theta = status_as_theta(sp.SeqState.DONE, sp.SeqError.NONE, frames_fired=20)
        mcu.seq_cancel()
        mcu.wait_till_operation_is_completed()

        serial.flags = 0  # the hold expired: the field went back to its other owners
        serial.theta = -12345
        mcu.turn_off_all_ports()
        mcu.wait_till_operation_is_completed()

        assert mcu.seq_status.state == sp.SeqState.DONE
        assert mcu.seq_status.frames_fired == 20
        assert mcu.theta_pos == -12345

    def test_is_parsed_from_bytes_14_to_17(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        serial.theta = status_as_theta(sp.SeqState.EXPOSING, sp.SeqError.NONE, detail=0, frames_fired=1234)
        mcu.seq_cancel()
        mcu.wait_till_operation_is_completed()

        status = mcu.seq_status
        assert status.state == sp.SeqState.EXPOSING
        assert status.error == sp.SeqError.NONE
        assert status.frames_fired == 1234

    def test_carries_the_abort_detail(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        serial.theta = status_as_theta(sp.SeqState.FAILED, sp.SeqError.STACK_OUT_OF_RANGE, detail=3)
        mcu.seq_cancel()
        mcu.wait_till_operation_is_completed()

        assert mcu.seq_status.state == sp.SeqState.FAILED
        assert mcu.seq_status.error == sp.SeqError.STACK_OUT_OF_RANGE
        assert mcu.seq_status.detail == 3


class TestThetaPosition:
    def test_theta_keeps_its_value_while_the_sequencer_owns_the_field(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        serial.theta = status_as_theta(sp.SeqState.EXPOSING, sp.SeqError.NONE, frames_fired=7)
        mcu.seq_cancel()
        mcu.wait_till_operation_is_completed()

        # Bytes 14..17 are the sequencer status while flagged, not a position.
        assert mcu.theta_pos == 0
        assert mcu.get_pos()[3] == 0

    def test_theta_is_parsed_when_nothing_else_owns_the_field(self, no_sequencer_mcu_and_serial):
        mcu, serial = no_sequencer_mcu_and_serial
        serial.theta = -12345
        mcu.turn_off_all_ports()
        mcu.wait_till_operation_is_completed()

        assert mcu.theta_pos == -12345


class TestSequencerFailureReporting:
    def test_execution_error_carries_the_decoded_seq_error(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        serial.theta = status_as_theta(sp.SeqState.FAILED, sp.SeqError.READY_LINE_STUCK, detail=2, frames_fired=5)
        serial.status = CMD_EXECUTION_STATUS.CMD_EXECUTION_ERROR

        mcu.seq_run(0)
        with pytest.raises(CommandAborted) as exc:
            mcu.wait_till_operation_is_completed()

        assert "READY_LINE_STUCK" in str(exc.value)
        assert "detail=2" in str(exc.value)
        assert exc.value.seq_status.error == sp.SeqError.READY_LINE_STUCK
        assert exc.value.seq_status.detail == 2
        assert exc.value.seq_status.frames_fired == 5

    def test_a_non_sequencer_command_failure_is_unchanged(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        serial.status = CMD_EXECUTION_STATUS.CMD_EXECUTION_ERROR

        mcu.move_x_usteps(10)
        with pytest.raises(CommandAborted) as exc:
            mcu.wait_till_operation_is_completed()

        assert exc.value.seq_status is None
        assert "firmware reported CMD_EXECUTION_ERROR" in str(exc.value)

    def test_a_failed_commit_names_the_program_problem(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        serial.theta = status_as_theta(sp.SeqState.IDLE, sp.SeqError.BAD_PROGRAM, detail=2)
        serial.status = CMD_EXECUTION_STATUS.CMD_EXECUTION_ERROR

        with pytest.raises(CommandAborted) as exc:
            mcu.seq_upload(golden_program())

        assert "BAD_PROGRAM" in str(exc.value)
