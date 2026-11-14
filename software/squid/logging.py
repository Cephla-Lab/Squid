import atexit
import logging as py_logging
import logging.handlers
import multiprocessing.util
import os
import os.path
import queue
import threading
import time
from typing import List, Optional, Tuple, Type
from types import TracebackType
import sys
import platformdirs

_squid_root_logger_name = "squid"
_baseline_log_format = (
    "%(asctime)s.%(msecs)03d - %(thread_id)d - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)"
)
_baseline_log_dateformat = "%Y-%m-%d %H:%M:%S"

# Public aliases for use by other modules (e.g., WarningErrorWidget in control/widgets.py)
LOG_FORMAT = _baseline_log_format
LOG_DATEFORMAT = _baseline_log_dateformat


# The idea for this CustomFormatter is cribbed from https://stackoverflow.com/a/56944256
class _CustomFormatter(py_logging.Formatter):
    GRAY = "\x1b[38;20m"
    YELLOW = "\x1b[33;20m"
    RED = "\x1b[31;20m"
    BOLD_RED = "\x1b[31;1m"
    RESET = "\x1b[0m"
    FORMAT = _baseline_log_format

    FORMATS = {
        py_logging.DEBUG: GRAY + FORMAT + RESET,
        py_logging.INFO: GRAY + FORMAT + RESET,
        py_logging.WARNING: YELLOW + FORMAT + RESET,
        py_logging.ERROR: RED + FORMAT + RESET,
        py_logging.CRITICAL: BOLD_RED + FORMAT + RESET,
    }

    # NOTE(imo): The datetime hackery is so that we can have millisecond timestamps using a period instead
    # of comma.  The default asctime + datefmt uses a comma.
    FORMATTERS = {
        level: py_logging.Formatter(fmt, datefmt=_baseline_log_dateformat) for (level, fmt) in FORMATS.items()
    }

    def format(self, record):
        return self.FORMATTERS[record.levelno].format(record)


def _thread_id_filter(record: logging.LogRecord):
    """Inject thread_id to log records: the thread that LOGGED. The first handler to see a record stamps
    it; the console and the files are written by another thread (see below) and must not re-stamp it."""
    record.__dict__.setdefault("thread_id", threading.get_native_id())
    return True


_COLOR_STREAM_HANDLER = py_logging.StreamHandler()
_COLOR_STREAM_HANDLER.setFormatter(_CustomFormatter())

# Make sure the squid root logger has all the handlers we want setup.  We could move this into a helper so it
# isn't done at the module level, but not needing to remember to call some helper to setup formatting is nice.
# Also set the default logging level to INFO on the stream handler, but DEBUG on the root logger so we can have
# other loggers at different levels.
_COLOR_STREAM_HANDLER.setLevel(py_logging.INFO)
py_logging.getLogger(_squid_root_logger_name).setLevel(py_logging.DEBUG)


# --- The console and the files are written by ONE writer thread, not by the thread that logs ----------
#
# A handler that writes and flushes a stream gives up the GIL at every write. While another thread is
# CPU-bound, getting it back costs a full interpreter switch interval each time. Measured 2026-09-21: one
# log.debug() with file handlers took 0.09 ms alone and 15.2 ms (3 x 5 ms) while another thread pickled
# camera frames for the save subprocess - and a camera read thread that logs three lines per frame fell
# behind its 41 ms frame period. Handing the record to a queue costs ~0.01 ms and no GIL hand-back.
#
# - The squid root logger carries a single _QueueingHandler. The handlers that do I/O ("sinks": the
#   console and every file handler added below) belong to the writer thread, and keep their own levels.
# - ERROR and above are on disk when the log call returns: the lines before a crash are the ones that
#   matter. Everything queued before them goes out with them, in order.
# - Handlers that other code attaches straight to a squid logger (the GUI's warning list, BufferingHandler)
#   are untouched: they run on the logging thread as before, and they do no file I/O.
# - fork() (how Linux starts the save subprocess) does not copy threads: the child gets a fresh queue and
#   a writer of its own; the queue is drained before forking so the writer is idle, not mid-write.


