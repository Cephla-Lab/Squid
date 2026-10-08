"""Cephla laser engine v2 — Squid driver. Variants (firmware VAR?): 400R / 400HP (400 µm fiber) and DF (50 µm fiber, for
the Dragonfly; adds a 560 nm fiber laser with an AOM on line 3).

One object owns the engine link (and, on DF, the engine's 560 nm source). It polls STAT? once a second, which is also the heartbeat the
firmware needs to stay armed; publishes per-line readiness; arms and brings every line up at Squid startup, and re-arms on use after
any disarm; and sets intensities. Exposure timing is NOT here: the Squid controller's TTL lines gate the lines in hardware
(IlluminationController, ShutterControlMode.TTL).

DF 560 nm: the laser runs at the operator's power (Laser Engine tab, remembered in cache/laser_engine_v2.yaml);
Squid's 560 intensity drives the AOM amplitude (line 3 analog); the AOM's on/off input is the controller's D3 TTL; the
shutter is safety only - open only while line 3 is READY, closed in every other state and before any source restart.
"""

import math
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
from control.laser_engine_v2_settings import load_settings
from control.laser_engine_v2_link import EngineCommandError, EngineLink, EngineLinkError
from control.laser_engine_v2_status import (
    ERROR_STATES,
    REFUSE_STATES,
    SOURCE_560_LINE,
    EngineV2Status,
    LineState,
    SourceStatus,
    is_560_line,
    parse_status,
)
from control.lighting import _DEFAULT_CHANNEL_MAPPINGS_TTL, IntensityControlMode, ShutterControlMode
from squid.abc import LightSource

IDN_PREFIX = "Cephla,LaserEngineCarrier-rev1,"
DEFAULT_CALIBRATION_DIR = Path(__file__).resolve().parent.parent / "machine_configs" / "intensity_calibrations"


class LaserEngineV2Error(RuntimeError):
    def __init__(self, channel_key: str, message: str, needs_operator: bool = False):
        super().__init__(f"[{channel_key}] {message}")
        self.channel_key = channel_key
        self.needs_operator = needs_operator  # only the operator can clear it (e.g. the 560 key cycle)


@dataclass(frozen=True)
class EngineOptions:
    """The engine's options at build: the Laser Engine tab's saved 560 settings (options_from_cache)."""

    source_idle_off_min: float = 30.0  # DF 560 source off after this long without use; 0 = 24 h (there is no "never")
    source_power_mw: Optional[float] = None  # DF: 560 laser power (mW) set by the operator; None = the source's minimum

    def __post_init__(self):
        if self.source_idle_off_min < 0:
            raise ValueError("source_idle_off_min must be >= 0 (0 = 24 h)")
        mw = self.source_power_mw
        if mw is not None:
            if isinstance(mw, bool) or not isinstance(mw, (int, float)) or not math.isfinite(mw):
                raise ValueError(f"source_power_mw = {mw!r}: a number of mW, or None for the source's minimum")
            object.__setattr__(self, "source_power_mw", float(mw))


def _uptime_text(ms: float) -> str:
    s = ms / 1000.0
    if s < 120:
        return f"{s:.0f} s"
    if s < 7200:
        return f"{s / 60:.0f} min"
    return f"{s / 3600:.1f} h"


AOM_FULL_SCALE_V = 5.0  # GATED3 = the AOM driver's analog input, 0-5 V; full scale = full transmission


def idle_off_seconds(minutes: float) -> float:
    """Seconds before an unused 560 source is switched off; 0 minutes = 24 h, so it is never left on indefinitely."""
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
            continue  # not a wavelength file (e.g. 560_aom.csv, the AOM's: load_aom_calibration)
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


