"""Andor HLE laser engine, controlled over USB HID.

An engine is one or more HLE units, each its own HID device (VID 0x1BDB, PID 0x0300) told apart by
its USB serial number. A unit speaks a one-byte-opcode protocol in 64-byte reports (report id 0), and
answers each command with a report that echoes the opcode, or 0xFF on error:

    0x01 mask         set the shutter byte: bit n open = line n may emit
    0x02              read the shutter byte
    0x04 line HH LL   set a line's transmission, 0-1000 for 0-100 %
    0x05 line         read a line's transmission
    0x08              line setup: eight big-endian nm*10 values; 0 = no line, below 100 nm = control line

Emission is gated by TTL (the microcontroller's lines): a line's on/off state is its shutter bit OR its
TTL input, so every shutter bit is cleared at start-up and left cleared, and the TTL lines do all the
switching. Active blanking (USE_ANDOR_ACTIVE_BLANKING) is the exception: when a channel's intensity is
set, its line's shutter bit is opened and every other line's is closed, on every unit, so that the
camera's exposure signal gates the one active line.

Channels (Squid wavelengths) map to lines through the channel groups shared with the other
software-controlled light sources (control.lighting.CHANNEL_GROUPS); a channel whose group no line
serves maps to None and is ignored.
"""

import struct
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, Optional, Tuple

import hid

import squid.logging
from control.lighting import (
    CHANNEL_GROUPS,
    IntensityControlMode,
    ShutterControlMode,
    channel_group_for,
)
from squid.abc import LightSource

log = squid.logging.get_logger(__name__)

ANDOR_HLE_VID = 0x1BDB
ANDOR_HLE_PID = 0x0300
REPORT_SIZE = 64
LINE_SETUP_SLOTS = 8  # the line-setup reply carries this many nm*10 values
CONTROL_LINE_BELOW_NM = 100

Channel = Tuple[str, int]  # (unit serial number, line index) - the values of channel_mappings


class LaserCommands(IntEnum):
    """Command codes for laser control protocol"""

    SET_SHUTTER_STATE = 0x01
    READ_SHUTTER_STATE = 0x02
    SET_TRANSMISSION = 0x04
    READ_TRANSMISSION = 0x05
    GET_LASER_LINE_SETUP = 0x08
    ERROR_RESPONSE = 0xFF


@dataclass
class LaserUnit:

    serial_number: str
    device: object  # hid.device
    lines: Dict[int, int] = field(default_factory=dict)  # line index -> wavelength [nm]
    shutter_mask: Optional[int] = None  # None until this driver has written it
    lock: threading.Lock = field(default_factory=threading.Lock)  # one transaction at a time; close() never during one