class _QueueingHandler(logging.handlers.QueueHandler):
    def emit(self, record: py_logging.LogRecord) -> None:
        super().emit(record)
        if record.levelno >= py_logging.ERROR and threading.current_thread() is not _writer._thread:
            flush()


def _new_writer(sinks=()) -> logging.handlers.QueueListener:
    writer = logging.handlers.QueueListener(queue.Queue(), *sinks, respect_handler_level=True)
    writer.start()
    return writer


_writer = _new_writer([_COLOR_STREAM_HANDLER])
_QUEUEING_HANDLER = _QueueingHandler(_writer.queue)
_QUEUEING_HANDLER.addFilter(_thread_id_filter)  # on the logging thread, before the record is queued
py_logging.getLogger(_squid_root_logger_name).addHandler(_QUEUEING_HANDLER)


def _sinks() -> Tuple[py_logging.Handler, ...]:
    return _writer.handlers


def _add_sink(handler: py_logging.Handler) -> None:
    _writer.handlers = _writer.handlers + (handler,)


def _remove_sink(handler: py_logging.Handler) -> None:
    flush()  # what was logged while it was attached belongs in it
    _writer.handlers = tuple(h for h in _writer.handlers if h is not handler)


def flush(timeout_s: float = 5.0) -> bool:
    """Wait until everything logged so far has been written. False if that took longer than timeout_s.

    Needed only by code that reads a log file back right after logging to it, and at exit; ERROR and
    above flush by themselves."""
    log_queue = _writer.queue
    deadline = time.monotonic() + timeout_s
    with log_queue.all_tasks_done:
        while log_queue.unfinished_tasks:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            log_queue.all_tasks_done.wait(remaining)
    return True


def _stop_writer() -> None:
    _writer.stop()  # writes what is queued, then ends the thread


def _restart_writer_in_forked_child() -> None:
    global _writer
    _writer = _new_writer(_writer.handlers)  # the parent's writer thread does not exist here
    _QUEUEING_HANDLER.queue = _writer.queue


atexit.register(_stop_writer)
# multiprocessing children leave through os._exit() and never run atexit; they do run these.
multiprocessing.util.Finalize(None, flush, exitpriority=0)
if sys.platform != "win32":
    os.register_at_fork(before=flush, after_in_child=_restart_writer_in_forked_child)


def get_logger(name: Optional[str] = None) -> py_logging.Logger:
    """
    Returns the top level squid logger instance by default, or a logger in the squid
    logging hierarchy if a non-None name is given.
    """
    if name is None:
        logger = py_logging.getLogger(_squid_root_logger_name)
    else:
        logger = py_logging.getLogger(_squid_root_logger_name).getChild(name)

    return logger


log = get_logger(__name__)


def set_stdout_log_level(level):
    """
    All squid code should use this set_stdout_log_level method, and the corresponding squid.logging.get_logger,
    to control squid-only logging.

    This does not modify the log level of loggers outside the squid logger hierarchy! If global logging control
    is needed the normal logging package tools can be used instead.  It also leaves FileHandler log levels such that
    they can always be outputting everything (regardless of what we set the stdout log level to)
    """
    for handler in _sinks():
        # We always want the file handlers to capture everything, so don't touch them.
        if isinstance(handler, logging.FileHandler):
            continue
        handler.setLevel(level)


