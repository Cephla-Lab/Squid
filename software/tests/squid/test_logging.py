import logging
import os
import queue
import sys
import tempfile
import threading

import pytest

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

    # Below ERROR, files are written by the logging writer thread: reading one back needs a flush().
    def line_count():
        assert squid.logging.flush()
        with open(log_file_name, "r") as fh:
            return len(list(fh))

    def contains(string):
        assert squid.logging.flush()
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


# --- Console and file output happen on a writer thread, not on the thread that logs --------------------
#
# A log call that writes and flushes a file gives up the GIL at every write. When another thread is
# CPU-bound, getting it back costs a full interpreter switch interval each time: measured 2026-09-21,
# one log.debug() went from 0.09 ms to 15.2 ms (3 x 5 ms) while another thread pickled camera frames,
# and a camera read thread that logs three lines per frame fell behind its 41 ms frame period. So the
# squid logger hands records to a queue; one writer thread owns the console and the files.


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture
def file_sink(tmp_path):
    path = str(tmp_path / "sink.log")
    handler = squid.logging.add_file_handler(path)
    assert handler is not None
    yield path, handler
    squid.logging.remove_handler(handler)


def test_the_file_is_not_written_on_the_thread_that_logs(file_sink, monkeypatch):
    path, handler = file_sink
    writer_threads = []
    real_emit = handler.emit
    monkeypatch.setattr(
        handler, "emit", lambda record: (writer_threads.append(threading.get_ident()), real_emit(record))
    )

    squid.logging.get_logger("nonblocking").debug("who writes this?")
    assert squid.logging.flush()

    assert writer_threads and threading.get_ident() not in writer_threads
    assert "who writes this?" in _read(path)


def test_the_thread_id_in_the_file_is_the_loggers_not_the_writers(file_sink):
    """The thread id is what made the 2026-09-21 stall diagnosable. It must name the thread that logged."""
    path, _ = file_sink
    ids = {}

    def log_from_a_thread():
        ids["logger"] = threading.get_native_id()
        squid.logging.get_logger("nonblocking").info("from a worker thread")

    thread = threading.Thread(target=log_from_a_thread)
    thread.start()
    thread.join()
    assert squid.logging.flush()

    line = next(line for line in _read(path).splitlines() if "from a worker thread" in line)
    assert f" - {ids['logger']} - " in line


def test_records_reach_the_file_in_the_order_they_were_logged(file_sink):
    path, _ = file_sink
    log = squid.logging.get_logger("nonblocking.order")
    for i in range(400):
        log.debug(f"line {i:04d}")
    assert squid.logging.flush()
    numbers = [int(line.split("line ")[1][:4]) for line in _read(path).splitlines() if "line " in line]
    assert numbers == list(range(400))


def test_an_error_is_on_disk_when_the_log_call_returns(file_sink):
    """The last lines before a crash are the ones that matter. ERROR and above do not wait for a flush()."""
    path, _ = file_sink
    log = squid.logging.get_logger("nonblocking.error")
    log.debug("context before the error")
    log.error("something broke")
    content = _read(path)  # no flush()
    assert "something broke" in content
    assert "context before the error" in content  # everything queued before it went out with it


def test_removing_a_file_handler_first_writes_what_was_logged_to_it(tmp_path):
    path = str(tmp_path / "per-acquisition.log")
    handler = squid.logging.add_file_handler(path)
    squid.logging.get_logger("nonblocking.remove").info("the last line of the acquisition")
    squid.logging.remove_handler(handler)  # no flush(): removing must not lose the tail
    assert "the last line of the acquisition" in _read(path)
    squid.logging.get_logger("nonblocking.remove").info("after removal")
    assert squid.logging.flush()
    assert "after removal" not in _read(path)


def test_handler_levels_still_apply(tmp_path):
    info_path = str(tmp_path / "info.log")
    info_only = squid.logging.add_file_handler(info_path, level=logging.INFO)
    try:
        log = squid.logging.get_logger("nonblocking.levels")
        log.debug("too quiet for this file")
        log.info("loud enough")
        assert squid.logging.flush()
        content = _read(info_path)
        assert "loud enough" in content and "too quiet" not in content
    finally:
        squid.logging.remove_handler(info_only)


def test_the_current_log_file_is_still_found(file_sink):
    path, _ = file_sink
    assert squid.logging.get_current_log_file_path() is not None


@pytest.mark.skipif(sys.platform == "win32", reason="fork does not exist on Windows")
def test_a_forked_child_can_still_log(tmp_path):
    """The writer thread does not survive fork() (Linux starts the save subprocess that way). The
    child must get a writer of its own, or everything it logs sits in a queue nobody reads."""
    path = str(tmp_path / "forked.log")
    handler = squid.logging.add_file_handler(path)
    try:
        pid = os.fork()
        if pid == 0:
            try:
                squid.logging.get_logger("nonblocking.fork").info("hello from the child")
                ok = squid.logging.flush(timeout_s=5)
            finally:
                os._exit(0 if ok else 3)
        _, status = os.waitpid(pid, 0)
        assert os.WEXITSTATUS(status) == 0, "the child could not flush its log"
        assert "hello from the child" in _read(path)
    finally:
        squid.logging.remove_handler(handler)
