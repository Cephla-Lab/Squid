"""Simulated hardware sequencer, shared by SimSerial and FirmwareSimSerial.

This is the MCU side of the contract in control/sequencer_program.py: it keeps a staging
buffer, validates SEQ_COMMIT the way seq::wire::parse_staging() + seq::validate() do, and
steps a coarse frame timeline on a background thread so `--simulation` and pytest can run a
real sequenced acquisition.

It is the missing link between a simulated microcontroller and a simulated camera: every
frame calls `on_hardware_trigger(camera_id)`, which the simulation Microscope wires to
SimulatedCamera.emit_hardware_triggered_frame().

Timeline fidelity is deliberately coarse.  Per frame it dwells
(strobe_delay + exposure + readout) of the camera(s) in the channel's mask, divided by
SPEED_UP_FACTOR.  It does not model ready lines, min trigger period, readout overlap or
per-step settling; the firmware's own native tests cover the engine's timing.

Threading contract
------------------
`dispatch()` is called by the serial simulator while it holds ITS lock, and takes this
object's lock briefly.  The run thread takes this object's lock for state changes and then
calls `emit_status_packet()` WITHOUT holding it, so the lock order is always
serial lock -> sequencer lock.  `on_hardware_trigger` is called with no lock held at all,
because it reaches into camera code.  `close()` joins the run thread, so the serial
simulator must call it before taking its own lock.
"""

import dataclasses
import struct
import threading
import time
from typing import Callable, List, Optional, Tuple

import squid.logging
import control.sequencer_program as sp
from control._def import CMD_EXECUTION_STATUS, CMD_SET

#: Divisor for the simulated frame timeline.  1.0 runs the programmed frame times in real
#: time, which is what `--simulation` wants; tests raise it so a 32-frame stack takes
#: milliseconds.  Read at call time, so monkeypatching the module attribute works.
SPEED_UP_FACTOR = 1.0

#: How often a running sequence emits a status packet.  The firmware broadcasts every 10 ms.
STATUS_INTERVAL_S = 0.01

#: What the firmware's dispatcher allow-table lets through while a sequence runs (design
#: S8).  Everything else is answered with CMD_EXECUTION_ERROR, because v1 tracks exactly one
#: pending command and SEQ_RUN is that command for the whole run.
ALLOWED_WHILE_RUNNING = frozenset({CMD_SET.HEARTBEAT, CMD_SET.SEQ_CANCEL, CMD_SET.TURN_OFF_ALL_PORTS})


@dataclasses.dataclass(frozen=True)
class CommandOutcome:
    """What the serial simulator should do with a command it just handed to the sequencer.

    `handled` True means the sequencer owns the command and the simulator must not run its
    own handler for it.  `status` is the CMD_EXECUTION_STATUS to answer with either way --
    the sequencer owns that byte whenever a run is in flight.
    """

    status: int
    handled: bool


def cameras_in_mask(camera_mask: int) -> List[int]:
    return [i for i in range(sp.MAX_CAMERAS) if camera_mask & (1 << i)]


