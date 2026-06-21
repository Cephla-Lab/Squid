from typing import Optional, Tuple


class CountStop:
    def __init__(self, target: int):
        self.target = target

    def met(self, emitted: int) -> bool:
        return emitted >= self.target


class RecordingRouter:
    """Maps incoming frames to (t,c,z)=(t_index,0,0), downsampling to `fps`."""

    def __init__(self, fps: float):
        self._min_period = 1.0 / fps if fps and fps > 0 else 0.0
        self._t_index = 0
        self._last_emit_ts: Optional[float] = None

    def route(self, timestamp: float) -> Optional[Tuple[int, int, int]]:
        if self._last_emit_ts is not None and (timestamp - self._last_emit_ts) < self._min_period - 1e-9:
            return None
        idx = (self._t_index, 0, 0)
        self._t_index += 1
        self._last_emit_ts = timestamp
        return idx
