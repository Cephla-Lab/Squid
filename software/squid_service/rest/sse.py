"""SSE endpoint (spec §2.6): session_started, Last-Event-Id replay, resume_gap, live tail.

The stream is exposed as a standalone async generator (``sse_event_stream``) rather
than a closure so it can be driven directly in tests. Both Starlette's ``TestClient``
and httpx's ``ASGITransport`` buffer the *entire* ASGI response before returning and
only deliver ``http.disconnect`` after the response has completed. An infinite SSE
generator therefore never terminates through those transports (its ``while True``
tail loop waits for a disconnect that can only arrive once the response completes,
which can only happen once the loop ends) -> deadlock. Tests iterate
``sse_event_stream`` directly, read a bounded number of events, then close the
generator, which runs the ``finally`` that unsubscribes from the bus.
"""

import asyncio
import functools
import json
import queue
from typing import AsyncIterator, Awaitable, Callable, Optional

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse


async def sse_event_stream(
    service,
    raw_last: Optional[str],
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[dict]:
    """Yield SSE dicts: session_started, optional replay + resume_gap, then live tail.

    Subscribes to the bus BEFORE computing the replay so no event published between
    subscribe and the first yield is missed. ``is_disconnected`` (production:
    ``request.is_disconnected``) is re-checked every loop iteration so the live tail
    stops promptly when the client goes away. The ``finally`` always unsubscribes,
    so closing the generator (client disconnect, cancellation, or ``aclose()``)
    releases the subscription.
    """
    bus = service.events
    q = bus.subscribe()  # subscribe BEFORE replay so nothing is missed
    yielded_up_to = 0
    try:
        yield {
            "id": str(bus.last_event_id),
            "event": "session_started",
            "data": json.dumps(
                {
                    "session_id": bus.session_id,
                    "current_state": service.state.value,
                    "last_event_id": bus.last_event_id,
                }
            ),
        }
        if raw_last is not None:
            try:
                last_id = int(raw_last)
            except ValueError:
                last_id = 0
            missed, gap = bus.replay_since(last_id)
            if gap:
                yield {
                    "id": str(bus.last_event_id),
                    "event": "resume_gap",
                    "data": json.dumps({"last_event_id": bus.last_event_id}),
                }
            for ev in missed:
                yielded_up_to = ev.id
                yield {"id": str(ev.id), "event": ev.event, "data": json.dumps(ev.data)}
        loop = asyncio.get_running_loop()
        while True:
            if await is_disconnected():
                break
            try:
                ev = await loop.run_in_executor(None, functools.partial(q.get, timeout=0.5))
            except queue.Empty:
                continue
            if ev.id <= yielded_up_to:  # already delivered via replay
                continue
            yield {"id": str(ev.id), "event": ev.event, "data": json.dumps(ev.data)}
    finally:
        bus.unsubscribe(q)


def build_sse_router() -> APIRouter:
    router = APIRouter(tags=["events"])

    @router.get("/v1/events")
    async def events(request: Request):
        service = request.app.state.service
        return EventSourceResponse(
            sse_event_stream(service, request.headers.get("last-event-id"), request.is_disconnected)
        )

    return router
