"""Squid's step-labeled sensor recording over the library's SensorSeries buffers.

The library deliberately keeps no recorder — each standalone plot writes its own CSV
(fluidics.sensor_series) — but Squid's Temperature tab records all channels into one
long-format CSV whose rows carry the running protocol step, so a TEC excursion can be
mapped back to the hybridization or wash it happened in. Producer threads feed it; the
GUI toggles it. Never raises on I/O — logs and carries on."""

import csv
import threading
import time
from typing import Dict, Optional

from fluidics.sensor_series import SensorSeries

import squid.logging

_log = squid.logging.get_logger(__name__)

FLUSH_INTERVAL_SECONDS = 1.0  # time-based, not per row: a 2 Hz feed must not sync the disk twice a second


class SensorRecorder:
    """Per-channel buffers plus an operator-toggled long-format CSV (time,channel,value,step)."""

    def __init__(self):
        self._series: Dict[str, SensorSeries] = {}
        self._lock = threading.Lock()  # producer threads record; the GUI thread toggles
        self._file = None
        self._writer = None
        self._step = ""
        self._flushed_at = 0.0

    def channel(self, name: str) -> SensorSeries:
        series = self._series.get(name)
        if series is None:
            with self._lock:
                series = self._series.setdefault(name, SensorSeries())
        return series

    @property
    def recording(self) -> bool:
        return self._file is not None

    def set_step_label(self, label: str) -> None:
        """What the run is doing, tagged onto the rows written from now on."""
        with self._lock:
            self._step = label or ""

    def record(self, name: str, value: float, t: Optional[float] = None) -> None:
        t = time.time() if t is None else t
        self.channel(name).append(value, t)
        with self._lock:
            if self._writer is None:
                return
            try:
                self._writer.writerow([f"{t:.3f}", name, value, self._step])
                now = time.monotonic()
                if now - self._flushed_at >= FLUSH_INTERVAL_SECONDS:
                    self._file.flush()
                    self._flushed_at = now
            except (OSError, csv.Error) as e:
                _log.warning(f"Sensor CSV write failed; stopping the recording: {e}")
                self._close_locked()

    def start_recording(self, path: str) -> bool:
        """Open `path` and write the header; True if the recording started."""
        with self._lock:
            self._close_locked()
            try:
                self._file = open(path, "w", newline="", encoding="utf-8")
                self._writer = csv.writer(self._file)
                self._writer.writerow(["time", "channel", "value", "step"])
                self._file.flush()
                self._flushed_at = time.monotonic()
                return True
            except OSError as e:
                _log.warning(f"Could not start the sensor recording at {path}: {e}")
                self._close_locked()
                return False

    def stop_recording(self) -> None:
        """Close the recording if one is open (flushes the tail). Idempotent."""
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except OSError as e:
                _log.warning(f"Sensor recording did not close cleanly: {e}")
        self._file = None
        self._writer = None
