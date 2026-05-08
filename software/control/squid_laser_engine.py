"""Cephla-built Squid laser engine — USB-serial controller + simulator.

The firmware (Teensy 4.1) exposes 5 wake/sleep laser channels but 6 TCM modules:
the 55x channel has two TCMs (one cool ~25°C, one hot ~99.7°C). Public API uses
5 channel keys: '405', '470', '55x', '638', '730'. Per-channel "ready" = all
underlying TCM modules in ACTIVE state.

See spec: docs/superpowers/specs/2026-05-07-squid-laser-engine-design.md
"""

import struct
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Iterable, List, Optional
from zlib import crc32

import serial
from qtpy.QtCore import QObject, Signal
from serial.tools import list_ports

import squid.logging


class LaserChannelState(IntEnum):
    """Mirrors the ChannelState enum in firmware (laser_engine.ino)."""

    WARMING_UP = 0
    CHECK_ACTIVE = 1
    ACTIVE = 2
    WAKE_UP = 3
    SLEEP = 4
    PREPARE_SLEEP = 5
    CHECK_ERROR = 6
    ERROR = 7


@dataclass(frozen=True)
class TcmModuleInfo:
    module_index: int  # 0..5
    state: LaserChannelState
    temperature_c: float
    setpoint_c: float
    setpoint_diff_c: float
    tec_voltage: float
    tec_current: float
    hi_temp_setpoint_c: float


# Severity ranking for display_state: smaller = more concerning.
_STATE_DISPLAY_PRIORITY = {
    LaserChannelState.ERROR: 0,
    LaserChannelState.CHECK_ERROR: 1,
    LaserChannelState.SLEEP: 2,
    LaserChannelState.PREPARE_SLEEP: 3,
    LaserChannelState.WAKE_UP: 4,
    LaserChannelState.WARMING_UP: 5,
    LaserChannelState.CHECK_ACTIVE: 6,
    LaserChannelState.ACTIVE: 7,
}


@dataclass(frozen=True)
class LaserChannelInfo:
    key: str  # '405' | '470' | '55x' | '638' | '730'
    laser_ttl_on: bool
    modules: tuple  # tuple[TcmModuleInfo, ...] — 1 module for most channels, 2 for 55x

    @property
    def is_ready(self) -> bool:
        return all(m.state == LaserChannelState.ACTIVE for m in self.modules)

    @property
    def is_error(self) -> bool:
        return any(m.state in (LaserChannelState.ERROR, LaserChannelState.CHECK_ERROR) for m in self.modules)

    @property
    def display_state(self) -> LaserChannelState:
        return min(self.modules, key=lambda m: _STATE_DISPLAY_PRIORITY[m.state]).state


@dataclass(frozen=True)
class SquidLaserEngineStatus:
    channels: dict  # dict[str, LaserChannelInfo] in display order
    timestamp_s: float

    def is_ready_for(self, keys) -> bool:
        for k in keys:
            info = self.channels.get(k)
            if info is None or not info.is_ready:
                return False
        return True

    def any_error(self) -> bool:
        return any(info.is_error for info in self.channels.values())


class SquidLaserEngineError(RuntimeError):
    """Raised when the laser engine reports an unrecoverable channel error."""

    def __init__(self, channel_key: str, message: str):
        super().__init__(f"[{channel_key}] {message}")
        self.channel_key = channel_key


# Channel keys in DISPLAY order (with 55x in the middle, per design).
_CHANNEL_DISPLAY_ORDER = ("405", "470", "55x", "638", "730")
# Firmware wake/sleep channel indices (0..4) keyed by display key.
_CHANNEL_KEY_TO_FIRMWARE_INDEX = {"405": 0, "470": 1, "638": 2, "730": 3, "55x": 4}
# TCM module indices owned by each laser channel key.
_CHANNEL_KEY_TO_MODULE_INDICES = {
    "405": (0,),
    "470": (1,),
    "638": (2,),
    "730": (3,),
    "55x": (4, 5),
}

