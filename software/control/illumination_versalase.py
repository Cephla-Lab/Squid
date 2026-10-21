"""Vortran Stradus VersaLase laser combiner, controlled over USB HID.

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

With ShutterControlMode.TTL (the normal Squid setup), emission stays enabled and
TTL lines (the microcontroller's, or the NL5's on AION) gate the light through one
of two VersaLase inputs, chosen with TtlInput:

- LASER_ENABLE (default): the DE-25 Laser Enable pins, standard on every VersaLase.
  Heads run CW (PUL=0); TTL high turns emission on at the laser power (LP).
- DIGITAL_MODULATION: the rear SMB inputs, an option not every head has. Heads run
  in digital modulation (PUL=1) and intensity sets the peak pulse power (PP). PUL is
  not stored by the laser, so it is set on every initialize().

With ShutterControlMode.Software, the heads run CW (PUL=0), intensity sets the
laser power (LP), and the shutter toggles emission (LE). Heads with the 5 second
emission delay enabled (DELAY=1) take 5 s to turn on, so this mode is not suited
to acquisition.
"""

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

import hid

from control.lighting import IntensityControlMode, ShutterControlMode
from squid.abc import LightSource
import squid.logging

log = squid.logging.get_logger(__name__)

VORTRAN_VID = 0x201A
VORTRAN_PID = 0x1001

REPORT_SIZE = 64
PAD = 0xFF
OP_SEND = 0xA0
OP_STATUS = 0xA1
OP_RESPONSE = 0xA2
OP_ACK = 0xA3
STATUS_READY = 0x01

# Channel groups shared with the other software-controlled light sources - every
# Squid channel in a group is served by the same laser line.
CHANNEL_GROUPS = [
    [405],
    [470, 488],
    [545, 550, 555, 561],
    [638, 640],
    [730, 735, 750],
]
# A head is assigned to a group when its nominal wavelength is this close to one of
# the group's wavelengths (e.g. a 639 nm head serves the 638/640 channels).
WAVELENGTH_MATCH_TOLERANCE_NM = 5

# Fault codes at or above this value block emission (see "?FC" in the VersaLase manual).
# Lower values are laser state (0 active, 1 standby, 2 warmup) or syntax errors.
FAULT_INTERLOCK_OPEN = 16
EMISSION_DELAY_S = 5.0


class VortranError(RuntimeError):
    pass


class TtlInput(Enum):
    """Which VersaLase input the TTL lines are wired to (only used with ShutterControlMode.TTL)."""

    LASER_ENABLE = "LASER_ENABLE"
    DIGITAL_MODULATION = "DIGITAL_MODULATION"


@dataclass
class HeadStatus:
    wavelength: int
    serial_number: str
    max_power_mw: float
    emission: bool
    modulation: bool  # True: digital modulation (TTL gated), False: CW
    set_power_mw: float  # PP with modulation, LP setting in CW
    measured_power_mw: float
    base_plate_temp_c: float
    optical_block_temp_c: float
    interlock_closed: bool
    fault_code: int
    fault_description: str  # empty when the fault code is 0 (emission active)
    hours: float

    @property
    def faulted(self) -> bool:
        return self.fault_code >= FAULT_INTERLOCK_OPEN


class StradusHead:
    """One laser head of the VersaLase, talking over its own HID device."""

    def __init__(self, device, path: bytes, timeout_s: float = 1.0):
        self._device = device
        self.path = path
        self._timeout_s = timeout_s
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

            deadline = time.monotonic() + self._timeout_s
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


