"""Focus log: the laser AF reading around each fluidics sequence (`imaging.focus_log: true` in the protocol header).

The coverslip height at imaging can differ between rounds that end with the same sequence on the same port, and
imaging is the only time it is seen. With the focus log on, the runner takes a laser AF displacement reading where
the stage is before each fluidics step starts and after each of its sequences has completed (incubation included,
before the next sequence starts), and appends one row per reading to focus_log.csv in the run folder.

Measurement only: z, the stage and the valves are not moved. A reading that fails, or laser AF that is not
initialized or has no reference, is a row with ok = false; it never changes the run.

The library runs the sequences on its own thread and reports each completed one there; that thread waits (at most
WAIT_S) while the runner thread takes the reading, so every laser AF call stays on the runner thread.
Qt-free."""

import csv
import math
import statistics
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import squid.logging

READINGS = 3  # displacement readings averaged per row (each one averages the laser AF's own frames)
WAIT_S = 30.0  # the longest a sequence boundary waits for its reading

COLUMNS = (
    "time",
    "elapsed_s",  # since the run started
    "step",  # the runner's step index (one fluidics step = one library run)
    "round",
    "attempt",
    "point",  # before: the step is about to start; after: `sequence` has just completed
    "sequence",  # 1-based position in the step's plan (repeats counted), blank for `before`
    "name",
    "type",
    "port",
    "volume_ul",
    "flow_rate_ul_min",
    "repeat",  # "k/n" when the row repeats
    "x_mm",
    "y_mm",
    "z_um",
    "piezo_um",
    "displacement_um",  # mean of the readings that found the spot
    "spread_um",  # max - min of those readings
    "readings_ok",
    "ok",
    "message",
)


@dataclass
class FocusReading:
    displacement_um: Optional[float]
    spread_um: Optional[float]
    readings_ok: int
    ok: bool
    message: str = ""


def read_focus(imaging, readings: int = READINGS) -> FocusReading:
    """`readings` laser AF displacement readings where the stage is, averaged. Stops at the first None (laser AF
    cannot be used: not initialized, no reference) without asking again."""
    values: List[float] = []
    for _ in range(readings):
        value = imaging.measure_focus_um()
        if value is None:
            return FocusReading(None, None, 0, False, "laser AF not available (not initialized or no reference)")
        if not math.isnan(value):
            values.append(float(value))
    if not values:
        return FocusReading(None, None, 0, False, "no laser AF spot")
    return FocusReading(statistics.fmean(values), max(values) - min(values), len(values), True)


def sequence_context(rows: List[dict], entry, position: int, total: int) -> dict:
    """The CSV columns that name a plan entry: the row it came from and which repeat it is."""
    row_index = getattr(entry, "row", None)
    if isinstance(row_index, int) and 0 <= row_index < len(rows):
        row = rows[row_index]
    else:
        row = getattr(entry, "sequence", None) or {}
    repeats = getattr(entry, "repeats", 1) or 1
    return {
        "sequence": f"{position + 1}/{total}",
        "name": row.get("name") or getattr(entry, "label", "") or "",
        "type": row.get("type", ""),
        "port": row.get("fluidic_port", ""),
        "volume_ul": row.get("volume", ""),
        "flow_rate_ul_min": row.get("flow_rate", ""),
        "repeat": f"{getattr(entry, 'repeat', 1)}/{repeats}" if repeats > 1 else "",
    }


def _fmt(value, digits: int) -> str:
    return "" if value is None else f"{value:.{digits}f}"


class FocusLog:
    """focus_log.csv for one run. Create it on the runner thread: record() and serve() run there, request() on the
    engine's thread (or on the runner thread, which then records at once)."""

    def __init__(
        self,
        path,
        imaging,
        started_at: float,
        readings: int = READINGS,
        wait_s: float = WAIT_S,
        clock: Callable[[], float] = time.time,
    ):
        self._log = squid.logging.get_logger(__name__)
        self.path = Path(path)
        self._imaging = imaging
        self._started_at = started_at
        self._readings = readings
        self._wait_s = wait_s
        self._clock = clock
        self._owner = threading.current_thread()
        self._lock = threading.Lock()
        self._request: Optional[Tuple[dict, threading.Event]] = None

    def record(self, context: dict) -> dict:
        """One reading now (runner thread), appended to the CSV. Never raises."""
        t = self._clock()
        try:
            reading = read_focus(self._imaging, self._readings)
        except Exception as e:
            reading = FocusReading(None, None, 0, False, f"reading failed: {e}")
        try:
            position = self._imaging.stage_position() or {}
        except Exception as e:
            self._log.warning(f"Focus log: could not read the stage position: {e}")
            position = {}
        row = {column: "" for column in COLUMNS}
        row.update({k: v for k, v in context.items() if k in row})
        row.update(
            {
                "time": datetime.fromtimestamp(t).isoformat(timespec="seconds"),
                "elapsed_s": f"{t - self._started_at:.1f}",
                "x_mm": _fmt(position.get("x_mm"), 4),
                "y_mm": _fmt(position.get("y_mm"), 4),
                "z_um": _fmt(position.get("z_um"), 2),
                "piezo_um": _fmt(position.get("piezo_um"), 2),
                "displacement_um": _fmt(reading.displacement_um, 3),
                "spread_um": _fmt(reading.spread_um, 3),
                "readings_ok": reading.readings_ok,
                "ok": "true" if reading.ok else "false",
                "message": reading.message,
            }
        )
        try:
            new = not self.path.exists()
            with open(self.path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=COLUMNS)
                if new:
                    writer.writeheader()
                writer.writerow(row)
        except Exception as e:
            self._log.warning(f"Focus log: could not write {self.path}: {e}")
        where = "before the step" if context.get("point") == "before" else f"after {row['sequence']} {row['name']}"
        value = f"{row['displacement_um']} um" if reading.ok else f"no reading ({reading.message})"
        self._log.info(f"Focus log: step {context.get('step')} ({context.get('round') or ''}) {where}: {value}")
        return row

    def request(self, context: dict) -> bool:
        """Engine thread, between two sequences: ask the runner thread for a reading and wait for it (at most
        wait_s; the run goes on either way). True when the reading was taken."""
        if threading.current_thread() is self._owner:
            self.record(context)
            return True
        done = threading.Event()
        with self._lock:
            self._request = (context, done)
        if done.wait(self._wait_s):
            return True
        with self._lock:
            if self._request is not None and self._request[1] is done:
                self._request = None
        self._log.warning(
            f"Focus log: no reading after sequence {context.get('sequence')} within {self._wait_s:g} s; "
            "the run goes on without it"
        )
        return False

    def serve(self) -> None:
        """Runner thread, from its poll loop: take the reading a sequence boundary is waiting for, if any."""
        with self._lock:
            pending, self._request = self._request, None
        if pending is None:
            return
        context, done = pending
        try:
            self.record(context)
        finally:
            done.set()