def register_crash_handler(handler, call_existing_too=True):
    """
    We want to make sure any uncaught exceptions are logged, so we have this mechanism for putting a hook into
    the python system that does custom logging when an exception bubbles all the way to the top.

    NOTE: We do our best below, but it is a really bad idea for your handler to raise an exception.
    """
    # The sys.excepthook docs are a good entry point for all of this
    # (here: https://docs.python.org/3/library/sys.html#sys.excepthook), but essentially there are 3 different ways
    # threads of execution can blow up.  We want to catch and log all 3 of them.
    old_excepthook = sys.excepthook
    old_thread_excepthook = threading.excepthook
    # The unraisable hook doesn't have the same signature as the excepthooks, but we can sort of shoehorn the arguments
    # into the same signature.  Also, this is an extremely rare (I'm not sure I've ever seen it?) failure mode, so
    # it should be okay.
    old_unraisable_hook = sys.unraisablehook

    logger = get_logger()

    def new_excepthook(exception_type: Type[BaseException], value: BaseException, tb: TracebackType):
        try:
            handler(exception_type, value, tb)
        except BaseException as e:
            logger.critical("Custom excepthook handler raised exception", e)
        if call_existing_too:
            old_excepthook(exception_type, value, tb)

    def new_thread_excepthook(hook_args: threading.ExceptHookArgs):
        exception_type = hook_args.exc_type
        value = hook_args.exc_type
        tb = hook_args.exc_traceback
        try:
            handler(exception_type, value, type(tb))
        except BaseException as e:
            logger.critical("Custom thread excepthook handler raised exception", e)
        if call_existing_too:
            old_thread_excepthook(exception_type, value, type(tb))

    def new_unraisable_hook(info):
        exception_type = info.exc_type
        tb = info.exc_traceback
        value = info.exc_value
        try:
            handler(exception_type, value, type(tb))
        except BaseException as e:
            logger.critical("Custom unraisable hook handler raised exception", e)
        if call_existing_too:
            old_unraisable_hook(info)

    logger.info(
        f"Registering custom excepthook, threading excepthook, and unraisable hook using handler={handler.__name__}"
    )
    sys.excepthook = new_excepthook
    threading.excepthook = new_thread_excepthook
    sys.unraisablehook = new_unraisable_hook


def setup_uncaught_exception_logging():
    """
    This will make sure uncaught exceptions are sent to the root squid logger as error messages.
    """
    logger = get_logger()

    def uncaught_exception_logger(exception_type: Type[BaseException], value: BaseException, tb: TracebackType):
        logger.exception("Uncaught Exception!", exc_info=value)

    register_crash_handler(uncaught_exception_logger, call_existing_too=False)


def get_default_log_directory():
    return platformdirs.user_log_path(_squid_root_logger_name, "cephla")


def add_file_logging(log_filename, replace_existing=False):
    abs_path = os.path.abspath(log_filename)
    for handler in _sinks():
        if isinstance(handler, logging.handlers.BaseRotatingHandler):
            if handler.baseFilename == abs_path:
                if replace_existing:
                    _remove_sink(handler)
                else:
                    log.error(f"RotatingFileHandler already exists for {abs_path}, and replace_existing==False!")
                    return False

    log_file_existed = False
    if os.path.isfile(abs_path):
        log_file_existed = True

    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    # For now, don't worry about rollover after a certain size or time.  Just get a new file per call.
    # NOTE(imo): We had issues with windows not defaulting to utf-8, so force that here.  But also let the handler
    # know that if it sees encoding errors, it should replace the error bytes with ? and continue.
    new_handler = logging.handlers.RotatingFileHandler(
        abs_path, maxBytes=0, backupCount=25, encoding="utf-8", errors="replace"
    )
    new_handler.setLevel(py_logging.DEBUG)

    formatter = py_logging.Formatter(fmt=_baseline_log_format, datefmt=_baseline_log_dateformat)
    new_handler.setFormatter(formatter)

    log.info(f"Adding new file logger writing to file '{new_handler.baseFilename}'")
    # We want a new log file every time we start, so force one at startup if the log file already existed.
    # Before the writer thread gets the handler: a rollover must not race a write.
    if log_file_existed:
        new_handler.doRollover()
    _add_sink(new_handler)

    return True


def add_file_handler(log_filename, replace_existing=False, level=py_logging.DEBUG) -> Optional[py_logging.Handler]:
    """
    Attach a plain FileHandler to the squid root logger and return it, so callers can later remove/close it.
    This uses the same baseline formatting + thread_id injection as squid's other logs.
    """
    abs_path = os.path.abspath(log_filename)

    # If a handler already exists for this exact path, optionally replace it.
    for handler in _sinks():
        if isinstance(handler, (logging.FileHandler, logging.handlers.BaseRotatingHandler)):
            if getattr(handler, "baseFilename", None) == abs_path:
                if not replace_existing:
                    log.warning(
                        f"FileHandler already exists for {abs_path} and replace_existing=False. "
                        f"Not adding duplicate handler."
                    )
                    return None
                _remove_sink(handler)
                try:
                    handler.close()
                except Exception as e:
                    log.warning(f"Failed to close existing handler for {abs_path}: {e}")

    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    new_handler = logging.FileHandler(abs_path, encoding="utf-8", errors="replace")
    new_handler.setLevel(level)
    new_handler.setFormatter(py_logging.Formatter(fmt=_baseline_log_format, datefmt=_baseline_log_dateformat))

    log.info(f"Adding new file handler writing to file '{abs_path}'")
    _add_sink(new_handler)
    return new_handler


