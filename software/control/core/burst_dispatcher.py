"""Hands validated bursts to the save pipeline on its own thread.

A hardware-sequenced burst reaches the save jobs only after it has been validated (count, order,
controller status). Doing that hand-over on the worker thread serialises it with everything the
worker owns - the stage move, autofocus, the next burst - and the hand-over is mostly WAITING: the
pending-jobs cap is smaller than a burst, so after the first few frames the caller blocks until the
save subprocess has worked the queue down (bench 2026-09-20: 0.36 s of a 1.47 s FOV).

In software-sequenced acquisitions that wait never lands on the worker: frames are dispatched from
the camera callback thread while the worker moves on. This gives a sequenced acquisition the same
shape without giving up validate-before-save - a burst is submitted only once it is valid.

Guarantees:
- ORDER: one thread, one FIFO. Bursts are dispatched in the order they were submitted, so the save
  jobs see frames in the same order as today, across FOVs and time points.
- MEMORY: at most one burst waits behind the one in flight. submit() blocks beyond that, which is how
  a disk that cannot keep up slows the acquisition down instead of filling memory.
- NO DEADLOCK: if dispatch raises, on_error is called once (the acquisition aborts) and later bursts
  are consumed and discarded, so a worker that is still handing over can never block forever.
"""

import queue
import threading
from typing import Any, Callable, Optional

import squid.logging

_STOP = object()


class BurstDispatcher:
    def __init__(self, dispatch: Callable[[Any], None], on_error: Callable[[Exception], None]):
        self._dispatch = dispatch
        self._on_error = on_error
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=1)
        self._lock = threading.Condition()
        self._outstanding = 0  # submitted and not yet dispatched (or discarded)
        self._failed = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="burst-dispatcher", daemon=True)
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def submit(self, burst: Any) -> None:
        """Queue a VALIDATED burst. Returns at once unless a burst is already waiting."""
        with self._lock:
            self._outstanding += 1
        self._queue.put(burst)

    def drain(self, timeout_s: Optional[float] = None) -> bool:
        """Wait until everything submitted so far has been dispatched. False on timeout."""
        with self._lock:
            return self._lock.wait_for(lambda: self._outstanding == 0, timeout=timeout_s)

    def stop(self) -> None:
        """Dispatch what was submitted, then end the thread."""
        if self._thread is None:
            return
        self._queue.put(_STOP)
        self._thread.join()
        self._thread = None

    def _run(self) -> None:
        while True:
            burst = self._queue.get()
            if burst is _STOP:
                return
            try:
                if self._failed:
                    self._log.error("A validated burst was discarded: the save pipeline failed earlier.")
                else:
                    self._dispatch(burst)
            except Exception as e:  # the thread must survive: a dead consumer would block submit() forever
                self._failed = True
                self._log.exception("Dispatching a validated burst failed; aborting the acquisition.")
                self._on_error(e)
            finally:
                with self._lock:
                    self._outstanding -= 1
                    self._lock.notify_all()
