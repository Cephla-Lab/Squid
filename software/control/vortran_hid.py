"""USB HID transport for Vortran Stradus laser heads.

Each laser head in a VersaLase enumerates as its own USB HID device (VID 0x201A,
PID 0x1001, product string "Stradus Laser") with no USB serial number, so heads
are told apart by the wavelength they report to "?LI".

The heads speak Vortran's ASCII protocol ("?LE" -> "?LE=1", "PP=50" -> "PP=50.0")
wrapped in 64-byte HID reports. The first byte of every output report is an opcode:

    0xA0  send a command / query (ASCII, CRLF terminated, padded with 0xFF)
    0xA1  poll the response status
    0xA2  fetch the response
    0xA3  acknowledge the response, freeing the head for the next command

A head keeps replaying its last response until it is acknowledged, so a reply is
only accepted when it echoes the command that was sent.

This module imports nothing from Squid, so the crash guard (control.versalase_guard)
can use it without loading Squid's configuration.
"""

import threading
import time
from typing import List, Optional

VORTRAN_VID = 0x201A
VORTRAN_PID = 0x1001

REPORT_SIZE = 64
PAD = 0xFF
OP_SEND = 0xA0
OP_STATUS = 0xA1
OP_RESPONSE = 0xA2
OP_ACK = 0xA3
STATUS_READY = 0x01

# Queries reply in ~15 ms, but a head only replies to a power command (LP/PP) after it has
# regulated to the new setting - about 1.2 s on the AION heads.
COMMAND_TIMEOUT_S = 3.0


class VortranError(RuntimeError):
    pass


class StradusHead:
    """One laser head of the VersaLase, talking over its own HID device."""

    def __init__(self, device, path: bytes, timeout_s: float = 1.0, command_timeout_s: float = COMMAND_TIMEOUT_S):
        self._device = device
        self.path = path
        self._timeout_s = timeout_s
        self._command_timeout_s = max(timeout_s, command_timeout_s)
        self._lock = threading.Lock()

        self.serial_number: Optional[str] = None
        self.wavelength: Optional[int] = None
        self.rated_power_mw: Optional[float] = None
        self.max_power_mw: Optional[float] = None
        self.emission_delay = False

    @staticmethod
    def _report(opcode: int, payload: bytes = b"") -> List[int]:
        body = bytes([opcode]) + payload
        if len(body) > REPORT_SIZE:
            raise ValueError(f"Command too long for one HID report: {payload!r}")
        # Leading 0x00 is the HID report ID.
        return [0x00] + list(body + bytes([PAD] * (REPORT_SIZE - len(body))))

    def _read(self, opcode: int) -> Optional[List[int]]:
        frame = self._device.read(REPORT_SIZE, 40)
        if frame and frame[0] == opcode:
            return frame
        return None

    @staticmethod
    def _decode(frame: List[int]) -> str:
        return bytes(b for b in frame[1:] if 0 < b < 0x80).decode("ascii").strip()

    def transact(self, command: str) -> str:
        """Send a command or query and return the value after '=' in its reply."""
        command = command.strip().upper()
        expected_prefix = command.split("=")[0] + "="
        with self._lock:
            self._device.write(self._report(OP_ACK))
            self._device.write(self._report(OP_SEND, (command + "\r\n").encode("ascii")))

            timeout_s = self._timeout_s if command.startswith("?") else self._command_timeout_s
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                self._device.write(self._report(OP_STATUS))
                frame = self._read(OP_STATUS)
                if frame is not None and frame[1] == STATUS_READY:
                    break

            self._device.write(self._report(OP_RESPONSE))
            reply = None
            while time.monotonic() < deadline:
                frame = self._read(OP_RESPONSE)
                if frame is None:
                    continue
                # Replies end with a "Stradus:0>" prompt line.
                first_line = (self._decode(frame).splitlines() or [""])[0]
                if first_line.upper().startswith(expected_prefix):
                    reply = first_line
                    break
            self._device.write(self._report(OP_ACK))

        if reply is None:
            raise VortranError(f"No reply to '{command}' from Vortran head {self.describe()}")
        return reply.split("=", 1)[1].strip()

    def query_float(self, query: str) -> float:
        return float(self.transact(query))

    def query_flag(self, query: str) -> bool:
        return self.transact(query) == "1"

    def set_flag(self, name: str, on: bool) -> bool:
        """Set a 0/1 command and return the state the head reports back."""
        return self.transact(f"{name}={int(on)}") == "1"

    def identify(self):
        # e.g. "VL07241K14, 11951, 639nm, 160mW, C"
        fields = [f.strip() for f in self.transact("?LI").split(",")]
        if len(fields) < 4 or not fields[2].upper().endswith("NM") or not fields[3].upper().endswith("MW"):
            raise VortranError(f"Unexpected ?LI reply from Vortran head: {fields}")
        self.serial_number = fields[0]
        self.wavelength = int(fields[2][:-2])
        self.rated_power_mw = float(fields[3][:-2])
        self.max_power_mw = self.query_float("?MAXP")
        self.emission_delay = self.query_flag("?DELAY")

    def describe(self) -> str:
        if self.wavelength is None:
            return str(self.path)
        return f"{self.wavelength} nm (S/N {self.serial_number})"

    def close(self):
        self._device.close()