class LaserEngineV2(QObject):
    status_updated = Signal(object)  # EngineV2Status
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
    simulated = False  # build_simulated_engine sets it: the Laser Engine tab then saves nothing to the cache

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
        self._calibration_dir = calibration_dir or DEFAULT_CALIBRATION_DIR
        self.notices: List[str] = []
        self._link: Optional[EngineLink] = None
        self.variant = ""
        self._latest: Optional[EngineV2Status] = None
        self._latest_raw: Optional[dict] = None
        self._last_event = ""
        self._status_lock = threading.Lock()
        self._lost = False
        self._lost_lock = threading.Lock()
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._percent: Dict[int, float] = {}  # last commanded drive per line, % of its ceiling
        self._light_source = None  # LaserEngineV2LightSource, created on first use
        self._source = None  # the engine's own 560 nm source (DF), when its driver is present
        self._source_status: Optional[SourceStatus] = None
        self._log = squid.logging.get_logger(self.__class__.__name__)
        # wavelength -> TTL port code: Microscope sets it to IlluminationController.channel_mappings_TTL
        self.ttl_map_provider: Optional[Callable[[], Dict[int, int]]] = None
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
        self.source_idle_off_s = idle_off_seconds(self.options.source_idle_off_min)  # the tab changes it
        self._source_queue: "queue.Queue" = queue.Queue()
        self._source_thread: Optional[threading.Thread] = None
        self._source_running = threading.Event()
        self._source_want_on = False  # what the driver has asked for
        self._source_enabled = False  # what the source thread has done
        self._source_enable_pending = False  # an enable is queued and not yet executed (no duplicates)
        self._source_requested_mw: Optional[float] = (
            None  # the operator's laser power, clamped to the source's limits; None = its minimum
        )
        self._source_pending_mw: Optional[float] = None
        self._source_last_use = time.monotonic()
        self._source_failures = 0  # consecutive failed enables
        self._source_error: Optional[str] = None  # set after SOURCE_ENABLE_ATTEMPTS failures; cleared by fault_reset()
        self._aom_volts: Optional[float] = None  # line 3 set-point = the AOM analog input; None = not sent yet
        # orders the wake's and the set-point's LINE3:SET; may take _source_lock inside it (link lost -> _on_lost ->
        # _disable_source), never the reverse: nothing holding _source_lock waits for _aom_lock
        self._aom_lock = threading.Lock()
        self._source_lock = threading.Lock()  # guards the check-then-act source fields shared between the two threads
        self._source_disable_pending = False  # a disable is queued and not yet executed (no duplicates)
        self._aom_cal: Optional[Tuple[np.ndarray, np.ndarray]] = None  # read at open (DF with a source)

    # ---- lifecycle -------------------------------------------------------------------------------------------------
    @property
    def link(self) -> EngineLink:
        if self._link is None:
            raise LaserEngineV2Error("engine", "not open")
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
            raise RuntimeError(f"{idn!r} is not a Cephla laser engine v2")
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
        """One FAULT:RESET at connect (the engine latches at every power-up), never retried, saying what it cleared:
        a fault from before Squid connected must stay visible to the operator."""
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
                    # the shutter is safety only: held open while the 560 is in use, never per exposure
                    self._link.command("SHUT:SRC MCU")
                return
            except EngineCommandError as e:
                if time.monotonic() >= deadline:
                    raise LaserEngineV2Error("engine", f"cannot configure the lines: {e.reason}") from e
                time.sleep(0.2)

    def _notice(self, text: str, warn: bool = True) -> None:
        (self._log.warning if warn else self._log.info)(text)
        self.notices.append(text)
        self.notice_added.emit(text)

    def start(self) -> None:
        self.open()
        if self._running.is_set():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._poll_loop, name="LaserEngineV2Poll", daemon=True)
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
            raise LaserEngineV2Error("engine", f"connection lost: {e}") from e

    def _stat(self) -> dict:
        try:
            raw = self.link.status()
        except EngineLinkError as e:
            self._signal_lost(str(e))
            raise LaserEngineV2Error("engine", f"connection lost: {e}") from e
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

    def poll_once(self) -> Optional[EngineV2Status]:
        """One STAT? round-trip. Never does source I/O: only the source thread does, so a slow source cannot delay this
        heartbeat."""
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
        self._after_poll(status)  # the 560 source decisions (and the held-open shutter)
        self._bringup_step(status)  # the startup bring-up, one non-blocking step per poll
        self.status_updated.emit(status)
        return status

    def get_latest_status(self) -> Optional[EngineV2Status]:
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

    # ---- wavelength -> line: the map that selects the TTL port, so intensity and exposure use one line ---------------
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

    def wavelengths_for_line(self, line: int) -> List[int]:
        """The wavelengths whose TTL port is this line (the inverse of line_for_wavelength, same map), ascending."""
        return sorted(w for w in self._ttl_map() if self.line_for_wavelength(w) == line)

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

    # ---- startup bring-up: every fitted line ready without the operator asking ---------------------------------------
    @property
    def bringup_state(self) -> str:
        if self._bringup_state == "running" and self._arm_wait_reason:
            return f"running - ARM waits: {self._arm_wait_reason}"
        return self._bringup_state

    def on_startup(self) -> None:
        """Once, from MicroscopeAddons.prepare_for_use: TECs on, then ARM and bring every line up, the 560 included.
        Runs one step per STAT? poll, so Squid's startup never waits for the TECs. The TECs are switched on here
        because the firmware does not switch them on by itself at power-up.
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

    def _bringup_step(self, status: EngineV2Status) -> None:
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
                except LaserEngineV2Error as e:
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
            raise LaserEngineV2Error("engine", f"cannot arm: {e.reason}") from e

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
            raise LaserEngineV2Error("engine", f"cannot arm: {e.reason}") from e
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
            raise LaserEngineV2Error("engine", f"fault reset refused: {e.reason}") from e

    # ---- enable on use ---------------------------------------------------------------------------------------------------
    def _ensure_ready_step(self, channel_key: str, status: EngineV2Status, tec_retried: Optional[set] = None) -> None:
        """One non-blocking step towards READY for one line. Raises LaserEngineV2Error for error/operator states."""
        info = status.channels[channel_key]
        n = self._line_of(channel_key)
        is560 = is_560_line(self._latest_raw or {}, n)
        if is560:
            self._touch_source()
        if info.state in REFUSE_STATES:
            raise LaserEngineV2Error(channel_key, info.reason, needs_operator=info.state == LineState.NEEDS_KEY)
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
                raise LaserEngineV2Error(channel_key, e.reason) from e
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
            except (LaserEngineV2Error, EngineCommandError) as e:
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
                    raise LaserEngineV2Error(key, "no such line")
                if status.channels[key].state == LineState.UNUSED:
                    raise LaserEngineV2Error(key, "nothing on this line in this variant")
                if not status.channels[key].is_ready:
                    self._ensure_ready_step(key, status, tec_retried)
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.query_interval_s)

    # ---- the engine's 560 source: checks and requests used above -----------------------------------------------------
    def _check_source_usable(self, channel_key: str) -> None:
        if self._source is None:
            raise LaserEngineV2Error(channel_key, "560 nm source not configured")
        st = self._source_status
        if st is not None and st.needs_key:
            raise LaserEngineV2Error(channel_key, "turn the 560 key OFF then ON", needs_operator=True)
        if st is not None and (st.fault or not st.link_ok):
            raise LaserEngineV2Error(channel_key, f"560 nm source not usable: {st.detail or 'not responding'}")

    def _wake_source(self) -> None:
        """Set the AOM to the current 560 intensity (0 V = dark until one is asked), then queue one source enable at the
        source's minimum (the operator's power follows once it is ready).
        The shutter is closed first, so the source never starts behind an open one. The enable_pending check-then-set and
        the queue/state update run under _source_lock; the shutter close and the LINE3:SET round-trips run outside it,
        so a concurrent _disable_source() cannot interleave with the update. A connection lost on the way (the shutter
        close swallows it) aborts the enable there: _on_lost has already switched the source off. `src` is
        captured once, so a concurrent _close_source() cannot leave _source_enable_pending set. The AOM read + send +
        cache runs under _aom_lock (not _source_lock), so it cannot interleave with _set_aom_volts on another thread.
        """
        src = self._source
        if src is None:
            return
        self._touch_source()
        with self._source_lock:
            if self._source_enable_pending:
                return  # the source thread has not run the last enable yet (callers repeat)
            self._source_enable_pending = True
        try:
            self._set_held_shutter(False, self._latest_raw or {})  # closed before the source starts; opens at READY
            with self._aom_lock:
                volts = self._aom_volts_for(self._percent.get(SOURCE_560_LINE, 0.0))
                self._cmd(f"LINE3:SET {volts:.3f}")  # the AOM amplitude; the AOM's on/off input is the D3 TTL
                self._aom_volts = volts  # accepted: what the engine now has
            start = src.min_power_mw  # the source starts at its own minimum; the operator's power follows at ready
            with self._source_lock:
                if self._lost:  # under the lock: _signal_lost sets _lost before its _disable_source takes the lock
                    raise LaserEngineV2Error("L3", "laser engine connection lost")
                requested = self._source_requested_mw if self._source_requested_mw is not None else start
                self._source_pending_mw = requested if requested > start else None
                self._source_want_on = True
                self._source_queue.put(("enable", start))
        except Exception as e:
            with self._source_lock:
                self._source_enable_pending = False
            if isinstance(e, EngineCommandError):  # callers (wait_until_ready) raise only LaserEngineV2Error
                raise LaserEngineV2Error("L3", e.reason) from e
            raise

    def _disable_source(self) -> None:
        if self._source is None:
            return
        with self._source_lock:
            if not self._source_want_on:
                return
            self._source_want_on = False
            self._source_pending_mw = None
            self._source_disable_pending = True  # source_step clears it once the disable has run
            self._source_queue.put(("disable", None))

    def _touch_source(self) -> None:
        self._source_last_use = time.monotonic()

    def _clear_source_error(self) -> None:
        self._source_error, self._source_failures = None, 0

    # ---- the engine's 560 source: open, source thread, close ---------------------------------------------------------
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
        self._source_status = self._judge_source(first, self._source)
        self._aom_cal = self._read_aom_calibration()
        saved = self.options.source_power_mw
        if saved is not None:
            mw = self._source_power_for(saved, self._source)
            self._source_requested_mw = mw
            if abs(mw - saved) > 1e-6:
                lo, hi = self._source.min_power_mw, self._source.max_power_mw
                self._notice(
                    f"saved 560 nm laser power {saved:.0f} mW is outside this laser's {lo:.0f}-{hi:.0f} mW: using {mw:.0f} mW"
                )
            else:
                self._log.info(f"560 nm laser power {mw:.0f} mW (saved setting)")

    def _start_source_thread(self) -> None:
        if self._source is None or self._source_running.is_set():
            return
        self._source_running.set()
        self._source_thread = threading.Thread(target=self._source_loop, name="LaserEngineV2Source", daemon=True)
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
        _source_enable_pending and _source_disable_pending are cleared only after this step's own poll, so a caller
        (wake_up / wait_until_ready) polling in between still sees the request as queued and does not queue another.
        The tail reconciles a source that is not wanted but still reads emitting or starting by queuing one disable.
        It lives here, not in _after_poll, so a failed disable is retried even after the engine link is lost (the poll
        thread stops then; this thread keeps stepping). It acts on the source's own reading, not on the judged
        status. A source that still reads on right after a disable gets a second, redundant disable at the next step;
        that is harmless."""
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
            self._source_status = self._judge_source(status, src)
        finally:
            if ran_enable:
                self._source_enable_pending = False
            with self._source_lock:
                if ran_disable:
                    # the disable this step ran (succeeded or failed) is no longer queued
                    self._source_disable_pending = False
                if (
                    status is not None  # the source's own reading, not the judged one
                    and not self._source_want_on
                    and not self._source_disable_pending
                    and (status.ready or status.starting)
                ):
                    # not wanted but still reads emitting/starting (e.g. a failed disable): queue one disable
                    self._source_disable_pending = True
                    self._source_queue.put(("disable", None))

    def _judge_source(self, status: SourceStatus, src) -> SourceStatus:
        """Add what only the engine knows: repeated enable failures are a fault; READY needs the power at the request.
        `src` is the source that reported `status` (close() may clear self._source meanwhile)."""
        if self._source_error is not None:
            return replace(status, fault=True, detail=self._source_error)
        if status.ready:
            target = self._source_requested_mw if self._source_requested_mw is not None else src.min_power_mw
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

    def _after_poll(self, status: EngineV2Status) -> None:
        """Poll thread: decide only. Queue source requests (never talk to the source here); short engine commands for the shutter.
        Retrying a disable that did not take is source_step's job (it keeps running after the engine link is lost);
        the shutter needs the engine link, so it is handled here. Sleep (LINE3:EN 0) and DISARM close the shutter in
        the firmware as well (PERMIT3 needs line 3 on; DISARM switches everything off); this closes it if it is not."""
        if self._source is None:
            return
        raw = self._latest_raw or {}
        if not self._source_want_on:
            self._set_held_shutter(False, raw)  # closed while the source is off; retried every poll while unwanted
            return
        l3 = status.channels.get("L3")
        st = self._source_status
        source_starting = st is not None and st.starting and not st.ready
        idle = time.monotonic() - self._source_last_use > self.source_idle_off_s
        not_armed = not status.armed and not self._source_enable_pending  # a STAT? taken just before this thread's ARM
        if not_armed or (l3 is not None and l3.state in ERROR_STATES) or idle:
            if idle:
                self._log.info(f"560 nm source idle for {self.source_idle_off_s / 60:.0f} min: switching it off")
            self._disable_source()
            self._set_held_shutter(False, raw)
        elif l3 is not None and l3.state == LineState.READY:
            self._set_held_shutter(True, raw)
        elif l3 is None or l3.state != LineState.STARTING or source_starting:
            # Open only at READY: NEEDS KEY, the source off or starting (also by itself, between two polls), line 3 off
            # or paused close it until READY again. The source stays wanted (the next use restarts it behind the closed
            # shutter). A line-3 ramp with the source ready leaves the shutter as it is: every AOM LINE3:SET ramps.
            self._set_held_shutter(False, raw)

    def _set_held_shutter(self, want_open: bool, raw: dict) -> None:
        """DF: the shutter is a safety device only - open while the 560 is in use (line 3 READY), closed in every other
        state, so the beam is blocked whenever the source is not known to be ready. Never per exposure: the AOM does
        exposure on/off (D3 TTL). `raw` = the latest STAT?.
        """
        if self.variant != "DF" or bool((raw.get("shutter") or {}).get("open")) == want_open:
            return
        try:
            self._cmd(f"SHUT:OPEN {int(want_open)}")
        except (EngineCommandError, LaserEngineV2Error) as e:
            self._log.warning(f"shutter {'open' if want_open else 'close'} not done: {e}")

    def _on_lost(self) -> None:
        self._disable_source()

    # ---- intensity (DF 560: the AOM amplitude, not the laser power) --------------------------------------------------
    SETTLE_TIMEOUT_S = 1.0  # firmware ramps 0 -> ceiling in 0.5 s

    def set_line_intensity(self, line: int, percent: float) -> None:
        """Raw drive, no calibration: % of the line's current ceiling. On DF line 3: the 560 intensity as the AOM amplitude
        (linear in volts, or through 560_aom.csv when present); the 560 laser power is the operator's, never changed here.
        """
        if self._lost:
            raise LaserEngineV2Error(f"L{line}", "laser engine connection lost")
        percent = max(0.0, min(100.0, float(percent)))
        raw = self._stat()  # fresh: whether to wait for the ramp depends on the line's state now
        if is_560_line(raw, line):
            if self._source is None:
                raise LaserEngineV2Error(f"L{line}", "560 nm source not configured")
            self._touch_source()  # an intensity request is use: keeps the source from idling off
            self._percent[line] = percent
            self._set_aom_volts(self._aom_volts_for(percent))
            return
        self._percent[line] = percent
        amps = percent / 100.0 * float(raw["lines"][line - 1]["max"])
        self._cmd(f"LINE{line}:SET {amps:.4f}")
        if raw["lines"][line - 1]["st"] != "OFF":
            self._wait_line_settled(line)  # an OFF line takes the set-point through the ramp when it is enabled

    def _wait_line_settled(self, line: int) -> None:
        """After a set-point change: wait out the firmware ramp (RAMP -> ON); warn after SETTLE_TIMEOUT_S."""
        deadline = time.monotonic() + self.SETTLE_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.poll_once() is None:
                raise LaserEngineV2Error(f"L{line}", "laser engine connection lost")
            if self._latest_raw["lines"][line - 1]["st"] != "RAMP":
                return
            time.sleep(0.02)
        self._log.warning(f"L{line} did not reach its set-point within {self.SETTLE_TIMEOUT_S} s")

    def get_line_intensity(self, line: int) -> float:
        if line in self._percent:
            return self._percent[line]
        raw = self._stat()
        ln = raw["lines"][line - 1]
        if is_560_line(raw, line):
            return self.aom_percent_for_volts(float(ln["target"] or 0.0))
        return 100.0 * float(ln["target"]) / float(ln["max"]) if ln["max"] else 0.0

    def set_wavelength_intensity(self, wavelength: int, percent: float) -> None:
        """Squid's intensity, % of optical power as for its other light sources, on the line whose TTL port this
        wavelength uses."""
        line = self.line_for_wavelength(wavelength)
        if line is None:
            raise LaserEngineV2Error(f"{wavelength} nm", "not on an engine port (D1-D5) in the illumination port map")
        percent = max(0.0, min(100.0, float(percent)))
        self._requested[wavelength] = percent
        if self.variant == "DF" and line == SOURCE_560_LINE:
            self.set_line_intensity(line, percent)  # 560: the AOM amplitude (its own calibration, 560_aom.csv)
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

    @property
    def source_status(self) -> Optional[SourceStatus]:
        """The engine-owned source's last judged status (read-only, for display); None when there is no source."""
        src = self._source  # local: close() can clear self._source on another thread
        return self._source_status if src is not None else None

    @property
    def source_limits_mw(self) -> Optional[Tuple[float, float]]:
        """(minimum, maximum) set-point of the engine-owned source in mW (read-only, for display); None when absent."""
        src = self._source  # local: close() can clear self._source on another thread
        if src is None:
            return None
        return float(src.min_power_mw), float(src.max_power_mw)

    @property
    def source_power_setpoint_mw(self) -> Optional[float]:
        """The operator's 560 nm laser power in mW (the source's minimum until one is set); None when there is no source."""
        src = self._source  # local: close() can clear self._source on another thread
        if src is None:
            return None
        mw = self._source_requested_mw
        return float(mw) if mw is not None else float(src.min_power_mw)

    def set_source_power_mw(self, mw: float) -> float:
        """The 560 nm laser power, set by the operator (Laser Engine tab), clamped to the source's limits.
        Applied now when the source is on, once it has started when it is starting, else at its next start. Returns the
        power applied. Only queues (the source thread does the I/O). Squid's intensity never changes it."""
        src = self._source  # local: close() can clear self._source on another thread
        if src is None:
            raise LaserEngineV2Error("L3", "560 nm source not configured")
        asked = float(mw)
        if not math.isfinite(asked):
            raise ValueError(f"560 nm laser power {mw!r}: not a number of mW")
        mw = self._source_power_for(asked, src)
        when = ""
        with self._source_lock:
            self._source_requested_mw = mw
            st = self._source_status  # the source's own "starting" reading: its enable has run, it is not ready yet
            starting = self._source_want_on and st is not None and st.starting
            if self._source_enable_pending or self._source_pending_mw is not None or starting:
                self._source_pending_mw = mw  # still starting at the minimum: go to it once the source is ready
            elif self._source_want_on:
                self._source_queue.put(("power", mw))
            else:
                when = " (applies when the 560 is next switched on)"
        text = f"560 nm laser set to {mw:.0f} mW{when}"
        if abs(mw - asked) > 1e-6:
            text += f" - {asked:.0f} mW is outside its {src.min_power_mw:.0f}-{src.max_power_mw:.0f} mW"
        self._notice(text, warn=False)
        return mw

    @staticmethod
    def _source_power_for(mw: float, src) -> float:
        """The laser power for a setting: clamped to the source's limits. `src` is the caller's source (close() may clear
        self._source meanwhile). The AOM is not touched here: it does the intensity."""
        return max(float(src.min_power_mw), min(float(mw), float(src.max_power_mw)))

    def _read_aom_calibration(self) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """<calibration dir>/560_aom.csv, when present, maps the 560 intensity to AOM volts; else linear.
        Read once, at open (before any thread runs)."""
        path = self._calibration_dir / AOM_CAL_FILE
        if not path.is_file():
            self._notice(f"no AOM calibration ({AOM_CAL_FILE}): 560 nm intensity is linear in AOM volts", warn=False)
            return None
        cal = load_aom_calibration(path)
        if cal is None:
            self._notice(f"AOM calibration {path} unreadable: 560 nm intensity is linear in AOM volts")
        else:
            self._notice(
                f"AOM calibration {AOM_CAL_FILE} loaded: 560 nm intensity follows its transmission", warn=False
            )
        return cal

    def _aom_volts_for(self, percent: float) -> float:
        """560 intensity (% of transmission) -> AOM volts: 5 V x % / 100, or the calibration's volts for that transmission.
        0 % = 0 V always: dark even when the calibration's first row transmits (np.interp would clamp to that row)."""
        frac = min(1.0, max(0.0, percent / 100.0))
        if frac <= 0.0:
            return 0.0
        cal = self._aom_cal
        if cal is None:
            return AOM_FULL_SCALE_V * frac
        trans, volts = cal
        return float(np.interp(frac, trans, volts))

    def aom_percent_for_volts(self, volts: float) -> float:
        """The 560 intensity (%) a line 3 set-point gives: the inverse of _aom_volts_for (for display; no I/O)."""
        if volts <= 0.0:
            return 0.0
        cal = self._aom_cal
        if cal is None:
            return 100.0 * min(1.0, max(0.0, volts / AOM_FULL_SCALE_V))
        trans, v = cal
        return 100.0 * float(np.interp(volts, v, trans))

    def _set_aom_volts(self, volts: float) -> None:
        """Line 3 set-point = the AOM analog input. _aom_lock covers compare + send + cache, not the ramp wait (poll_once
        can re-enter _wake_source)."""
        with self._aom_lock:
            if self._aom_volts is not None and abs(volts - self._aom_volts) < 1e-3:
                return
            try:
                self._cmd(f"LINE3:SET {volts:.3f}")
            except EngineCommandError as e:
                raise LaserEngineV2Error("L3", e.reason) from e
            self._aom_volts = volts  # cached only once the engine has accepted it: a refused set is sent again
        self._wait_line_settled(SOURCE_560_LINE)  # the AOM input ramps like any set-point (at once while line 3 is off)

    def set_source_idle_off_min(self, minutes: float) -> None:
        """The Laser Engine tab's idle-off (the tab also saves it for the next session). 0 = 24 h."""
        self.source_idle_off_s = idle_off_seconds(minutes)

    @property
    def light_source(self) -> "LaserEngineV2LightSource":
        if self._light_source is None:
            self._light_source = LaserEngineV2LightSource(self)
        return self._light_source


