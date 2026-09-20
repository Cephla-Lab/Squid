import json
import os
import time
from typing import List, Dict, Optional, Tuple, Union

import squid.logging
import control._def
from control._def import *
from control.microcontroller import CommandAborted, Microcontroller
from squid.abc import AbstractFilterWheelController, FilterWheelInfo
from squid.config import SquidFilterWheelConfig


_log = squid.logging.get_logger(__name__)


# ---------------------------------------------------------------------------------------------------------
# Where the wheel is, across a restart.
#
# TEMPORARY, TO BE REPLACED BY A POSITION READBACK. The controller does not report the wheel's position: the
# status packet carries X, Y and Z only, and there is no command that returns W. The slot and the turn count are
# therefore this process's own bookkeeping. A `--skip-init` restart (GUI "restart" after a settings change) does
# not reset the controller and must not move the wheel - the system is meant to stay on the same channel - so the
# new process has to be told where the old one left the wheel. It is told through this file, written after every
# successful move and home and read back on a skip_init start. Limits, all of which a readback would remove:
#   * a wheel turned by hand, or a controller power-cycled between the two processes, is not noticed here (the
#     second is caught by the firmware rejecting the first move, which re-homes);
#   * a process that dies mid-move leaves the previous move's record. The first move after a restore is
#     therefore always sent, never skipped as "already there": moves are absolute, so the wheel cannot end on
#     the wrong filter, and the turn count can be off by at most that one move.
# Replace with: firmware >= 1.6 can report W through SET_ENCODER_REPORTING (ENC_POS and ENC_POS - XACTUAL); a
# permanent W field in the status packet would let every host read it for free.
# ---------------------------------------------------------------------------------------------------------
_WHEEL_CACHE_PATH = "cache/filter_wheel_position.json"
_MAX_WHEEL_CACHE_BYTES = 4096


def load_cached_wheel_state(cache_path: Optional[str] = None) -> Dict[int, Tuple[int, int]]:
    """{wheel_id: (slot, turns)} written by cache_wheel_state(), or {} when there is no usable file. A file
    that cannot be read or parsed is treated exactly like a missing one, as the stage position cache does:
    an unreadable cache must not stop the software from starting."""
    cache_path = cache_path or _WHEEL_CACHE_PATH  # resolved at call time, so tests can redirect it
    if not os.path.isfile(cache_path):
        return {}
    try:
        with open(cache_path, "r") as f:
            contents = f.read(_MAX_WHEEL_CACHE_BYTES + 1)
        if len(contents) > _MAX_WHEEL_CACHE_BYTES:
            raise ValueError(f"file is larger than {_MAX_WHEEL_CACHE_BYTES} bytes")
        data = json.loads(contents)
        state = {}
        for wheel_id, entry in data["wheels"].items():
            slot, turns = entry["position"], entry["turns"]
            if type(slot) is not int or type(turns) is not int:  # bool is an int subclass: reject it too
                raise ValueError(f"wheel {wheel_id}: position and turns must be integers")
            state[int(wheel_id)] = (slot, turns)
        return state
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError) as e:
        _log.warning(
            f"Filter wheel position cache '{cache_path}' is unusable ({e!r}); continuing as if there were none."
        )
        return {}


def cache_wheel_state(state: Dict[int, Tuple[int, int]], cache_path: Optional[str] = None) -> None:
    """Write {wheel_id: (slot, turns)} atomically. Never raises: failing to write the record must not fail the
    filter change that was just completed."""
    cache_path = cache_path or _WHEEL_CACHE_PATH
    try:
        os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
        tmp_path = f"{cache_path}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(
                {"version": 1, "wheels": {str(k): {"position": p, "turns": t} for k, (p, t) in sorted(state.items())}},
                f,
            )
        os.replace(tmp_path, cache_path)
    except OSError as e:
        _log.warning(f"Could not write the filter wheel position cache '{cache_path}': {e}")


