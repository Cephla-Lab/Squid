"""Slack notification settings dialog for Squid microscope GUI.

Provides a non-modal dialog for configuring Slack Bot Token, Channel ID,
and notification preferences. Changes apply immediately without restart.
"""

import os
from typing import Optional

import yaml

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QCheckBox,
    QPushButton,
    QGroupBox,
    QMessageBox,
    QFormLayout,
)

import control._def
from control.slack_notifier import SlackNotifier
import squid.logging

log = squid.logging.get_logger(__name__)

CACHE_FILE = "cache/slack_settings.yaml"

# Mapping from cache keys to (control._def attribute, default value getter)
_SETTINGS_MAP = {
    "enabled": ("ENABLED", lambda: False),
    "bot_token": ("BOT_TOKEN", lambda: None),
    "channel_id": ("CHANNEL_ID", lambda: None),
    "notify_on_error": ("NOTIFY_ON_ERROR", lambda: True),
    "notify_on_timepoint_complete": ("NOTIFY_ON_TIMEPOINT_COMPLETE", lambda: True),
    "send_mosaic_snapshots": ("SEND_MOSAIC_SNAPSHOTS", lambda: True),
    "notify_on_acquisition_start": ("NOTIFY_ON_ACQUISITION_START", lambda: True),
    "notify_on_acquisition_finished": ("NOTIFY_ON_ACQUISITION_FINISHED", lambda: True),
}


def _load_cached_settings() -> dict:
    """Load settings from the cache file if it exists.

    Returns:
        Dictionary of cached settings, or empty dict if not found.
    """
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        log.warning(f"Failed to load Slack settings from cache: {e}")
        return {}


