"""Tests for the CancelToken."""

import pytest
import threading
import time

from squid.core.utils.cancel_token import (
    CancelToken,
    CancellationError,
    TokenState,
)


class TestCancelToken:
    """Tests for CancelToken functionality."""

    def test_initial_state(self):
        """Test token starts in running state."""
        token = CancelToken()
        assert token.state == TokenState.RUNNING
        assert token.is_running
        assert not token.is_cancelled
        assert not token.is_paused

    def test_cancel(self):
        """Test cancellation."""
        token = CancelToken()
        token.cancel("test reason")

        assert token.state == TokenState.CANCELLED
        assert token.is_cancelled
        assert not token.is_running
        assert token.cancel_reason == "test reason"

    def test_raise_if_cancelled(self):
        """Test raise_if_cancelled raises on cancellation."""
        token = CancelToken()

        # Should not raise when not cancelled
        token.raise_if_cancelled()

        # Should raise after cancellation
        token.cancel()
        with pytest.raises(CancellationError):
            token.raise_if_cancelled()

    def test_pause_resume(self):
        """Test pause and resume."""
        token = CancelToken()

        token.pause()
        assert token.state == TokenState.PAUSED
        assert token.is_paused
        assert not token.is_running

        token.resume()
        assert token.state == TokenState.RUNNING
        assert token.is_running
        assert not token.is_paused

    def test_wait_if_paused(self):
        """Test wait_if_paused blocks when paused."""
        token = CancelToken()
        token.pause()

        # Should timeout when paused
        result = token.wait_if_paused(timeout=0.1)
        assert not result

    def test_wait_if_paused_resumes(self):
        """Test wait_if_paused unblocks on resume."""
        token = CancelToken()
        token.pause()

        resumed = threading.Event()

        def waiter():
            token.wait_if_paused()
            resumed.set()

        thread = threading.Thread(target=waiter)
        thread.start()

        time.sleep(0.05)
        assert not resumed.is_set()

        token.resume()
        thread.join(timeout=1.0)
        assert resumed.is_set()

    def test_wait_if_paused_raises_on_cancel(self):
        """Test wait_if_paused raises on cancellation."""
        token = CancelToken()
        token.pause()

        error_raised = threading.Event()

        def waiter():
            try:
                token.wait_if_paused()
            except CancellationError:
                error_raised.set()

        thread = threading.Thread(target=waiter)
        thread.start()

        time.sleep(0.05)
        token.cancel()
        thread.join(timeout=1.0)

        assert error_raised.is_set()

    def test_check_point(self):
        """Test check_point combines cancel and pause checks."""
        token = CancelToken()

        # Should not raise when running
        token.check_point()

        # Should raise when cancelled
        token.cancel()
        with pytest.raises(CancellationError):
            token.check_point()

    def test_reset(self):
        """Test reset returns to running state."""
        token = CancelToken()
        token.cancel("reason")

        token.reset()
        assert token.state == TokenState.RUNNING
        assert token.cancel_reason is None

    def test_child_token(self):
        """Test child token inherits parent state."""
        parent = CancelToken()
        child = parent.create_child()

        assert child.is_running
        assert not child.is_cancelled

        # Parent cancellation affects child
        parent.cancel()
        assert child.is_cancelled

    def test_child_token_independent_cancel(self):
        """Test child can be cancelled independently."""
        parent = CancelToken()
        child = parent.create_child()

        child.cancel()

        assert child.is_cancelled
        assert not parent.is_cancelled

    def test_child_token_inherits_pause(self):
        """Test child inherits parent pause state."""
        parent = CancelToken()
        child = parent.create_child()

        parent.pause()
        assert child.is_paused

        parent.resume()
        assert not child.is_paused


class TestCancelTokenThreadSafety:
    """Tests for CancelToken thread safety."""

    def test_concurrent_cancel(self):
        """Test concurrent cancel calls are safe."""
        token = CancelToken()
        results = []

        def cancel_task():
            try:
                token.cancel()
                results.append(True)
            except Exception:
                results.append(False)

        threads = [threading.Thread(target=cancel_task) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(results)
        assert token.is_cancelled

    def test_concurrent_state_access(self):
        """Test concurrent state access is safe."""
        token = CancelToken()
        errors = []

        def reader():
            for _ in range(100):
                try:
                    _ = token.state
                    _ = token.is_cancelled
                    _ = token.is_paused
                except Exception as e:
                    errors.append(e)

        def writer():
            for i in range(100):
                try:
                    if i % 2 == 0:
                        token.pause()
                    else:
                        token.resume()
                except Exception as e:
                    errors.append(e)

        threads = [
            threading.Thread(target=reader),
            threading.Thread(target=reader),
            threading.Thread(target=writer),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