# Wavelength -> channel key. Optical aliases included.
_WAVELENGTH_TO_CHANNEL = {
    405: "405",
    470: "470",
    488: "470",
    545: "55x",
    550: "55x",
    555: "55x",
    561: "55x",
    638: "638",
    640: "638",
    730: "730",
    735: "730",
    750: "730",
}

_NUM_LASER_CH = 5
_NUM_TEMP_CH = 6
_TCM_BLOCK_BYTES = 7  # state(1) + temp(2) + voltage(2) + current(2)
_STATUS_PAYLOAD_BYTES = 1 + _NUM_LASER_CH + _NUM_TEMP_CH * _TCM_BLOCK_BYTES + _NUM_TEMP_CH * 2 + _NUM_TEMP_CH * 2

# Public alias for downstream consumers (e.g. the laser engine GUI widget).
LASER_CHANNEL_ORDER = _CHANNEL_DISPLAY_ORDER


def _parse_status_packet(payload: bytes):
    """Parse a verified-CRC status payload (without trailing CRC32) from the laser engine.

    Returns SquidLaserEngineStatus on success, None if payload is malformed or not a status packet.
    """
    if len(payload) < _STATUS_PAYLOAD_BYTES or payload[0:1] != b"S":
        return None

    laser_ttl = [bool(payload[1 + i]) for i in range(_NUM_LASER_CH)]

    modules = {}  # module_index -> TcmModuleInfo
    base = 1 + _NUM_LASER_CH
    for i in range(_NUM_TEMP_CH):
        offset = base + i * _TCM_BLOCK_BYTES
        state_val = payload[offset]
        try:
            state = LaserChannelState(state_val)
        except ValueError:
            state = LaserChannelState.ERROR
        temp_c = struct.unpack(">h", payload[offset + 1 : offset + 3])[0] / 100.0
        voltage = struct.unpack(">h", payload[offset + 3 : offset + 5])[0] / 100.0
        current = struct.unpack(">h", payload[offset + 5 : offset + 7])[0] / 100.0
        modules[i] = {"state": state, "temp": temp_c, "voltage": voltage, "current": current}

    diff_base = base + _NUM_TEMP_CH * _TCM_BLOCK_BYTES
    for i in range(_NUM_TEMP_CH):
        diff_c = struct.unpack(">h", payload[diff_base + i * 2 : diff_base + i * 2 + 2])[0] / 100.0
        modules[i]["diff"] = diff_c

    hi_base = diff_base + _NUM_TEMP_CH * 2
    for i in range(_NUM_TEMP_CH):
        hi_c = struct.unpack(">h", payload[hi_base + i * 2 : hi_base + i * 2 + 2])[0] / 100.0
        modules[i]["hi_temp"] = hi_c

    # Build LaserChannelInfo for each display key, in display order.
    channels = {}
    for key in _CHANNEL_DISPLAY_ORDER:
        module_indices = _CHANNEL_KEY_TO_MODULE_INDICES[key]
        firmware_idx = _CHANNEL_KEY_TO_FIRMWARE_INDEX[key]
        infos = tuple(
            TcmModuleInfo(
                module_index=mi,
                state=modules[mi]["state"],
                temperature_c=modules[mi]["temp"],
                setpoint_c=modules[mi]["temp"] - modules[mi]["diff"],
                setpoint_diff_c=modules[mi]["diff"],
                tec_voltage=modules[mi]["voltage"],
                tec_current=modules[mi]["current"],
                hi_temp_setpoint_c=modules[mi]["hi_temp"],
            )
            for mi in module_indices
        )
        channels[key] = LaserChannelInfo(
            key=key,
            laser_ttl_on=laser_ttl[firmware_idx],
            modules=infos,
        )

    return SquidLaserEngineStatus(channels=channels, timestamp_s=time.time())


def _build_command_packet(cmd_byte: bytes, channel_index: Optional[int] = None) -> bytes:
    """Build a wire-format command packet matching the firmware protocol.

    Format: cmd_byte [+ struct.pack('<I', channel_index)] + crc32_le + b'\\x0A\\x0D'.
    """
    body = cmd_byte
    if channel_index is not None:
        body = body + struct.pack("<I", channel_index)
    return body + struct.pack("<I", crc32(body)) + b"\x0a\x0d"