class SlackSettingsDialog(QDialog):
    """Non-modal dialog for configuring Slack notifications.

    Settings are saved to a cache file and override INI config values
    at runtime. Changes take effect immediately.
    """

    settings_changed = Signal()

    def __init__(
        self,
        slack_notifier: Optional[SlackNotifier] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._slack_notifier = slack_notifier
        self.setWindowTitle("Slack Notifications")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setModal(False)
        self.setMinimumWidth(500)

        self._setup_ui()
        self._load_settings()
        self._connect_signals()

    def _setup_ui(self):
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)

        # API Configuration section
        api_group = QGroupBox("Slack API Configuration")
        api_layout = QFormLayout()

        self.checkbox_enabled = QCheckBox("Enable Slack Notifications")
        api_layout.addRow(self.checkbox_enabled)

        # Bot Token
        self.lineedit_bot_token = QLineEdit()
        self.lineedit_bot_token.setPlaceholderText("xoxb-...")
        self.lineedit_bot_token.setEchoMode(QLineEdit.Password)
        api_layout.addRow("Bot Token:", self.lineedit_bot_token)

        # Show/hide token button
        token_buttons = QHBoxLayout()
        self.btn_show_token = QPushButton("Show")
        self.btn_show_token.setCheckable(True)
        self.btn_show_token.setMaximumWidth(60)
        token_buttons.addWidget(self.btn_show_token)
        token_buttons.addStretch()
        api_layout.addRow("", token_buttons)

        # Channel ID
        self.lineedit_channel_id = QLineEdit()
        self.lineedit_channel_id.setPlaceholderText("C0123456789")
        api_layout.addRow("Channel ID:", self.lineedit_channel_id)

        # Test button
        self.btn_test = QPushButton("Test Connection")
        api_layout.addRow("", self.btn_test)

        # Help text
        help_label = QLabel(
            "<small>Get Bot Token from your Slack App settings.<br>"
            "Channel ID: Right-click channel > View channel details > Copy ID</small>"
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: gray;")
        api_layout.addRow(help_label)

        api_group.setLayout(api_layout)
        layout.addWidget(api_group)

        # Notification settings section
        notif_group = QGroupBox("Notification Settings")
        notif_layout = QVBoxLayout()

        self.checkbox_notify_error = QCheckBox("Notify on errors")
        self.checkbox_notify_error.setToolTip("Send a Slack message when an error occurs during acquisition")
        notif_layout.addWidget(self.checkbox_notify_error)

        self.checkbox_notify_timepoint = QCheckBox("Notify on timepoint completion")
        self.checkbox_notify_timepoint.setToolTip("Send a Slack message after each timepoint completes")
        notif_layout.addWidget(self.checkbox_notify_timepoint)

        self.checkbox_send_mosaic = QCheckBox("Include mosaic snapshots")
        self.checkbox_send_mosaic.setToolTip("Upload mosaic screenshot with timepoint notifications")
        notif_layout.addWidget(self.checkbox_send_mosaic)

        self.checkbox_notify_start = QCheckBox("Notify on acquisition start")
        self.checkbox_notify_start.setToolTip("Send a Slack message when an acquisition begins")
        notif_layout.addWidget(self.checkbox_notify_start)

        self.checkbox_notify_finished = QCheckBox("Notify on acquisition finished")
        self.checkbox_notify_finished.setToolTip("Send a Slack message when an acquisition completes")
        notif_layout.addWidget(self.checkbox_notify_finished)

        notif_group.setLayout(notif_layout)
        layout.addWidget(notif_group)

        # Status label
        self.label_status = QLabel("")
        self.label_status.setStyleSheet("color: gray;")
        layout.addWidget(self.label_status)

        # Buttons
        button_layout = QHBoxLayout()
        self.btn_save = QPushButton("Save")
        self.btn_close = QPushButton("Close")
        button_layout.addStretch()
        button_layout.addWidget(self.btn_save)
        button_layout.addWidget(self.btn_close)
        layout.addLayout(button_layout)

    def _connect_signals(self):
        """Connect UI signals to handlers."""
        self.btn_show_token.toggled.connect(self._toggle_token_visibility)
        self.btn_test.clicked.connect(self._test_connection)
        self.btn_save.clicked.connect(self._save_settings)
        self.btn_close.clicked.connect(self.close)

        # Enable/disable controls based on enabled checkbox
        self.checkbox_enabled.toggled.connect(self._update_controls_state)

    def _toggle_token_visibility(self, show: bool):
        """Toggle visibility of the bot token."""
        if show:
            self.lineedit_bot_token.setEchoMode(QLineEdit.Normal)
            self.btn_show_token.setText("Hide")
        else:
            self.lineedit_bot_token.setEchoMode(QLineEdit.Password)
            self.btn_show_token.setText("Show")

    def _update_controls_state(self, enabled: bool):
        """Update the enabled state of notification controls."""
        self.lineedit_bot_token.setEnabled(enabled)
        self.lineedit_channel_id.setEnabled(enabled)
        self.btn_show_token.setEnabled(enabled)
        self.btn_test.setEnabled(enabled)
        self.checkbox_notify_error.setEnabled(enabled)
        self.checkbox_notify_timepoint.setEnabled(enabled)
        self.checkbox_send_mosaic.setEnabled(enabled)
        self.checkbox_notify_start.setEnabled(enabled)
        self.checkbox_notify_finished.setEnabled(enabled)

    def _load_settings(self):
        """Load settings from cache file or use defaults from _def."""
        cached = _load_cached_settings()

        # Map widgets to their cache keys for easy population
        checkbox_map = {
            "enabled": self.checkbox_enabled,
            "notify_on_error": self.checkbox_notify_error,
            "notify_on_timepoint_complete": self.checkbox_notify_timepoint,
            "send_mosaic_snapshots": self.checkbox_send_mosaic,
            "notify_on_acquisition_start": self.checkbox_notify_start,
            "notify_on_acquisition_finished": self.checkbox_notify_finished,
        }
        lineedit_map = {
            "bot_token": self.lineedit_bot_token,
            "channel_id": self.lineedit_channel_id,
        }

        # Load checkbox values (prefer cache, fall back to _def)
        for key, checkbox in checkbox_map.items():
            attr = _SETTINGS_MAP[key][0]
            default = getattr(control._def.SlackNotifications, attr)
            checkbox.setChecked(cached.get(key, default))

        # Load line edit values (prefer cache, fall back to _def)
        for key, lineedit in lineedit_map.items():
            attr = _SETTINGS_MAP[key][0]
            default = getattr(control._def.SlackNotifications, attr) or ""
            value = cached.get(key) or default
            lineedit.setText(value)

        if cached:
            log.info("Loaded Slack settings from cache")

        self._update_controls_state(self.checkbox_enabled.isChecked())

    def _save_settings(self):
        """Save settings to cache file and update runtime config."""
        bot_token = self.lineedit_bot_token.text().strip() or None
        channel_id = self.lineedit_channel_id.text().strip() or None

        # Update runtime config
        control._def.SlackNotifications.ENABLED = self.checkbox_enabled.isChecked()
        control._def.SlackNotifications.BOT_TOKEN = bot_token
        control._def.SlackNotifications.CHANNEL_ID = channel_id
        control._def.SlackNotifications.NOTIFY_ON_ERROR = self.checkbox_notify_error.isChecked()
        control._def.SlackNotifications.NOTIFY_ON_TIMEPOINT_COMPLETE = self.checkbox_notify_timepoint.isChecked()
        control._def.SlackNotifications.SEND_MOSAIC_SNAPSHOTS = self.checkbox_send_mosaic.isChecked()
        control._def.SlackNotifications.NOTIFY_ON_ACQUISITION_START = self.checkbox_notify_start.isChecked()
        control._def.SlackNotifications.NOTIFY_ON_ACQUISITION_FINISHED = self.checkbox_notify_finished.isChecked()

        # Update notifier if available
        if self._slack_notifier:
            self._slack_notifier.bot_token = bot_token
            self._slack_notifier.channel_id = channel_id

        # Save to cache file
        settings = {
            "enabled": self.checkbox_enabled.isChecked(),
            "bot_token": bot_token,
            "channel_id": channel_id,
            "notify_on_error": self.checkbox_notify_error.isChecked(),
            "notify_on_timepoint_complete": self.checkbox_notify_timepoint.isChecked(),
            "send_mosaic_snapshots": self.checkbox_send_mosaic.isChecked(),
            "notify_on_acquisition_start": self.checkbox_notify_start.isChecked(),
            "notify_on_acquisition_finished": self.checkbox_notify_finished.isChecked(),
        }

        try:
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            with open(CACHE_FILE, "w") as f:
                yaml.dump(settings, f, default_flow_style=False)
            self.label_status.setText("Settings saved")
            self.label_status.setStyleSheet("color: green;")
            log.info("Slack settings saved to cache")
        except Exception as e:
            self.label_status.setText(f"Failed to save: {e}")
            self.label_status.setStyleSheet("color: red;")
            log.error(f"Failed to save Slack settings: {e}")

        self.settings_changed.emit()

    def _test_connection(self):
        """Test the Slack API connection."""
        bot_token = self.lineedit_bot_token.text().strip()
        channel_id = self.lineedit_channel_id.text().strip()

        if not bot_token:
            QMessageBox.warning(self, "Test Failed", "Please enter a Bot Token")
            return
        if not channel_id:
            QMessageBox.warning(self, "Test Failed", "Please enter a Channel ID")
            return

        # Create a temporary notifier for testing with the entered credentials
        temp_notifier = SlackNotifier(bot_token=bot_token, channel_id=channel_id)
        old_enabled = control._def.SlackNotifications.ENABLED
        control._def.SlackNotifications.ENABLED = True

        try:
            success, message = temp_notifier.test_connection()
        finally:
            temp_notifier.close()
            control._def.SlackNotifications.ENABLED = old_enabled

        if success:
            self.label_status.setText("Test successful!")
            self.label_status.setStyleSheet("color: green;")
            QMessageBox.information(self, "Test Successful", "Slack API connection successful!")
        else:
            self.label_status.setText(f"Test failed: {message}")
            self.label_status.setStyleSheet("color: red;")
            QMessageBox.warning(self, "Test Failed", f"Connection failed: {message}")

    def set_slack_notifier(self, notifier: SlackNotifier):
        """Set the Slack notifier instance."""
        self._slack_notifier = notifier


def load_slack_settings_from_cache():
    """Load Slack settings from cache file into runtime config.

    This should be called during application startup to restore
    user preferences from the cache file.
    """
    cached = _load_cached_settings()
    if not cached:
        return

    for key, (attr, _) in _SETTINGS_MAP.items():
        if key in cached:
            setattr(control._def.SlackNotifications, attr, cached[key])

    log.info("Loaded Slack settings from cache into runtime config")
