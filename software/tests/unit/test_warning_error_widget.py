"""Unit tests for WarningErrorWidget and QtLoggingHandler.

Port of upstream 17ed8c7b tests.
"""

import logging
import time

import pytest
from unittest.mock import MagicMock, patch


class TestQtLoggingHandler:
    """Test the QtLoggingHandler class."""

    def test_handler_exists(self):
        """QtLoggingHandler should be importable."""
        from squid.ui.widgets.warning_error_widget import QtLoggingHandler

        assert QtLoggingHandler is not None

    def test_handler_default_level(self):
        """Handler should default to WARNING level."""
        from squid.ui.widgets.warning_error_widget import QtLoggingHandler

        handler = QtLoggingHandler()
        assert handler.level == logging.WARNING

    def test_handler_custom_level(self):
        """Handler should accept custom min_level."""
        from squid.ui.widgets.warning_error_widget import QtLoggingHandler

        handler = QtLoggingHandler(min_level=logging.ERROR)
        assert handler.level == logging.ERROR

    def test_handler_has_signal(self):
        """Handler should expose signal_message_logged property."""
        from squid.ui.widgets.warning_error_widget import QtLoggingHandler

        handler = QtLoggingHandler()
        assert hasattr(handler, "signal_message_logged")

    def test_handler_emit_formats_message(self):
        """Handler emit should format the log record."""
        from squid.ui.widgets.warning_error_widget import QtLoggingHandler

        handler = QtLoggingHandler()
        emitted_messages = []

        def capture(level, logger_name, message):
            emitted_messages.append((level, logger_name, message))

        handler.signal_message_logged.connect(capture)

        record = logging.LogRecord(
            name="test.logger",
            level=logging.WARNING,
            pathname="test.py",
            lineno=42,
            msg="Test warning message",
            args=(),
            exc_info=None,
        )
        record.thread_id = 12345  # Simulate the filter

        handler.emit(record)

        assert len(emitted_messages) == 1
        level, logger_name, message = emitted_messages[0]
        assert level == logging.WARNING
        assert logger_name == "test.logger"
        assert "Test warning message" in message