class SquidLaserEngineBase(QObject):
    """Shared logic for the real and simulation engines.

    Subclasses must implement: start, close, _send_query,
    _send_wake(channel_index), _send_sleep(channel_index).
    """

    status_updated = Signal(object)  # SquidLaserEngineStatus
    connection_lost = Signal(str)

    WAVELENGTH_TO_CHANNEL = dict(_WAVELENGTH_TO_CHANNEL)
    CHANNEL_ORDER = _CHANNEL_DISPLAY_ORDER

    # Default poll cadence and acquisition-gate ceiling. Override per-instance
    # via the constructor / wait_until_ready arg if a specific test / hardware
    # bring-up needs different timing.
    DEFAULT_QUERY_INTERVAL_S = 1.0
    READY_TIMEOUT_S = 300.0  # 5 min — gate raises if a channel never reaches ACTIVE.

    def __init__(self, query_interval_s: Optional[float] = None):
        super().__init__()
        self.query_interval_s = query_interval_s if query_interval_s is not None else self.DEFAULT_QUERY_INTERVAL_S
        self._latest_status: Optional[SquidLaserEngineStatus] = None
        self._status_lock = threading.Lock()
        self._connection_lost = False
        self._connection_lost_lock = threading.Lock()
        self._log = squid.logging.get_logger(self.__class__.__name__)

    # ── Public API ──────────────────────────────────────────────────────────

    def get_latest_status(self) -> Optional[SquidLaserEngineStatus]:
        with self._status_lock:
            return self._latest_status

    def is_connection_lost(self) -> bool:
        return self._connection_lost

    def channel_keys_for_wavelengths(self, wavelengths: Iterable[int]) -> List[str]:
        seen = set()
        keys = []
        for w in wavelengths:
            k = self.WAVELENGTH_TO_CHANNEL.get(w)
            if k is not None and k not in seen:
                seen.add(k)
                keys.append(k)
        return keys

    def wake_up(self, channel_key: str) -> None:
        idx = _CHANNEL_KEY_TO_FIRMWARE_INDEX[channel_key]
        self._send_wake(idx)

    def put_to_sleep(self, channel_key: str) -> None:
        idx = _CHANNEL_KEY_TO_FIRMWARE_INDEX[channel_key]
        self._send_sleep(idx)

    def wake_up_all(self) -> None:
        for k in _CHANNEL_DISPLAY_ORDER:
            self.wake_up(k)

    def sleep_all(self) -> None:
        for k in _CHANNEL_DISPLAY_ORDER:
            self.put_to_sleep(k)

    def wait_until_ready(
        self,
        channel_keys: List[str],
        timeout_s: float = 300.0,
        cancel_fn: Callable[[], bool] = lambda: False,
    ) -> bool:
        """Block until all requested channels reach ACTIVE.

        Returns True on success; False on timeout, cancel, or connection_lost.
        Raises SquidLaserEngineError if any requested channel is in ERROR/CHECK_ERROR.
        """
        # Fast path: already ready.
        status = self.get_latest_status()
        if status is not None and status.is_ready_for(channel_keys):
            return True

        # Wake any sleeping needed channels.
        if status is not None:
            for k in channel_keys:
                info = status.channels.get(k)
                if info is not None and info.display_state in (
                    LaserChannelState.SLEEP,
                    LaserChannelState.PREPARE_SLEEP,
                ):
                    self.wake_up(k)

        deadline = time.monotonic() + timeout_s
        poll_interval = min(0.1, self.query_interval_s)
        while True:
            if cancel_fn():
                return False
            if self.is_connection_lost():
                return False
            if time.monotonic() >= deadline:
                return False
            status = self.get_latest_status()
            if status is not None:
                # Detect ERROR on any needed channel.
                for k in channel_keys:
                    info = status.channels.get(k)
                    if info is not None and info.is_error:
                        raise SquidLaserEngineError(k, f"channel reports {info.display_state.name}")
                if status.is_ready_for(channel_keys):
                    return True
            time.sleep(poll_interval)

    # ── Internal helpers for subclasses ─────────────────────────────────────

    def _publish_status(self, status: SquidLaserEngineStatus) -> None:
        with self._status_lock:
            self._latest_status = status
        self.status_updated.emit(status)

    def _signal_connection_lost(self, message: str) -> None:
        with self._connection_lost_lock:
            if self._connection_lost:
                return
            self._connection_lost = True
        # Emit outside the lock so signal handlers don't block other callers.
        self.connection_lost.emit(message)

    # ── Subclass hooks ──────────────────────────────────────────────────────

    def _send_query(self) -> None:
        raise NotImplementedError

    def _send_wake(self, channel_index: int) -> None:
        raise NotImplementedError

    def _send_sleep(self, channel_index: int) -> None:
        raise NotImplementedError

    def start(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class SquidLaserEngine_Simulation(SquidLaserEngineBase):
    """Simulator that emits synthesized status on the same cadence as the real engine.

    Test hooks: force_hold_state(key, state), force_error(key), force_connection_lost(msg).
    """

    def __init__(self, query_interval_s: Optional[float] = None, transition_seconds: float = 3.0):
        super().__init__(query_interval_s=query_interval_s)
        self._transition_seconds = transition_seconds
        # Per-firmware-module transition deadline (monotonic). When time >= deadline,
        # the module advances toward its target state. None means "stable".
        self._module_states = {i: LaserChannelState.WARMING_UP for i in range(_NUM_TEMP_CH)}
        self._module_deadlines = {i: time.monotonic() + transition_seconds for i in range(_NUM_TEMP_CH)}
        self._held_states = {}  # firmware-module-index -> LaserChannelState (force-hold)
        self._state_lock = threading.Lock()
        self._tick_thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        # Tracks whether any module state changed since the last publish. True
        # initially so the first tick still emits a baseline status.
        self._dirty = True

    # ── Public test hooks ───────────────────────────────────────────────────

    def force_hold_state(self, channel_key: str, state: LaserChannelState) -> None:
        with self._state_lock:
            for mi in _CHANNEL_KEY_TO_MODULE_INDICES[channel_key]:
                self._held_states[mi] = state
                if self._module_states[mi] != state:
                    self._module_states[mi] = state
                    self._dirty = True

    def force_error(self, channel_key: str) -> None:
        self.force_hold_state(channel_key, LaserChannelState.ERROR)

    def force_connection_lost(self, message: str = "simulated drop") -> None:
        self._signal_connection_lost(message)

    # ── Simulation tick ─────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running.is_set():
            return
        self._running.set()
        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._tick_thread.start()

    def close(self) -> None:
        if not self._running.is_set():
            return
        self._running.clear()
        if self._tick_thread:
            self._tick_thread.join(timeout=2.0)
            self._tick_thread = None

    def _tick_loop(self) -> None:
        while self._running.is_set():
            self._advance_states()
            # Skip emitting when nothing changed — keeps idle simulators quiet.
            with self._state_lock:
                should_publish = self._dirty
                self._dirty = False
            if should_publish:
                # _build_status takes a snapshot under the lock; emit outside.
                self._publish_status(self._build_status())
            time.sleep(self.query_interval_s)

    def _advance_states(self) -> None:
        with self._state_lock:
            now = time.monotonic()
            for mi, state in list(self._module_states.items()):
                if mi in self._held_states:
                    held = self._held_states[mi]
                    if self._module_states[mi] != held:
                        self._module_states[mi] = held
                        self._dirty = True
                    continue
                deadline = self._module_deadlines.get(mi)
                if deadline is not None and now >= deadline:
                    next_state = self._next_state_in_transition(state)
                    if next_state == state:
                        self._module_deadlines[mi] = None  # stable
                    else:
                        self._module_states[mi] = next_state
                        self._module_deadlines[mi] = now + self._transition_seconds
                        self._dirty = True

    def _next_state_in_transition(self, state):
        # WAKE_UP -> WARMING_UP -> CHECK_ACTIVE -> ACTIVE
        # PREPARE_SLEEP -> SLEEP
        forward = {
            LaserChannelState.WAKE_UP: LaserChannelState.WARMING_UP,
            LaserChannelState.WARMING_UP: LaserChannelState.CHECK_ACTIVE,
            LaserChannelState.CHECK_ACTIVE: LaserChannelState.ACTIVE,
            LaserChannelState.PREPARE_SLEEP: LaserChannelState.SLEEP,
        }
        return forward.get(state, state)

    def _build_status(self) -> SquidLaserEngineStatus:
        # Synthesize a status for all channels using the current per-module states.
        # Use temps near 25°C, the hi-temp module near 99.7°C.
        # Take a snapshot under the lock so we don't read while another
        # thread is mid-write to _module_states.
        with self._state_lock:
            states_snapshot = dict(self._module_states)
        module_data = {}
        for mi, state in states_snapshot.items():
            is_hi = mi == 5
            base_temp = 99.7 if is_hi else 25.0
            temp = base_temp if state == LaserChannelState.ACTIVE else base_temp - 1.5
            module_data[mi] = TcmModuleInfo(
                module_index=mi,
                state=state,
                temperature_c=temp,
                setpoint_c=base_temp,
                setpoint_diff_c=temp - base_temp,
                tec_voltage=0.5,
                tec_current=0.1,
                hi_temp_setpoint_c=99.7,
            )

        channels = {}
        for key in _CHANNEL_DISPLAY_ORDER:
            module_indices = _CHANNEL_KEY_TO_MODULE_INDICES[key]
            channels[key] = LaserChannelInfo(
                key=key,
                laser_ttl_on=False,
                modules=tuple(module_data[mi] for mi in module_indices),
            )
        return SquidLaserEngineStatus(channels=channels, timestamp_s=time.time())

    # ── Subclass hooks: wake/sleep ──────────────────────────────────────────

    def _send_wake(self, channel_index: int) -> None:
        # Firmware: ch4 wakes both modules 4 and 5.
        modules_to_wake = (4, 5) if channel_index == 4 else (channel_index,)
        with self._state_lock:
            for mi in modules_to_wake:
                if mi in self._held_states:
                    continue
                if self._module_states[mi] != LaserChannelState.WAKE_UP:
                    self._module_states[mi] = LaserChannelState.WAKE_UP
                    self._dirty = True
                self._module_deadlines[mi] = time.monotonic() + self._transition_seconds

    def _send_sleep(self, channel_index: int) -> None:
        modules_to_sleep = (4, 5) if channel_index == 4 else (channel_index,)
        with self._state_lock:
            for mi in modules_to_sleep:
                if mi in self._held_states:
                    continue
                if self._module_states[mi] != LaserChannelState.PREPARE_SLEEP:
                    self._module_states[mi] = LaserChannelState.PREPARE_SLEEP
                    self._dirty = True
                self._module_deadlines[mi] = time.monotonic() + self._transition_seconds

    def _send_query(self) -> None:
        # Simulator publishes on its own tick; query is a no-op.
        pass


class SquidLaserEngine(SquidLaserEngineBase):
    """USB-serial controller for the Cephla Squid laser engine.

    Two background threads (mirroring the reference pc-side python):
      - query thread: sends 'Q' every query_interval_s
      - receive thread: parses incoming packets and emits status_updated
    """

    BAUDRATE = 115200

    def __init__(
        self,
        sn: Optional[str] = None,
        device: Optional[str] = None,
        query_interval_s: Optional[float] = None,
        _test_serial=None,
    ):
        super().__init__(query_interval_s=query_interval_s)
        self.sn = sn
        self.device = device
        self._serial = _test_serial  # Production: opened in start(); tests inject directly.
        self._serial_lock = threading.Lock()
        self._running = threading.Event()
        self._query_thread: Optional[threading.Thread] = None
        self._receive_thread: Optional[threading.Thread] = None
        self._crc_mismatch_count = 0
        self._parse_failure_count = 0

    @property
    def crc_mismatch_count(self) -> int:
        return self._crc_mismatch_count

    @property
    def parse_failure_count(self) -> int:
        return self._parse_failure_count

    # ── Public API: start / close ───────────────────────────────────────────

    def start(self) -> None:
        if self._running.is_set():
            return
        if self._serial is None:
            self._serial = self._open_serial()
        self._running.set()
        self._query_thread = threading.Thread(target=self._query_loop, daemon=True)
        self._receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._query_thread.start()
        self._receive_thread.start()

    def close(self) -> None:
        if not self._running.is_set():
            return
        self._running.clear()
        # Close the port first so any blocking read() unblocks promptly,
        # then join — otherwise a thread stuck in read() outlives close()
        # and could deref a None _serial.
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                self._log.exception("Error closing serial port")
        for t in (self._query_thread, self._receive_thread):
            if t is not None:
                t.join(timeout=2.0)
        self._query_thread = None
        self._receive_thread = None
        self._serial = None

    # ── Subclass hooks ──────────────────────────────────────────────────────

    def _send_query(self) -> None:
        self._write_packet(_build_command_packet(b"Q"))

    def _send_wake(self, channel_index: int) -> None:
        self._write_packet(_build_command_packet(b"W", channel_index=channel_index))

    def _send_sleep(self, channel_index: int) -> None:
        self._write_packet(_build_command_packet(b"S", channel_index=channel_index))

    # ── Internals ───────────────────────────────────────────────────────────

    def _open_serial(self):
        port_path = self.device
        if self.sn is not None:
            for p in list_ports.comports():
                if p.serial_number == self.sn:
                    port_path = p.device
                    break
            if port_path is None:
                raise RuntimeError(f"SquidLaserEngine: no USB device found with serial number {self.sn!r}")
        elif port_path is None:
            raise RuntimeError("SquidLaserEngine: must provide either sn or device")
        return serial.Serial(port_path, baudrate=self.BAUDRATE, timeout=0.1)

    def _write_packet(self, packet: bytes) -> None:
        if self._serial is None or self.is_connection_lost():
            return
        try:
            with self._serial_lock:
                self._serial.write(packet)
        except Exception as e:
            # During shutdown close() clears _running and closes the port; a
            # racing write hits a TypeError from pyserial's nulled fd. Suppress
            # silently in that case; otherwise treat as a real disconnect.
            if not self._running.is_set():
                return
            self._log.error(f"SquidLaserEngine write failed: {e}")
            self._signal_connection_lost(str(e))
            self._running.clear()

    def _query_loop(self) -> None:
        while self._running.is_set():
            self._send_query()
            time.sleep(self.query_interval_s)

    def _receive_loop(self) -> None:
        # Accumulate bytes until we see the \x0A\x0D terminator, matching pc-side-python.py.
        msg = bytearray()
        while self._running.is_set():
            try:
                chunk = self._serial.read(1)
            except Exception as e:
                # Shutdown path: close() already cleared _running and closed
                # the port — pyserial then raises (variously SerialException,
                # OSError, or TypeError when fd is None). Exit quietly.
                if not self._running.is_set():
                    return
                self._log.error(f"SquidLaserEngine read failed: {e}")
                self._signal_connection_lost(str(e))
                self._running.clear()
                return
            if not chunk:
                continue
            byte = chunk[0]
            if byte == 0x0D and len(msg) >= 1 and msg[-1] == 0x0A:
                # Frame complete: msg[:-1] is the inner payload+CRC.
                inner = bytes(msg[:-1])
                msg = bytearray()
                self._handle_frame(inner)
            else:
                msg.append(byte)
                # Defensive: clamp the buffer in case the firmware sends garbage.
                if len(msg) > 1024:
                    # Defensive clamp. Preserve a trailing \x0A so we don't accidentally
                    # drop a valid frame whose terminator straddles the discard boundary.
                    msg = bytearray(b"\x0a") if msg[-1] == 0x0A else bytearray()

    def _handle_frame(self, frame: bytes) -> None:
        if len(frame) < 4:
            return
        body = frame[:-4]
        received_crc = struct.unpack("<I", frame[-4:])[0]
        if crc32(body) != received_crc:
            self._crc_mismatch_count += 1
            return
        if not body:
            return
        kind = body[0:1]
        if kind == b"S":
            status = _parse_status_packet(body)
            if status is None:
                self._parse_failure_count += 1
                return
            self._publish_status(status)
        # ACK ('A'), NAK ('N'), and per-channel ('G') frames are ignored for now.