class VersaLase(LightSource):
    def __init__(
        self,
        shutter_mode: ShutterControlMode = ShutterControlMode.TTL,
        ttl_input: TtlInput = TtlInput.LASER_ENABLE,
        vid: int = VORTRAN_VID,
        pid: int = VORTRAN_PID,
        timeout_s: float = 1.0,
    ):
        self.vid = vid
        self.pid = pid
        self.timeout_s = timeout_s
        self.heads: List[StradusHead] = []
        self.channel_mappings: Dict[int, Optional[StradusHead]] = {wl: None for group in CHANNEL_GROUPS for wl in group}
        self.shutter_mode = shutter_mode
        self.ttl_input = ttl_input
        # Software shutter mode: LP is only accepted while emission is on, so the
        # requested power is kept here and applied when the shutter opens.
        self._requested_power_mw: Dict[str, float] = {}

    def initialize(self) -> bool:
        paths = [d["path"] for d in hid.enumerate(self.vid, self.pid)]
        if not paths:
            raise RuntimeError(f"No Vortran laser heads found with VID:PID {self.vid:04x}:{self.pid:04x}")

        for path in paths:
            device = hid.device()
            device.open_path(path)
            device.set_nonblocking(0)
            head = StradusHead(device, path, self.timeout_s)
            self.heads.append(head)
            head.identify()
            fault = int(head.transact("?FC"))
            if fault >= FAULT_INTERLOCK_OPEN:
                raise RuntimeError(f"Vortran head {head.describe()} reports fault code {fault}: {head.transact('?FD')}")
            log.info(f"Found Vortran head {head.describe()}, max power {head.max_power_mw} mW")

        for head in self.heads:
            group = next(
                (
                    g
                    for g in CHANNEL_GROUPS
                    if any(abs(head.wavelength - wl) <= WAVELENGTH_MATCH_TOLERANCE_NM for wl in g)
                ),
                None,
            )
            if group is None:
                log.error(f"Vortran head {head.describe()} does not match any Squid channel")
                continue
            for wl in group:
                if self.channel_mappings[wl] is not None:
                    raise RuntimeError(f"Two Vortran heads serve the {wl} nm channel")
                self.channel_mappings[wl] = head
            log.info(f"Mapped channels {group} to Vortran head {head.describe()}")

        self.set_shutter_control_mode(self.shutter_mode)
        return True

    def set_intensity_control_mode(self, mode: IntensityControlMode):
        if mode != IntensityControlMode.Software:
            raise NotImplementedError("Only software intensity control is supported for the Vortran VersaLase")

    def get_intensity_control_mode(self) -> IntensityControlMode:
        return IntensityControlMode.Software

    def set_shutter_control_mode(self, mode: ShutterControlMode):
        ttl = mode == ShutterControlMode.TTL
        for head in self.heads:
            if ttl and self.ttl_input == TtlInput.DIGITAL_MODULATION:
                # Modulation first, so enabling emission cannot leave a head in CW.
                if not head.set_flag("PUL", True):
                    raise RuntimeError(
                        f"Vortran head {head.describe()} refused digital modulation (PUL=1). Heads without the "
                        'optional SMB modulation inputs are gated through Laser Enable: VORTRAN_TTL_INPUT = "LASER_ENABLE"'
                    )
                self._enable_emission(head)
            elif ttl:
                # CW at LP; the Laser Enable input switches emission on and off.
                if head.set_flag("PUL", False):
                    raise RuntimeError(f"Vortran head {head.describe()} refused CW mode (PUL=0)")
                self._enable_emission(head)
            else:
                head.set_flag("LE", False)
                if head.set_flag("PUL", False):
                    raise RuntimeError(f"Vortran head {head.describe()} refused CW mode (PUL=0)")
                if head.emission_delay:
                    log.warning(
                        f"Vortran head {head.describe()} has the {EMISSION_DELAY_S:.0f} s emission delay enabled; "
                        "every software shutter open will take that long"
                    )
        self.shutter_mode = mode

    def get_shutter_control_mode(self) -> ShutterControlMode:
        return self.shutter_mode

    def _enable_emission(self, head: StradusHead):
        if head.set_flag("LE", True):
            return
        # With DELAY=1 the head reports LE=0 until the delay has elapsed.
        deadline = time.monotonic() + EMISSION_DELAY_S + 2.0
        while time.monotonic() < deadline:
            time.sleep(0.25)
            if head.query_flag("?LE"):
                return
        raise RuntimeError(f"Vortran head {head.describe()} did not enable emission (fault {head.transact('?FC')})")

    def set_shutter_state(self, head: Optional[StradusHead], on: bool):
        if head is None:
            return
        if not on:
            head.set_flag("LE", False)
            return
        self._enable_emission(head)
        if self.shutter_mode == ShutterControlMode.Software and head.serial_number in self._requested_power_mw:
            head.transact(f"LP={self._requested_power_mw[head.serial_number]:.1f}")

    def get_shutter_state(self, head: Optional[StradusHead]) -> bool:
        if head is None:
            return False
        if self.shutter_mode == ShutterControlMode.TTL:
            # Emission stays enabled; whether light is out is up to the TTL line.
            return False
        return head.query_flag("?LE")

    def set_intensity(self, head: Optional[StradusHead], intensity: float):
        """Set a head's power as a percentage (0-100) of its maximum power."""
        if head is None:
            return
        power_mw = head.max_power_mw * min(max(intensity, 0.0), 100.0) / 100.0
        if self._uses_peak_power():
            head.transact(f"PP={power_mw:.1f}")
        elif self.shutter_mode == ShutterControlMode.TTL:
            # Emission is held on, so LP is accepted straight away.
            head.transact(f"LP={power_mw:.1f}")
        else:
            self._requested_power_mw[head.serial_number] = power_mw
            if head.query_flag("?LE"):
                head.transact(f"LP={power_mw:.1f}")

    def get_intensity(self, head: Optional[StradusHead]) -> float:
        """Return a head's power setting as a percentage of its maximum power."""
        if head is None:
            return 0
        if self._uses_peak_power():
            power_mw = head.query_float("?PP")
        elif self.shutter_mode == ShutterControlMode.TTL:
            power_mw = head.query_float("?LPS")
        else:
            power_mw = self._requested_power_mw.get(head.serial_number, head.query_float("?LPS"))
        return power_mw / head.max_power_mw * 100.0

    def _uses_peak_power(self) -> bool:
        return self.shutter_mode == ShutterControlMode.TTL and self.ttl_input == TtlInput.DIGITAL_MODULATION

    def get_status(self) -> List[HeadStatus]:
        """Read every head's status, lowest wavelength first (about 10 queries per head)."""
        return [self._head_status(head) for head in sorted(self.heads, key=lambda h: h.wavelength)]

    @staticmethod
    def _head_status(head: StradusHead) -> HeadStatus:
        modulation = head.query_flag("?PUL")
        fault_code = int(head.transact("?FC"))
        return HeadStatus(
            wavelength=head.wavelength,
            serial_number=head.serial_number,
            max_power_mw=head.max_power_mw,
            emission=head.query_flag("?LE"),
            modulation=modulation,
            set_power_mw=head.query_float("?PP" if modulation else "?LPS"),
            measured_power_mw=head.query_float("?LP"),
            base_plate_temp_c=head.query_float("?BPT"),
            optical_block_temp_c=head.query_float("?OBT"),
            interlock_closed=head.query_flag("?IL"),
            fault_code=fault_code,
            fault_description=head.transact("?FD") if fault_code else "",
            hours=head.query_float("?LH"),
        )

    def shut_down(self):
        for head in self.heads:
            try:
                head.set_flag("LE", False)
            except Exception as e:
                log.error(f"Failed to turn off Vortran head {head.describe()}: {e}")
            finally:
                head.close()
        self.heads = []
