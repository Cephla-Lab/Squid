"""Tests for the simulated hardware sequencer behind SimSerial and FirmwareSimSerial.

Everything here goes through a real Microcontroller, so the same code path the microscope
uses is exercised: stage with SEQ_WRITE, seal with SEQ_COMMIT, SEQ_RUN, and watch the run
through the status bytes of the 10 ms packet.
"""

import threading
import time

import pytest

import control.sequencer_program as sp
import control.sequencer_sim as seq_sim
from control._def import CMD_EXECUTION_STATUS, CMD_SET
from control.firmware_sim_serial import FirmwareSimSerial
from control.microcontroller import CommandAborted, Microcontroller, SimSerial

from tests.control.test_sequencer_program import minimal_program

# Every frame of these programs is milliseconds of simulated dwell; run the timeline far
# faster than real time so the suite stays quick.
TEST_SPEED_UP = 500.0

RUN_TIMEOUT_S = 20.0


@pytest.fixture(autouse=True)
def fast_timeline(monkeypatch):
    monkeypatch.setattr(seq_sim, "SPEED_UP_FACTOR", TEST_SPEED_UP)


@pytest.fixture(params=["SimSerial", "FirmwareSimSerial"])
def mcu(request):
    serial = SimSerial() if request.param == "SimSerial" else FirmwareSimSerial(strict=True)
    controller = Microcontroller(serial, reset_and_initialize=False)
    yield controller
    controller.close()


def sequencer_of(mcu):
    return mcu._serial.sequencer


class TriggerRecorder:
    """Stands in for the simulated camera: records every hardware trigger the MCU emits."""

    def __init__(self):
        self.lock = threading.Lock()
        self.camera_ids = []

    def __call__(self, camera_id):
        with self.lock:
            self.camera_ids.append(camera_id)

    @property
    def count(self):
        with self.lock:
            return len(self.camera_ids)


def stack_program(n_channels=2, n_layers=3, **loop_overrides):
    """A piezo stack with `n_channels` channels, all on camera 0."""
    loop = minimal_program().loop.replace(
        stack_axis_type=sp.StackAxisType.PIEZO,
        stack_axis_id=0,
        dz=100,
        n_layers=n_layers,
        **loop_overrides,
    )
    return minimal_program(
        loop=loop,
        channels=tuple(
            sp.SeqChannelSpec(exposure_us=5000, camera_mask=1, illum_ttl_mask=1 << i) for i in range(n_channels)
        ),
        cameras=(sp.SeqCameraSpec(trigger_mode=sp.TriggerMode.LEVEL, strobe_delay_us=300, readout_time_us=10000),),
    )


def _wait_until(predicate, what, timeout_s=RUN_TIMEOUT_S):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError(f"timed out waiting for {what}")


def _write_word(mcu, index, data):
    """A raw SEQ_WRITE, for staging bytes SequencerProgram.pack() would never produce."""
    cmd = bytearray(mcu.tx_buffer_length)
    cmd[1] = CMD_SET.SEQ_WRITE
    cmd[2] = index
    cmd[3:7] = data
    mcu.send_command(cmd)
    mcu.wait_till_operation_is_completed()


def _commit(mcu, length, crc):
    cmd = bytearray(mcu.tx_buffer_length)
    cmd[1] = CMD_SET.SEQ_COMMIT
    cmd[2] = (length >> 8) & 0xFF
    cmd[3] = length & 0xFF
    cmd[4] = (crc >> 8) & 0xFF
    cmd[5] = crc & 0xFF
    mcu.send_command(cmd)


def _stage_raw(mcu, staged: bytes):
    for index, data in sp.split_words(staged):
        _write_word(mcu, index, data)


class TestFirmwareVersion:
    def test_the_simulator_reports_sequencer_firmware(self, mcu):
        assert mcu.firmware_version == sp.MIN_FIRMWARE_VERSION
        assert mcu.supports_hardware_sequencer()

    def test_status_is_idle_before_anything_runs(self, mcu):
        assert mcu.seq_status.state == sp.SeqState.IDLE
        assert mcu.seq_status.error == sp.SeqError.NONE
        assert mcu.seq_status.frames_fired == 0


