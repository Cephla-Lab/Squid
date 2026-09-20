"""Host mirror of the firmware hardware-sequencer wire contract.

The authority for everything in this module is, in order:

  firmware/controller/src/sequencer/seq_wire.h    staging layout, opcodes, status bytes
  firmware/controller/src/sequencer/seq_types.h   SeqLoop / SeqChannel, limits, SeqError
  firmware/controller/src/sequencer/seq_engine.h  SeqState
  firmware/controller/src/constants_protocol.h    the v1 opcode numbers

tests/control/test_sequencer_program.py parses those headers and fails if this file drifts
from them.  Change both or neither.

This module is deliberately pure: no Qt, no hardware imports, no control._def.  It is
imported by the microcontroller driver, by the serial simulators and by tests.

Wire notes
----------
The host stages a program with SEQ_WRITE (one 4-byte word per command, at an absolute word
offset, so a blind v1 resend is idempotent), seals it with SEQ_COMMIT (unpadded byte length
+ CRC-16/CCITT-FALSE), then sends SEQ_RUN once per FOV.  All staged structs are packed
little-endian; every multi-byte field *inside a command or response packet* is big-endian,
because that is what the v1 protocol does everywhere else.
"""

import dataclasses
import struct
from enum import IntEnum
from typing import List, Sequence, Tuple, Union

# --- opcodes (seq_wire.h kOpSeq*, constants_protocol.h SEQ_*) ------------------------------

OP_SEQ_WRITE = 60
OP_SEQ_COMMIT = 61
OP_SEQ_RUN = 62
OP_SEQ_CANCEL = 63

#: Opcodes the host may send while a sequence is running.  Anything else is rejected by the
#: firmware's dispatcher allow-table (design S8) -- the host must not send it in the first
#: place, because v1 tracks exactly one pending command.
SEQ_OPCODES = frozenset({OP_SEQ_WRITE, OP_SEQ_COMMIT, OP_SEQ_RUN, OP_SEQ_CANCEL})

# --- response status bytes (seq_wire.h kStatusByte*) ---------------------------------------
# On firmware < MIN_FIRMWARE_VERSION these four bytes are the (never written) theta position.

STATUS_BYTE_STATE = 14  # SeqState (3 b) << 5 | SeqError (5 b)
STATUS_BYTE_DETAIL = 15  # channel index / axis id the error refers to
STATUS_BYTE_FRAMES_HI = 16  # frames_fired, big-endian like the positions
STATUS_BYTE_FRAMES_LO = 17

#: First firmware version that implements this contract (seq_wire.h kFwMajor/kFwMinor).
#: Older firmware routes unknown opcodes to callback_default() and answers
#: COMPLETED_WITHOUT_ERRORS, so every seq_* call must fail loud below this (design S5).
MIN_FIRMWARE_VERSION = (1, 7)

# --- limits (seq_types.h) ------------------------------------------------------------------

WIRE_VERSION = 1
WORD_BYTES = 4
MAX_CHANNELS = 16
MAX_CAMERAS = 8
NONE_ID = 0xFF
EDGE_PULSE_US = 50
#: Upper bound for every duration the engine handles (~17.9 min).  Keeps any two engine
#: timestamps within 2^31 us of each other, which its wrap-safe comparisons require.
MAX_DURATION_US = 0x3FFFFFFF

#: SeqEngine::load() calls seq::validate() with n_axes = n_dacs = 8 (seq_engine.cpp).
N_AXES = 8
N_DACS = 8
#: A piezo stack target is a u16 DAC code; SeqEngine::stack_range_ok() refuses the run
#: outright if any layer of any channel would leave 0..65535.
PIEZO_DAC_MIN = 0
PIEZO_DAC_MAX = 65535

#: frames_fired travels as a u16 in the status packet.
MAX_FRAMES = 65535

# --- staging layout (seq_wire.h) -----------------------------------------------------------

WIRE_HEADER_FORMAT = "<BBHI"  # version, n_cameras, reserved, wait_timeout_us
SEQ_LOOP_FORMAT = "<BBiHBIBB"  # axis type, axis id, dz, n_layers, order, z_settle, ret, n_ch
SEQ_CHANNEL_FORMAT = "<BiBBBHIBiB"  # see SeqChannelSpec.pack
WIRE_CAMERA_FORMAT = "<BBBBIII"  # see SeqCameraSpec.pack