def remove_handler(handler: py_logging.Handler) -> None:
    """
    Remove the given handler from the squid root logger and close it.
    Safe to call even if the handler was already removed.
    """
    root_logger = get_logger()
    handler_desc = getattr(handler, "baseFilename", repr(handler))

    try:
        _remove_sink(handler)  # a file handler from add_file_handler(); harmless for any other
        root_logger.removeHandler(handler)
    except ValueError:
        # Handler wasn't attached - this is expected and fine
        log.debug(f"Handler {handler_desc} was not attached (already removed)")
    except Exception as e:
        # Unexpected error - log it but still try to close
        log.warning(f"Unexpected error removing handler {handler_desc}: {e}")

    try:
        handler.close()
    except Exception as e:
        log.warning(f"Failed to close handler {handler_desc}: {e}")


def get_current_log_file_path() -> Optional[str]:
    """
    Get the path to the current log file, if any file logging is configured.

    Returns:
        The absolute path to the log file, or None if no file logging is configured.
    """
    for handler in _sinks():
        if isinstance(handler, (py_logging.FileHandler, py_logging.handlers.BaseRotatingHandler)):
            return getattr(handler, "baseFilename", None)
    return None


class BufferingHandler(py_logging.Handler):
    """Logging handler that buffers messages for later retrieval.

    This handler is designed for GUI consumption but has no GUI dependencies.
    It can safely be used in any context including:
    - GUI applications (poll with a timer)
    - Headless scripts (programmatic access to recent warnings)
    - Forked subprocesses (queue is ignored, no crash)

    Fork-compatible: Uses a standard Python queue.Queue which, when inherited
    by forked subprocesses, becomes an isolated copy (not shared with parent).
    Messages accumulate in the subprocess with no consumer, but the bounded
    size (MAX_BUFFERED_MESSAGES) prevents memory growth.

    Thread-safe: Python's queue.Queue handles cross-thread access safely.
    Multiple threads can call emit() concurrently, and get_pending() can
    be called while other threads emit. The dropped_count is protected by
    a lock for accurate counting under contention.

    Example usage::

        handler = BufferingHandler()
        squid.logging.get_logger().addHandler(handler)

        # Later, retrieve buffered messages
        for level, name, msg in handler.get_pending():
            print(f"[{level}] {name}: {msg}")

        # Check if any messages were dropped due to full buffer
        if handler.dropped_count > 0:
            print(f"Warning: {handler.dropped_count} messages were dropped")
    """

    MAX_BUFFERED_MESSAGES = 1000  # Limit memory usage in case consumer is slow/absent

    def __init__(self, min_level: int = py_logging.WARNING):
        """Create a buffering handler.

        Args:
            min_level: Minimum log level to capture (default: WARNING).
        """
        super().__init__()
        self.setLevel(min_level)
        self._queue: queue.Queue = queue.Queue(maxsize=self.MAX_BUFFERED_MESSAGES)
        self._dropped_count = 0
        self._dropped_count_lock = threading.Lock()
        self.setFormatter(py_logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATEFORMAT))
        self.addFilter(_thread_id_filter)

    @property
    def dropped_count(self) -> int:
        """Number of messages dropped due to full buffer."""
        with self._dropped_count_lock:
            return self._dropped_count

    def emit(self, record: py_logging.LogRecord):
        """Buffer a log record. Drops message if buffer is full."""
        try:
            msg = self.format(record)
            self._queue.put_nowait((record.levelno, record.name, msg))
        except queue.Full:
            with self._dropped_count_lock:
                self._dropped_count += 1
        except Exception:
            self.handleError(record)

    def get_pending(self) -> List[Tuple[int, str, str]]:
        """Retrieve and clear all pending messages.

        Returns:
            List of (level, logger_name, formatted_message) tuples.
        """
        messages = []
        while True:
            try:
                messages.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return messages