class TestRun:
    def test_a_two_channel_three_layer_run_fires_six_frames(self, mcu):
        recorder = TriggerRecorder()
        sequencer_of(mcu).on_hardware_trigger = recorder

        mcu.seq_upload(stack_program(n_channels=2, n_layers=3))
        mcu.seq_run(0)
        mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)

        assert recorder.count == 6
        assert recorder.camera_ids == [0] * 6
        assert mcu.seq_status.frames_fired == 6
        assert mcu.seq_status.state == sp.SeqState.DONE
        assert mcu.seq_status.error == sp.SeqError.NONE

    def test_a_run_can_be_repeated_without_re_uploading(self, mcu):
        recorder = TriggerRecorder()
        sequencer_of(mcu).on_hardware_trigger = recorder

        mcu.seq_upload(stack_program(n_channels=1, n_layers=2))
        for _ in range(3):
            mcu.seq_run(0)
            mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)

        assert recorder.count == 6
        assert mcu.seq_status.frames_fired == 2  # per run, not cumulative

    def test_triggers_every_camera_in_the_mask(self, mcu):
        recorder = TriggerRecorder()
        sequencer_of(mcu).on_hardware_trigger = recorder
        program = minimal_program(
            channels=(sp.SeqChannelSpec(exposure_us=2000, camera_mask=0b11),),
            cameras=(
                sp.SeqCameraSpec(trigger_mode=sp.TriggerMode.LEVEL, readout_time_us=1000),
                sp.SeqCameraSpec(trigger_mode=sp.TriggerMode.LEVEL, readout_time_us=1000),
            ),
        )

        mcu.seq_upload(program)
        mcu.seq_run(0)
        mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)

        assert sorted(recorder.camera_ids) == [0, 1]
        # frames_fired counts exposure steps, not triggers (SeqEngine::schedule_exposures)
        assert mcu.seq_status.frames_fired == 1

    def test_the_run_stays_in_progress_until_it_finishes(self, mcu):
        mcu.seq_upload(stack_program(n_channels=2, n_layers=8))
        mcu.seq_run(0)
        assert mcu.is_busy()
        mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)
        assert not mcu.is_busy()


class TestRunRejections:
    def test_run_without_a_committed_program(self, mcu):
        mcu.seq_run(0)
        with pytest.raises(CommandAborted) as exc:
            mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)

        assert exc.value.seq_status.error == sp.SeqError.NOT_COMMITTED

    def test_run_with_a_piezo_stack_out_of_range(self, mcu):
        mcu.seq_upload(stack_program(n_channels=1, n_layers=10))  # dz=100 -> spans start..start+900
        mcu.seq_run(65000)
        with pytest.raises(CommandAborted) as exc:
            mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)

        assert exc.value.seq_status.error == sp.SeqError.STACK_OUT_OF_RANGE
        assert exc.value.seq_status.state == sp.SeqState.FAILED

    def test_a_negative_piezo_start_is_out_of_range(self, mcu):
        mcu.seq_upload(stack_program(n_channels=1, n_layers=2))
        mcu.seq_run(-1)
        with pytest.raises(CommandAborted) as exc:
            mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)

        assert exc.value.seq_status.error == sp.SeqError.STACK_OUT_OF_RANGE
        assert exc.value.seq_status.detail == 0xFF

    def test_nothing_is_triggered_when_the_run_is_refused(self, mcu):
        recorder = TriggerRecorder()
        sequencer_of(mcu).on_hardware_trigger = recorder

        mcu.seq_upload(stack_program(n_channels=1, n_layers=10))
        mcu.seq_run(65000)
        with pytest.raises(CommandAborted):
            mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)

        assert recorder.count == 0


