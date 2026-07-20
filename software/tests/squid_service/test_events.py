import queue

from squid_service.events import EventBus


def test_publish_monotonic_ids_and_subscriber_delivery():
    bus = EventBus()
    q = bus.subscribe()
    e1 = bus.publish("state_changed", {"old": "A", "new": "B"})
    e2 = bus.publish("progress", {"n": 1})
    assert (e1.id, e2.id) == (1, 2)
    assert q.get_nowait() is e1
    assert q.get_nowait() is e2
    bus.unsubscribe(q)
    bus.publish("progress", {"n": 2})
    with __import__("pytest").raises(queue.Empty):
        q.get_nowait()


def test_replay_since():
    bus = EventBus()
    for i in range(5):
        bus.publish("progress", {"n": i})
    events, gap = bus.replay_since(2)
    assert [e.id for e in events] == [3, 4, 5]
    assert gap is False


def test_replay_gap_when_buffer_overflows():
    bus = EventBus(buffer_size=3)
    for i in range(10):
        bus.publish("progress", {"n": i})
    events, gap = bus.replay_since(1)  # id 2 has been evicted
    assert gap is True
    assert [e.id for e in events] == [8, 9, 10]


def test_replay_since_current_is_empty_no_gap():
    bus = EventBus(buffer_size=3)
    for i in range(10):
        bus.publish("progress", {"n": i})
    events, gap = bus.replay_since(10)
    assert events == [] and gap is False


def test_session_id_stable():
    bus = EventBus()
    assert bus.session_id == bus.session_id
    assert len(bus.session_id) >= 8
