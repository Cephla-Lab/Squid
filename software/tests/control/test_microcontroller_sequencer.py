"""Tests for Microcontroller.seq_* — the wire layer that speaks to the hardware sequencer.

These use a recording serial stub rather than the sequencer simulator: the point here is
exactly what goes on the wire, the firmware version gate, and how a failed SEQ command is
surfaced.  End-to-end running lives in test_sequencer_simulation.py.
"""

import struct
import threading

import pytest

import control.sequencer_program as sp
from control._def import CMD_EXECUTION_STATUS, CMD_SET
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

    Bytes 14..17 used to carry a theta position no firmware ever wrote; from firmware 1.7
    they are the sequencer status (seq_wire.h).  Reusing the existing response builder keeps
    this test honest about the packet layout.
    """
    packed = bytes(
        sp.SequencerStatus(state=state, error=error, detail=detail, frames_fired=frames_fired).to_response_bytes()
    )
    return int.from_bytes(packed, "big", signed=True)


class RecordingSerial(AbstractCephlaMicroSerial):
    """Answers every command immediately, records it, and reports a chosen firmware version."""

    def __init__(self, firmware_version=sp.MIN_FIRMWARE_VERSION, status=CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS):
        super().__init__()
        self._lock = threading.Lock()
        self.firmware_version = firmware_version
        self.status = status
        self.theta = 0
        self.commands = []
        self._buffer = bytearray()
        self._closed = False

    def commands_of_type(self, command_code):
        return [c for c in self.commands if c[1] == command_code]

    def write(self, data, reconnect_tries: int = 0) -> int:
        with self._lock:
            self.commands.append(bytes(data))
            self._buffer.extend(
                SimSerial.response_bytes_for(
                    data[0],
                    self.status,
                    0,
                    0,
                    0,
                    self.theta,
                    False,
                    False,
                    firmware_version=self.firmware_version,
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
def legacy_mcu_and_serial():
    serial = RecordingSerial(firmware_version=(1, 6))
    mcu = Microcontroller(serial, reset_and_initialize=False)
    yield mcu, serial
    mcu.close()


class TestFirmwareGate:
    def test_reports_support_on_1_7(self, mcu_and_serial):
        mcu, _ = mcu_and_serial
        assert mcu.firmware_version == (1, 7)
        assert mcu.supports_hardware_sequencer()

    def test_reports_no_support_on_1_6(self, legacy_mcu_and_serial):
        mcu, _ = legacy_mcu_and_serial
        assert mcu.firmware_version == (1, 6)
        assert not mcu.supports_hardware_sequencer()

    def test_seq_upload_raises_on_old_firmware(self, legacy_mcu_and_serial):
        mcu, serial = legacy_mcu_and_serial
        with pytest.raises(SequencerNotSupportedError, match=r"1\.6"):
            mcu.seq_upload(minimal_program())
        assert serial.commands_of_type(CMD_SET.SEQ_WRITE) == []

    def test_seq_run_raises_on_old_firmware(self, legacy_mcu_and_serial):
        mcu, serial = legacy_mcu_and_serial
        with pytest.raises(SequencerNotSupportedError):
            mcu.seq_run(0)
        assert serial.commands_of_type(CMD_SET.SEQ_RUN) == []

    def test_seq_cancel_raises_on_old_firmware(self, legacy_mcu_and_serial):
        mcu, serial = legacy_mcu_and_serial
        with pytest.raises(SequencerNotSupportedError):
            mcu.seq_cancel()
        assert serial.commands_of_type(CMD_SET.SEQ_CANCEL) == []

    def test_the_error_names_the_required_version(self, legacy_mcu_and_serial):
        mcu, _ = legacy_mcu_and_serial
        with pytest.raises(SequencerNotSupportedError, match=r"1\.7"):
            mcu.seq_run(0)


class TestSeqUpload:
    def test_writes_every_word_then_commits(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        program = golden_program()
        mcu.seq_upload(program)

        writes = serial.commands_of_type(CMD_SET.SEQ_WRITE)
        commits = serial.commands_of_type(CMD_SET.SEQ_COMMIT)
        assert len(writes) == len(program.words()) == 20
        assert len(commits) == 1
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

        commit = serial.commands_of_type(CMD_SET.SEQ_COMMIT)[0]
        assert struct.unpack(">H", commit[2:4])[0] == len(program.pack()) == 80
        assert struct.unpack(">H", commit[4:6])[0] == program.crc16()

    def test_commit_crc_is_the_ccitt_false_value(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        program = golden_program()
        mcu.seq_upload(program)

        commit = serial.commands_of_type(CMD_SET.SEQ_COMMIT)[0]
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
        assert serial.commands_of_type(CMD_SET.SEQ_COMMIT) == []


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
    def test_is_none_before_any_sequencer_firmware_packet(self, legacy_mcu_and_serial):
        mcu, _ = legacy_mcu_and_serial
        assert mcu.seq_status is None

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
    def test_theta_stays_zero_on_sequencer_firmware(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        serial.theta = status_as_theta(sp.SeqState.EXPOSING, sp.SeqError.NONE, frames_fired=7)
        mcu.seq_cancel()
        mcu.wait_till_operation_is_completed()

        # Bytes 14..17 are the sequencer status now, not a position.
        assert mcu.theta_pos == 0
        assert mcu.get_pos()[3] == 0

    def test_theta_is_still_parsed_on_older_firmware(self, legacy_mcu_and_serial):
        mcu, serial = legacy_mcu_and_serial
        serial.theta = -12345
        mcu.turn_off_all_ports()
        mcu.wait_till_operation_is_completed()

        assert mcu.theta_pos == -12345


class TestSequencerFailureReporting:
    def test_execution_error_carries_the_decoded_seq_error(self, mcu_and_serial):
        mcu, serial = mcu_and_serial
        serial.theta = status_as_theta(sp.SeqState.FAILED, sp.SeqError.READY_TIMEOUT, detail=2, frames_fired=5)
        serial.status = CMD_EXECUTION_STATUS.CMD_EXECUTION_ERROR

        mcu.seq_run(0)
        with pytest.raises(CommandAborted) as exc:
            mcu.wait_till_operation_is_completed()

        assert "READY_TIMEOUT" in str(exc.value)
        assert "detail=2" in str(exc.value)
        assert exc.value.seq_status.error == sp.SeqError.READY_TIMEOUT
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
