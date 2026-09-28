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

    # ---- hooks: startup bring-up (Task 4), engine-owned source (Task 6) ----------------------------------------------
    def _bringup_step(self, status: EngineRev1Status) -> None:
        pass

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
