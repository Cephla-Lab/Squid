"""Handshake for acquisition outputs written outside the worker's save jobs.

The GUI saves the mosaic view for timepoint ``t`` from its own thread pool, some time after the worker
says the timepoint finished. The transfer manifest may only call a timepoint (or the run) fully listed
once those files exist, and the worker cannot tell "no save was dispatched" from "the GUI has not got to
it yet". So the worker side records that a reply is *expected* for ``t``; the writer's side answers with
either the save's future or "nothing to write"; and the worker waits on both the answer and the future.
Nothing is expected unless a writer announced itself, so headless runs never wait.
"""

import concurrent.futures
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set, Tuple

import squid.logging

_log = squid.logging.get_logger(__name__)

_POLL_S = 0.05


@dataclass(frozen=True)
class FinishedOutputs:
    outputs: Tuple[Tuple[int, str], ...]  # (time_point, directory written), handed out once
    complete: bool  # every expected reply arrived and every writer in scope finished without error


class PendingOutputs:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._awaiting_reply: Set[int] = set()
        self._writers: Dict[int, List[Tuple[concurrent.futures.Future, str]]] = {}

    def reset(self) -> None:
        with self._cond:
            self._awaiting_reply.clear()
            self._writers.clear()

    def expect(self, time_point: int) -> None:
        """A reply (register or nothing_to_write) will arrive for ``time_point``."""
        with self._cond:
            self._awaiting_reply.add(int(time_point))

    def register(self, time_point: int, future: concurrent.futures.Future, output_dir: str) -> None:
        with self._cond:
            self._writers.setdefault(int(time_point), []).append((future, str(output_dir)))
            self._awaiting_reply.discard(int(time_point))
            self._cond.notify_all()

    def nothing_to_write(self, time_point: int) -> None:
        with self._cond:
            self._awaiting_reply.discard(int(time_point))
            self._cond.notify_all()

    def wait(
        self, time_point: Optional[int], timeout_s: float, abort_fn: Optional[Callable[[], bool]] = None
    ) -> FinishedOutputs:
        """Wait (bounded, abort-aware) for the replies and writers of ``time_point`` (None = all of them).

        Finished writers are returned and forgotten; unfinished ones and missing replies stay, so a later
        wait (the one before the end record) still picks them up.
        """
        deadline = time.monotonic() + timeout_s
        in_scope = (lambda t: True) if time_point is None else (lambda t: t == time_point)

        def expired() -> bool:
            return time.monotonic() >= deadline or (abort_fn is not None and abort_fn())

        with self._cond:
            while any(in_scope(t) for t in self._awaiting_reply) and not expired():
                self._cond.wait(_POLL_S)
            complete = not any(in_scope(t) for t in self._awaiting_reply)
            candidates = [(t, f, d) for t, writers in self._writers.items() if in_scope(t) for f, d in writers]

        while any(not f.done() for _, f, _ in candidates) and not expired():
            concurrent.futures.wait([f for _, f, _ in candidates if not f.done()], timeout=_POLL_S)

        outputs: List[Tuple[int, str]] = []
        with self._cond:
            for t, future, output_dir in candidates:
                if not future.done():
                    complete = False
                    continue
                self._writers[t].remove((future, output_dir))
                if not self._writers[t]:
                    del self._writers[t]
                if future.exception() is not None:
                    _log.warning(f"Output writer for {output_dir} failed: {future.exception()}")
                    complete = False
                    continue
                outputs.append((t, output_dir))
        return FinishedOutputs(tuple(sorted(outputs)), complete)
