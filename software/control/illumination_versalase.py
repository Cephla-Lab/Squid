"""Vortran Stradus VersaLase laser combiner, controlled over USB HID (see control.vortran_hid
for the transport; each laser head is its own HID device, identified by its wavelength).

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

initialize() also starts a crash guard (control.versalase_guard), a separate process that turns
emission off if Squid's process ends without calling shut_down().
"""

import os
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

import hid

import control.versalase_guard
from control.lighting import IntensityControlMode, ShutterControlMode
from control.vortran_hid import (  # noqa: F401 - the transport's names stay importable from here
    COMMAND_TIMEOUT_S,
    OP_ACK,
    OP_RESPONSE,
    OP_SEND,
    OP_STATUS,
    PAD,
    REPORT_SIZE,
    STATUS_READY,
    VORTRAN_PID,
    VORTRAN_VID,
    StradusHead,
    VortranError,
)
from squid.abc import LightSource
import squid.logging

log = squid.logging.get_logger(__name__)

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
SHUTDOWN_ATTEMPTS = 3


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

    @property
    def faulted(self) -> bool:
        return self.fault_code >= FAULT_INTERLOCK_OPEN


class VersaLase(LightSource):
    def __init__(
        self,
        shutter_mode: ShutterControlMode = ShutterControlMode.TTL,
        ttl_input: TtlInput = TtlInput.LASER_ENABLE,
        vid: int = VORTRAN_VID,
        pid: int = VORTRAN_PID,
        timeout_s: float = 1.0,
        crash_guard: bool = True,
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
        self._crash_guard = crash_guard
        self._guard: Optional[subprocess.Popen] = None

    def initialize(self) -> bool:
        # Before any head is touched, so a crash during initialize() is covered too.
        if self._crash_guard and self._guard is None:
            self._start_crash_guard()
        try:
            self._open_heads()
            self._map_channels()
            self.set_shutter_control_mode(self.shutter_mode)
        except Exception:
            # A later head failing must not leave the earlier ones emitting or their HID handles open.
            self.shut_down()
            self.channel_mappings = {wl: None for wl in self.channel_mappings}
            raise
        return True

    def _start_crash_guard(self):
        try:
            self._guard = control.versalase_guard.start(os.getpid())
            log.info(f"VersaLase crash guard running as process {self._guard.pid}")
        except Exception as e:
            log.error(f"Could not start the VersaLase crash guard - emission stays on if Squid is force quit: {e}")

    def _open_heads(self):
        paths = [d["path"] for d in hid.enumerate(self.vid, self.pid)]
        if not paths:
            raise RuntimeError(f"No Vortran laser heads found with VID:PID {self.vid:04x}:{self.pid:04x}")

        for path in paths:
            device = hid.device()
            device.open_path(path)
            head = StradusHead(device, path, self.timeout_s)
            self.heads.append(head)  # before anything else can fail, so shut_down() closes it
            device.set_nonblocking(0)
            head.identify()
            fault = int(head.transact("?FC"))
            if fault >= FAULT_INTERLOCK_OPEN:
                raise RuntimeError(f"Vortran head {head.describe()} reports fault code {fault}: {head.transact('?FD')}")
            log.info(f"Found Vortran head {head.describe()}, max power {head.max_power_mw} mW")

    def _map_channels(self):
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
        """Read every head's status, lowest wavelength first (about 9 queries per head)."""
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
        )

    def shut_down(self):
        still_on = []
        for head in self.heads:
            try:
                if not self._turn_off(head):
                    still_on.append(head.describe())
            finally:
                head.close()
        self.heads = []
        if still_on:
            # Keep the guard: it turns these heads off, with fresh handles, once this process ends.
            fallback = "the crash guard turns them off when Squid exits" if self._guard else "no crash guard is running"
            log.error(f"Vortran head(s) {', '.join(still_on)} did not confirm emission off; {fallback}")
            return
        # Every head confirmed it is off, so the guard must not act when this process ends (or race a
        # restarted Squid).
        if self._guard is not None:
            self._guard.terminate()
            self._guard = None

    @staticmethod
    def _turn_off(head: StradusHead) -> bool:
        """Send LE=0 until the head confirms emission is off; False if it never does."""
        for attempt in range(1, SHUTDOWN_ATTEMPTS + 1):
            try:
                if not head.set_flag("LE", False):
                    return True
                log.warning(f"Vortran head {head.describe()} still reports emission on (attempt {attempt})")
            except Exception as e:
                log.warning(f"Failed to turn off Vortran head {head.describe()} (attempt {attempt}): {e}")
        return False
