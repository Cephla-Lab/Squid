import logging
import tempfile

import squid.core.logging


def test_root_logger():
    root_logger = squid.core.logging.get_logger()
    assert root_logger.name == squid.core.logging._squid_root_logger_name


def test_children_loggers():
    child_a = "a"
    child_b = "b"

    child_a_logger = squid.core.logging.get_logger(child_a)
    child_b_logger = child_a_logger.getChild(child_b)

    assert child_a_logger.name == f"{squid.core.logging._squid_root_logger_name}.{child_a}"
    assert (
        child_b_logger.name
        == f"{squid.core.logging._squid_root_logger_name}.{child_a}.{child_b}"
    )


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

    assert squid.core.logging.add_file_logging(log_file_name)
    assert not squid.core.logging.add_file_logging(log_file_name)

    initial_line_count = line_count()
    log = squid.core.logging.get_logger("log test")
    squid.core.logging.set_stdout_log_level(logging.DEBUG)

    log.debug("debug msg")
    debug_ling_count = line_count()
    assert debug_ling_count > initial_line_count

    squid.core.logging.set_stdout_log_level(logging.INFO)

    a_debug_message = "another message but when stdout is at INFO"
    log.debug(a_debug_message)
    assert line_count() > debug_ling_count
    assert contains(a_debug_message)


def test_add_file_handler():
    """Test that add_file_handler creates a handler that logs messages."""
    log_file_name = tempfile.mktemp(suffix=".log")

    def line_count():
        with open(log_file_name, "r") as fh:
            return len(list(fh))

    def contains(string):
        with open(log_file_name, "r") as fh:
            for line in fh:
                if string in line:
                    return True
        return False

    # Add a file handler
    handler = squid.core.logging.add_file_handler(log_file_name)
    assert handler is not None

    # Log something
    log = squid.core.logging.get_logger("test_add_file_handler")
    test_message = "test message for add_file_handler"
    log.info(test_message)

    # Verify the message was written
    assert line_count() > 0
    assert contains(test_message)

    # Clean up
    squid.core.logging.remove_handler(handler)


def test_add_file_handler_no_duplicate():
    """Test that add_file_handler returns None if handler already exists."""
    log_file_name = tempfile.mktemp(suffix=".log")

    handler1 = squid.core.logging.add_file_handler(log_file_name)
    assert handler1 is not None

    # Second call without replace_existing should return None
    handler2 = squid.core.logging.add_file_handler(log_file_name, replace_existing=False)
    assert handler2 is None

    # Clean up
    squid.core.logging.remove_handler(handler1)


def test_add_file_handler_replace_existing():
    """Test that add_file_handler can replace an existing handler."""
    log_file_name = tempfile.mktemp(suffix=".log")

    handler1 = squid.core.logging.add_file_handler(log_file_name)
    assert handler1 is not None

    # Second call with replace_existing should return a new handler
    handler2 = squid.core.logging.add_file_handler(log_file_name, replace_existing=True)
    assert handler2 is not None
    assert handler2 is not handler1

    # Clean up
    squid.core.logging.remove_handler(handler2)


def test_remove_handler():
    """Test that remove_handler removes and closes a handler."""
    log_file_name = tempfile.mktemp(suffix=".log")

    handler = squid.core.logging.add_file_handler(log_file_name)
    assert handler is not None

    root_logger = squid.core.logging.get_logger()
    assert handler in root_logger.handlers

    # Remove the handler
    squid.core.logging.remove_handler(handler)

    # Handler should no longer be in the list
    assert handler not in root_logger.handlers


def test_remove_handler_safe_double_call():
    """Test that remove_handler is safe to call multiple times."""
    log_file_name = tempfile.mktemp(suffix=".log")

    handler = squid.core.logging.add_file_handler(log_file_name)
    assert handler is not None

    # Remove twice - should not raise
    squid.core.logging.remove_handler(handler)
    squid.core.logging.remove_handler(handler)  # Should not raise
