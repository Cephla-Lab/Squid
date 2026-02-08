"""Status bar widget for displaying logged warnings and errors.

Port of upstream 17ed8c7b - feat: Add status bar widget for displaying warnings and errors.
"""

import logging
import re
import time
from datetime import datetime

from qtpy.QtCore import QObject, QPoint, Qt, QTimer, Signal
from qtpy.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import squid.core.logging


class _QtLogSignalHolder(QObject):
    """QObject that holds the signal for QtLoggingHandler.

    Defined at module level to avoid dynamic class creation.
    """

    message_logged = Signal(int, str, str)  # level, logger_name, message


class QtLoggingHandler(logging.Handler):
    """Logging handler that emits Qt signals for WARNING+ messages.

    Thread-safe: Qt signal system handles cross-thread delivery automatically.
    Used by WarningErrorWidget to display warnings/errors in the status bar.
    """

    def __init__(self, min_level: int = logging.WARNING):
        super().__init__()
        self.setLevel(min_level)
        self._signal_holder = _QtLogSignalHolder()
        self.setFormatter(
            logging.Formatter(
                fmt=squid.core.logging.LOG_FORMAT,
                datefmt=squid.core.logging.LOG_DATEFORMAT,
            )
        )
        # Intentionally reuse the private thread_id filter from squid.core.logging for consistent
        # formatting across all log handlers. This creates a controlled dependency on
        # squid.core.logging's internal API.
        self.addFilter(squid.core.logging._thread_id_filter)

    @property
    def signal_message_logged(self):
        return self._signal_holder.message_logged

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            self._signal_holder.message_logged.emit(record.levelno, record.name, msg)
        except Exception:
            self.handleError(record)


