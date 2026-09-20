import threading
from collections import deque
from typing import Deque, Generic, List, Optional, Sequence, Tuple, TypeVar

T = TypeVar("T")


class PendingCaptures(Generic[T]):
    """The captures the acquisition worker is expecting frames for, in order.

    The camera delivers frames on its own thread and carries no acquisition metadata, so the
    worker pairs each arriving frame with the next expected capture. An ordinary acquisition
    expects one capture at a time; a hardware-sequenced burst queues all N up front and the N
    frames arrive back-to-back with no host action in between.

    A pairing mistake does not crash anything - it saves the right pixels under the wrong
    channel or z level. So within one batch the camera's frame ids must be consecutive, and a
    gap is recorded for the caller to act on rather than tolerated. Across separate batches no
    continuity is required (the camera may have produced other frames in between).

    One batch may be outstanding at a time: queueing a second one would make "which capture does
    this frame belong to" depend on timing.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._queue: Deque[T] = deque()
        self._next_frame_id: Optional[int] = None
        self._first_gap: Optional[Tuple[int, int]] = None

    def expect(self, captures: Sequence[T]) -> None:
        """Queue the captures the next frames belong to. Called before the trigger / SEQ_RUN."""
        if not captures:
            raise ValueError("expect() needs at least one capture")
        with self._lock:
            if self._queue:
                raise RuntimeError(
                    f"{len(self._queue)} capture(s) are still outstanding; a new batch cannot be queued until "
                    "their frames arrived or drop_remaining() was called"
                )
            self._queue.extend(captures)
            self._next_frame_id = None
            self._first_gap = None

    def take(self, frame_id: int) -> Optional[T]:
        """Pair an arriving frame with the next expected capture (camera thread).

        Returns None when no capture is expected - the frame belongs to nothing.
        """
        with self._lock:
            if not self._queue:
                return None
            if self._next_frame_id is not None and frame_id != self._next_frame_id and self._first_gap is None:
                self._first_gap = (self._next_frame_id, frame_id)
            self._next_frame_id = frame_id + 1
            return self._queue.popleft()

    def drop_remaining(self) -> List[T]:
        """Forget the captures whose frames will never come (cancel, failed run)."""
        with self._lock:
            remaining = list(self._queue)
            self._queue.clear()
            return remaining

    @property
    def empty(self) -> bool:
        with self._lock:
            return not self._queue

    def __len__(self) -> int:
        with self._lock:
            return len(self._queue)

    @property
    def gap_detected(self) -> bool:
        with self._lock:
            return self._first_gap is not None

    @property
    def first_gap(self) -> Optional[Tuple[int, int]]:
        """(expected frame id, frame id that arrived) for the first gap in the current batch."""
        with self._lock:
            return self._first_gap
