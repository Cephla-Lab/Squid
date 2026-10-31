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
from typing import Dict, List, Optional, Tuple

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
    """One HLE unit: its open HID device, its lines, and the shutter byte last written to it."""

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
                device.set_nonblocking(0)
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
        with unit.lock:
            if unit.device is None:
                raise RuntimeError(f"Andor laser unit {unit.serial_number} is closed")
            unit.device.write(b"\x00" + command)  # report id 0 first
            deadline = time.monotonic() + self.timeout_s
            reply = b""
            while time.monotonic() < deadline:
                data = unit.device.read(REPORT_SIZE, int(self.timeout_s * 1000))
                if data:
                    # The unit sends report id 0 first; tolerate a reply without it
                    reply = bytes(data[1 : reply_length + 1] if data[0] == 0x00 else data[:reply_length])
                    break
        opcode = command[0]
        if not reply:
            raise RuntimeError(f"Andor laser unit {unit.serial_number} did not answer command 0x{opcode:02x}")
        if reply[0] == LaserCommands.ERROR_RESPONSE:
            raise RuntimeError(f"Andor laser unit {unit.serial_number} rejected command 0x{opcode:02x}")
        if reply[0] != opcode:
            raise RuntimeError(
                f"Andor laser unit {unit.serial_number} answered command 0x{opcode:02x} with 0x{reply[0]:02x}"
            )
        return reply

    def _set_shutter_mask(self, unit: LaserUnit, mask: int):
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

    def _unit_for(self, channel: Channel) -> Tuple[LaserUnit, int]:
        unit_id, line = channel
        return self.units[unit_id], line

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

    def set_intensity(self, channel: Optional[Channel], intensity: float):
        """Set a line's transmission as a percentage (0-100). A channel the engine has no line for is ignored."""
        if channel is None:
            return
        if intensity < 0.0 or intensity > 100.0:
            raise ValueError(f"Invalid intensity {intensity}. Must be 0.0-100.0")
        unit, line = self._unit_for(channel)
        if self.active_blanking:
            # Only the line being used may emit: close every other line on every unit, open this one
            for other in self.units.values():
                if other is not unit:
                    self._set_shutter_mask(other, 0)
            self._set_shutter_mask(unit, 1 << line)
        transmission = int(round(intensity * 10))
        self._transact(unit, struct.pack(">BBH", LaserCommands.SET_TRANSMISSION, line, transmission), 1)

    def get_intensity(self, channel: Optional[Channel]) -> float:
        if channel is None:
            return 0.0
        unit, line = self._unit_for(channel)
        reply = self._transact(unit, struct.pack(">BB", LaserCommands.READ_TRANSMISSION, line), 3)
        return struct.unpack(">H", reply[1:3])[0] / 10.0

    def set_shutter_state(self, channel: Optional[Channel], state: bool):
        raise NotImplementedError(
            "The Andor laser engine's shutters follow TTL (and active blanking); they are not set per channel in software"
        )

    def get_shutter_state(self, channel: Optional[Channel]) -> bool:
        if channel is None:
            return False
        unit, line = self._unit_for(channel)
        reply = self._transact(unit, struct.pack(">B", LaserCommands.READ_SHUTTER_STATE), 2)
        return bool((reply[1] >> line) & 0x01)

    def shut_down(self):
        """Zero every line, close every shutter and release every unit; safe to call more than once."""
        units, self.units = list(self.units.values()), {}
        for unit in units:
            try:
                for line in unit.lines:
                    self._transact(unit, struct.pack(">BBH", LaserCommands.SET_TRANSMISSION, line, 0), 1)
                unit.shutter_mask = None  # write the closed state even if it is what we last sent
                self._set_shutter_mask(unit, 0)
            except Exception as e:
                log.warning(f"Could not turn off Andor laser unit {unit.serial_number} before closing it: {e}")
            finally:
                with unit.lock:  # a transaction in flight on another thread finishes first
                    if unit.device is not None:
                        try:
                            unit.device.close()
                        except Exception as e:
                            log.warning(f"Failed to close Andor laser unit {unit.serial_number}: {e}")
                        unit.device = None