class AndorLaser(LightSource):
    def __init__(
        self,
        vid: int = ANDOR_HLE_VID,
        pid: int = ANDOR_HLE_PID,
        active_blanking: bool = False,
        timeout_s: float = 0.5,
    ):
        self.vid = vid
        self.pid = pid
        self.active_blanking = active_blanking
        self.timeout_s = timeout_s
        self.units: Dict[str, LaserUnit] = {}
        # Engine-wide: a line selection (close every other unit, open one line) and shut_down() must not
        # interleave with each other across threads; the per-unit locks only keep single transactions whole.
        self._lock = threading.RLock()
        self.channel_mappings: Dict[int, Optional[Channel]] = {wl: None for group in CHANNEL_GROUPS for wl in group}
        self.intensity_control_mode = IntensityControlMode.Software
        self.shutter_control_mode = ShutterControlMode.TTL

    # ----------------------------------------------------------------- bring-up

    def initialize(self) -> bool:
        serial_numbers = [d["serial_number"] for d in hid.enumerate(self.vid, self.pid) if d.get("serial_number")]
        if not serial_numbers:
            raise RuntimeError(f"No laser devices found with VID:PID {self.vid:04x}:{self.pid:04x}")
        log.info(f"Found {len(serial_numbers)} Andor laser unit(s): {serial_numbers}")

        try:
            for serial_number in serial_numbers:
                device = hid.device()
                device.open(self.vid, self.pid, serial_number)
                unit = LaserUnit(serial_number=serial_number, device=device)
                self.units[serial_number] = unit  # before anything else can fail, so shut_down() closes it
                # Non-blocking: in cython-hidapi read(n, 0) is a plain hid_read(), which on a blocking device
                # waits forever when nothing is queued (bench 2026-10-30: start-up hung on the first unit).
                # Reads that wait pass a positive timeout, which hid_read_timeout() honours either way.
                device.set_nonblocking(1)
                self._set_shutter_mask(unit, 0)  # the power-on shutter state is unknown
                unit.lines = self._read_line_setup(unit)
                log.info(f"Andor laser unit {serial_number} lines: {unit.lines}")
            self._map_channels()
        except Exception as e:
            # A later unit failing must not leave the earlier ones open or half set up.
            self.shut_down()
            self.channel_mappings = {wl: None for wl in self.channel_mappings}
            raise RuntimeError(f"Andor laser engine failed to initialise: {e}") from e
        return True

    def _map_channels(self):
        for unit in self.units.values():
            for line, wavelength in unit.lines.items():
                group = channel_group_for(wavelength)
                if group is None:
                    log.error(
                        f"Andor laser unit {unit.serial_number} line {line} ({wavelength} nm) matches no Squid channel"
                    )
                    continue
                taken = self.channel_mappings[group[0]]
                if taken is not None:
                    raise RuntimeError(
                        f"Two Andor laser lines serve the {group} nm channels: unit {taken[0]} line {taken[1]} "
                        f"and unit {unit.serial_number} line {line} ({wavelength} nm)"
                    )
                for wl in group:
                    self.channel_mappings[wl] = (unit.serial_number, line)
                log.info(f"Mapped channels {group} to Andor laser unit {unit.serial_number}, line {line}")

    # ----------------------------------------------------------------- HID transactions

    def _transact(self, unit: LaserUnit, command: bytes, reply_length: int) -> bytes:
        """Send one command and return its reply (without the report id); raises on error or timeout."""
        opcode = command[0]
        with unit.lock:
            if unit.device is None:
                raise RuntimeError(f"Andor laser unit {unit.serial_number} is closed")
            # A reply that arrived after an earlier deadline is still queued; it must not answer this command.
            # (Immediate: the device is non-blocking, see initialize().)
            while unit.device.read(REPORT_SIZE, 0):
                pass
            unit.device.write(b"\x00" + command)  # report id 0 first
            deadline = time.monotonic() + self.timeout_s
            reply = b""
            while True:
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                if remaining_ms <= 0:
                    break
                data = unit.device.read(REPORT_SIZE, remaining_ms)
                if not data:
                    continue
                # Report id 0 first, as the unit sends it; tolerate a reply without it
                candidate = bytes(data[1 : reply_length + 1] if data[0] == 0x00 else data[:reply_length])
                if candidate and candidate[0] in (opcode, LaserCommands.ERROR_RESPONSE):
                    reply = candidate
                    break
                log.debug(
                    f"Andor laser unit {unit.serial_number}: skipping report {candidate.hex()} while awaiting 0x{opcode:02x}"
                )
        if not reply:
            raise RuntimeError(f"Andor laser unit {unit.serial_number} did not answer command 0x{opcode:02x}")
        if reply[0] == LaserCommands.ERROR_RESPONSE:
            raise RuntimeError(f"Andor laser unit {unit.serial_number} rejected command 0x{opcode:02x}")
        return reply

    def _set_shutter_mask(self, unit: LaserUnit, mask: int):
        """Write the shutter byte unless it is what this driver last wrote."""
        if unit.shutter_mask == mask:
            return
        self._transact(unit, struct.pack(">BB", LaserCommands.SET_SHUTTER_STATE, mask), 1)
        unit.shutter_mask = mask

    def _read_line_setup(self, unit: LaserUnit) -> Dict[int, int]:
        reply = self._transact(unit, struct.pack(">B", LaserCommands.GET_LASER_LINE_SETUP), 1 + 2 * LINE_SETUP_SLOTS)
        lines = {}
        for index in range(LINE_SETUP_SLOTS):
            chunk = reply[1 + 2 * index : 3 + 2 * index]
            if len(chunk) < 2:
                break
            wavelength = struct.unpack(">H", chunk)[0] // 10
            if wavelength >= CONTROL_LINE_BELOW_NM:
                lines[index] = wavelength
        return lines

    # ----------------------------------------------------------------- LightSource

    def set_intensity_control_mode(self, mode: IntensityControlMode):
        if mode != IntensityControlMode.Software:
            raise NotImplementedError("Only software intensity control is supported for the Andor laser engine")

    def get_intensity_control_mode(self) -> IntensityControlMode:
        return self.intensity_control_mode

    def set_shutter_control_mode(self, mode: ShutterControlMode):
        if mode != ShutterControlMode.TTL:
            raise NotImplementedError("Only TTL shutter control is supported for the Andor laser engine")

    def get_shutter_control_mode(self) -> ShutterControlMode:
        return self.shutter_control_mode

    def select(self, channel: Optional[Channel]) -> None:
        """With active blanking, arm only the selected line: every other line on every unit is closed, so the
        camera's exposure signal can open just this one. None (a channel with no laser line) closes them all."""
        if not self.active_blanking:
            return
        with self._lock:
            selected = self.units[channel[0]] if channel is not None else None
            for unit in self.units.values():
                if unit is not selected:
                    self._set_shutter_mask(unit, 0)
            if selected is not None:
                self._set_shutter_mask(selected, 1 << channel[1])

    def set_intensity(self, channel: Optional[Channel], intensity: float):
        """Set a line's transmission as a percentage (0-100). A channel the engine has no line for is ignored."""
        if channel is None:
            return
        if intensity < 0.0 or intensity > 100.0:
            raise ValueError(f"Invalid intensity {intensity}. Must be 0.0-100.0")
        unit_id, line = channel
        with self._lock:
            self.select(channel)  # also for callers that set the power without going through the controller
            transmission = int(round(intensity * 10))
            self._transact(
                self.units[unit_id], struct.pack(">BBH", LaserCommands.SET_TRANSMISSION, line, transmission), 1
            )

    def get_intensity(self, channel: Optional[Channel]) -> float:
        if channel is None:
            return 0.0
        unit_id, line = channel
        reply = self._transact(self.units[unit_id], struct.pack(">BB", LaserCommands.READ_TRANSMISSION, line), 3)
        return struct.unpack(">H", reply[1:3])[0] / 10.0

    def set_shutter_state(self, channel: Optional[Channel], state: bool):
        raise NotImplementedError(
            "The Andor laser engine's shutters follow TTL (and active blanking); they are not set per channel in software"
        )

    def get_shutter_state(self, channel: Optional[Channel]) -> bool:
        if channel is None:
            return False
        unit_id, line = channel
        unit = self.units[unit_id]
        if unit.shutter_mask is None:  # nothing else writes the byte, so after our first write the cache is the truth
            reply = self._transact(unit, struct.pack(">B", LaserCommands.READ_SHUTTER_STATE), 2)
            unit.shutter_mask = reply[1]
        return bool((unit.shutter_mask >> line) & 0x01)

    def shut_down(self):
        """Zero every line, close every shutter and release every unit; safe to call more than once.

        Every step is attempted on its own: a line that rejects its zeroing must not keep the other lines
        at power or the shutter byte open.
        """
        with self._lock:
            units, self.units = list(self.units.values()), {}
            for unit in units:
                for line in unit.lines:
                    self._try(
                        unit, f"zero line {line}", struct.pack(">BBH", LaserCommands.SET_TRANSMISSION, line, 0), 1
                    )
                self._try(unit, "close the shutters", struct.pack(">BB", LaserCommands.SET_SHUTTER_STATE, 0), 1)
                with unit.lock:  # a transaction in flight on another thread finishes first
                    if unit.device is not None:
                        try:
                            unit.device.close()
                        except Exception as e:
                            log.warning(f"Failed to close Andor laser unit {unit.serial_number}: {e}")
                        unit.device = None

    def _try(self, unit: LaserUnit, what: str, command: bytes, reply_length: int):
        try:
            self._transact(unit, command, reply_length)
        except Exception as e:
            log.warning(f"Could not {what} on Andor laser unit {unit.serial_number} before closing it: {e}")
