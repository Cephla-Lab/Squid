import logging
import queue
import tempfile

import squid.logging
from squid.logging import BufferingHandler


class TestBufferingHandler:
    """Tests for BufferingHandler - the headless-safe logging handler with bounded buffer."""

    def test_buffers_messages_at_or_above_level(self):
        """Handler buffers messages at or above its configured level."""
        handler = BufferingHandler(min_level=logging.WARNING)

        logger = logging.getLogger("test.buffering.level")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)

        try:
            logger.debug("debug - should be ignored")
            logger.info("info - should be ignored")
            logger.warning("warning - should be captured")
            logger.error("error - should be captured")

            pending = handler.get_pending()

            assert len(pending) == 2
            assert pending[0][0] == logging.WARNING
            assert "warning - should be captured" in pending[0][2]
            assert pending[1][0] == logging.ERROR
            assert "error - should be captured" in pending[1][2]
        finally:
            logger.removeHandler(handler)

    def test_get_pending_clears_buffer(self):
        """get_pending() returns messages and clears the buffer."""
        handler = BufferingHandler(min_level=logging.WARNING)

        logger = logging.getLogger("test.buffering.clear")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)

        try:
            logger.warning("first warning")
            logger.warning("second warning")

            # First call returns both messages
            first_pending = handler.get_pending()
            assert len(first_pending) == 2

            # Second call returns empty (buffer was cleared)
            second_pending = handler.get_pending()
            assert len(second_pending) == 0

            # New messages still get captured
            logger.error("new error")
            third_pending = handler.get_pending()
            assert len(third_pending) == 1
        finally:
            logger.removeHandler(handler)

    def test_returns_tuple_of_level_name_message(self):
        """get_pending() returns tuples of (level, logger_name, formatted_message)."""
        handler = BufferingHandler(min_level=logging.WARNING)

        logger = logging.getLogger("test.buffering.tuple")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)

        try:
            logger.warning("test message")

            pending = handler.get_pending()
            assert len(pending) == 1

            level, name, message = pending[0]
            assert level == logging.WARNING
            assert name == "test.buffering.tuple"
            assert "test message" in message
        finally:
            logger.removeHandler(handler)

    def test_queue_overflow_drops_messages_and_tracks_count(self):
        """When queue is full, new messages are dropped (not blocking) and counted."""
        handler = BufferingHandler(min_level=logging.WARNING)
        # Manually reduce max size for testing
        original_max = BufferingHandler.MAX_BUFFERED_MESSAGES

        try:
            # Create a handler with tiny queue for testing overflow
            handler._queue = queue.Queue(maxsize=3)

            logger = logging.getLogger("test.buffering.overflow")
            logger.setLevel(logging.DEBUG)
            logger.addHandler(handler)

            try:
                # Initially no dropped messages
                assert handler.dropped_count == 0

                # Fill the queue
                logger.warning("msg 1")
                logger.warning("msg 2")
                logger.warning("msg 3")

                # Still no dropped messages
                assert handler.dropped_count == 0

                # This should be dropped (queue full), not block
                logger.warning("msg 4 - should be dropped")
                logger.warning("msg 5 - should be dropped")

                # Dropped count should be 2
                assert handler.dropped_count == 2

                pending = handler.get_pending()
                # Only first 3 should be present
                assert len(pending) == 3
                assert "msg 1" in pending[0][2]
                assert "msg 2" in pending[1][2]
                assert "msg 3" in pending[2][2]

                # Dropped count persists after get_pending
                assert handler.dropped_count == 2
            finally:
                logger.removeHandler(handler)
        finally:
            BufferingHandler.MAX_BUFFERED_MESSAGES = original_max

    def test_empty_buffer_returns_empty_list(self):
        """get_pending() returns empty list when no messages buffered."""
        handler = BufferingHandler(min_level=logging.WARNING)
        assert handler.get_pending() == []

    def test_can_be_used_without_qt(self):
        """BufferingHandler can be imported and used without Qt installed.

        This test verifies the handler is headless-safe. The import at the top
        of this file already proves Qt isn't required to import BufferingHandler.
        This test confirms full functionality works without Qt.
        """
        # No Qt imports in this test file - BufferingHandler was imported at top
        handler = BufferingHandler(min_level=logging.WARNING)

        logger = logging.getLogger("test.buffering.headless")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)

        try:
            logger.error("headless message")

            pending = handler.get_pending()
            assert len(pending) == 1
            assert "headless message" in pending[0][2]
        finally:
            logger.removeHandler(handler)

    def test_includes_thread_id_in_formatted_message(self):
        """Formatted messages include thread_id from the filter."""
        handler = BufferingHandler(min_level=logging.WARNING)

        logger = logging.getLogger("test.buffering.threadid")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)

        try:
            logger.warning("thread test")

            pending = handler.get_pending()
            assert len(pending) == 1
            # The format includes thread_id after timestamp
            # Format: "%(asctime)s.%(msecs)03d - %(thread_id)d - ..."
            # Check the message contains a numeric thread ID
            message = pending[0][2]
            # Message should contain " - <number> - " pattern for thread_id
            import re

            assert re.search(r" - \d+ - ", message), f"Expected thread_id in message: {message}"
        finally:
            logger.removeHandler(handler)


def test_root_logger():
    root_logger = squid.logging.get_logger()
    assert root_logger.name == squid.logging._squid_root_logger_name


def test_children_loggers():
    child_a = "a"
    child_b = "b"

    child_a_logger = squid.logging.get_logger(child_a)
    child_b_logger = child_a_logger.getChild(child_b)

    assert child_a_logger.name == f"{squid.logging._squid_root_logger_name}.{child_a}"
    assert child_b_logger.name == f"{squid.logging._squid_root_logger_name}.{child_a}.{child_b}"


def test_file_loggers():
    log_file_name = tempfile.mktemp()

    def line_count():
        with open(log_file_name, "r") as fh:
            return len(list(fh))

    def contains(string):
        with open(log_file_name, "r") as fh:
            for l in fh:
                if string in l:
                    return True
        return False

    assert squid.logging.add_file_logging(log_file_name)
    assert not squid.logging.add_file_logging(log_file_name)

    initial_line_count = line_count()
    log = squid.logging.get_logger("log test")
    squid.logging.set_stdout_log_level(logging.DEBUG)

    log.debug("debug msg")
    debug_ling_count = line_count()
    assert debug_ling_count > initial_line_count

    squid.logging.set_stdout_log_level(logging.INFO)

    a_debug_message = "another message but when stdout is at INFO"
    log.debug(a_debug_message)
    assert line_count() > debug_ling_count
    assert contains(a_debug_message)