WIRE_HEADER_BYTES = struct.calcsize(WIRE_HEADER_FORMAT)  # 8
SEQ_LOOP_BYTES = struct.calcsize(SEQ_LOOP_FORMAT)  # 15
SEQ_CHANNEL_BYTES = struct.calcsize(SEQ_CHANNEL_FORMAT)  # 20
WIRE_CAMERA_BYTES = struct.calcsize(WIRE_CAMERA_FORMAT)  # 16

LOOP_OFFSET = 8  # SeqLoop is 15 B here, followed by 1 pad byte
CHANNELS_OFFSET = 24
STAGING_BYTES = CHANNELS_OFFSET + MAX_CHANNELS * SEQ_CHANNEL_BYTES + MAX_CAMERAS * WIRE_CAMERA_BYTES  # 472


def program_bytes(n_channels: int, n_cameras: int) -> int:
    """The unpadded staged length a program of this shape occupies (seq_wire.h)."""
    return CHANNELS_OFFSET + n_channels * SEQ_CHANNEL_BYTES + n_cameras * WIRE_CAMERA_BYTES


# --- enums (mirrored BY NUMBER -- see seq_types.h / seq_engine.h) --------------------------


class StackAxisType(IntEnum):
    STEPPER = 0
    PIEZO = 1


class Order(IntEnum):
    CHANNELS_INNER = 0
    Z_INNER = 1


class TriggerMode(IntEnum):
    EDGE = 0
    LEVEL = 1


class SeqError(IntEnum):
    """seq_types.h SeqError.  Values are wire format -- append only, never renumber."""

    NONE = 0
    BAD_LAYER_COUNT = 1
    BAD_CHANNEL_COUNT = 2
    BAD_STACK_AXIS = 3
    BAD_CHANNEL = 4
    BAD_CAMERA = 5
    BAD_EXPOSURE = 6
    WAIT_TIMEOUT = 7
    MOVE_FAILED = 8
    READY_TIMEOUT = 9
    CANCELED = 10
    STACK_OUT_OF_RANGE = 11
    INTERLOCK_OPEN = 12
    HOST_ABORT = 13
    BAD_DURATION = 14
    BUSY = 15
    BAD_PROGRAM = 16
    NOT_COMMITTED = 17
    EDGE_QUEUE_FULL = 18


class SeqState(IntEnum):
    """seq_engine.h SeqState.  Three bits of the status byte -- append only."""

    IDLE = 0
    PREP = 1
    WAIT_HW = 2
    EXPOSING = 3
    ABORTING = 4
    DONE = 5
    FAILED = 6
    RETURNING = 7


TERMINAL_STATES = frozenset({SeqState.DONE, SeqState.FAILED})


class ProgramValidationError(ValueError):
    """A program the firmware would reject, caught before anything is put on the wire.

    Carries the SeqError/detail pair the firmware would have reported, so the simulator can
    answer with exactly those status bytes.
    """

    def __init__(self, error: SeqError, detail: int, message: str):
        super().__init__(f"{SeqError(error).name}(detail={detail}): {message}")
        self.error = SeqError(error)
        self.detail = detail


# --- CRC-16/CCITT-FALSE --------------------------------------------------------------------