class TestCommit:
    def test_a_corrupt_crc_is_refused(self, mcu):
        staged = stack_program(n_channels=1, n_layers=1).pack()
        _stage_raw(mcu, staged)
        _commit(mcu, len(staged), sp.crc16_ccitt_false(staged) ^ 0x0001)

        with pytest.raises(CommandAborted) as exc:
            mcu.wait_till_operation_is_completed()
        assert exc.value.seq_status.error == sp.SeqError.BAD_PROGRAM
        assert exc.value.seq_status.detail == sp.BAD_PROGRAM_CRC

    def test_a_wrong_length_is_refused(self, mcu):
        staged = stack_program(n_channels=1, n_layers=1).pack()
        _stage_raw(mcu, staged)
        short = staged[:-4]
        _commit(mcu, len(short), sp.crc16_ccitt_false(short))

        with pytest.raises(CommandAborted) as exc:
            mcu.wait_till_operation_is_completed()
        assert exc.value.seq_status.error == sp.SeqError.BAD_PROGRAM
        assert exc.value.seq_status.detail == sp.BAD_PROGRAM_MISMATCH

    def test_a_program_that_fails_validation_is_refused_with_its_own_error(self, mcu):
        # Stage a valid program, then corrupt channel 0's exposure to zero -- something
        # pack() would never emit, so it has to be written by hand.
        staged = bytearray(stack_program(n_channels=1, n_layers=1).pack())
        staged[sp.CHANNELS_OFFSET + 10 : sp.CHANNELS_OFFSET + 14] = b"\x00\x00\x00\x00"  # exposure_us
        _stage_raw(mcu, bytes(staged))
        _commit(mcu, len(staged), sp.crc16_ccitt_false(bytes(staged)))

        with pytest.raises(CommandAborted) as exc:
            mcu.wait_till_operation_is_completed()
        assert exc.value.seq_status.error == sp.SeqError.BAD_EXPOSURE

    def test_a_resent_word_is_idempotent(self, mcu):
        program = stack_program(n_channels=1, n_layers=2)
        staged = program.pack()
        for index, data in sp.split_words(staged):
            _write_word(mcu, index, data)
            _write_word(mcu, index, data)  # the v1 blind resend
        _commit(mcu, len(staged), sp.crc16_ccitt_false(staged))
        mcu.wait_till_operation_is_completed()

        assert sequencer_of(mcu).committed_program == program

    def test_any_write_unseals_the_committed_program(self, mcu):
        """callback_seq_write() sets committed = false: a half-written program must never
        be runnable, so SEQ_RUN after a bare SEQ_WRITE answers NotCommitted."""
        mcu.seq_upload(stack_program(n_channels=1, n_layers=2))
        assert sequencer_of(mcu).committed_program is not None

        _stage_raw(mcu, stack_program(n_channels=2, n_layers=5).pack())
        assert sequencer_of(mcu).committed_program is None

        mcu.seq_run(0)
        with pytest.raises(CommandAborted) as exc:
            mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)
        assert exc.value.seq_status.error == sp.SeqError.NOT_COMMITTED


class TestCancel:
    def test_cancel_mid_run_stops_after_the_current_frame(self, mcu):
        recorder = TriggerRecorder()
        sequencer_of(mcu).on_hardware_trigger = recorder
        program = stack_program(n_channels=2, n_layers=16)  # 32 frames

        mcu.seq_upload(program)
        mcu.seq_run(0)
        _wait_until(lambda: recorder.count >= 2, "the run to fire a couple of frames")
        mcu.seq_cancel()
        # The run's own waiter returns: SEQ_CANCEL completes when the engine is terminal.
        mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)

        assert 0 < mcu.seq_status.frames_fired < program.n_frames
        assert recorder.count == mcu.seq_status.frames_fired
        assert mcu.seq_status.state == sp.SeqState.DONE
        assert mcu.seq_status.error == sp.SeqError.CANCELED

    def test_cancel_is_a_completed_command_not_an_error(self, mcu):
        mcu.seq_upload(stack_program(n_channels=1, n_layers=16))
        mcu.seq_run(0)
        mcu.seq_cancel()
        mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)  # must not raise

    def test_cancel_when_nothing_runs_is_harmless(self, mcu):
        mcu.seq_cancel()
        mcu.wait_till_operation_is_completed()
        assert mcu.seq_status.state == sp.SeqState.IDLE

    def test_a_cancelled_program_can_be_run_again(self, mcu):
        mcu.seq_upload(stack_program(n_channels=1, n_layers=16))
        mcu.seq_run(0)
        mcu.seq_cancel()
        mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)

        recorder = TriggerRecorder()
        sequencer_of(mcu).on_hardware_trigger = recorder
        mcu.seq_run(0)
        mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)

        assert recorder.count == 16
        assert mcu.seq_status.error == sp.SeqError.NONE


