"""Cephla laser engine, carrier rev 1 — Squid driver.

One object owns the engine link (and, on DF, the engine's 560 nm source). It polls STAT? once a second, which is also the heartbeat the
firmware needs to stay armed; publishes per-line readiness; arms and brings every line up at Squid startup, and re-arms on use after
any disarm; and sets intensities. Exposure timing is NOT here: the Squid controller's TTL lines gate the lines in hardware
(IlluminationController, ShutterControlMode.TTL).
"""

import queue
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Protocol, Tuple

import numpy as np
import pandas as pd
from qtpy.QtCore import QObject, Signal

import squid.logging
from control._def import source_code_to_port_index
from control.laser_engine_rev1_link import EngineCommandError, EngineLink, EngineLinkError
from control.laser_engine_rev1_status import (
    ERROR_STATES,
    REFUSE_STATES,
    SOURCE_560_LINE,
    EngineRev1Status,
    LineState,
    SourceStatus,
    is_560_line,
    parse_status,
)
from control.lighting import _DEFAULT_CHANNEL_MAPPINGS_TTL, IntensityControlMode, ShutterControlMode
from squid.abc import LightSource

IDN_PREFIX = "Cephla,LaserEngineCarrier-rev1,"
DEFAULT_CALIBRATION_DIR = Path(__file__).resolve().parent.parent / "machine_configs" / "intensity_calibrations"


class LaserEngineRev1Error(RuntimeError):
    def __init__(self, channel_key: str, message: str, needs_operator: bool = False):
        super().__init__(f"[{channel_key}] {message}")
        self.channel_key = channel_key
        self.needs_operator = needs_operator  # only the operator can clear it (e.g. the 560 key cycle)


@dataclass(frozen=True)
class EngineOptions:
    """Machine options (Task 7 reads them from the .ini). Rulings of 2026-09-27."""

    source_idle_off_min: float = 30.0  # DF 560 source off after this long without use; 0 = 24 h (there is no "never")
    aom_in_path: bool = False  # DF: the 560 AOM is aligned into the beam path (changing it needs optical re-alignment)
    shutter_with_aom: str = (
        "gate"  # with the AOM: "gate" = the shutter also follows each exposure; "open" = held open, AOM gates
    )
    aom_attenuation: bool = (
        False  # Task 9: below the 560 minimum, dim with the AOM (needs 560_aom.csv); off until bench-tested
    )

    def __post_init__(self):
        if self.shutter_with_aom not in ("gate", "open"):
            raise ValueError(f"shutter_with_aom = {self.shutter_with_aom!r}: 'gate' or 'open'")
        if self.source_idle_off_min < 0:
            raise ValueError("source_idle_off_min must be >= 0 (0 = 24 h)")
        if self.aom_attenuation and not self.aom_in_path:
            raise ValueError("AOM attenuation needs the AOM in the beam path (aom_in_path)")
        if self.shutter_with_aom == "open" and not self.aom_in_path:
            raise ValueError("shutter_with_aom = 'open' needs the AOM in the beam path (aom_in_path)")


def _uptime_text(ms: float) -> str:
    s = ms / 1000.0
    if s < 120:
        return f"{s:.0f} s"
    if s < 7200:
        return f"{s / 60:.0f} min"
    return f"{s / 3600:.1f} h"


AOM_FULL_SCALE_V = 5.0  # GATED3 = the AOM driver's analog input, 0-5 V; full scale = full transmission


def idle_off_seconds(minutes: float) -> float:
    """Ruling 4: the 560 source idle-off time; 0 means 24 h (the source is never left on indefinitely)."""
    if minutes < 0:
        raise ValueError("idle-off minutes must be >= 0 (0 = 24 h)")
    return float(minutes if minutes > 0 else 24 * 60) * 60.0


class SourceDriver(Protocol):
    """The engine's own free-space source (DF: the 560 nm fiber laser). Implementations must never raise from poll()."""

    max_power_mw: float
    min_power_mw: float  # the source's own minimum set-point (read from the device)

    def poll(self) -> SourceStatus: ...

    def set_power_mw(self, mw: float) -> None: ...

    def enable(self) -> None: ...

    def disable(self) -> None: ...

    def close(self) -> None: ...


def load_intensity_calibrations(directory: Path) -> Dict[int, Tuple[np.ndarray, np.ndarray]]:
    """Squid's calibration files, <wavelength>.csv with "DAC Percent" and "Optical Power (mW)" (tools/generate_intensity_calibrations.py).
    For this engine "DAC Percent" is the % of the line's current ceiling. Returns wavelength -> (optical %, drive %), ascending.
    """
    luts: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
    if not directory.is_dir():
        return luts
    for path in sorted(directory.glob("*.csv")):
        try:
            wavelength = int(path.stem)
        except ValueError:
            continue  # not a wavelength file (e.g. 560_aom.csv, Task 9)
        try:
            data = pd.read_csv(path)
            power = data["Optical Power (mW)"].to_numpy(dtype=float)
            drive = np.clip(data["DAC Percent"].to_numpy(dtype=float), 0.0, 100.0)
        except (OSError, KeyError, ValueError) as e:
            squid.logging.get_logger(__name__).warning(f"intensity calibration {path} not used: {e}")
            continue
        if len(power) < 2 or power.max() <= 0:
            continue
        order = np.argsort(power)
        luts[wavelength] = (power[order] / power.max() * 100.0, drive[order])
    return luts


def calibrated_drive_percent(lut: Tuple[np.ndarray, np.ndarray], percent: float) -> float:
    """% of optical power -> % of the line's current ceiling (same interpolation as IlluminationController._apply_lut)."""
    power_pct, drive_pct = lut
    return float(np.clip(np.interp(np.clip(percent, 0.0, 100.0), power_pct, drive_pct), 0.0, 100.0))


AOM_CAL_FILE = "560_aom.csv"  # columns "AOM Volts", "Transmission" (any scale)