class SimulatedSequencer:
    """The MCU-side sequencer, as far as the host can tell."""

    def __init__(self, emit_status_packet: Callable[[], None]):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._emit_status_packet = emit_status_packet

        self._lock = threading.Lock()
        self._staging = bytearray(sp.STAGING_BYTES)
        self._program: Optional[sp.SequencerProgram] = None

        self._state = sp.SeqState.IDLE
        self._error = sp.SeqError.NONE
        self._detail = 0
        self._frames_fired = 0

        # Mirrors the firmware's mcu_cmd_execution_in_progress / mcu_cmd_execution_status
        # globals: a run owns the pending command until the engine is terminal.
        self._cmd_in_progress = False
        self._cmd_status = CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS

        self._cancel = threading.Event()
        self._abort = threading.Event()
        self._thread: Optional[threading.Thread] = None

        #: Called once per triggered camera, per frame, from the run thread with no lock
        #: held.  The simulation Microscope points this at the simulated camera.
        self.on_hardware_trigger: Optional[Callable[[int], None]] = None

    # --- state the serial simulator and tests read ----------------------------------------

    def running(self) -> bool:
        with self._lock:
            return self._cmd_in_progress

    def status(self) -> sp.SequencerStatus:
        with self._lock:
            return sp.SequencerStatus(
                state=self._state, error=self._error, detail=self._detail, frames_fired=self._frames_fired
            )

    def status_bytes(self) -> Tuple[int, int, int, int]:
        """Response bytes 14..17."""
        return self.status().to_response_bytes()

    def execution_status(self) -> int:
        """The CMD_EXECUTION_STATUS byte the MCU reports right now."""
        with self._lock:
            if self._cmd_in_progress:
                return CMD_EXECUTION_STATUS.IN_PROGRESS
            return self._cmd_status

    @property
    def committed_program(self) -> Optional[sp.SequencerProgram]:
        with self._lock:
            return self._program

    # --- command entry point --------------------------------------------------------------

    def dispatch(self, command: bytes) -> CommandOutcome:
        """Let the sequencer see a command before the serial simulator handles it."""
        code = command[1]

        # Firmware process_serial_message() defaults the status to success on every command
        # except HEARTBEAT, whose ack must not clobber a pending failure.
        if code != CMD_SET.HEARTBEAT:
            with self._lock:
                if not self._cmd_in_progress:
                    self._cmd_status = CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS

        if self.running() and code not in ALLOWED_WHILE_RUNNING:
            self._log.warning(f"command {code} refused: a sequence is running")
            return CommandOutcome(status=CMD_EXECUTION_STATUS.CMD_EXECUTION_ERROR, handled=True)

        if code in sp.SEQ_OPCODES:
            self._handle_seq(code, command)
            return CommandOutcome(status=self.execution_status(), handled=True)

        if code == CMD_SET.TURN_OFF_ALL_PORTS and self.running():
            # Cutting the lasers behind the engine's back would let the run complete
            # "successfully" with dark frames, so it aborts through the engine instead.
            self.host_abort()

        # Not ours: the simulator handles it, but the status byte is still the MCU's one
        # global, so a run in flight keeps reporting IN_PROGRESS.
        return CommandOutcome(status=self.execution_status(), handled=False)

    def host_abort(self) -> None:
        """TURN_OFF_ALL_PORTS / serial watchdog: terminal immediately, light off."""
        self._abort.set()
        self._fail(sp.SeqError.HOST_ABORT, 0)

    def close(self) -> None:
        """Stop a run in flight and join its thread.  Must not be called under the serial
        simulator's lock: the run thread calls back into it to emit packets."""
        self._abort.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
            if thread.is_alive():
                self._log.warning("simulated sequencer run thread did not stop within 2s")
        with self._lock:
            self._cmd_in_progress = False

    # --- opcode handling ------------------------------------------------------------------

    def _handle_seq(self, code: int, command: bytes) -> None:
        if code == CMD_SET.SEQ_WRITE:
            self._seq_write(command[2], command[3:7])
        elif code == CMD_SET.SEQ_COMMIT:
            length = struct.unpack(">H", command[2:4])[0]
            crc = struct.unpack(">H", command[4:6])[0]
            self._seq_commit(length, crc)
        elif code == CMD_SET.SEQ_RUN:
            self._seq_run(struct.unpack(">i", command[2:6])[0])
        elif code == CMD_SET.SEQ_CANCEL:
            self._cancel.set()

    def _seq_write(self, word_index: int, data: bytes) -> None:
        offset = word_index * sp.WORD_BYTES
        if offset + sp.WORD_BYTES > sp.STAGING_BYTES:
            self._reject(sp.SeqError.BAD_PROGRAM, 0, f"SEQ_WRITE word {word_index} past the staging area")
            return
        with self._lock:
            self._staging[offset : offset + sp.WORD_BYTES] = data

    def _seq_commit(self, length: int, crc: int) -> None:
        if length > sp.STAGING_BYTES:
            self._reject(sp.SeqError.BAD_PROGRAM, 0, f"commit length {length} > staging area {sp.STAGING_BYTES}")
            return
        with self._lock:
            staged = bytes(self._staging[:length])
        actual_crc = sp.crc16_ccitt_false(staged)
        if actual_crc != crc:
            self._reject(sp.SeqError.BAD_PROGRAM, 0, f"CRC 0x{actual_crc:04X} != committed 0x{crc:04X}")
            return
        try:
            program = sp.unpack(staged)
            program.validate()
        except sp.ProgramValidationError as e:
            self._reject(e.error, e.detail, str(e))
            return
        with self._lock:
            self._program = program
            self._state = sp.SeqState.IDLE
            self._error = sp.SeqError.NONE
            self._detail = 0
        self._log.debug(f"committed a {program.n_channels}-channel program, {program.n_frames} frames")

    def _seq_run(self, stack_start: int) -> None:
        with self._lock:
            program = self._program
        if program is None:
            self._reject(sp.SeqError.NOT_COMMITTED, 0, "SEQ_RUN without a committed program")
            return
        try:
            program.check_stack_range(stack_start)
        except sp.ProgramValidationError as e:
            # The engine refuses the whole run before the first move: nothing is triggered.
            self._fail(e.error, e.detail)
            return

        self._cancel.clear()
        self._abort.clear()
        with self._lock:
            self._frames_fired = 0
            self._state = sp.SeqState.WAIT_HW
            self._error = sp.SeqError.NONE
            self._detail = 0
            self._cmd_in_progress = True
        self._thread = threading.Thread(
            target=self._run_timeline, args=(program, stack_start), name="SimulatedSequencer", daemon=True
        )
        self._thread.start()

    # --- failure paths --------------------------------------------------------------------

    def _reject(self, error: sp.SeqError, detail: int, reason: str) -> None:
        """A transport-level refusal (bad staging, nothing committed): the engine stays
        Idle, but the status bytes carry the SeqError."""
        self._log.warning(f"sequencer command refused: {reason}")
        with self._lock:
            self._error = error
            self._detail = detail
            self._cmd_status = CMD_EXECUTION_STATUS.CMD_EXECUTION_ERROR

    def _fail(self, error: sp.SeqError, detail: int) -> None:
        """The engine's fail(): terminal, light off, motion stopped."""
        self._log.warning(f"sequencer failed: {sp.SeqError(error).name} detail={detail}")
        with self._lock:
            self._state = sp.SeqState.FAILED
            self._error = error
            self._detail = detail
            self._cmd_in_progress = False
            self._cmd_status = CMD_EXECUTION_STATUS.CMD_EXECUTION_ERROR

    # --- the run thread -------------------------------------------------------------------

    def _run_timeline(self, program: sp.SequencerProgram, stack_start: int) -> None:
        total_steps = program.n_frames
        try:
            for step in range(total_steps):
                if self._abort.is_set():
                    return  # host_abort() already set the terminal state
                if self._cancel.is_set():
                    break  # cancel never truncates an exposure, so it stops between frames
                _layer, channel_index = program.step_to_layer_channel(step)
                channel = program.channels[channel_index]

                with self._lock:
                    self._state = sp.SeqState.EXPOSING
                    self._frames_fired += 1
                for camera_id in cameras_in_mask(channel.camera_mask):
                    trigger = self.on_hardware_trigger
                    if trigger is not None:
                        trigger(camera_id)
                self._emit()
                self._dwell(_frame_time_s(program, channel))

            if self._abort.is_set():
                return
            self._finish(program, total_steps)
        except Exception:
            self._log.exception("simulated sequencer run failed")
            self._fail(sp.SeqError.MOVE_FAILED, 0)
        finally:
            self._emit()

    def _finish(self, program: sp.SequencerProgram, total_steps: int) -> None:
        """SeqEngine::finish(): optional return move, then Done."""
        with self._lock:
            canceled = self._cancel.is_set() and self._frames_fired < total_steps
        if program.loop.return_to_start:
            with self._lock:
                self._state = sp.SeqState.RETURNING
            self._emit()
            self._dwell(program.loop.z_settle_us / 1e6)
        if self._abort.is_set():
            return
        with self._lock:
            self._state = sp.SeqState.DONE
            if canceled:
                self._error = sp.SeqError.CANCELED
            self._cmd_in_progress = False
            # A cancelled run still COMPLETED: the engine reached Done, not Failed.
            self._cmd_status = CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS

    def _dwell(self, seconds: float) -> None:
        """Sleep, emitting status packets meanwhile and returning early on abort."""
        deadline = time.time() + seconds / SPEED_UP_FACTOR
        while not self._abort.is_set():
            remaining = deadline - time.time()
            if remaining <= 0:
                return
            time.sleep(min(remaining, STATUS_INTERVAL_S))
            self._emit()

    def _emit(self) -> None:
        # Never called with self._lock held: the serial simulator takes its own lock here
        # and then reads our status back.
        self._emit_status_packet()


def _frame_time_s(program: sp.SequencerProgram, channel: sp.SeqChannelSpec) -> float:
    """Coarse per-frame dwell: the slowest camera in this channel's mask."""
    worst_us = 0
    for camera_id in cameras_in_mask(channel.camera_mask):
        camera = program.cameras[camera_id]
        worst_us = max(worst_us, camera.strobe_delay_us + channel.exposure_us + camera.readout_time_us)
    return worst_us / 1e6