class _SameKey(dict):
    """IlluminationController's channel map for this source: every wavelength maps to itself. The engine resolves the
    line at call time from the TTL port map, so intensity and exposure always use the same port."""

    def __missing__(self, key):
        return key


class LaserEngineV2LightSource(LightSource):
    """IlluminationController's view of the engine: intensity over USB; on/off = Squid controller TTL (hardware-timed)."""

    def __init__(self, engine: LaserEngineV2):
        self._engine = engine
        self.channel_mappings = _SameKey()  # empty: constructing the controller reads nothing from the engine
        self._unmapped_warned: set = set()
        self._log = squid.logging.get_logger(self.__class__.__name__)

    def initialize(self):
        self._engine.open()
        return True

    def set_intensity_control_mode(self, mode):
        if mode != IntensityControlMode.Software:
            raise ValueError("laser engine v2 takes its set-point over USB (IntensityControlMode.Software)")

    def get_intensity_control_mode(self):
        return IntensityControlMode.Software

    def set_shutter_control_mode(self, mode):
        if mode != ShutterControlMode.TTL:  # a software gate would hold a line emitting between exposures
            raise ValueError("laser engine v2 is gated by the Squid controller TTL lines (ShutterControlMode.TTL)")

    def get_shutter_control_mode(self):
        return ShutterControlMode.TTL

    def set_shutter_state(self, channel, on):
        raise ValueError("laser engine v2 is gated by the Squid controller TTL lines, not by software")

    def get_shutter_state(self, channel):
        return False  # no software gate; exposure is the TTL line

    def set_intensity(self, channel, intensity):  # channel = wavelength (nm)
        """Wake the line on use (re-arm + enable), then send the set-point. Can block the caller for up to three STAT?
        round-trips (the wake) plus the firmware ramp wait (SETTLE_TIMEOUT_S, <= ~1 s)."""
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


def _production_source_factory() -> Optional[Callable[[], SourceDriver]]:
    """open_560_source from control.laser_engine_v2_560_driver: the DF 560 nm source driver, which finds the source by
    its own USB IDs. That module is not in this repository (it is supplied separately); without it this returns None
    and line 3 reads NOT_CONFIGURED, the other lines are unaffected."""
    try:
        from control.laser_engine_v2_560_driver import open_560_source
    except ImportError:
        return None
    return open_560_source


def options_from_cache(cache_path: Optional[Path] = None) -> EngineOptions:
    """The 560 power and idle-off as last set in the Laser Engine tab (cache/laser_engine_v2.yaml; defaults when absent)."""
    settings = load_settings(cache_path)
    return EngineOptions(source_idle_off_min=settings.idle_off_560_min, source_power_mw=settings.power_560_mw)


def build_from_config(sn: Optional[str], options: EngineOptions) -> LaserEngineV2:
    """The engine on the USB device with serial number `sn` (laser_engine_sn in the machine .ini), and the 560 source when the
    build has its driver."""
    return LaserEngineV2(
        link_factory=lambda: EngineLink.open(sn=sn),
        source_factory=_production_source_factory(),
        options=options,
    )