def load_aom_calibration(path: Path) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """(relative transmission 0..1 ascending, AOM volts), from 0 V up to the voltage of peak transmission; None if unusable."""
    try:
        data = pd.read_csv(path).sort_values("AOM Volts")
        volts = data["AOM Volts"].to_numpy(dtype=float)
        trans = data["Transmission"].to_numpy(dtype=float)
    except (OSError, KeyError, ValueError):
        return None
    if len(volts) < 2 or trans.max() <= 0:
        return None
    peak = int(np.argmax(trans))
    rising = np.maximum.accumulate(trans[: peak + 1]) / trans.max()  # np.interp needs ascending x
    return rising, volts[: peak + 1]


class LaserEngineRev1(QObject):
    status_updated = Signal(object)  # EngineRev1Status
    connection_lost = Signal(str)
    notice_added = Signal(str)  # an operator-facing message for the Laser Engine tab (also logged)

    READY_TIMEOUT_S = 300.0
    DEFAULT_QUERY_INTERVAL_S = 1.0  # every STAT? is the heartbeat: must stay well under HOST_TIMEOUT_S
    HOST_TIMEOUT_S = 5
    CONFIG_RETRY_S = 3.0  # expanders come up within ~1 s of a cold power-up (firmware retries them once a second)
    SOURCE_ENABLE_ATTEMPTS = 3  # consecutive enable failures before the source is reported as a fault
    SOURCE_SETTLE_TOL_MW = 2.0  # READY needs the measured power within max(this, SOURCE_SETTLE_TOL_FRAC x request)
    SOURCE_SETTLE_TOL_FRAC = 0.05
    POLL_JOIN_TIMEOUT_S = 4.0  # close(): one STAT? can take the link's 3 s read timeout (EngineLink.open), + 1 s

    def __init__(
        self,
        link_factory: Callable[[], EngineLink],
        source_factory: Optional[Callable[[], object]] = None,
        query_interval_s: Optional[float] = None,
        options: Optional[EngineOptions] = None,
        calibration_dir: Optional[Path] = None,
    ):
        super().__init__()
        self._link_factory = link_factory
        self._source_factory = source_factory
        self.query_interval_s = query_interval_s or self.DEFAULT_QUERY_INTERVAL_S
        self.options = options or EngineOptions()
        self.shutter_with_aom = self.options.shutter_with_aom  # the tab can change it for the session (Task 6)
        self._calibration_dir = calibration_dir or DEFAULT_CALIBRATION_DIR
        self.notices: List[str] = []
        self._link: Optional[EngineLink] = None
        self.variant = ""
        self._latest: Optional[EngineRev1Status] = None
        self._latest_raw: Optional[dict] = None
        self._last_event = ""
        self._status_lock = threading.Lock()
        self._lost = False
        self._lost_lock = threading.Lock()
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._percent: Dict[int, float] = {}  # last commanded drive per line, % of its ceiling (Task 5)
        self._light_source = None  # LaserEngineRev1LightSource, created on first use (Task 5)
        self._source = None  # engine-owned 560 nm source (Task 6)
        self._source_status: Optional[SourceStatus] = None
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.ttl_map_provider: Optional[Callable[[], Dict[int, int]]] = (
            None  # Task 7: IlluminationController.channel_mappings_TTL
        )
        self._bringup_state = ""  # "", "running", "done", "cancelled: ..."
        self._bringup_keys: List[str] = []
        self._bringup_armed = False  # the bring-up has seen the engine armed (a later disarm cancels it)
        self._bringup_waiting: set = set()  # lines waiting for the operator (notice given once)
        self._bringup_dropped: List[str] = []
        self._bringup_lock = threading.Lock()
        self._arm_wait_reason: Optional[str] = None  # last transient ARM refusal, until an ARM is accepted
        self._requested: Dict[int, float] = {}  # last requested intensity per wavelength, % of optical power
        self._luts: Optional[Dict[int, Tuple[np.ndarray, np.ndarray]]] = None  # loaded on first use
        self._linear_logged: set = set()
        self.source_idle_off_s = idle_off_seconds(
            self.options.source_idle_off_min
        )  # the tab can change it (session only)
        self._source_queue: "queue.Queue" = queue.Queue()
        self._source_thread: Optional[threading.Thread] = None
        self._source_running = threading.Event()
        self._source_want_on = False  # what the driver has asked for
        self._source_enabled = False  # what the source thread has done
        self._source_enable_pending = False  # an enable is queued and not yet executed (no duplicates)
        self._source_requested_mw: Optional[float] = (
            None  # the laser power to run at (already clamped / split with the AOM)
        )
        self._source_pending_mw: Optional[float] = None
        self._source_last_use = time.monotonic()
        self._source_failures = 0  # consecutive failed enables
        self._source_error: Optional[str] = None  # set after SOURCE_ENABLE_ATTEMPTS failures; cleared by fault_reset()
        self._clamp_warned = False
        self._aom_volts: Optional[float] = None  # line 3 set-point = the AOM analog input; None = full transmission
        self._source_lock = threading.Lock()  # guards the check-then-act source fields shared between the two threads
        self._source_disable_pending = False  # a disable is queued and not yet executed (no duplicates; review fix 1)
        self._aom_cal: Optional[Tuple[np.ndarray, np.ndarray]] = None
        self._aom_cal_read = False

    # ---- lifecycle -------------------------------------------------------------------------------------------------
    @property
    def link(self) -> EngineLink:
        if self._link is None:
            raise LaserEngineRev1Error("engine", "not open")
        return self._link

    def open(self) -> None:
        """Connect and configure. Idempotent. Never arms."""
        if self._link is not None:
            return
        link = self._link_factory()
        link.resync()
        idn = link.query("*IDN?")
        if not idn.startswith(IDN_PREFIX):
            link.close()
            raise RuntimeError(f"{idn!r} is not a rev 1 laser engine")
        self._link = link
        try:
            self.variant = link.query("VAR?")
            link.command(f"HOST:TIMEOUT {self.HOST_TIMEOUT_S}")
            self._configure_lines()
            self._reset_at_connect(link.status())
        except Exception:
            self._link = None  # a later open() starts again instead of returning early on a half-configured link
            link.close()
            raise
        self._open_source()

    def _reset_at_connect(self, raw: dict) -> None:
        """Ruling 2: one FAULT:RESET at connect, never retried, and say what it cleared (the engine latches at every power-up)."""
        latched = []
        if not raw["in"].get("latch_ok", 1):
            latched.append("hardware fault latch")
        if raw.get("fault_names"):
            latched.append("faults " + ", ".join(raw["fault_names"]))
        if not latched:
            return
        what = (
            "; ".join(latched)
            + f"; last event {raw.get('last_event') or 'none'}, engine up {_uptime_text(raw.get('t', 0))}"
        )
        try:
            self._link.command("FAULT:RESET")
        except EngineCommandError as e:
            self._notice(f"NOT cleared at connect ({e.reason}): {what} - fix the cause, then Reset faults")
            return
        self._notice(f"cleared at connect: {what}", warn=bool(raw.get("fault_names")))

    def _configure_lines(self) -> None:
        deadline = time.monotonic() + self.CONFIG_RETRY_S
        while True:
            try:
                for n in range(1, 6):
                    self._link.command(f"LINE{n}:MOD INT")  # set-point from the engine DAC (USB), never the analog jack
                    self._link.command(f"LINE{n}:GATE 0")  # exposure timing = Squid controller TTL
                if self.variant == "DF":
                    self._link.command(
                        f"SHUT:SRC {self._shutter_src()}"
                    )  # TTL: the shutter follows D3 (Task 6 has "open")
                return
            except EngineCommandError as e:
                if time.monotonic() >= deadline:
                    raise LaserEngineRev1Error("engine", f"cannot configure the lines: {e.reason}") from e
                time.sleep(0.2)

    def _shutter_held_open(self) -> bool:
        """DF with the AOM in the beam path and the shutter mode "open": the shutter is held open, the AOM gates each exposure."""
        return self.variant == "DF" and self.options.aom_in_path and self.shutter_with_aom == "open"

    def _shutter_src(self) -> str:
        return "MCU" if self._shutter_held_open() else "TTL"

    def _notice(self, text: str, warn: bool = True) -> None:
        (self._log.warning if warn else self._log.info)(text)
        self.notices.append(text)
        self.notice_added.emit(text)

    def start(self) -> None:
        self.open()
        if self._running.is_set():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._poll_loop, name="LaserEngineRev1Poll", daemon=True)
        self._thread.start()
        self._start_source_thread()

    def close(self) -> None:
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=self.POLL_JOIN_TIMEOUT_S)
            self._thread = None
        self._close_source()
        if self._link is not None:
            try:
                self._link.command("DISARM")
            except EngineLinkError:
                pass  # link already gone: the engine disarms itself after HOST_TIMEOUT_S
            self._link.close()
            self._link = None

    # ---- helpers that turn a broken link into "connection lost" --------------------------------------------------------
    def _cmd(self, line: str) -> str:
        try:
            return self.link.command(line)
        except EngineCommandError:
            raise
        except EngineLinkError as e:
            self._signal_lost(str(e))
            raise LaserEngineRev1Error("engine", f"connection lost: {e}") from e

    def _stat(self) -> dict:
        try:
            raw = self.link.status()
        except EngineLinkError as e:
            self._signal_lost(str(e))
            raise LaserEngineRev1Error("engine", f"connection lost: {e}") from e
        with self._status_lock:
            self._latest_raw = raw
        return raw

    # ---- polling / heartbeat -------------------------------------------------------------------------------------------
    def _poll_loop(self) -> None:
        while self._running.is_set():
            try:
                if self.poll_once() is None and self._lost:
                    return
            except Exception as e:  # a bug (e.g. an unexpected STAT? shape) must not stop the heartbeat silently
                self._log.exception("laser engine poll thread")
                self._signal_lost(f"poll thread error: {e}")  # the tab's banner, the 560 off, waits return False
                return
            time.sleep(self.query_interval_s)

    def poll_once(self) -> Optional[EngineRev1Status]:
        """One STAT? round-trip. Never does source I/O (that runs on the source thread, Task 6)."""
        link = self._link  # close() on another thread may clear it
        if self._lost or link is None:
            return None
        try:
            raw = link.status()
        except EngineLinkError as e:
            self._signal_lost(str(e))
            return None
        event = raw.get("last_event", "")
        if event and event != self._last_event:
            self._log.warning(f"laser engine event: {event}")  # e.g. HOST_LOST / ARM_DROPPED, which are not faults
        self._last_event = event
        status = parse_status(raw, source=self._source_status, has_source=self._source is not None)
        with self._status_lock:
            self._latest, self._latest_raw = status, raw
        self._after_poll(status)  # Task 6: the 560 source decisions (and the held-open shutter)
        self._bringup_step(status)  # Task 4: the startup bring-up, one non-blocking step per poll
        self.status_updated.emit(status)
        return status

    def get_latest_status(self) -> Optional[EngineRev1Status]:
        with self._status_lock:
            return self._latest

    def is_connection_lost(self) -> bool:
        return self._lost

    def _signal_lost(self, message: str) -> None:
        with self._lost_lock:
            if self._lost:
                return
            self._lost = True
        self._log.error(f"laser engine connection lost: {message}")
        self._on_lost()
        self.connection_lost.emit(message)

    # ARM refusals that clear by themselves within seconds: wait for them instead of aborting (firmware app.cpp arm())
    TRANSIENT_ARM_REASONS = (
        "cover interlock open",
        "WDOG_5V low",
        "expanders not responding",
        "DAC not initialised",
        "12V_OK low",
        "PG_BUCKS low",
    )

    # ---- wavelength -> line: the same map that selects the TTL port (ruling 5) ---------------------------------------
    def _ttl_map(self) -> Dict[int, int]:
        provider = self.ttl_map_provider
        return provider() if provider is not None else _DEFAULT_CHANNEL_MAPPINGS_TTL

    def line_for_wavelength(self, wavelength) -> Optional[int]:
        """Squid port Dn is cabled to engine TTLn, so the line is the port the wavelength's TTL uses (D3/D4 codes are swapped)."""
        code = self._ttl_map().get(wavelength)
        if code is None:
            return None
        index = source_code_to_port_index(code)
        return index + 1 if 0 <= index < 5 else None

    def channel_keys_for_wavelengths(self, wavelengths: Iterable[int]) -> List[str]:
        keys: List[str] = []
        for w in wavelengths:
            n = self.line_for_wavelength(w)
            if n is not None and f"L{n}" not in keys:
                keys.append(f"L{n}")
        self.note_use(keys)  # Squid asks at acquisition start and live start
        return keys

    def note_use(self, channel_keys: Iterable[str]) -> None:
        """The lines are being used (Squid's acquisition calls this for every FOV). On DF, L3 in use keeps the 560 source from
        idling off. The L3 emission tap / hour meter cannot tell: on DF they include TOK3, which reads low."""
        if self.variant == "DF" and "L3" in channel_keys:
            self._touch_source()

    @staticmethod
    def _line_of(channel_key: str) -> int:
        return int(channel_key.lstrip("L"))

    # ---- startup bring-up (ruling 1) -------------------------------------------------------------------------------------
    @property
    def bringup_state(self) -> str:
        if self._bringup_state == "running" and self._arm_wait_reason:
            return f"running - ARM waits: {self._arm_wait_reason}"
        return self._bringup_state

    def on_startup(self) -> None:
        """Once, from MicroscopeAddons.prepare_for_use: TECs on, then ARM and bring every line up, the 560 included.
        Runs one step per STAT? poll, so Squid's startup never waits for the TECs. TEC auto-on in firmware: to-do (§3.5d).
        """
        raw = self._stat()
        for i, ln in enumerate(raw["lines"]):
            if ln.get("tok_req") and ln.get("kind") != "NONE":
                try:
                    self._cmd(f"TEC{i + 1}:OUT 1")
                except EngineCommandError as e:
                    self._log.warning(f"TEC{i + 1} did not switch on: {e.reason}")
        self._bringup_keys = [
            f"L{i + 1}"
            for i, ln in enumerate(raw["lines"])
            if ln.get("kind") != "NONE" and not (is_560_line(raw, i + 1) and self._source is None)
        ]
        self._bringup_armed = False
        self._bringup_dropped = []
        self._bringup_state = "running"
        self._log.info(f"laser engine startup: arming and bringing up {', '.join(self._bringup_keys)}")
        self.poll_once()  # first step now: the ARM

    def _end_bringup(self, result: str) -> None:
        self._bringup_state = result
        if result == "done":
            self._log.info("laser engine startup: every line ready")
        else:
            self._notice(f"startup bring-up {result}")

    def _bringup_step(self, status: EngineRev1Status) -> None:
        if self._bringup_state != "running" or not self._bringup_lock.acquire(blocking=False):
            return
        try:
            if status.armed:
                self._bringup_armed = True
            elif self._bringup_armed:  # the first disarm of any kind ends it; lines then come up on use only
                self._end_bringup(
                    f"cancelled: the engine disarmed ({status.last_event or 'DISARM'}) - lines come up on use"
                )
                return
            system_faults = [f for f in status.fault_names if f != "TOK_LOST"]
            if system_faults:
                self._end_bringup(
                    f"cancelled: engine fault {', '.join(system_faults)} - Reset faults; lines then come up on use"
                )
                return
            pending = [k for k in self._bringup_keys if not status.channels[k].is_ready]
            if not pending:
                dropped = self._bringup_dropped
                self._end_bringup(f"done except {', '.join(dropped)}" if dropped else "done")
                return
            for key in pending:
                try:
                    self._ensure_ready_step(key, status)
                except LaserEngineRev1Error as e:
                    if e.channel_key == "engine":  # a real ARM refusal (key off, fault latched) or the link: give up
                        self._end_bringup(f"cancelled: {e} - lines come up on use")
                        return
                    if e.needs_operator:  # e.g. the 560 key: keep waiting for the operator
                        if key not in self._bringup_waiting:
                            self._bringup_waiting.add(key)
                            self._notice(f"startup: {key} waits for the operator: {status.channels[key].reason}")
                        continue
                    self._bringup_keys.remove(key)
                    self._bringup_dropped.append(key)
                    # the refusal itself (e.g. the firmware's reason), not the line's status text
                    self._notice(f"startup: {key} not brought up: {e}")
                if not status.armed:
                    return  # one ARM per step; the lines follow at the next poll
        finally:
            self._bringup_lock.release()

    # ---- consent / faults ------------------------------------------------------------------------------------------------
    def arm(self) -> None:
        try:
            self._cmd("ARM")
        except EngineCommandError as e:
            raise LaserEngineRev1Error("engine", f"cannot arm: {e.reason}") from e

    def _try_arm(self) -> bool:
        """True when armed; False on a transient refusal (retried at the next step); raises on a real refusal."""
        try:
            self._cmd("ARM")
        except EngineCommandError as e:
            if e.reason.startswith(self.TRANSIENT_ARM_REASONS):
                if e.reason != self._arm_wait_reason:  # log each new reason once, not every second
                    self._log.info(f"laser engine not ready to arm yet: {e.reason}")
                self._arm_wait_reason = e.reason
                return False
            self._arm_wait_reason = None
            raise LaserEngineRev1Error("engine", f"cannot arm: {e.reason}") from e
        self._arm_wait_reason = None
        return True

    def disarm(self) -> None:
        if self._bringup_state == "running":
            self._end_bringup("cancelled: disarmed by the operator")
        self._disable_source()
        self._cmd("DISARM")

    def fault_reset(self) -> None:
        self._clear_source_error()
        try:
            self._cmd("FAULT:RESET")
        except EngineCommandError as e:
            raise LaserEngineRev1Error("engine", f"fault reset refused: {e.reason}") from e

    # ---- enable on use ---------------------------------------------------------------------------------------------------
    def _ensure_ready_step(self, channel_key: str, status: EngineRev1Status, tec_retried: Optional[set] = None) -> None:
        """One non-blocking step towards READY for one line. Raises LaserEngineRev1Error for error/operator states."""
        info = status.channels[channel_key]
        n = self._line_of(channel_key)
        is560 = is_560_line(self._latest_raw or {}, n)
        if is560:
            self._touch_source()
        if info.state in REFUSE_STATES:
            raise LaserEngineRev1Error(channel_key, info.reason, needs_operator=info.state == LineState.NEEDS_KEY)
        if info.state == LineState.NOT_ARMED:
            if self._try_arm():
                self._bringup_armed = True  # a disarm after this (even before the next poll) ends a running bring-up
        elif info.state == LineState.WARMING_UP:
            if tec_retried is not None and channel_key not in tec_retried:
                tec_retried.add(channel_key)
                try:
                    self._cmd(f"TEC{n}:OUT 1")  # idempotent; covers a TCM that was silent at on_startup()
                except EngineCommandError as e:
                    self._log.warning(f"TEC{n} did not switch on: {e.reason}")
        elif info.state == LineState.OFF:
            if is560:
                self._check_source_usable(channel_key)  # fail before touching line 3 or the source
            try:
                self._cmd(f"LINE{n}:EN 1")
            except EngineCommandError as e:
                if e.reason.startswith("TOK low"):
                    return
                raise LaserEngineRev1Error(channel_key, e.reason) from e
            if is560:
                self._wake_source()
        elif info.state == LineState.SOURCE_OFF:
            self._check_source_usable(channel_key)
            self._wake_source()
        # STARTING / PAUSED / READY: nothing to do; the firmware or the source moves on by itself

    def wake_up(self, channel_key: str) -> None:
        """Non-blocking (live view, set_intensity): up to three steps towards READY. Never raises."""
        for _ in range(3):
            status = self.poll_once()
            if status is None or channel_key not in status.channels:
                return
            if status.channels[channel_key].state not in (LineState.NOT_ARMED, LineState.OFF, LineState.SOURCE_OFF):
                return
            try:
                self._ensure_ready_step(channel_key, status)
            except (LaserEngineRev1Error, EngineCommandError) as e:
                self._log.warning(f"laser engine wake_up({channel_key}): {e}")
                return

    def wake_up_all(self) -> None:
        status = self.poll_once()
        for key, info in status.channels.items() if status else []:
            if info.state not in (LineState.UNUSED, LineState.NOT_CONFIGURED):
                self.wake_up(key)

    def put_to_sleep(self, channel_key: str) -> None:
        n = self._line_of(channel_key)
        if is_560_line(self._latest_raw or {}, n):
            self._disable_source()
        self._cmd(f"LINE{n}:EN 0")

    def sleep_all(self) -> None:
        for n in range(1, 6):
            self.put_to_sleep(f"L{n}")

    def wait_until_ready(
        self, channel_keys: List[str], timeout_s: float = 300.0, cancel_fn: Callable[[], bool] = lambda: False
    ) -> bool:
        deadline = time.monotonic() + timeout_s
        tec_retried: set = set()
        while True:
            if cancel_fn() or self._lost:
                return False
            status = self.poll_once()
            if status is None:
                return False
            if status.is_ready_for(channel_keys):
                return True
            for key in channel_keys:
                if key not in status.channels:
                    raise LaserEngineRev1Error(key, "no such line")
                if status.channels[key].state == LineState.UNUSED:
                    raise LaserEngineRev1Error(key, "nothing on this line in this variant")
                if not status.channels[key].is_ready:
                    self._ensure_ready_step(key, status, tec_retried)
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.query_interval_s)

    # ---- engine-owned source hooks used above (Task 6 implements them) ---------------------------------------------
    def _check_source_usable(self, channel_key: str) -> None:
        if self._source is None:
            raise LaserEngineRev1Error(channel_key, "560 nm source not configured")
        st = self._source_status
        if st is not None and st.needs_key:
            raise LaserEngineRev1Error(channel_key, "turn the 560 key OFF then ON", needs_operator=True)
        if st is not None and (st.fault or not st.link_ok):
            raise LaserEngineRev1Error(channel_key, f"560 nm source not usable: {st.detail or 'not responding'}")

    def _wake_source(self) -> None:
        """Review fix 2b: the enable_pending check-then-set and the queue/state update happen under the lock; only the
        serial round-trip (_cmd) runs outside it, so a concurrent _disable_source() cannot interleave mid-update.
        Review round 2, finding 3: `src` is captured once at the top and used throughout, so a concurrent
        _close_source() clearing self._source to None cannot make `self._source.min_power_mw` raise and leave
        `_source_enable_pending` stuck True (the exception guard below then covers everything up to the final put)."""
        src = self._source
        if src is None:
            return
        self._touch_source()
        with self._source_lock:
            if self._source_enable_pending:
                return  # the source thread has not run the last enable yet (wake_up / wait_until_ready call this every poll)
            self._source_enable_pending = True
        try:
            volts = self._aom_volts if self._aom_volts is not None else self._aom_full_volts()
            self._cmd(f"LINE3:SET {volts:.3f}")  # the AOM analog (harmless without an AOM); TTL3 gates it
            start = src.min_power_mw  # ruling 4: start at the source's own minimum, then the request
            with self._source_lock:
                requested = self._source_requested_mw if self._source_requested_mw is not None else start
                self._source_pending_mw = requested if requested > start else None
                self._source_want_on = True
                self._source_queue.put(("enable", start))
        except Exception:
            with self._source_lock:
                self._source_enable_pending = False
            raise

    def _disable_source(self) -> None:
        if self._source is None:
            return
        with self._source_lock:
            if not self._source_want_on:
                return
            self._source_want_on = False
            self._source_pending_mw = None
            self._source_disable_pending = True  # review fix 1: source_step clears it once the disable has run
            self._source_queue.put(("disable", None))

    def _touch_source(self) -> None:
        self._source_last_use = time.monotonic()

    def _clear_source_error(self) -> None:
        self._source_error, self._source_failures = None, 0

    # ---- hooks: engine-owned source (Task 6) ------------------------------------------------------------------------
    def _open_source(self) -> None:
        if self.variant != "DF" or self._source_factory is None:
            return
        try:
            self._source = self._source_factory()
        except Exception as e:  # the engine stays usable on its other lines; L3 reads NOT_CONFIGURED
            self._log.error(f"560 nm source not available: {e}")
            self._source = None
            return
        first = self._source.poll()  # known (e.g. key not cycled) before line 3 is touched
        if first.ready or first.starting:  # left on by a session that ended without switching it off (e.g. a crash)
            try:
                self._source.disable()  # the source thread is not running yet: direct call
            except Exception as e:
                self._log.error(f"560 nm source: switching off at connect: {e}")
            self._notice("560 nm source was on at connect (the last session did not switch it off): switched off")
            first = self._source.poll()
        self._source_status = self._judge_source(first)

    def _start_source_thread(self) -> None:
        if self._source is None or self._source_running.is_set():
            return
        self._source_running.set()
        self._source_thread = threading.Thread(target=self._source_loop, name="LaserEngineRev1Source", daemon=True)
        self._source_thread.start()

    def _source_loop(self) -> None:
        while self._source_running.is_set():
            try:
                self.source_step()
            except (
                Exception
            ) as e:  # a driver bug must not kill the thread silently: report the source as not responding
                self._log.exception("560 nm source thread")
                self._source_status = SourceStatus(link_ok=False, detail=f"source thread error: {e}")
            time.sleep(self.query_interval_s)

    def source_step(self) -> None:
        """One pass of the source thread: run queued requests, then poll. The only place that does source I/O.
        Review fix 2a: `_source_enable_pending` is cleared only after this step's poll + judge_source, so a caller
        (wake_up / wait_until_ready) polling in between still sees the enable as pending and does not queue a second one.
        Review round 2, finding 1: the "source unwanted but still emitting/starting" reconcile lives HERE (not in
        _after_poll), using this step's own fresh status, so it keeps running after the engine link is lost (the poll
        thread stops calling _after_poll once poll_once() returns None, but this thread keeps stepping).
        Review round 2, finding 2: `_source_disable_pending` is cleared only after the post-step poll/judge (the same
        deferred pattern as `_source_enable_pending`/ran_enable), so this step's own reconcile never re-queues a
        disable it just ran on a status that has not caught up yet."""
        src = self._source
        if src is None:
            return
        ran_enable = False
        ran_disable = False
        while True:
            try:
                op, arg = self._source_queue.get_nowait()
            except queue.Empty:
                break
            try:
                if op == "enable":
                    ran_enable = True
                    src.set_power_mw(arg)
                    src.enable()
                    self._source_enabled, self._source_failures = True, 0
                elif op == "power":
                    src.set_power_mw(arg)
                elif op == "disable":
                    ran_disable = True
                    src.disable()
                    self._source_enabled = False
            except Exception as e:
                self._log.error(f"560 nm source {op}: {e}")
                if op == "enable":
                    self._source_failures += 1
                    if self._source_failures >= self.SOURCE_ENABLE_ATTEMPTS:
                        self._source_error = f"enable failed {self._source_failures}x: {e}"
        status = None
        try:
            status = src.poll()
            with self._source_lock:
                pending = self._source_pending_mw
                if status.ready and pending is not None:
                    self._source_pending_mw = None
                else:
                    pending = None
            if pending is not None:
                try:
                    src.set_power_mw(pending)
                except Exception as e:
                    self._log.error(f"560 nm source power: {e}")
            self._source_status = self._judge_source(status)
        finally:
            if ran_enable:
                self._source_enable_pending = False
            with self._source_lock:
                if ran_disable:
                    # the disable we just ran (succeeded or failed) is no longer "in the queue": release the flag now,
                    # on this step's own fresh status, not an earlier stale one (review round 2, finding 2).
                    self._source_disable_pending = False
                if (
                    status is not None
                    and not self._source_want_on
                    and not self._source_disable_pending
                    and (status.ready or status.starting)
                ):
                    # the source is not wanted but still reads emitting/starting: queue one disable. Lives here (not
                    # in _after_poll) so a failed disable is retried even after the engine link is lost and the poll
                    # thread stops running (review round 2, finding 1).
                    self._source_disable_pending = True
                    self._source_queue.put(("disable", None))

    def _judge_source(self, status: SourceStatus) -> SourceStatus:
        """Add what only the engine knows: repeated enable failures are a fault; READY needs the power at the request."""
        if self._source_error is not None:
            return replace(status, fault=True, detail=self._source_error)
        if status.ready:
            target = self._source_requested_mw if self._source_requested_mw is not None else self._source.min_power_mw
            tol = max(self.SOURCE_SETTLE_TOL_MW, self.SOURCE_SETTLE_TOL_FRAC * target)
            settled = self._source_pending_mw is None and abs(status.power_mw - target) <= tol
            return replace(status, settled=settled)
        return status

    def _close_source(self) -> None:
        if self._source is None:
            return
        self._source_running.clear()
        if self._source_thread is not None:
            self._source_thread.join(timeout=3.0)
            self._source_thread = None
        for action in (self._source.disable, self._source.close):  # always off on close, whatever was requested
            try:
                action()
            except Exception:
                self._log.exception("560 nm source shutdown")
        self._source = None

    def _after_poll(self, status: EngineRev1Status) -> None:
        """Poll thread: decide only. Queue source requests (never talk to the source here); short engine commands for the shutter.
        Review round 2, finding 1: the "still on but not wanted" reconcile moved to source_step's own tail (it needs to
        keep running after the engine link is lost, which stops this method from being called at all). This method
        keeps only the held-open shutter close, which needs the engine link and so must stay on the poll thread."""
        if self._source is None:
            return
        raw = self._latest_raw or {}
        if not self._source_want_on:
            self._set_held_shutter(False, raw)  # held-open mode: close it, retried every poll while unwanted
            return
        l3 = status.channels.get("L3")
        idle = time.monotonic() - self._source_last_use > self.source_idle_off_s
        not_armed = not status.armed and not self._source_enable_pending  # a STAT? taken just before this thread's ARM
        if not_armed or (l3 is not None and l3.state in ERROR_STATES) or idle:
            if idle:
                self._log.info(f"560 nm source idle for {self.source_idle_off_s / 60:.0f} min: switching it off")
            self._disable_source()
            self._set_held_shutter(False, raw)
        elif l3 is not None and l3.state == LineState.READY:
            self._set_held_shutter(True, raw)

    def _set_held_shutter(self, want_open: bool, raw: dict) -> None:
        """AOM "open" mode only: the shutter open while the 560 is ready, closed once it is switched off."""
        if not self._shutter_held_open() or bool((raw.get("shutter") or {}).get("open")) == want_open:
            return
        try:
            self._cmd(f"SHUT:OPEN {int(want_open)}")
        except (EngineCommandError, LaserEngineRev1Error) as e:
            self._log.warning(f"shutter {'open' if want_open else 'close'} not done (retried at the next poll): {e}")

    def _on_lost(self) -> None:
        self._disable_source()

    # ---- intensity (Task 5) ------------------------------------------------------------------------------------------
    SETTLE_TIMEOUT_S = 1.0  # firmware ramps 0 -> ceiling in 0.5 s

    def set_line_intensity(self, line: int, percent: float) -> None:
        """Raw drive, no calibration: % of the line's current ceiling (on DF line 3: % of the 560 source's maximum power)."""
        if self._lost:
            raise LaserEngineRev1Error(f"L{line}", "laser engine connection lost")
        percent = max(0.0, min(100.0, float(percent)))
        raw = self._stat()  # fresh: whether to wait for the ramp depends on the line's state now
        self._percent[line] = percent
        if is_560_line(raw, line):
            self._set_source_power(percent)
            return
        amps = percent / 100.0 * float(raw["lines"][line - 1]["max"])
        self._cmd(f"LINE{line}:SET {amps:.4f}")
        if raw["lines"][line - 1]["st"] != "OFF":
            self._wait_line_settled(line)  # an OFF line takes the set-point through the ramp when it is enabled

    def _wait_line_settled(self, line: int) -> None:
        """After a set-point change: wait out the firmware ramp (RAMP -> ON); warn after SETTLE_TIMEOUT_S."""
        deadline = time.monotonic() + self.SETTLE_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.poll_once() is None:
                raise LaserEngineRev1Error(f"L{line}", "laser engine connection lost")
            if self._latest_raw["lines"][line - 1]["st"] != "RAMP":
                return
            time.sleep(0.02)
        self._log.warning(f"L{line} did not reach its set-point within {self.SETTLE_TIMEOUT_S} s")

    def get_line_intensity(self, line: int) -> float:
        if line in self._percent:
            return self._percent[line]
        ln = self._stat()["lines"][line - 1]
        return 100.0 * float(ln["target"]) / float(ln["max"]) if ln["max"] else 0.0

    def set_wavelength_intensity(self, wavelength: int, percent: float) -> None:
        """Squid's intensity (ruling 3): % of optical power, on the line whose TTL port this wavelength uses."""
        line = self.line_for_wavelength(wavelength)
        if line is None:
            raise LaserEngineRev1Error(f"{wavelength} nm", "not on an engine port (D1-D5) in the illumination port map")
        percent = max(0.0, min(100.0, float(percent)))
        self._requested[wavelength] = percent
        if self.variant == "DF" and line == SOURCE_560_LINE:
            self.set_line_intensity(line, percent)  # 560: % of the source's maximum power, linear in mW
            return
        if self._luts is None:
            self._luts = load_intensity_calibrations(self._calibration_dir)
        lut = self._luts.get(wavelength)
        if lut is None:
            if wavelength not in self._linear_logged:
                self._linear_logged.add(wavelength)
                self._log.info(
                    f"{wavelength} nm: no intensity calibration in {self._calibration_dir} - linear % of the L{line} current ceiling"
                )
            self.set_line_intensity(line, percent)
            return
        self.set_line_intensity(line, calibrated_drive_percent(lut, percent))

    def get_wavelength_intensity(self, wavelength: int) -> float:
        if wavelength in self._requested:
            return self._requested[wavelength]
        line = self.line_for_wavelength(wavelength)
        return self.get_line_intensity(line) if line is not None else 0.0

    def _set_source_power(self, percent: float) -> None:
        if self._source is None:
            raise LaserEngineRev1Error("L3", "560 nm source not configured")
        self._touch_source()
        mw = self._source_power_for(percent / 100.0 * self._source.max_power_mw)  # % of maximum power, linear in mW
        with self._source_lock:
            self._source_requested_mw = mw
            if self._source_enable_pending or self._source_pending_mw is not None:
                self._source_pending_mw = (
                    mw  # still starting at the minimum: go to the new request once the source is ready
                )
            elif self._source_want_on:
                self._source_queue.put(("power", mw))

    def _source_power_for(self, mw: float) -> float:
        """Laser power for a request. Below the source's minimum: dim with the AOM (option on + calibrated), else clamp + warn once."""
        floor = self._source.min_power_mw
        cal = self._aom_calibration()
        if cal is None:
            if mw >= floor:
                return mw
            if not self._clamp_warned:
                self._clamp_warned = True
                self._notice(
                    f"560 nm: {mw:.0f} mW requested is below the 560 nm minimum of {floor:.0f} mW - running at the minimum"
                )
            return floor
        trans, volts = cal
        self._set_aom_volts(
            float(np.interp(min(1.0, max(0.0, mw / floor)), trans, volts))
        )  # full transmission at >= floor
        return max(mw, floor)

    def _aom_calibration(self) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        if not self.options.aom_attenuation:
            return None
        if not self._aom_cal_read:
            self._aom_cal_read = True
            path = self._calibration_dir / AOM_CAL_FILE
            self._aom_cal = load_aom_calibration(path)
            if self._aom_cal is None:
                self._notice(
                    f"AOM attenuation is on but {path} is missing or unreadable: below-minimum 560 requests run at the minimum"
                )
        return self._aom_cal

    def _aom_full_volts(self) -> float:
        cal = self._aom_calibration()
        return float(cal[1][-1]) if cal is not None else AOM_FULL_SCALE_V  # the peak-transmission voltage

    def _set_aom_volts(self, volts: float) -> None:
        now = self._aom_volts if self._aom_volts is not None else self._aom_full_volts()
        if abs(volts - now) < 1e-3:
            return
        self._aom_volts = volts
        self._cmd(f"LINE3:SET {volts:.3f}")
        self._wait_line_settled(3)  # the AOM input ramps like any line set-point (returns at once while line 3 is off)

    def set_source_idle_off_min(self, minutes: float) -> None:
        """Tab control, this session only (the .ini sets the default). 0 = 24 h."""
        self.source_idle_off_s = idle_off_seconds(minutes)

    def set_shutter_with_aom(self, mode: str) -> None:
        """Tab control, this session only. "gate": the shutter also follows each exposure; "open": held open while the 560 is ready."""
        if mode not in ("gate", "open"):
            raise ValueError(f"shutter mode {mode!r}: 'gate' or 'open'")
        if not self.options.aom_in_path:
            raise ValueError("no AOM in the beam path (LASER_ENGINE_REV1_AOM_IN_PATH): the shutter always gates")
        self.shutter_with_aom = mode  # first, so the poll thread does not re-open it
        if mode == "gate":
            self._cmd("SHUT:OPEN 0")
        self._cmd(f"SHUT:SRC {self._shutter_src()}")

    @property
    def light_source(self) -> "LaserEngineRev1LightSource":
        if self._light_source is None:
            self._light_source = LaserEngineRev1LightSource(self)
        return self._light_source


