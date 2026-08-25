"""In-process event bus backing the SSE stream (spec §2.6)."""

import itertools
import queue
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from typing import List, Tuple


@dataclass(frozen=True)
class Event:
    id: int
    event: str
    data: dict


class EventBus:
    """Thread-safe pub/sub with a bounded replay buffer.

    Subscribers get an unbounded queue.Queue of Event. Replay resumes from a
    Last-Event-Id; if events between that id and the oldest buffered event
    have been evicted from the ring buffer, the second return value is True
    and the client must hard-resync (spec §2.6).
    """

    def __init__(self, buffer_size: int = 1024):
        self._lock = threading.Lock()
        self._counter = itertools.count(1)
        self._buffer: deque = deque(maxlen=buffer_size)
        self._subscribers: List[queue.Queue] = []
        self._session_id = uuid.uuid4().hex
        self._last_id = 0

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def last_event_id(self) -> int:
        with self._lock:
            return self._last_id

    def publish(self, event: str, data: dict) -> Event:
        with self._lock:
            ev = Event(id=next(self._counter), event=event, data=data)
            self._last_id = ev.id
            self._buffer.append(ev)
            subscribers = list(self._subscribers)
        for q in subscribers:
            q.put(ev)
        return ev

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def replay_since(self, last_event_id: int) -> Tuple[List[Event], bool]:
        """Return events after `last_event_id`, plus whether a gap occurred.

        The oldest id still available in the ring buffer defines the boundary
        of what can be replayed without loss. If the client's last-seen id
        falls more than one below that boundary, at least one event it never
        saw has been evicted, so `gap` is True and the client must hard-resync.
        When the buffer is empty, the boundary is simply "one past the last
        published id" (nothing has been evicted since there is nothing to
        evict from).
        """
        with self._lock:
            buffered = list(self._buffer)
            last_id = self._last_id
        oldest_buffered = buffered[0].id if buffered else last_id + 1
        gap = last_event_id < oldest_buffered - 1
        missed = [e for e in buffered if e.id > last_event_id]
        return missed, gap