class TestAllowTableWhileRunning:
    def test_heartbeat_is_allowed_and_does_not_disturb_the_run(self, mcu):
        recorder = TriggerRecorder()
        sequencer_of(mcu).on_hardware_trigger = recorder
        program = stack_program(n_channels=2, n_layers=16)

        mcu.seq_upload(program)
        mcu.seq_run(0)
        _wait_until(lambda: recorder.count >= 1, "the run to start")
        mcu.send_heartbeat()
        mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)

        assert recorder.count == program.n_frames
        assert mcu.seq_status.frames_fired == program.n_frames
        assert mcu.seq_status.error == sp.SeqError.NONE

    def test_a_disallowed_command_is_refused_while_running(self, mcu):
        """The refusal is latched, not answered at once.

        send_position_update() reports IN_PROGRESS while a run holds the pending command,
        so the CMD_EXECUTION_ERROR the dispatcher latched only reaches the host when the
        run goes terminal (serial_communication.cpp + seq_transport_tick()).
        """
        program = stack_program(n_channels=2, n_layers=16)
        mcu.seq_upload(program)
        mcu.seq_run(0)
        _wait_until(lambda: mcu.seq_status.frames_fired >= 1, "the run to start")

        mcu.move_x_usteps(100)
        with pytest.raises(CommandAborted):
            mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)
        # the run itself was not disturbed
        assert mcu.seq_status.state == sp.SeqState.DONE
        assert mcu.seq_status.frames_fired == program.n_frames

    def test_reset_is_allowed_while_running(self, mcu):
        """seq::wire::allowed_while_running() lets RESET through: a host that restarted
        mid-run must be able to recover the controller.  seq_transport_reset() then aborts
        the run through the engine and drops the committed program.

        Not waited on with wait_till_operation_is_completed(): callback_reset() forces the
        firmware's cmd_id back to 0 (and Microcontroller.reset() mirrors that), which the
        serial simulators have never modelled -- they echo the command's own id.  That gap
        predates the sequencer and is orthogonal to it.
        """
        mcu.seq_upload(stack_program(n_channels=2, n_layers=16))
        mcu.seq_run(0)
        _wait_until(lambda: mcu.seq_status.frames_fired >= 1, "the run to start")

        mcu.reset()
        # Wait on the HOST's view: the simulator flips state before the read thread has
        # consumed the packet that carries it.
        _wait_until(lambda: mcu.seq_status.state == sp.SeqState.FAILED, "the host to see the aborted run")

        assert mcu.seq_status.error == sp.SeqError.HOST_ABORT
        assert not sequencer_of(mcu).running()
        assert sequencer_of(mcu).committed_program is None

    def test_turn_off_all_ports_aborts_the_run(self, mcu):
        """The safety command itself SUCCEEDS; the run fails behind it.

        sequence_commands.cpp marks this a quiet_abort, so
        wait_till_operation_is_completed() returning is NOT enough to conclude the run
        succeeded -- the caller has to read seq_status.
        """
        recorder = TriggerRecorder()
        sequencer_of(mcu).on_hardware_trigger = recorder
        program = stack_program(n_channels=2, n_layers=16)

        mcu.seq_upload(program)
        mcu.seq_run(0)
        _wait_until(lambda: recorder.count >= 1, "the run to start")
        mcu.turn_off_all_ports()
        mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)  # must NOT raise

        assert mcu.seq_status.error == sp.SeqError.HOST_ABORT
        assert mcu.seq_status.state == sp.SeqState.FAILED
        assert recorder.count < program.n_frames

    def test_a_second_run_while_running_is_refused(self, mcu):
        mcu.seq_upload(stack_program(n_channels=2, n_layers=16))
        mcu.seq_run(0)
        _wait_until(lambda: mcu.seq_status.frames_fired >= 1, "the run to start")

        mcu.seq_run(0)
        with pytest.raises(CommandAborted):
            mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)


