"""Qt-free sensor buffers for the fluidics tabs: producer threads append, the GUI polls windows on a
timer, and a CSV is written only while the operator records. Never raises on I/O — logs and carries on."""

import bisect
import csv
import threading
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

import squid.logging

_log = squid.logging.get_logger(__name__)


class SensorSeries:
    def __init__(self, maxlen: int = 36000):
        self._t = deque(maxlen=maxlen)
        self._v = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, value: float, t: Optional[float] = None) -> None:
        with self._lock:
            self._t.append(time.time() if t is None else t)
            self._v.append(value)

    def window(self, seconds: Optional[float] = None) -> Tuple[List[float], List[float]]:
        with self._lock:
            ts, vs = list(self._t), list(self._v)
        if seconds is not None and ts:
            start = bisect.bisect_left(ts, ts[-1] - seconds)  # timestamps are appended in order
            ts, vs = ts[start:], vs[start:]
        return ts, vs


class SensorRecorder:
    def __init__(self):
        self._series: Dict[str, SensorSeries] = {}
        self._lock = threading.RLock()
        self._file = None
        self._writer = None
        self._step = ""

    def channel(self, name: str) -> SensorSeries:
        with self._lock:
            return self._series.setdefault(name, SensorSeries())

    @property
    def recording(self) -> bool:
        return self._file is not None

    def set_step_label(self, label: str) -> None:
        self._step = label or ""

    def record(self, name: str, value: float, t: Optional[float] = None) -> None:
        t = time.time() if t is None else t
        self.channel(name).append(value, t)
        with self._lock:
            if self._writer is not None:
                try:
                    self._writer.writerow([f"{t:.3f}", name, value, self._step])
                    self._file.flush()
                except Exception as e:
                    _log.warning(f"Sensor CSV write failed; stopping the recording: {e}")
                    self.stop_recording()

    def start_recording(self, path: str) -> bool:
        with self._lock:
            self.stop_recording()
            try:
                self._file = open(path, "w", newline="", encoding="utf-8")
                self._writer = csv.writer(self._file)
                self._writer.writerow(["time", "channel", "value", "step"])
                self._file.flush()
                return True
            except OSError as e:
                _log.warning(f"Could not start the sensor recording at {path}: {e}")
                self._file = None
                self._writer = None
                return False

    def stop_recording(self) -> None:
        with self._lock:
            if self._file is not None:
                try:
                    self._file.close()
                except OSError:
                    pass
            self._file = None
            self._writer = None