class TestWarningErrorWidget:
    """Test the WarningErrorWidget class."""

    def test_widget_exists(self):
        """WarningErrorWidget should be importable."""
        from squid.ui.widgets.warning_error_widget import WarningErrorWidget

        assert WarningErrorWidget is not None

    def test_widget_has_constants(self):
        """Widget should have rate limiting and message limit constants."""
        from squid.ui.widgets.warning_error_widget import WarningErrorWidget

        assert WarningErrorWidget.MAX_MESSAGES == 100
        assert WarningErrorWidget.RATE_LIMIT_WINDOW_MS == 1000
        assert WarningErrorWidget.RATE_LIMIT_MAX_MESSAGES == 10

    @pytest.mark.usefixtures("qapp")
    def test_widget_initially_hidden(self, qapp):
        """Widget should start hidden (no messages)."""
        from squid.ui.widgets.warning_error_widget import WarningErrorWidget

        widget = WarningErrorWidget()
        assert not widget.has_messages()

    @pytest.mark.usefixtures("qapp")
    def test_add_message_shows_widget(self, qapp):
        """Adding a message should make widget visible."""
        from squid.ui.widgets.warning_error_widget import WarningErrorWidget

        widget = WarningErrorWidget()
        widget.add_message(logging.WARNING, "test", "Test warning")
        assert widget.has_messages()
        assert widget.isVisible()

    @pytest.mark.usefixtures("qapp")
    def test_dismiss_current_removes_message(self, qapp):
        """dismiss_current should remove most recent message."""
        from squid.ui.widgets.warning_error_widget import WarningErrorWidget

        widget = WarningErrorWidget()
        widget.add_message(logging.WARNING, "test", "Warning 1")
        widget.add_message(logging.WARNING, "test", "Warning 2")
        assert len(widget._messages) == 2

        widget.dismiss_current()
        assert len(widget._messages) == 1

    @pytest.mark.usefixtures("qapp")
    def test_clear_all_removes_all_messages(self, qapp):
        """clear_all should remove all messages."""
        from squid.ui.widgets.warning_error_widget import WarningErrorWidget

        widget = WarningErrorWidget()
        widget.add_message(logging.WARNING, "test", "Warning 1")
        widget.add_message(logging.ERROR, "test", "Error 1")
        assert len(widget._messages) == 2

        widget.clear_all()
        assert len(widget._messages) == 0
        assert not widget.has_messages()

    @pytest.mark.usefixtures("qapp")
    def test_deduplication(self, qapp):
        """Identical messages should be deduplicated with count."""
        from squid.ui.widgets.warning_error_widget import WarningErrorWidget

        widget = WarningErrorWidget()
        # Use formatted messages that would have same core content
        widget.add_message(logging.WARNING, "test", "2026-01-24 12:00:00 - test - WARNING - Same message")
        widget.add_message(logging.WARNING, "test", "2026-01-24 12:00:01 - test - WARNING - Same message")
        widget.add_message(logging.WARNING, "test", "2026-01-24 12:00:02 - test - WARNING - Same message")

        assert len(widget._messages) == 1
        assert widget._messages[0]["count"] == 3

    @pytest.mark.usefixtures("qapp")
    def test_rate_limiting_warnings(self, qapp):
        """Warnings should be rate limited."""
        from squid.ui.widgets.warning_error_widget import WarningErrorWidget

        widget = WarningErrorWidget()
        # Add more than the rate limit
        for i in range(15):
            widget.add_message(logging.WARNING, "test", f"Warning {i}")

        # Should have rate limited some
        assert len(widget._messages) <= 10
        assert widget.get_dropped_count() > 0

    @pytest.mark.usefixtures("qapp")
    def test_errors_bypass_rate_limit(self, qapp):
        """ERROR level messages should bypass rate limiting."""
        from squid.ui.widgets.warning_error_widget import WarningErrorWidget

        widget = WarningErrorWidget()
        # Fill up with warnings first
        for i in range(10):
            widget.add_message(logging.WARNING, "test", f"Warning {i}")

        # Now add errors - they should still come through
        widget.add_message(logging.ERROR, "test", "Error 1")
        widget.add_message(logging.ERROR, "test", "Error 2")

        # We should have warnings + errors
        error_msgs = [m for m in widget._messages if m["level"] >= logging.ERROR]
        assert len(error_msgs) == 2

    @pytest.mark.usefixtures("qapp")
    def test_max_messages_eviction(self, qapp):
        """Should evict oldest messages when MAX_MESSAGES is reached."""
        from squid.ui.widgets.warning_error_widget import WarningErrorWidget

        widget = WarningErrorWidget()
        # Temporarily disable rate limiting by using errors
        for i in range(widget.MAX_MESSAGES + 10):
            widget.add_message(logging.ERROR, "test", f"Error {i}")

        assert len(widget._messages) == widget.MAX_MESSAGES

    @pytest.mark.usefixtures("qapp")
    def test_extract_datetime(self, qapp):
        """_extract_datetime should parse log timestamps."""
        from squid.ui.widgets.warning_error_widget import WarningErrorWidget
        from datetime import datetime

        widget = WarningErrorWidget()
        # Format: "2026-01-22 23:44:23.123 - ..."
        msg = "2026-01-22 23:44:23.123 - test - WARNING - Message"
        dt = widget._extract_datetime(msg)

        assert dt.year == 2026
        assert dt.month == 1
        assert dt.day == 22
        assert dt.hour == 23
        assert dt.minute == 44
        assert dt.second == 23

    @pytest.mark.usefixtures("qapp")
    def test_extract_core_message(self, qapp):
        """_extract_core_message should strip timestamp/thread/location."""
        from squid.ui.widgets.warning_error_widget import WarningErrorWidget

        widget = WarningErrorWidget()
        msg = "2026-01-22 23:44:23.123 - 12345 - test - WARNING - This is the core message (widgets.py:123)"
        core = widget._extract_core_message(msg)

        assert core == "This is the core message"

    @pytest.mark.usefixtures("qapp")
    def test_format_display_message_truncation(self, qapp):
        """_format_display_message should truncate long messages."""
        from squid.ui.widgets.warning_error_widget import WarningErrorWidget

        widget = WarningErrorWidget()
        long_msg = "A" * 100  # Very long message
        formatted = widget._format_display_message(long_msg)

        assert len(formatted) <= 60
        assert formatted.endswith("...")


class TestMainWindowIntegration:
    """Test main window integration with WarningErrorWidget."""

    def test_main_window_has_warning_widget(self):
        """Main window should have warningErrorWidget attribute."""
        import inspect
        from squid.ui.main_window import HighContentScreeningGui

        source = inspect.getsource(HighContentScreeningGui.__init__)
        assert "warningErrorWidget" in source
        assert "WarningErrorWidget" in source

    def test_main_window_has_handler_methods(self):
        """Main window should have handler connection methods."""
        from squid.ui.main_window import HighContentScreeningGui

        assert hasattr(HighContentScreeningGui, "_connect_warning_handler")
        assert hasattr(HighContentScreeningGui, "_disconnect_warning_handler")

    def test_show_event_connects_handler(self):
        """showEvent should call _connect_warning_handler."""
        import inspect
        from squid.ui.main_window import HighContentScreeningGui

        source = inspect.getsource(HighContentScreeningGui.showEvent)
        assert "_connect_warning_handler" in source

    def test_close_event_disconnects_handler(self):
        """closeEvent should call _disconnect_warning_handler."""
        import inspect
        from squid.ui.main_window import HighContentScreeningGui

        source = inspect.getsource(HighContentScreeningGui.closeEvent)
        assert "_disconnect_warning_handler" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
