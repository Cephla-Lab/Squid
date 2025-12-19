"""Tests for safe_callback utility."""

import pytest
from squid.core.utils.safe_callback import safe_callback


class TestSafeCallback:
    """Test suite for safe_callback function."""

    def test_successful_callback_returns_value(self):
        """Successful callback should return value in result."""

        def add(a, b):
            return a + b

        result = safe_callback(add, 1, 2)

        assert result.success is True
        assert result.value == 3
        assert result.error is None
        assert result.stack_trace is None

    def test_failed_callback_contains_error(self):
        """Failed callback should contain exception and stack trace."""

        def explode():
            raise ValueError("boom")

        result = safe_callback(explode)

        assert result.success is False
        assert result.value is None
        assert isinstance(result.error, ValueError)
        assert "boom" in str(result.error)
        assert result.stack_trace is not None
        assert "ValueError" in result.stack_trace

    def test_on_error_callback_is_called(self):
        """on_error handler should be called with exception and traceback."""
        errors = []

        def explode():
            raise ValueError("boom")

        def on_error(e, tb):
            errors.append((e, tb))

        safe_callback(explode, on_error=on_error)

        assert len(errors) == 1
        assert isinstance(errors[0][0], ValueError)
        assert "boom" in str(errors[0][0])
        assert errors[0][1] is not None  # stack trace

    def test_on_error_callback_failure_doesnt_crash(self):
        """If on_error handler fails, safe_callback should still return."""

        def explode():
            raise ValueError("original error")

        def bad_handler(e, tb):
            raise RuntimeError("handler also explodes")

        # Should not raise - handler failure is logged but contained
        result = safe_callback(explode, on_error=bad_handler)

        assert result.success is False
        assert isinstance(result.error, ValueError)

    def test_kwargs_are_passed(self):
        """Keyword arguments should be passed to callback."""

        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"

        result = safe_callback(greet, "World", greeting="Hi")

        assert result.success is True
        assert result.value == "Hi, World!"

    def test_raise_if_error_raises(self):
        """raise_if_error should re-raise the exception."""

        def explode():
            raise ValueError("boom")

        result = safe_callback(explode)

        with pytest.raises(ValueError) as exc_info:
            result.raise_if_error()

        assert "boom" in str(exc_info.value)

    def test_raise_if_error_noop_on_success(self):
        """raise_if_error should do nothing on success."""

        def ok():
            return 42

        result = safe_callback(ok)

        # Should not raise
        result.raise_if_error()
