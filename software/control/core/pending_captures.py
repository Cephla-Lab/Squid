import threading
from collections import deque
from typing import Any, Callable, Deque, Generic, List, Optional, Sequence, Tuple, TypeVar

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

    A batch belongs to one run of the camera stream, and says where its paired frames go. The
    worker gives a batch up when its burst fails, restarts the stream and queues the retry's batch
    for the new run - possibly while a frame callback of the old run is still on its way to pairing.
    That frame is refused by take() under the same lock that replaced the batch, so it can neither
    consume the retry's captures nor, if it was paired before the batch was given up, be delivered
    anywhere but to the sink of its own batch.

    An ordinary capture's batch is queued for no particular run instead: it follows the camera. The
    trigger itself may restart the stream under it (a confocal unit re-arming the camera after a
    stalled scan, then scanning again) and the frame then arrives from the new run. The worker never
    restarts the stream under an ordinary capture, so no frame of an earlier run can be waiting for it.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._queue: Deque[T] = deque()
        self._stream_generation: Optional[int] = None
        self._deliver: Optional[Callable[[Any, T], None]] = None
        self._next_frame_id: Optional[int] = None
        self._first_gap: Optional[Tuple[int, int]] = None

    def expect(
        self, captures: Sequence[T], *, stream_generation: Optional[int], deliver: Callable[[Any, T], None]
    ) -> None:
        """Queue the captures the next frames belong to. Called before the trigger / SEQ_RUN.

        stream_generation: the run of the camera stream the frames will be read in, or None for a batch
            that follows the camera and pairs frames of whichever run they come from.
        deliver: where each (frame, capture) pair goes.
        """
        if not captures:
            raise ValueError("expect() needs at least one capture")
        with self._lock:
            if self._queue:
                raise RuntimeError(
                    f"{len(self._queue)} capture(s) are still outstanding; a new batch cannot be queued until "
                    "their frames arrived or drop_remaining() was called"
                )
            self._queue.extend(captures)
            self._stream_generation = stream_generation
            self._deliver = deliver
            self._next_frame_id = None
            self._first_gap = None

    def take(self, frame_id: int, *, stream_generation: int) -> Optional[Tuple[T, Callable[[Any, T], None]]]:
        """Pair an arriving frame with the next expected capture (camera thread).

        Returns (capture, deliver) for the batch the frame belongs to, or None when it belongs to
        nothing: no capture is expected, or the queued batch is for another run of the camera
        stream than the one the frame was read in (it does not count as a gap either).
        """
        with self._lock:
            if not self._queue:
                return None
            if self._stream_generation is not None and stream_generation != self._stream_generation:
                return None
            if self._next_frame_id is not None and frame_id != self._next_frame_id and self._first_gap is None:
                self._first_gap = (self._next_frame_id, frame_id)
            self._next_frame_id = frame_id + 1
            return self._queue.popleft(), self._deliver

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
    def stream_generation(self) -> Optional[int]:
        """The run of the camera stream the queued batch is for; None when nothing is queued or it follows the camera."""
        with self._lock:
            return self._stream_generation if self._queue else None

    @property
    def gap_detected(self) -> bool:
        with self._lock:
            return self._first_gap is not None

    @property
    def first_gap(self) -> Optional[Tuple[int, int]]:
        """(expected frame id, frame id that arrived) for the first gap in the current batch."""
        with self._lock:
            return self._first_gap
