"""Tests for squid.core.logging.BufferingHandler."""

import logging
import threading

import pytest

from squid.core.logging import BufferingHandler


class TestBufferingHandler:
    def test_basic_emit_and_get_pending(self):
        handler = BufferingHandler(min_level=logging.WARNING)
        logger = logging.getLogger("test.buffering.basic")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        logger.warning("test warning")
        logger.error("test error")

        messages = handler.get_pending()
        assert len(messages) == 2
        assert messages[0][0] == logging.WARNING
        assert "test warning" in messages[0][2]
        assert messages[1][0] == logging.ERROR
        assert "test error" in messages[1][2]

        logger.removeHandler(handler)

    def test_level_filtering(self):
        handler = BufferingHandler(min_level=logging.WARNING)
        logger = logging.getLogger("test.buffering.filter")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        logger.info("should not appear")
        logger.debug("should not appear")
        logger.warning("should appear")

        messages = handler.get_pending()
        assert len(messages) == 1
        assert "should appear" in messages[0][2]

        logger.removeHandler(handler)

    def test_get_pending_drains_queue(self):
        handler = BufferingHandler()
        logger = logging.getLogger("test.buffering.drain")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        logger.warning("msg1")
        messages = handler.get_pending()
        assert len(messages) == 1

        # Second call should return empty
        messages = handler.get_pending()
        assert len(messages) == 0

        logger.removeHandler(handler)

    def test_queue_overflow_tracks_dropped_count(self):
        handler = BufferingHandler(min_level=logging.WARNING, maxsize=3)
        logger = logging.getLogger("test.buffering.overflow")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        for i in range(5):
            logger.warning(f"msg {i}")

        assert handler.dropped_count == 2
        messages = handler.get_pending()
        assert len(messages) == 3

        logger.removeHandler(handler)

    def test_thread_safety(self):
        handler = BufferingHandler(min_level=logging.WARNING, maxsize=1000)
        logger = logging.getLogger("test.buffering.threads")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        def emit_messages(thread_id, count):
            for i in range(count):
                logger.warning(f"thread {thread_id} msg {i}")

        threads = [threading.Thread(target=emit_messages, args=(t, 50)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        messages = handler.get_pending()
        assert len(messages) == 200
        assert handler.dropped_count == 0

        logger.removeHandler(handler)