class TestTimeline:
    def test_the_run_takes_about_the_programmed_time_scaled_down(self, mcu):
        exposure_us, readout_us, strobe_us = 500000, 100000, 300
        program = minimal_program(
            loop=minimal_program().loop.replace(n_layers=10, z_settle_us=0, return_to_start=False),
            channels=(sp.SeqChannelSpec(exposure_us=exposure_us, camera_mask=1),),
            cameras=(
                sp.SeqCameraSpec(
                    trigger_mode=sp.TriggerMode.LEVEL, strobe_delay_us=strobe_us, readout_time_us=readout_us
                ),
            ),
        )
        programmed_s = program.n_frames * (exposure_us + readout_us + strobe_us) / 1e6

        mcu.seq_upload(program)
        start = time.time()
        mcu.seq_run(0)
        mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)
        elapsed = time.time() - start

        # Lower bound only: CI runners are slow, but the timeline must not be free.
        assert elapsed >= 0.5 * programmed_s / TEST_SPEED_UP

    def test_close_stops_a_running_sequence(self, mcu):
        mcu.seq_upload(stack_program(n_channels=2, n_layers=200))
        mcu.seq_run(0)
        _wait_until(lambda: mcu.seq_status.frames_fired >= 1, "the run to start")

        sequencer_of(mcu).close()
        assert not sequencer_of(mcu).running()


class TestSimulatedSequencerUnit:
    """Direct tests of the shared sequencer, without a serial simulator around it."""

    def sequencer(self):
        return seq_sim.SimulatedSequencer(emit_status_packet=lambda: None)

    def test_a_write_past_the_staging_area_is_refused(self):
        sequencer = self.sequencer()
        command = bytearray(8)
        command[1] = CMD_SET.SEQ_WRITE
        command[2] = sp.STAGING_BYTES // sp.WORD_BYTES  # one word too far
        outcome = sequencer.dispatch(bytes(command))

        assert outcome.status == CMD_EXECUTION_STATUS.CMD_EXECUTION_ERROR
        assert outcome.handled
        assert sequencer.status().error == sp.SeqError.BAD_PROGRAM

    def test_a_commit_longer_than_the_staging_area_is_refused(self):
        sequencer = self.sequencer()
        length = sp.STAGING_BYTES + 4
        command = bytearray(8)
        command[1] = CMD_SET.SEQ_COMMIT
        command[2] = length >> 8
        command[3] = length & 0xFF
        outcome = sequencer.dispatch(bytes(command))

        assert outcome.status == CMD_EXECUTION_STATUS.CMD_EXECUTION_ERROR
        assert sequencer.status().error == sp.SeqError.BAD_PROGRAM

    def test_a_non_sequencer_command_is_not_handled_here(self):
        sequencer = self.sequencer()
        command = bytearray(8)
        command[1] = CMD_SET.MOVE_X
        outcome = sequencer.dispatch(bytes(command))

        assert not outcome.handled
        assert outcome.status == CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS

    def test_turn_off_all_ports_is_not_handled_here_so_the_ports_still_turn_off(self):
        sequencer = self.sequencer()
        command = bytearray(8)
        command[1] = CMD_SET.TURN_OFF_ALL_PORTS
        outcome = sequencer.dispatch(bytes(command))

        assert not outcome.handled