class SquidFilterWheel(AbstractFilterWheelController):
    """SQUID filter wheel controller supporting multiple filter wheels.

    Each wheel is identified by a wheel_id (typically 1, 2, etc.) and has its own
    configuration including motor_slot_index which determines which hardware axis to use:
    - motor_slot_index 3 -> W axis (first filter wheel)
    - motor_slot_index 4 -> W2 axis (second filter wheel)

    Note: W and W2 share the same motor settings (microstepping, current, velocity,
    acceleration, screw pitch) as they use identical hardware.
    """

    def __init__(
        self,
        microcontroller: Microcontroller,
        configs: Union[SquidFilterWheelConfig, Dict[int, SquidFilterWheelConfig]],
        skip_init: bool = False,
    ):
        """Initialize the SQUID filter wheel controller.

        Args:
            microcontroller: The microcontroller instance for hardware control.
            configs: Either a single SquidFilterWheelConfig (backward compatible) or
                     a dict mapping wheel_id -> SquidFilterWheelConfig for multi-wheel support.
            skip_init: If True, skip hardware initialization (for restart after settings change).
        """
        if microcontroller is None:
            raise Exception("Error, microcontroller is needed by the SquidFilterWheel")

        self.microcontroller = microcontroller

        # Read through the module rather than the `from control._def import *` binding above:
        # that binding is taken at import time and would not see an ini override.
        self.wrap = self._parse_wrap(control._def.SQUID_FILTERWHEEL_WRAP)

        # Fail loudly on a host/firmware version mismatch before any moves
        # are issued — runs unconditionally (including the skip_init restart
        # path) because firmware could have been re-flashed between launches.
        fw = self.microcontroller.firmware_version
        _log.info(f"SquidFilterWheel.__init__: firmware v{fw[0]}.{fw[1]}, skip_init={skip_init}")
        if fw < self._MIN_FIRMWARE_VERSION:
            min_major, min_minor = self._MIN_FIRMWARE_VERSION
            raise RuntimeError(
                f"SquidFilterWheel requires firmware >= v{min_major}.{min_minor} "
                f"(got v{fw[0]}.{fw[1]}). Older firmware does not anchor the "
                f"filter-wheel driver position to 0 after homing, so absolute "
                f"MOVETO targets would land at the wrong slot; the W2 MOVETO "
                f"command also does not exist on older firmware. Re-flash "
                f"firmware from firmware/controller."
            )

        # Convert single config to dict format for uniform handling
        if isinstance(configs, SquidFilterWheelConfig):
            self._configs: Dict[int, SquidFilterWheelConfig] = {1: configs}
        else:
            self._configs = configs

        # Track per-wheel positions (wheel_id -> position index) and, for shortest-path
        # moves that cross the index flag, the number of whole turns the driver's
        # coordinate has accumulated since homing (wheel_id -> int, may be negative).
        self._positions: Dict[int, int] = {}
        self._turns: Dict[int, int] = {}
        # Whether the tracked slot can be believed. False from construction until a successful home (or a
        # restored record), and again after a failed home or recovery. A move on a wheel whose position is
        # unknown homes first. See the note above load_cached_wheel_state().
        self._position_known: Dict[int, bool] = {}
        # Set by a restore: the record may be one move old, so the next move is always sent.
        self._restored_unverified: Dict[int, bool] = {}

        for wheel_id, config in self._configs.items():
            _log.info(
                f"Filter wheel {wheel_id}: motor_slot={config.motor_slot_index}, "
                f"range=[{config.min_index},{config.max_index}], offset={config.offset}mm"
            )

        if not skip_init:
            # Configure each wheel
            for wheel_id, config in self._configs.items():
                self._configure_wheel(wheel_id, config)
                # Unknown until homed: INITFILTERWHEEL re-initialised the axis, prepare_for_use() homes it.
                self._set_unknown(wheel_id)
        else:
            # Restart: the controller was not reset and the wheel has not moved, so the system stays on the
            # same channel - provided this process learns where the previous one left the wheel. Nothing is
            # assumed: no usable record means unknown, and an unknown wheel is homed before it is used.
            cached = load_cached_wheel_state()
            for wheel_id, config in self._configs.items():
                slot, turns = cached.get(wheel_id, (None, None))
                if slot is not None and config.min_index <= slot <= config.max_index:
                    self._positions[wheel_id] = slot
                    self._turns[wheel_id] = turns
                    self._position_known[wheel_id] = True
                    self._restored_unverified[wheel_id] = True
                    _log.info(f"skip_init=True: filter wheel {wheel_id} restored at slot {slot}, turn {turns}")
                else:
                    self._set_unknown(wheel_id)
                    _log.warning(
                        f"skip_init=True: no usable position record for filter wheel {wheel_id} "
                        f"(cached={cached.get(wheel_id)}); it will be homed before it is used"
                    )

        self._available_filter_wheels: List[int] = []

    # Map motor_slot_index to AXIS protocol constants for MCU communication.
    # Note: These are PROTOCOL constants (AXIS.W=5, AXIS.W2=6), NOT firmware array indices.
    # The firmware has a separate mapping: w=3, w2=4 for internal arrays.
    # The protocol_axis_to_internal() function in firmware handles this conversion.
    _MOTOR_SLOT_TO_AXIS = {3: AXIS.W, 4: AXIS.W2}

    # Map motor_slot_index to the Microcontroller method names that drive
    # that axis. Keeps the slot→method dispatch in one place instead of
    # branching `if motor_slot == 3 / == 4` at every call site.
    _MOTOR_SLOT_MCU_METHODS = {
        3: {"home": "home_w", "move_to_usteps": "move_w_to_usteps"},
        4: {"home": "home_w2", "move_to_usteps": "move_w2_to_usteps"},
    }

    _RECOVERABLE_MOVE_ERRORS = (TimeoutError, CommandAborted)

    # Minimum firmware that anchors the W/W2 driver position to 0 at home
    # (finalize_homing_w/_w2) and reports CMD_EXECUTION_ERROR on failed
    # moves. Sending MOVETO_W against older firmware would target the
    # wrong absolute slot because X_ACTUAL would still be at the
    # limit-switch latch value.
    _MIN_FIRMWARE_VERSION = (1, 2)

    # Shortest-path slot changes. The wheel is rotary with no end stop, so a slot change can take the
    # shorter way round, crossing the index flag: 8 -> 1 is one slot, not seven. The driver coordinate
    # stays continuous across the flag via a per-wheel turn counter; homing re-anchors it.
    #
    # Three states, from the squid_filterwheel_wrap ini key (control._def.SQUID_FILTERWHEEL_WRAP):
    #   "auto" (default)  on when the controller runs firmware >= 1.6, off below
    #   True              on from firmware 1.4 (the first that accepts the negative targets a backward wrap
    #                     lands on) - for a machine where a 1 -> 8 move has been seen to complete
    #   False             always the flag-free arc, as before
    # Why 1.6 for "auto": crossing the flag was verified on the bench with firmware 1.6 (2026-09-07, Squid+
    # 8-slot wheel, encoder streamed: 54 slot changes incl. nine 8 <-> 1 wraps, drift 1 ustep). The reason the
    # flag does not stop the wheel - enableHomingLimit() makes STOPL the home reference, whose stop needs home
    # tracking that the firmware never starts - is the same code on 1.4 and 1.5, but no wheel has been watched
    # crossing its flag there. The host cannot see which driver chip a controller carries, and does not need
    # to: the flag is the TMC4361A's business, not the driver's, and firmware 1.6 ships on the TMC2240
    # controllers only.
    wrap = "auto"

    # Ceiling on the net turn count before the wheel is re-homed. Shortest-path slot changes
    # that net to a full turn (1 -> 4 -> 7 -> 1 on an 8-slot wheel) add one turn per cycle, and
    # _plan_move's absolute target grows with it, so the driver coordinate would drift for as
    # long as the machine runs. Four bytes of MOVETO payload hold roughly 168k turns, past
    # which Microcontroller._move_axis_to_usteps raises ValueError - not a recoverable move
    # error, so it would surface as a crash rather than a retry. 10000 turns is far below that
    # and still hundreds of thousands of slot changes apart, so the ~4 s re-home is not a cost
    # any real session notices.
    REHOME_AFTER_TURNS: int = 10000

    def _set_unknown(self, wheel_id: int):
        """Forget where the wheel is, here and in the record a restart would read. The tracked slot keeps a
        placeholder (min_index, where a home leaves it) so the getters keep their shape; position_is_known()
        says whether to believe it."""
        self._positions[wheel_id] = self._configs[wheel_id].min_index
        self._turns[wheel_id] = 0
        self._position_known[wheel_id] = False
        self._restored_unverified[wheel_id] = False
        self._persist()

    def _persist(self):
        """Record the wheels whose position is known. An unknown wheel is left out, so a restart homes it."""
        cache_wheel_state(
            {i: (self._positions[i], self._turns.get(i, 0)) for i in self._configs if self._position_known.get(i)}
        )

    def position_is_known(self, wheel_id: Optional[int] = None) -> bool:
        """True when the wheel (or, with no argument, every configured wheel) has been homed by this process
        or restored from the previous one's record, and nothing has failed since."""
        ids = [wheel_id] if wheel_id is not None else list(self._configs)
        return all(self._position_known.get(i, False) for i in ids)

    def _configure_wheel(self, wheel_id: int, config: SquidFilterWheelConfig):
        """Configure a single filter wheel motor."""
        motor_slot = config.motor_slot_index
        axis = self._MOTOR_SLOT_TO_AXIS.get(motor_slot)
        if axis is None:
            raise ValueError(f"Unsupported motor_slot_index: {motor_slot}. Expected 3 (W) or 4 (W2).")

        self.microcontroller.init_filter_wheel(axis)
        time.sleep(0.5)
        self.microcontroller.configure_squidfilter(axis)
        time.sleep(0.5)

        # Common PID setup for both wheels (they share identical encoder settings)
        # Use protocol axis (AXIS.W / AXIS.W2), not motor_slot index (3 / 4),
        # because the firmware's protocol_axis_to_internal() handles mapping.
        if HAS_ENCODER_W:
            self.microcontroller.set_pid_arguments(axis, PID_P_W, PID_I_W, PID_D_W)
            self.microcontroller.configure_stage_pid(axis, config.transitions_per_revolution, ENCODER_FLIP_DIR_W)
            self.microcontroller.turn_on_stage_pid(axis, ENABLE_PID_W)

    @staticmethod
    def _delta_to_usteps(delta_mm: float) -> int:
        """Microsteps the firmware will be commanded to step for `delta_mm` mm.

        Includes STAGE_MOVEMENT_SIGN_W so the result already accounts for
        which direction the motor needs to drive to advance through slots.
        """
        return int(
            STAGE_MOVEMENT_SIGN_W * delta_mm / (SCREW_PITCH_W_MM / (MICROSTEPPING_DEFAULT_W * FULLSTEPS_PER_REV_W))
        )

    # "auto" turns wrapping on from the firmware it was verified on; an explicit True needs only the
    # firmware that accepts a backward wrap's negative targets (before 1.4 xmin stays at the latch).
    _WRAP_AUTO_MIN_FIRMWARE = (1, 6)
    _WRAP_MIN_FIRMWARE = (1, 4)

    @staticmethod
    def _parse_wrap(value):
        """The ini value as one of "auto", True, False. Anything else is a configuration error: a typo
        must not silently become 'on' (bool("off") is True)."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() == "auto":
            return "auto"
        raise ValueError(f"squid_filterwheel_wrap must be auto, True or False, not {value!r}")

    def _wrap_enabled(self) -> bool:
        wrap = self._parse_wrap(self.wrap)
        if wrap is False:
            return False
        minimum = self._WRAP_AUTO_MIN_FIRMWARE if wrap == "auto" else self._WRAP_MIN_FIRMWARE
        return tuple(self.microcontroller.firmware_version) >= minimum

    @staticmethod
    def _usteps_per_turn() -> int:
        return SquidFilterWheel._delta_to_usteps(SCREW_PITCH_W_MM)

    @staticmethod
    def _shortest_slot_delta(delta: int, slots: int) -> int:
        """Signed slot delta with the smaller magnitude around the circle; a half-turn tie goes forward."""
        d = delta % slots
        if 2 * d > slots:  # more than half a turn forward: go backward instead; an exact half turn goes forward
            d -= slots
        return d

    def _plan_move(self, wheel_id: int, target_pos: int):
        """Absolute driver target (usteps) and the turn count it lands on, for a move from the tracked
        position to `target_pos`. With wrapping enabled the shorter way round is taken, crossing the
        index flag when that is shorter; the turn counter keeps the driver coordinate continuous."""
        config = self._configs[wheel_id]
        slots = config.max_index - config.min_index + 1
        current_pos = self._positions[wheel_id]
        delta = target_pos - current_pos
        if self._wrap_enabled():
            delta = self._shortest_slot_delta(delta, slots)
        linear = self._turns.get(wheel_id, 0) * slots + (current_pos - config.min_index) + delta
        turns, idx = divmod(linear, slots)
        usteps = self._target_pos_to_usteps(config, config.min_index + idx) + turns * self._usteps_per_turn()
        return usteps, turns

    @staticmethod
    def _target_pos_to_usteps(config: SquidFilterWheelConfig, target_pos: int) -> int:
        """Absolute target microstep address for a given slot index.

        Assumes the firmware anchors X_ACTUAL to 0 at the home reference
        (see finalize_homing_w / finalize_homing_w2 in firmware).
        """
        step_size_mm = SCREW_PITCH_W_MM / (config.max_index - config.min_index + 1)
        target_mm_from_home = config.offset + (target_pos - config.min_index) * step_size_mm
        return SquidFilterWheel._delta_to_usteps(target_mm_from_home)

    def _mcu_method(self, wheel_id: int, action: str):
        """Resolve the Microcontroller method for `action` on this wheel's axis."""
        motor_slot = self._configs[wheel_id].motor_slot_index
        methods = self._MOTOR_SLOT_MCU_METHODS.get(motor_slot)
        if methods is None:
            raise ValueError(f"Unsupported motor_slot_index: {motor_slot}. Expected 3 (W) or 4 (W2).")
        return getattr(self.microcontroller, methods[action])

    def _move_to_usteps(self, wheel_id: int, usteps: int):
        """Dispatch an absolute MOVETO_W / MOVETO_W2 by motor_slot_index."""
        self._mcu_method(wheel_id, "move_to_usteps")(usteps)

    def _move_to_usteps_with_resend(self, wheel_id: int, usteps: int):
        """Issue an absolute MOVETO and wait; on a *recoverable* CommandAborted,
        ack and resend the identical MOVETO once.

        Only a recoverable abort (firmware-reported CMD_EXECUTION_ERROR) is
        resent: it means the firmware rejected the move before the motor moved
        (e.g. tmc4361A_moveTo returned non-zero, or the move arrived before
        INITFILTERWHEEL), so resending the same absolute target is safe and
        avoids the ~4 s re-home cost. A non-recoverable abort (ack timeout /
        checksum failure after retries) leaves the motor state uncertain and is
        re-raised — as is a TimeoutError and a failed resend — so the caller
        decides whether to re-home. The pending abort is acknowledged before
        the resend so the next send_command doesn't log a spurious "not cleared
        before new command sent" warning.
        """
        try:
            self._move_to_usteps(wheel_id, usteps)
            self.microcontroller.wait_till_operation_is_completed()
            return
        except CommandAborted as e:
            if not e.recoverable:
                raise
            _log.warning(f"Filter wheel {wheel_id} move aborted ({e}); resending in software...")
            self.microcontroller.acknowledge_aborted_command()
        self._move_to_usteps(wheel_id, usteps)
        self.microcontroller.wait_till_operation_is_completed()
        _log.info(f"Filter wheel {wheel_id} software resend succeeded")

    def _move_to_position(self, wheel_id: int, target_pos: int):
        """Move wheel to target position using absolute MOVETO; recover on failure.

        Recovery is conditioned on failure type, because the absolute-move
        approach already self-corrects on the next *successful* command —
        the goal here is just to make sure that next command actually goes
        out, with the right re-home cost.

        - CommandAborted (CMD_EXECUTION_ERROR): firmware rejected the move
          before the motor moved (e.g. tmc4361A_moveTo returned non-zero
          or move arrived before INITFILTERWHEEL). The motor didn't move;
          a plain resend is safe and avoids the ~4 s re-home cost.
        - TimeoutError (ack never arrived): motor state is uncertain (could
          be partially moved). Re-home to re-anchor the coordinate frame,
          then retry to the same absolute target.

        Raises:
            TimeoutError or CommandAborted: If all attempts fail.
        """
        config = self._configs[wheel_id]

        if not self._position_known.get(wheel_id, False):
            _log.info(f"Filter wheel {wheel_id}: position unknown, homing before the move")
            self._home_wheel(wheel_id)
        current_pos = self._positions[wheel_id]

        # "Already there" is only concluded from a position this process has established itself. The first
        # move after a restore is sent regardless: the record may be one move old, the move is absolute, and
        # sending it to a wheel that is already there costs one command.
        if target_pos == current_pos and not self._restored_unverified.get(wheel_id, False):
            return

        # Keep the driver coordinate bounded (see REHOME_AFTER_TURNS). Homing re-anchors both
        # the tracked position and the turn count, so the move is then planned from scratch.
        turns = self._turns.get(wheel_id, 0)
        if abs(turns) >= self.REHOME_AFTER_TURNS:
            _log.info(
                f"filter wheel {wheel_id}: re-homing after {turns} net turns to keep the driver coordinate bounded"
            )
            self._home_wheel(wheel_id)
            current_pos = self._positions[wheel_id]

        target_usteps, target_turns = self._plan_move(wheel_id, target_pos)
        _log.info(
            f"Filter wheel {wheel_id}: {current_pos} -> {target_pos} (usteps={target_usteps}, turns={target_turns})"
        )

        try:
            self._move_to_usteps_with_resend(wheel_id, target_usteps)
            self._positions[wheel_id] = target_pos
            self._turns[wheel_id] = target_turns
            self._restored_unverified[wheel_id] = False
            self._persist()
            return
        except self._RECOVERABLE_MOVE_ERRORS as e:
            # CMD_EXECUTION_ERROR survived a resend, or the ack never arrived
            # (motor state uncertain) — re-home to re-anchor the coordinate
            # frame, then retry to the same absolute target.
            _log.warning(f"Filter wheel {wheel_id} move failed ({e}); re-homing to re-sync...")

        # Clear any pending abort (set when wait_till_operation_is_completed
        # raised CommandAborted) so the home command's send_command doesn't
        # log a spurious "not cleared before new command sent" warning.
        if self.microcontroller.last_command_aborted_error is not None:
            self.microcontroller.acknowledge_aborted_command()
        self._home_wheel(wheel_id)
        # Homing re-anchored the coordinate (turns = 0): plan the retry from there.
        target_usteps, target_turns = self._plan_move(wheel_id, target_pos)
        try:
            self._move_to_usteps(wheel_id, target_usteps)
            self.microcontroller.wait_till_operation_is_completed()
            self._positions[wheel_id] = target_pos
            self._turns[wheel_id] = target_turns
            self._persist()
            _log.info(f"Filter wheel {wheel_id} recovery via re-home succeeded, now at position {target_pos}")
        except self._RECOVERABLE_MOVE_ERRORS:
            _log.error(f"Filter wheel {wheel_id} movement failed even after re-home. Hardware may need attention.")
            self._set_unknown(wheel_id)  # the ack never came or the move was refused: do not trust the record
            raise

    def _home_wheel(self, wheel_id: int):
        """Home a wheel, then drive to its first slot (config.min_index) absolutely.

        The firmware anchors the driver's X_ACTUAL counter to 0 at the
        home reference, so the host can target absolute slot positions
        as `slot_index * usteps_per_slot + offset_usteps` thereafter.

        On failure, `_positions[wheel_id]` is *not* updated and may now be
        stale relative to physical hardware — callers should treat the
        wheel's position as unknown until a successful home completes.
        """
        config = self._configs[wheel_id]
        _log.info(f"Homing filter wheel {wheel_id} (prev tracked={self._positions.get(wheel_id)})")
        home_start = time.monotonic()
        # Unknown until this home has fully succeeded; the record is withdrawn now, so a process that dies
        # during the home does not leave a restart believing the pre-home slot.
        self._position_known[wheel_id] = False
        self._restored_unverified[wheel_id] = False
        self._persist()

        try:
            self._mcu_method(wheel_id, "home")()
            self.microcontroller.wait_till_operation_is_completed(15)
        except Exception:
            _log.error(
                f"Filter wheel {wheel_id} home command failed; physical "
                f"position is unknown and tracked position may be stale."
            )
            raise

        # The post-home offset move is subject to the same recoverable
        # CMD_EXECUTION_ERROR as any slot move, so resend once on abort (see
        # _move_to_usteps_with_resend). Re-homing is not a recovery option
        # here — we are already inside the home path — so a failed resend or
        # any other error propagates. Homing runs uncaught during startup, so
        # without this resend a single transient abort would crash launch.
        try:
            self._move_to_usteps_with_resend(wheel_id, self._delta_to_usteps(config.offset))
        except Exception:
            _log.error(
                f"Filter wheel {wheel_id} home succeeded but offset move failed; "
                f"wheel is at the home reference, not slot {config.min_index}. "
                f"Tracked position not updated."
            )
            raise

        self._positions[wheel_id] = config.min_index
        self._turns[wheel_id] = 0
        self._position_known[wheel_id] = True
        self._persist()
        _log.info(f"Filter wheel {wheel_id} homed in {time.monotonic() - home_start:.2f}s")

    def initialize(self, filter_wheel_indices: List[int]):
        """Initialize the filter wheel controller with the given wheel indices.

        Args:
            filter_wheel_indices: List of wheel indices to activate.
        """
        # Validate that all requested wheels are configured
        for idx in filter_wheel_indices:
            if idx not in self._configs:
                raise ValueError(f"Filter wheel index {idx} is not configured")
        self._available_filter_wheels = filter_wheel_indices

    @property
    def available_filter_wheels(self) -> List[int]:
        return self._available_filter_wheels

    def get_filter_wheel_info(self, index: int) -> FilterWheelInfo:
        """Get information about a specific filter wheel.

        Args:
            index: The wheel index.

        Returns:
            FilterWheelInfo with slot count and names.
        """
        if index not in self._configs:
            raise ValueError(f"Filter wheel index {index} not found")

        config = self._configs[index]
        return FilterWheelInfo(
            index=index,
            number_of_slots=config.max_index - config.min_index + 1,
            slot_names=[str(i) for i in range(config.min_index, config.max_index + 1)],
        )

    def home(self, index: Optional[int] = None):
        """Home filter wheel(s).

        Args:
            index: Specific wheel index to home. If None, homes all configured wheels.
        """
        if index is not None:
            if index not in self._configs:
                raise ValueError(f"Filter wheel index {index} not found")
            self._home_wheel(index)
        else:
            # Home all wheels
            for wheel_id in self._configs.keys():
                self._home_wheel(wheel_id)

    def _step_position(self, wheel_id: int, direction: int):
        """Move position by one step in the given direction.

        Args:
            wheel_id: The ID of the wheel to move.
            direction: +1 for next position, -1 for previous position.
        """
        if wheel_id not in self._configs:
            raise ValueError(f"Filter wheel index {wheel_id} not found")

        config = self._configs[wheel_id]
        current_pos = self._positions[wheel_id]
        new_pos = current_pos + direction
        slots = config.max_index - config.min_index + 1

        if self._wrap_enabled():
            new_pos = config.min_index + (new_pos - config.min_index) % slots
        if config.min_index <= new_pos <= config.max_index:
            self._move_to_position(wheel_id, new_pos)

    def next_position(self, wheel_id: int = 1):
        """Move to the next position on a wheel.

        Args:
            wheel_id: The wheel to move (defaults to 1 for backward compatibility).
        """
        self._step_position(wheel_id, 1)

    def previous_position(self, wheel_id: int = 1):
        """Move to the previous position on a wheel.

        Args:
            wheel_id: The wheel to move (defaults to 1 for backward compatibility).
        """
        self._step_position(wheel_id, -1)

    def set_filter_wheel_position(self, positions: Dict[int, int]):
        """Set filter wheel positions.

        Args:
            positions: Dict mapping wheel_id -> target position.
                       Position values are 1-indexed (typically 1-8).
        """
        for wheel_id, pos in positions.items():
            if wheel_id not in self._configs:
                raise ValueError(f"Filter wheel index {wheel_id} not found")

            config = self._configs[wheel_id]
            if pos not in range(config.min_index, config.max_index + 1):
                raise ValueError(f"Filter wheel {wheel_id} position {pos} is out of range")

            self._move_to_position(wheel_id, pos)

    def get_filter_wheel_position(self) -> Dict[int, int]:
        """Get current positions of all configured wheels.

        Returns:
            Dict mapping wheel_id -> current position.
        """
        return dict(self._positions)

    def set_delay_offset_ms(self, delay_offset_ms: float):
        """Set delay offset (not used by SQUID filter wheel)."""
        pass

    def get_delay_offset_ms(self) -> Optional[float]:
        """Get delay offset (always 0 for SQUID filter wheel)."""
        return 0

    def set_delay_ms(self, delay_ms: float):
        """Set delay (not used by SQUID filter wheel)."""
        pass

    def get_delay_ms(self) -> Optional[float]:
        """Get delay (always 0 for SQUID filter wheel)."""
        return 0

    def close(self):
        """Close the filter wheel controller (no-op for SQUID)."""
        pass