class _SameKey(dict):
    """IlluminationController's channel map for this source: every wavelength maps to itself (ruling 5). The engine resolves the
    line at call time from the TTL port map, so intensity and exposure always use the same port."""

    def __missing__(self, key):
        return key


class LaserEngineRev1LightSource(LightSource):
    """IlluminationController's view of the engine: intensity over USB; on/off = Squid controller TTL (hardware-timed)."""

    def __init__(self, engine: LaserEngineRev1):
        self._engine = engine
        self.channel_mappings = _SameKey()  # empty: constructing the controller reads nothing from the engine
        self._unmapped_warned: set = set()
        self._log = squid.logging.get_logger(self.__class__.__name__)

    def initialize(self):
        self._engine.open()
        return True

    def set_intensity_control_mode(self, mode):
        if mode != IntensityControlMode.Software:
            raise ValueError("the rev 1 laser engine takes its set-point over USB (IntensityControlMode.Software)")

    def get_intensity_control_mode(self):
        return IntensityControlMode.Software

    def set_shutter_control_mode(self, mode):
        if mode != ShutterControlMode.TTL:  # a software gate would hold a line emitting between exposures
            raise ValueError(
                "the rev 1 laser engine is gated by the Squid controller TTL lines (ShutterControlMode.TTL)"
            )

    def get_shutter_control_mode(self):
        return ShutterControlMode.TTL

    def set_shutter_state(self, channel, on):
        raise ValueError("the rev 1 laser engine is gated by the Squid controller TTL lines, not by software")

    def get_shutter_state(self, channel):
        return False  # no software gate; exposure is the TTL line

    def set_intensity(self, channel, intensity):  # channel = wavelength (nm)
        line = self._engine.line_for_wavelength(channel)
        if line is None:  # not an engine port: the controller still selects its TTL port right after this call
            if channel not in self._unmapped_warned:
                self._unmapped_warned.add(channel)
                self._log.warning(
                    f"{channel} nm is not on an engine port (D1-D5): intensity not sent to the laser engine"
                )
            return
        self._engine.wake_up(f"L{line}")  # re-arm + enable on use first; never raises
        self._engine.set_wavelength_intensity(
            channel, intensity
        )  # then the set-point: waits out the ramp when the line is on

    def get_intensity(self, channel) -> float:
        return self._engine.get_wavelength_intensity(channel)

    def shut_down(self):
        self._engine.close()


def _production_source_factory(source_sn: Optional[str]):
    try:
        from control.laser_engine_rev1_l3_driver import open_l3_source  # Task 10; absent from builds without it
    except ImportError:
        return None
    return lambda: open_l3_source(sn=source_sn)


def options_from_def() -> EngineOptions:
    import control._def as d  # read at call time: the .ini has been applied by then

    return EngineOptions(
        source_idle_off_min=d.LASER_ENGINE_REV1_SOURCE_IDLE_OFF_MIN,
        aom_in_path=bool(d.LASER_ENGINE_REV1_AOM_IN_PATH),
        shutter_with_aom=str(d.LASER_ENGINE_REV1_SHUTTER_WITH_AOM),
        aom_attenuation=bool(d.LASER_ENGINE_REV1_AOM_ATTENUATION),
    )


def build_from_config(sn: Optional[str], source_sn: Optional[str], options: EngineOptions) -> LaserEngineRev1:
    return LaserEngineRev1(
        link_factory=lambda: EngineLink.open(sn=sn),
        source_factory=_production_source_factory(source_sn),
        options=options,
    )
