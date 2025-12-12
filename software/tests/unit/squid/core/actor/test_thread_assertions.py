"""Tests for thread assertion utilities."""

import threading

import pytest

from squid.core.actor.thread_assertions import (
    assert_backend_thread,
    assert_not_backend_thread,
    clear_backend_thread,
    get_backend_thread,
    set_backend_thread,
)


class TestThreadAssertions:
    """Tests for thread assertion functions."""

    def setup_method(self):
        """Clear backend thread before each test."""
        clear_backend_thread()

    def teardown_method(self):
        """Clear backend thread after each test."""
        clear_backend_thread()

    def test_set_backend_thread(self):
        """set_backend_thread should store the thread."""
        thread = threading.current_thread()
        set_backend_thread(thread)
        assert get_backend_thread() is thread

    def test_clear_backend_thread(self):
        """clear_backend_thread should reset to None."""
        set_backend_thread(threading.current_thread())
        clear_backend_thread()
        assert get_backend_thread() is None

    def test_assert_backend_thread_passes_when_on_backend(self):
        """assert_backend_thread should pass when called from backend thread."""
        set_backend_thread(threading.current_thread())
        # Should not raise
        assert_backend_thread("test operation")

    def test_assert_backend_thread_fails_when_on_wrong_thread(self):
        """assert_backend_thread should raise when called from wrong thread."""
        # Create a different thread and set it as backend
        backend = threading.Thread(target=lambda: None, name="BackendThread")
        set_backend_thread(backend)

        # Current thread is not the backend thread
        with pytest.raises(RuntimeError) as exc_info:
            assert_backend_thread("test operation")

        assert "must run on backend thread" in str(exc_info.value)
        assert "BackendThread" in str(exc_info.value)

    def test_assert_not_backend_thread_passes_when_not_on_backend(self):
        """assert_not_backend_thread should pass when not on backend thread."""
        # Create a different thread and set it as backend
        backend = threading.Thread(target=lambda: None, name="BackendThread")
        set_backend_thread(backend)

        # Current thread is not the backend thread - should pass
        assert_not_backend_thread("test operation")

    def test_assert_not_backend_thread_fails_when_on_backend(self):
        """assert_not_backend_thread should raise when on backend thread."""
        set_backend_thread(threading.current_thread())

        with pytest.raises(RuntimeError) as exc_info:
            assert_not_backend_thread("test operation")

        assert "must NOT run on backend thread" in str(exc_info.value)

    def test_assertions_noop_when_not_configured(self):
        """Assertions should pass (noop) when backend thread not configured."""
        # No backend thread set
        assert get_backend_thread() is None

        # Both should pass without error
        assert_backend_thread("test operation")
        assert_not_backend_thread("test operation")

    def test_assert_backend_thread_from_different_thread(self):
        """Test assertion behavior when called from a spawned thread."""
        set_backend_thread(threading.current_thread())
        error_occurred = []

        def check_assertion():
            try:
                assert_backend_thread("worker operation")
            except RuntimeError as e:
                error_occurred.append(e)

        thread = threading.Thread(target=check_assertion)
        thread.start()
        thread.join()

        # Should have raised an error in the spawned thread
        assert len(error_occurred) == 1
        assert "must run on backend thread" in str(error_occurred[0])
