"""Cephla laser engine, carrier rev 1 — Squid driver.

One object owns the engine link (and, on DF, the engine's 560 nm source). It polls STAT? once a second, which is also the heartbeat the
firmware needs to stay armed; publishes per-line readiness; arms and brings every line up at Squid startup, and re-arms on use after
any disarm; and sets intensities. Exposure timing is NOT here: the Squid controller's TTL lines gate the lines in hardware
(IlluminationController, ShutterControlMode.TTL).
"""

import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from qtpy.QtCore import QObject, Signal

import squid.logging
from control._def import source_code_to_port_index
from control.laser_engine_rev1_link import EngineCommandError, EngineLink, EngineLinkError
from control.laser_engine_rev1_status import (
    ERROR_STATES,
    REFUSE_STATES,
    EngineRev1Status,
    LineState,
    SourceStatus,
    is_560_line,
    parse_status,
)
from control.lighting import _DEFAULT_CHANNEL_MAPPINGS_TTL

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


class LaserEngineRev1(QObject):
    status_updated = Signal(object)  # EngineRev1Status
    connection_lost = Signal(str)
    notice_added = Signal(str)  # an operator-facing message for the Laser Engine tab (also logged)

    READY_TIMEOUT_S = 300.0
    DEFAULT_QUERY_INTERVAL_S = 1.0  # every STAT? is the heartbeat: must stay well under HOST_TIMEOUT_S
    HOST_TIMEOUT_S = 5
    CONFIG_RETRY_S = 3.0  # expanders come up within ~1 s of a cold power-up (firmware retries them once a second)

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
        self.variant = link.query("VAR?")
        link.command(f"HOST:TIMEOUT {self.HOST_TIMEOUT_S}")
        self._configure_lines()
        self._reset_at_connect(link.status())
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
            self._thread.join(timeout=2.0)
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
            if self.poll_once() is None and self._lost:
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
                    self._notice(f"startup: {key} not brought up: {status.channels[key].reason or e}")
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
                if not status.channels[key].is_ready:
                    self._ensure_ready_step(key, status, tec_retried)
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.query_interval_s)

    # ---- engine-owned source hooks used above (Task 6 implements them) ---------------------------------------------
    def _check_source_usable(self, channel_key: str) -> None:
        if self._source is None:
            raise LaserEngineRev1Error(channel_key, "560 nm source not configured")

    def _wake_source(self) -> None:
        pass

    def _disable_source(self) -> None:
        pass

    def _touch_source(self) -> None:
        pass

    def _clear_source_error(self) -> None:
        pass

    # ---- hooks: engine-owned source (Task 6) ------------------------------------------------------------------------
    def _open_source(self) -> None:
        pass

    def _start_source_thread(self) -> None:
        pass

    def _close_source(self) -> None:
        pass

    def _after_poll(self, status: EngineRev1Status) -> None:
        pass

    def _on_lost(self) -> None:
        pass