def crc16_ccitt_false(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, xorout 0.

    Check value: crc16_ccitt_false(b"123456789") == 0x29B1.
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def split_words(data: bytes) -> List[Tuple[int, bytes]]:
    """Split staged bytes into the (word index, 4 bytes) pairs SEQ_WRITE carries.

    The tail is zero padded to a word.  Real programs are always word aligned (every part of
    the layout is a multiple of 4), but nothing in the wire contract promises that.
    """
    padding = (-len(data)) % WORD_BYTES
    padded = bytes(data) + bytes(padding)
    return [(i // WORD_BYTES, padded[i : i + WORD_BYTES]) for i in range(0, len(padded), WORD_BYTES)]


def _coerce(enum_cls, value: int):
    """The enum member for this wire value, or the raw int when the firmware knows a value
    we do not.  Range rejection is validate()'s job, not the parser's."""
    try:
        return enum_cls(value)
    except ValueError:
        return value


def _error_name(value: Union[SeqError, int]) -> str:
    try:
        return SeqError(value).name
    except ValueError:
        return f"UNKNOWN({int(value)})"


class _Replaceable:
    def replace(self, **changes):
        """dataclasses.replace, as a method -- these specs are frozen."""
        return dataclasses.replace(self, **changes)


# --- program pieces ------------------------------------------------------------------------
# Field order here is readability order (required first).  The WIRE order is fixed by the
# struct format strings above and by pack()/unpack(); do not infer it from the dataclass.


@dataclasses.dataclass(frozen=True)
class SeqLoopSpec(_Replaceable):
    """seq_types.h SeqLoop, minus n_channels (derived from SequencerProgram.channels)."""

    stack_axis_type: StackAxisType
    stack_axis_id: int  # stepper axis id, or DAC id when PIEZO
    dz: int  # usteps (stepper) or DAC LSB (piezo) per layer, signed
    n_layers: int  # >= 1
    order: Order
    z_settle_us: int  # wait after the stack move reports done
    return_to_start: bool

    def pack(self, n_channels: int) -> bytes:
        return struct.pack(
            SEQ_LOOP_FORMAT,
            int(self.stack_axis_type),
            self.stack_axis_id,
            self.dz,
            self.n_layers,
            int(self.order),
            self.z_settle_us,
            int(bool(self.return_to_start)),
            n_channels,
        )

    @staticmethod
    def unpack_with_n_channels(data: bytes) -> Tuple["SeqLoopSpec", int]:
        (axis_type, axis_id, dz, n_layers, order, z_settle_us, return_to_start, n_channels) = struct.unpack(
            SEQ_LOOP_FORMAT, data
        )
        loop = SeqLoopSpec(
            stack_axis_type=_coerce(StackAxisType, axis_type),
            stack_axis_id=axis_id,
            dz=dz,
            n_layers=n_layers,
            order=_coerce(Order, order),
            z_settle_us=z_settle_us,
            return_to_start=bool(return_to_start),
        )
        return loop, n_channels


@dataclasses.dataclass(frozen=True)
class SeqChannelSpec(_Replaceable):
    """seq_types.h SeqChannel."""

    exposure_us: int  # > 0
    camera_mask: int = 1  # != 0; bit i = camera i
    illum_ttl_mask: int = 0  # TTL ports ON during exposure (0 = LED-matrix only)
    intensity_dac: int = NONE_ID  # NONE_ID, or DAC id (pre-armed during previous readout)
    intensity: int = 0  # DAC value
    led_pattern: int = NONE_ID  # NONE_ID, or LED-matrix pattern id
    filter_wheel: int = NONE_ID  # NONE_ID, or wheel axis id
    filter_target: int = 0  # absolute microsteps (host owns slot -> ustep mapping)
    z_offset: int = 0  # per-channel stack-axis offset
    flags: int = 0  # reserved, 0

    def pack(self) -> bytes:
        return struct.pack(
            SEQ_CHANNEL_FORMAT,
            self.filter_wheel,
            self.filter_target,
            self.illum_ttl_mask,
            self.led_pattern,
            self.intensity_dac,
            self.intensity,
            self.exposure_us,
            self.camera_mask,
            self.z_offset,
            self.flags,
        )

    @staticmethod
    def unpack(data: bytes) -> "SeqChannelSpec":
        (
            filter_wheel,
            filter_target,
            illum_ttl_mask,
            led_pattern,
            intensity_dac,
            intensity,
            exposure_us,
            camera_mask,
            z_offset,
            flags,
        ) = struct.unpack(SEQ_CHANNEL_FORMAT, data)
        return SeqChannelSpec(
            exposure_us=exposure_us,
            camera_mask=camera_mask,
            illum_ttl_mask=illum_ttl_mask,
            intensity_dac=intensity_dac,
            intensity=intensity,
            led_pattern=led_pattern,
            filter_wheel=filter_wheel,
            filter_target=filter_target,
            z_offset=z_offset,
            flags=flags,
        )


@dataclasses.dataclass(frozen=True)
class SeqCameraSpec(_Replaceable):
    """seq_wire.h WireCamera (the wire form of seq_types.h SeqCameraConfig)."""

    trigger_mode: TriggerMode
    ready_line: int = NONE_ID  # NONE_ID = timing model, else ready input index
    ready_active_high: bool = False
    readout_overlap_safe: bool = True  # False = no motion during this camera's readout
    strobe_delay_us: int = 0  # trigger assert -> illumination on
    readout_time_us: int = 0  # model-based readiness after exposure end
    min_trigger_period_us: int = 0  # 0 = no constraint

    def pack(self) -> bytes:
        return struct.pack(
            WIRE_CAMERA_FORMAT,
            int(self.trigger_mode),
            self.ready_line,
            int(bool(self.ready_active_high)),
            int(bool(self.readout_overlap_safe)),
            self.strobe_delay_us,
            self.readout_time_us,
            self.min_trigger_period_us,
        )

    @staticmethod
    def unpack(data: bytes) -> "SeqCameraSpec":
        (
            trigger_mode,
            ready_line,
            ready_active_high,
            readout_overlap_safe,
            strobe_delay_us,
            readout_time_us,
            min_trigger_period_us,
        ) = struct.unpack(WIRE_CAMERA_FORMAT, data)
        return SeqCameraSpec(
            trigger_mode=_coerce(TriggerMode, trigger_mode),
            ready_line=ready_line,
            ready_active_high=bool(ready_active_high),
            readout_overlap_safe=bool(readout_overlap_safe),
            strobe_delay_us=strobe_delay_us,
            readout_time_us=readout_time_us,
            min_trigger_period_us=min_trigger_period_us,
        )


@dataclasses.dataclass(frozen=True)
class SequencerProgram:
    """One staged sequencer program: what SEQ_WRITE uploads and SEQ_COMMIT seals."""

    loop: SeqLoopSpec
    channels: Sequence[SeqChannelSpec]
    cameras: Sequence[SeqCameraSpec]
    wait_timeout_us: int

    def __post_init__(self):
        # Tuples so the frozen dataclass is really immutable and comparable after unpack().
        object.__setattr__(self, "channels", tuple(self.channels))
        object.__setattr__(self, "cameras", tuple(self.cameras))

    @property
    def n_channels(self) -> int:
        return len(self.channels)

    @property
    def n_cameras(self) -> int:
        return len(self.cameras)

    @property
    def n_frames(self) -> int:
        """Exposure steps in the run.  SeqEngine bumps frames_fired once per step, whatever
        the channel's camera_mask holds."""
        return self.loop.n_layers * self.n_channels

    def nbytes(self) -> int:
        return program_bytes(self.n_channels, self.n_cameras)

    def step_to_layer_channel(self, step: int) -> Tuple[int, int]:
        """The (layer, channel index) of acquisition step k.

        Mirrors SeqEngine::step_to_layer_channel(); the host needs the same mapping to line
        up the frames it receives with the z positions it asked for.
        """
        if self.loop.order == Order.CHANNELS_INNER:
            return step // self.n_channels, step % self.n_channels
        return step % self.loop.n_layers, step // self.loop.n_layers

    def stack_target(self, layer: int, channel_index: int, stack_start: int) -> int:
        """Mirrors SeqEngine::stack_target_for()."""
        return stack_start + layer * self.loop.dz + self.channels[channel_index].z_offset

    def check_stack_range(self, stack_start: int) -> None:
        """Raise if any target of the run would leave the stack axis range.

        Mirrors SeqEngine::stack_range_ok(), which refuses the whole run before the first
        move rather than discovering the problem part-way through.  detail is the offending
        channel index, or 0xFF for the start position (which is also the return target).
        """
        if self.loop.stack_axis_type == StackAxisType.PIEZO:
            low, high = PIEZO_DAC_MIN, PIEZO_DAC_MAX  # a piezo target is a u16 DAC code
        else:
            low, high = -(2**31), 2**31 - 1
        if not low <= stack_start <= high:
            raise ProgramValidationError(
                SeqError.STACK_OUT_OF_RANGE, 0xFF, f"stack start {stack_start} outside the axis range {low}..{high}"
            )
        # Targets are linear in the layer index, so checking both ends covers every layer.
        span = (self.loop.n_layers - 1) * self.loop.dz
        for i, channel in enumerate(self.channels):
            first = stack_start + channel.z_offset
            last = first + span
            if not (low <= first <= high and low <= last <= high):
                raise ProgramValidationError(
                    SeqError.STACK_OUT_OF_RANGE,
                    i,
                    f"channel {i} spans {min(first, last)}..{max(first, last)}, outside the axis range {low}..{high}",
                )

    def validate(self) -> None:
        """Raise ProgramValidationError if the firmware would reject this program.

        Mirrors the structural checks of seq::wire::parse_staging() and the semantic checks
        of seq::validate(), so the user gets a readable error before ~20 serial round trips.
        """
        if not 1 <= self.n_channels <= MAX_CHANNELS:
            raise ProgramValidationError(
                SeqError.BAD_CHANNEL_COUNT, 0, f"{self.n_channels} channels, must be 1..{MAX_CHANNELS}"
            )
        if not 1 <= self.n_cameras <= MAX_CAMERAS:
            raise ProgramValidationError(SeqError.BAD_CAMERA, 0, f"{self.n_cameras} cameras, must be 1..{MAX_CAMERAS}")
        if self.loop.n_layers < 1:
            raise ProgramValidationError(SeqError.BAD_LAYER_COUNT, 0, "n_layers must be >= 1")
        if self.loop.n_layers > MAX_FRAMES:
            raise ProgramValidationError(
                SeqError.BAD_LAYER_COUNT, 0, f"n_layers {self.loop.n_layers} does not fit the u16 wire field"
            )
        if self.n_frames > MAX_FRAMES:
            raise ProgramValidationError(
                SeqError.BAD_LAYER_COUNT,
                0,
                f"{self.loop.n_layers} layers x {self.n_channels} channels = {self.n_frames} frames, "
                f"but frames_fired is a u16 (max {MAX_FRAMES})",
            )

        axis_limit = {StackAxisType.STEPPER: N_AXES, StackAxisType.PIEZO: N_DACS}.get(self.loop.stack_axis_type)
        if axis_limit is None:
            raise ProgramValidationError(
                SeqError.BAD_STACK_AXIS, 0, f"unknown stack_axis_type {self.loop.stack_axis_type!r}"
            )
        if not 0 <= self.loop.stack_axis_id < axis_limit:
            raise ProgramValidationError(
                SeqError.BAD_STACK_AXIS, 0, f"stack_axis_id {self.loop.stack_axis_id} must be 0..{axis_limit - 1}"
            )

        self._check_duration("wait_timeout_us", self.wait_timeout_us, 0)
        self._check_duration("z_settle_us", self.loop.z_settle_us, 0)
        for i, camera in enumerate(self.cameras):
            for name in ("strobe_delay_us", "readout_time_us", "min_trigger_period_us"):
                self._check_duration(f"camera {i} {name}", getattr(camera, name), i)

        for i, channel in enumerate(self.channels):
            if not 0 < channel.exposure_us <= MAX_DURATION_US:
                raise ProgramValidationError(
                    SeqError.BAD_EXPOSURE, i, f"exposure_us {channel.exposure_us} must be 1..{MAX_DURATION_US}"
                )
            if channel.camera_mask == 0:
                raise ProgramValidationError(SeqError.BAD_CAMERA, i, "camera_mask must select at least one camera")
            if channel.camera_mask >> self.n_cameras:
                raise ProgramValidationError(
                    SeqError.BAD_CAMERA,
                    i,
                    f"camera_mask 0b{channel.camera_mask:b} selects a camera the program does not configure "
                    f"({self.n_cameras} cameras)",
                )
            if channel.filter_wheel != NONE_ID and not 0 <= channel.filter_wheel < N_AXES:
                raise ProgramValidationError(
                    SeqError.BAD_CHANNEL, i, f"filter_wheel {channel.filter_wheel} must be 0..{N_AXES - 1} or NONE_ID"
                )
            if channel.intensity_dac != NONE_ID and not 0 <= channel.intensity_dac < N_DACS:
                raise ProgramValidationError(
                    SeqError.BAD_CHANNEL, i, f"intensity_dac {channel.intensity_dac} must be 0..{N_DACS - 1} or NONE_ID"
                )

        # Anything still out of range is a field that does not fit its wire type.
        try:
            self._pack_raw()
        except struct.error as e:
            raise ProgramValidationError(SeqError.BAD_PROGRAM, 0, f"a field does not fit the wire format: {e}") from e

    @staticmethod
    def _check_duration(name: str, value: int, detail: int) -> None:
        if not 0 <= value <= MAX_DURATION_US:
            raise ProgramValidationError(
                SeqError.BAD_DURATION, detail, f"{name} = {value} exceeds the engine bound {MAX_DURATION_US}"
            )

    def _pack_raw(self) -> bytes:
        out = bytearray(self.nbytes())
        out[0:WIRE_HEADER_BYTES] = struct.pack(
            WIRE_HEADER_FORMAT, WIRE_VERSION, self.n_cameras, 0, self.wait_timeout_us
        )
        out[LOOP_OFFSET : LOOP_OFFSET + SEQ_LOOP_BYTES] = self.loop.pack(self.n_channels)
        # out[LOOP_OFFSET + SEQ_LOOP_BYTES] is the pad byte -- already zero.
        offset = CHANNELS_OFFSET
        for channel in self.channels:
            out[offset : offset + SEQ_CHANNEL_BYTES] = channel.pack()
            offset += SEQ_CHANNEL_BYTES
        for camera in self.cameras:
            out[offset : offset + WIRE_CAMERA_BYTES] = camera.pack()
            offset += WIRE_CAMERA_BYTES
        return bytes(out)

    def pack(self) -> bytes:
        """The staged bytes, unpadded.  This length is what SEQ_COMMIT carries."""
        self.validate()
        return self._pack_raw()

    def words(self) -> List[Tuple[int, bytes]]:
        """(word index, 4 bytes) pairs, one SEQ_WRITE each, at absolute offsets."""
        return split_words(self.pack())

    def crc16(self) -> int:
        """CRC-16/CCITT-FALSE over exactly the bytes SEQ_COMMIT declares."""
        return crc16_ccitt_false(self.pack())


def unpack(data: bytes) -> SequencerProgram:
    """Parse staged bytes back into a program.

    Mirrors seq::wire::parse_staging(), including its SeqError/detail pairs, so the
    simulator's SEQ_COMMIT answers what the firmware would answer.
    """
    length = len(data)
    if length < CHANNELS_OFFSET or length > STAGING_BYTES:
        raise ProgramValidationError(
            SeqError.BAD_PROGRAM, 0, f"staged length {length} outside {CHANNELS_OFFSET}..{STAGING_BYTES}"
        )
    version, n_cameras, _reserved, wait_timeout_us = struct.unpack(WIRE_HEADER_FORMAT, data[:WIRE_HEADER_BYTES])
    if version != WIRE_VERSION:
        raise ProgramValidationError(SeqError.BAD_PROGRAM, 1, f"wire version {version}, expected {WIRE_VERSION}")
    if not 1 <= n_cameras <= MAX_CAMERAS:
        raise ProgramValidationError(SeqError.BAD_CAMERA, 0, f"n_cameras {n_cameras} must be 1..{MAX_CAMERAS}")

    loop, n_channels = SeqLoopSpec.unpack_with_n_channels(data[LOOP_OFFSET : LOOP_OFFSET + SEQ_LOOP_BYTES])
    if not 1 <= n_channels <= MAX_CHANNELS:
        raise ProgramValidationError(
            SeqError.BAD_CHANNEL_COUNT, 0, f"n_channels {n_channels} must be 1..{MAX_CHANNELS}"
        )
    expected = program_bytes(n_channels, n_cameras)
    if length != expected:
        raise ProgramValidationError(
            SeqError.BAD_PROGRAM,
            2,
            f"staged length {length} != {expected} for {n_channels} channels and {n_cameras} cameras",
        )
    if loop.n_layers * n_channels > MAX_FRAMES:
        raise ProgramValidationError(
            SeqError.BAD_LAYER_COUNT,
            0,
            f"{loop.n_layers} layers x {n_channels} channels exceeds the u16 frame counter",
        )

    offset = CHANNELS_OFFSET
    channels = []
    for _ in range(n_channels):
        channels.append(SeqChannelSpec.unpack(data[offset : offset + SEQ_CHANNEL_BYTES]))
        offset += SEQ_CHANNEL_BYTES
    cameras = []
    for _ in range(n_cameras):
        cameras.append(SeqCameraSpec.unpack(data[offset : offset + WIRE_CAMERA_BYTES]))
        offset += WIRE_CAMERA_BYTES

    return SequencerProgram(loop=loop, channels=channels, cameras=cameras, wait_timeout_us=wait_timeout_us)


@dataclasses.dataclass(frozen=True)
class SequencerStatus:
    """Sequencer progress as it rides bytes 14..17 of every 10 ms status packet."""

    state: SeqState
    error: Union[SeqError, int]
    detail: int
    frames_fired: int

    @classmethod
    def from_response_bytes(cls, state_byte: int, detail: int, frames_hi: int, frames_lo: int) -> "SequencerStatus":
        return cls(
            # 3 bits -> every value is a defined SeqState; 5 bits -> SeqError may not be.
            state=SeqState(state_byte >> 5),
            error=_coerce(SeqError, state_byte & 0x1F),
            detail=detail,
            frames_fired=(frames_hi << 8) | frames_lo,
        )

    def to_response_bytes(self) -> Tuple[int, int, int, int]:
        return (
            (int(self.state) << 5) | (int(self.error) & 0x1F),
            self.detail & 0xFF,
            (self.frames_fired >> 8) & 0xFF,
            self.frames_fired & 0xFF,
        )

    @property
    def error_name(self) -> str:
        return _error_name(self.error)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def __str__(self) -> str:
        return f"{self.state.name}/{self.error_name} detail={self.detail} frames_fired={self.frames_fired}"
