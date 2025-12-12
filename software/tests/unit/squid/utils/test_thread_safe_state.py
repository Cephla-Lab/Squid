"""Tests for thread-safe state utilities."""

import threading
import time
from squid.core.utils.thread_safe_state import ThreadSafeValue, ThreadSafeFlag


class TestThreadSafeValue:
    """Test suite for ThreadSafeValue."""

    def test_get_set(self):
        """Basic get and set operations."""
        v = ThreadSafeValue(initial_value=42)
        assert v.get() == 42
        v.set(100)
        assert v.get() == 100

    def test_initial_none(self):
        """Default initial value is None."""
        v = ThreadSafeValue()
        assert v.get() is None

    def test_get_and_clear(self):
        """get_and_clear returns value and sets to None atomically."""
        v = ThreadSafeValue(initial_value="hello")
        assert v.get_and_clear() == "hello"
        assert v.get() is None

    def test_update_atomic(self):
        """update() applies function atomically."""
        v = ThreadSafeValue(initial_value=0)

        def increment(x):
            return x + 1

        result = v.update(increment)
        assert result == 1
        assert v.get() == 1

    def test_concurrent_updates(self):
        """Concurrent updates should not lose any increments."""
        v = ThreadSafeValue(initial_value=0)

        def increment_many():
            for _ in range(1000):
                v.update(lambda x: x + 1)

        threads = [threading.Thread(target=increment_many) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # If there were race conditions, we'd get less than 10000
        assert v.get() == 10000

    def test_locked_context_manager(self):
        """locked() context manager provides exclusive access."""
        v = ThreadSafeValue(initial_value={"count": 0})

        with v.locked() as value:
            # Can safely modify mutable value
            value["count"] += 1

        assert v.get()["count"] == 1


class TestThreadSafeFlag:
    """Test suite for ThreadSafeFlag."""

    def test_initial_state_false(self):
        """Default initial state is False."""
        f = ThreadSafeFlag()
        assert f.is_set() is False

    def test_initial_state_true(self):
        """Can set initial state to True."""
        f = ThreadSafeFlag(initial=True)
        assert f.is_set() is True

    def test_set_clear(self):
        """set() and clear() work correctly."""
        f = ThreadSafeFlag(initial=False)
        f.set()
        assert f.is_set() is True
        f.clear()
        assert f.is_set() is False

    def test_wait_returns_immediately_if_set(self):
        """wait() returns True immediately if flag is set."""
        f = ThreadSafeFlag(initial=True)
        start = time.time()
        result = f.wait(timeout=1.0)
        elapsed = time.time() - start

        assert result is True
        assert elapsed < 0.1  # Should be nearly instant

    def test_wait_times_out_if_not_set(self):
        """wait() returns False after timeout if flag not set."""
        f = ThreadSafeFlag(initial=False)
        start = time.time()
        result = f.wait(timeout=0.05)
        elapsed = time.time() - start

        assert result is False
        assert elapsed >= 0.05

    def test_wait_wakes_on_set(self):
        """wait() returns True when another thread sets the flag."""
        f = ThreadSafeFlag(initial=False)

        def setter():
            time.sleep(0.02)
            f.set()

        t = threading.Thread(target=setter)
        t.start()

        start = time.time()
        result = f.wait(timeout=1.0)
        elapsed = time.time() - start

        t.join()

        assert result is True
        assert elapsed < 0.5  # Should wake up quickly after set()

    def test_wait_and_clear(self):
        """wait_and_clear() waits, returns True, and clears atomically."""
        f = ThreadSafeFlag(initial=True)

        result = f.wait_and_clear(timeout=0.1)

        assert result is True
        assert f.is_set() is False

    def test_wait_and_clear_timeout(self):
        """wait_and_clear() returns False on timeout without clearing."""
        f = ThreadSafeFlag(initial=False)

        result = f.wait_and_clear(timeout=0.05)

        assert result is False
        # Flag should still be False (wasn't cleared because we timed out)
        assert f.is_set() is False