class WarningErrorWidget(QWidget):
    """Status bar widget displaying logged warnings and errors.

    Features:
    - Color-coded: yellow for warnings, red for errors
    - Shows timestamp for each message
    - Expandable popup showing all messages when multiple exist
    - Deduplication: repeated identical messages show count instead of duplicates
    - Rate limiting: max 10 messages per second to prevent GUI freeze
    """

    MAX_MESSAGES = 100  # Prevent unbounded memory growth
    RATE_LIMIT_WINDOW_MS = 1000  # 1 second window
    RATE_LIMIT_MAX_MESSAGES = 10  # Max messages per window

    def __init__(self, parent=None):
        super().__init__(parent)
        # List of dicts with keys: id, level, logger_name, message, count, datetime
        self._messages: list[dict] = []
        self._next_message_id = 0
        self._rate_limit_timestamps: list[float] = []  # For rate limiting
        self._dropped_count = 0  # Track rate-limited messages
        self._popup = None
        self._handler = None
        self._poll_timer = None
        self._setup_ui()

    def connect_handler(self, handler) -> None:
        """Connect a BufferingHandler and start polling it via QTimer.

        Args:
            handler: A squid.core.logging.BufferingHandler instance.
        """
        self.disconnect_handler()
        self._handler = handler
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_messages)
        self._poll_timer.start(100)  # 100ms polling interval

    def disconnect_handler(self) -> None:
        """Stop polling and disconnect the handler."""
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        self._handler = None

    def _poll_messages(self) -> None:
        """Poll the handler for pending messages."""
        if self._handler is None:
            return
        try:
            for level, logger_name, message in self._handler.get_pending():
                self.add_message(level, logger_name, message)
        except Exception:
            # Qt silently swallows exceptions in timer callbacks.
            # Log to stderr as a last resort.
            import traceback
            traceback.print_exc()

    def closeEvent(self, event):
        """Clean up popup and handler when widget is closed."""
        self.disconnect_handler()
        self._cleanup_popup()
        super().closeEvent(event)

    def _cleanup_popup(self):
        """Safely clean up popup if it exists."""
        if self._popup is not None:
            try:
                self._popup.hide()
                self._popup.deleteLater()
            except RuntimeError:
                # Popup may already be deleted
                pass
            self._popup = None

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(6)

        # Level icon (warning/error indicator) - circular badge
        self.label_icon = QLabel()
        self.label_icon.setFixedSize(20, 20)
        self.label_icon.setAlignment(Qt.AlignCenter)

        # Message text
        self.label_text = QLabel()
        self.label_text.setTextInteractionFlags(Qt.TextSelectableByMouse)

        # Expand button (shows when multiple messages or dropped messages)
        self.btn_expand = QPushButton()
        self.btn_expand.setFixedHeight(18)
        self.btn_expand.setMinimumWidth(32)  # Allow width to grow for longer text
        self.btn_expand.setCursor(Qt.PointingHandCursor)
        self.btn_expand.setStyleSheet(
            "QPushButton { background-color: #666; color: white; border-radius: 9px; "
            "font-size: 11px; font-weight: bold; padding: 0px 6px; }"
            "QPushButton:hover { background-color: #444; }"
            "QPushButton:pressed { background-color: #222; }"
        )
        self.btn_expand.clicked.connect(self._on_expand_clicked)
        self.btn_expand.setVisible(False)

        # Dismiss button (X)
        self.btn_dismiss = QPushButton("✕")
        self.btn_dismiss.setFixedSize(18, 18)
        self.btn_dismiss.setCursor(Qt.PointingHandCursor)
        self.btn_dismiss.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #888; font-size: 14px; padding: 0px; }"
            "QPushButton:hover { color: #000; }"
        )
        self.btn_dismiss.clicked.connect(self.dismiss_current)

        layout.addWidget(self.label_icon)
        layout.addWidget(self.label_text)
        layout.addWidget(self.btn_expand)
        layout.addWidget(self.btn_dismiss)
        layout.addStretch()  # Push everything to the left

    def _on_expand_clicked(self):
        """Handle expand button click."""
        self._toggle_popup()

    def _toggle_popup(self):
        """Toggle the popup showing all messages."""
        if self._popup is not None and self._popup.isVisible():
            self._cleanup_popup()
            return
        self._show_popup()

    def _show_popup(self):
        """Show popup with scrollable list of all messages."""
        # Recreate popup each time to ensure fresh state
        self._cleanup_popup()

        self._popup = QFrame(self.window(), Qt.Popup | Qt.FramelessWindowHint)
        self._popup.setStyleSheet(
            "QFrame { background-color: white; border: 1px solid #aaa; border-radius: 6px; }"
        )

        popup_layout = QVBoxLayout(self._popup)
        popup_layout.setContentsMargins(0, 0, 0, 0)
        popup_layout.setSpacing(0)

        # Header with title and Clear All button
        header = QWidget()
        header.setStyleSheet("background-color: #f5f5f5; border-bottom: 1px solid #ddd;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_label = QLabel(f"<b>Warnings & Errors</b> ({len(self._messages)})")
        btn_clear = QPushButton("Clear All")
        btn_clear.setCursor(Qt.PointingHandCursor)
        btn_clear.setStyleSheet(
            "QPushButton { background-color: #e74c3c; color: white; border: none; "
            "border-radius: 4px; padding: 4px 12px; font-weight: bold; }"
            "QPushButton:hover { background-color: #c0392b; }"
        )
        btn_clear.clicked.connect(self._clear_all_from_popup)
        header_layout.addWidget(header_label)
        header_layout.addStretch()
        header_layout.addWidget(btn_clear)
        popup_layout.addWidget(header)

        # Scrollable list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea { border: none; background: white; }")

        list_widget = QWidget()
        list_widget.setStyleSheet("background: white;")
        list_layout = QVBoxLayout(list_widget)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)

        # Add messages (newest first) - use message ID for dismiss callback
        for msg in reversed(self._messages):
            item_widget = self._create_popup_item(msg)
            list_layout.addWidget(item_widget)

        list_layout.addStretch()
        scroll.setWidget(list_widget)
        popup_layout.addWidget(scroll)

        # Size and position
        self._popup.setFixedWidth(550)
        self._popup.setMinimumHeight(100)
        self._popup.setMaximumHeight(350)

        # Position above this widget (popup appears above status bar)
        # with bounds checking to stay on screen
        global_pos = self.mapToGlobal(QPoint(0, 0))
        popup_height = min(350, 50 + len(self._messages) * 60)
        self._popup.setFixedHeight(popup_height)

        # Calculate position, ensuring popup stays on screen
        popup_x = global_pos.x()
        popup_y = global_pos.y() - popup_height - 5

        # Get available screen geometry
        screen = QApplication.screenAt(global_pos)
        if screen is not None:
            screen_geo = screen.availableGeometry()
            # Ensure popup doesn't go above screen top
            if popup_y < screen_geo.top():
                # Show below the widget instead
                popup_y = global_pos.y() + self.height() + 5
            # Ensure popup doesn't go off right edge (and not past left edge on narrow screens)
            if popup_x + 550 > screen_geo.right():
                popup_x = max(screen_geo.left(), screen_geo.right() - 550)

        self._popup.move(popup_x, popup_y)
        self._popup.show()

    def _create_popup_item(self, msg: dict) -> QWidget:
        """Create a single item widget for the popup list."""
        level = msg["level"]
        message = msg["message"]
        count = msg["count"]
        dt = msg["datetime"]
        msg_id = msg["id"]

        item = QWidget()
        is_error = level >= logging.ERROR
        bg_color = "#fef2f2" if is_error else "#fefce8"
        item.setStyleSheet(f"background-color: {bg_color}; border-bottom: 1px solid #eee;")

        layout = QHBoxLayout(item)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        # Level indicator
        icon_label = QLabel("⬤")
        icon_color = "#dc2626" if is_error else "#ca8a04"
        icon_label.setStyleSheet(f"color: {icon_color}; font-size: 8px;")
        icon_label.setFixedWidth(14)
        icon_label.setAlignment(Qt.AlignCenter)

        # Date/Time - show full date and time
        time_str = dt.strftime("%m/%d %H:%M:%S")
        time_label = QLabel(time_str)
        time_label.setStyleSheet("color: #666; font-size: 11px; font-family: monospace;")
        time_label.setFixedWidth(90)

        # Message (allow wrapping)
        core_msg = self._extract_core_message(message)
        if count > 1:
            core_msg = f"{core_msg} <b style='color: #666;'>(×{count})</b>"
        msg_label = QLabel(core_msg)
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("font-size: 12px; color: #333;")
        msg_label.setTextFormat(Qt.RichText)
        msg_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        # Dismiss button - use message ID for stable reference
        btn_dismiss = QPushButton("✕")
        btn_dismiss.setFixedSize(20, 20)
        btn_dismiss.setCursor(Qt.PointingHandCursor)
        btn_dismiss.setStyleSheet(
            "QPushButton { background: #ddd; border: none; color: #666; border-radius: 10px; font-size: 12px; }"
            "QPushButton:hover { background: #ccc; color: #333; }"
        )
        btn_dismiss.clicked.connect(lambda checked, mid=msg_id: self._dismiss_by_id(mid))

        layout.addWidget(icon_label, 0, Qt.AlignTop)
        layout.addWidget(time_label, 0, Qt.AlignTop)
        layout.addWidget(msg_label, 1)
        layout.addWidget(btn_dismiss, 0, Qt.AlignTop)

        return item

    def _dismiss_by_id(self, msg_id: int):
        """Dismiss a message by its unique ID."""
        for i, msg in enumerate(self._messages):
            if msg["id"] == msg_id:
                self._messages.pop(i)
                self._update_display()
                if self._popup is not None:
                    if self._messages:
                        # Refresh popup with updated list
                        self._cleanup_popup()
                        self._show_popup()
                    else:
                        self._cleanup_popup()
                return

    def _clear_all_from_popup(self):
        """Clear all messages and close popup."""
        self.clear_all()
        self._cleanup_popup()

    def add_message(self, level: int, logger_name: str, message: str):
        """Add a new warning/error message to the queue."""
        # Rate limiting - but never rate-limit ERROR or higher (they're too important to drop)
        now_ms = time.time() * 1000
        cutoff = now_ms - self.RATE_LIMIT_WINDOW_MS
        self._rate_limit_timestamps = [t for t in self._rate_limit_timestamps if t > cutoff]

        if level < logging.ERROR and len(self._rate_limit_timestamps) >= self.RATE_LIMIT_MAX_MESSAGES:
            self._dropped_count += 1
            self._update_display()  # Update to show dropped count
            return  # Rate limited

        # Extract datetime from message or use current time
        dt = self._extract_datetime(message)

        # Deduplication - check if identical message already exists
        # Note: duplicates don't consume rate limit slots since they don't create new entries
        core_msg = self._extract_core_message(message)
        for i, msg in enumerate(self._messages):
            if self._extract_core_message(msg["message"]) == core_msg and msg["level"] == level:
                # Update with new datetime and increment count
                msg["datetime"] = dt
                msg["count"] += 1
                msg["message"] = message  # Update to latest message text
                self._messages.append(self._messages.pop(i))  # Move to end
                self._update_display()
                return

        # New message - consume rate limit slot and assign unique ID
        self._rate_limit_timestamps.append(now_ms)
        if len(self._messages) >= self.MAX_MESSAGES:
            self._messages.pop(0)

        new_msg = {
            "id": self._next_message_id,
            "level": level,
            "logger_name": logger_name,
            "message": message,
            "count": 1,
            "datetime": dt,
        }
        self._next_message_id += 1
        self._messages.append(new_msg)
        self._update_display()

    def dismiss_current(self):
        """Dismiss the most recent message."""
        if self._messages:
            self._messages.pop()
            self._update_display()

    def clear_all(self):
        """Clear all messages and reset dropped count."""
        self._messages.clear()
        self._dropped_count = 0
        self._update_display()

    def get_dropped_count(self) -> int:
        """Return the number of messages dropped due to rate limiting."""
        return self._dropped_count

    def has_messages(self) -> bool:
        """Return True if there are pending messages."""
        return len(self._messages) > 0

    def _update_display(self):
        """Update the main widget display."""
        if not self._messages:
            self.setVisible(False)
            return

        self.setVisible(True)
        msg = self._messages[-1]
        level = msg["level"]
        message = msg["message"]
        count = msg["count"]
        dt = msg["datetime"]
        is_error = level >= logging.ERROR

        # Colors
        if is_error:
            bg_color = "#fef2f2"
            text_color = "#b91c1c"
            icon_text = "✕"
            icon_style = (
                "background-color: #dc2626; color: white; font-weight: bold; font-size: 12px; border-radius: 10px;"
            )
        else:
            bg_color = "#fefce8"
            text_color = "#a16207"
            icon_text = "!"
            icon_style = (
                "background-color: #eab308; color: white; font-weight: bold; font-size: 14px; border-radius: 10px;"
            )

        self.setStyleSheet(f"background-color: {bg_color}; border-radius: 4px;")
        self.label_icon.setText(icon_text)
        self.label_icon.setStyleSheet(icon_style)

        # Format message with compact time (HH:MM only)
        time_str = dt.strftime("%H:%M")
        display_msg = self._format_display_message(message)
        if count > 1:
            display_msg = f"[{time_str}] {display_msg} (×{count})"
        else:
            display_msg = f"[{time_str}] {display_msg}"
        self.label_text.setText(display_msg)
        self.label_text.setStyleSheet(f"color: {text_color}; font-weight: bold;")

        # Tooltip shows full message with date and dropped count if any
        full_time = dt.strftime("%Y-%m-%d %H:%M:%S")
        tooltip = f"{full_time}\n{self._extract_core_message(message)}"
        if self._dropped_count > 0:
            tooltip += f"\n\n⚠ {self._dropped_count} message(s) dropped due to rate limiting"
        self.setToolTip(tooltip)

        # Show expand button if multiple messages or dropped messages
        msg_count = len(self._messages)
        if msg_count > 1 or self._dropped_count > 0:
            if self._dropped_count > 0:
                # Show both additional messages and dropped count
                extra = msg_count - 1
                if extra > 0:
                    self.btn_expand.setText(f"+{extra} ({self._dropped_count}⚠)")
                else:
                    self.btn_expand.setText(f"({self._dropped_count}⚠)")
            else:
                self.btn_expand.setText(f"+{msg_count - 1}")
            self.btn_expand.setVisible(True)
        else:
            self.btn_expand.setVisible(False)

    def _extract_datetime(self, message: str) -> datetime:
        """Extract datetime from log message."""
        # Format: "2026-01-22 23:44:23.123 - ..."
        try:
            if " - " in message:
                datetime_part = message.split(" - ")[0]
                # Parse "2026-01-22 23:44:23.123"
                if "." in datetime_part:
                    datetime_part = datetime_part.rsplit(".", 1)[0]
                return datetime.strptime(datetime_part, "%Y-%m-%d %H:%M:%S")
        except (ValueError, IndexError):
            # Timestamp is optional - fall back to current time if parsing fails
            pass
        return datetime.now()

    # Pattern to match file location suffix like " (widgets.py:123)"
    _FILE_LOCATION_PATTERN = re.compile(r" \([^)]+:\d+\)$")

    def _extract_core_message(self, message: str) -> str:
        """Extract core message content (without timestamp/thread/location)."""
        for marker in [" - WARNING - ", " - ERROR - ", " - CRITICAL - "]:
            if marker in message:
                parts = message.split(marker, 1)
                if len(parts) > 1:
                    msg = parts[1]
                    # Strip file location suffix like " (widgets.py:123)" but not arbitrary parentheses
                    msg = self._FILE_LOCATION_PATTERN.sub("", msg)
                    return msg
        return message

    def _format_display_message(self, message: str) -> str:
        """Format message for single-line display."""
        msg = self._extract_core_message(message)
        if len(msg) > 60:
            msg = msg[:57] + "..."
        return msg
