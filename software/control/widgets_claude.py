"""Anthropic API key dialog for Claude Code integration.

Provides a dialog for entering the Anthropic API key used when launching
Claude Code from the GUI. The key is cached locally in cache/claude_api_key.yaml.
"""

import os

import yaml

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
)

import control._def
import squid.logging

log = squid.logging.get_logger(__name__)

CACHE_FILE = "cache/claude_api_key.yaml"

_MASK_CHAR = "\u2022"  # bullet character for masking


def load_claude_api_key_from_cache():
    """Load Anthropic API key from cache file into runtime config.

    This should be called during application startup to restore
    the API key from the cache file.
    """
    if not os.path.exists(CACHE_FILE):
        return
    try:
        with open(CACHE_FILE, "r") as f:
            data = yaml.safe_load(f)
        if data is None:
            return
        if not isinstance(data, dict):
            log.error("Anthropic API key cache file has unexpected format (expected YAML dict)")
            return
        key = data.get("api_key")
        if key:
            if not isinstance(key, str):
                log.error("Anthropic API key cache has invalid type " f"(expected str, got {type(key).__name__})")
                return
            control._def.ANTHROPIC_API_KEY = key
            log.info("Loaded Anthropic API key from cache")
    except (yaml.YAMLError, OSError) as e:
        log.error(f"Failed to load Anthropic API key from cache: {e}")


class ClaudeApiKeyDialog(QDialog):
    """Dialog for entering the Anthropic API key used by Claude Code."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set Anthropic API Key")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setModal(True)
        self.setMinimumWidth(500)

        self._stored_key = ""
        self._is_visible = False

        self._setup_ui()
        self._load_key()
        self._connect_signals()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # API key input — multi-line for long keys
        layout.addWidget(QLabel("API Key:"))
        self.textedit_api_key = QPlainTextEdit()
        self.textedit_api_key.setPlaceholderText("sk-ant-...")
        self.textedit_api_key.setMaximumHeight(60)
        self.textedit_api_key.setTabChangesFocus(True)
        layout.addWidget(self.textedit_api_key)

        # Show/hide toggle
        toggle_layout = QHBoxLayout()
        self.btn_show = QPushButton("Show")
        self.btn_show.setCheckable(True)
        self.btn_show.setMaximumWidth(60)
        toggle_layout.addWidget(self.btn_show)
        toggle_layout.addStretch()
        layout.addLayout(toggle_layout)

        # Help text
        help_label = QLabel(
            "<small>Get your API key from "
            '<a href="https://console.anthropic.com/settings/keys">console.anthropic.com</a>.<br>'
            "The key is stored locally and passed to Claude Code on launch.</small>"
        )
        help_label.setWordWrap(True)
        help_label.setOpenExternalLinks(True)
        help_label.setStyleSheet("color: gray;")
        layout.addWidget(help_label)

        # Status label
        self.label_status = QLabel("")
        self.label_status.setStyleSheet("color: gray;")
        layout.addWidget(self.label_status)

        # Buttons
        button_layout = QHBoxLayout()
        self.btn_clear = QPushButton("Clear")
        self.btn_save = QPushButton("Save")
        self.btn_close = QPushButton("Close")
        button_layout.addWidget(self.btn_clear)
        button_layout.addStretch()
        button_layout.addWidget(self.btn_save)
        button_layout.addWidget(self.btn_close)
        layout.addLayout(button_layout)

    def _connect_signals(self):
        self.btn_show.toggled.connect(self._toggle_visibility)
        self.btn_clear.clicked.connect(self._clear_key)
        self.btn_save.clicked.connect(self._save_key)
        self.btn_close.clicked.connect(self.close)
        self.textedit_api_key.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self):
        if self._is_visible:
            self._stored_key = self.textedit_api_key.toPlainText().replace("\n", "").strip()

    def _toggle_visibility(self, show: bool):
        self._is_visible = show
        if show:
            self.textedit_api_key.setReadOnly(False)
            self.textedit_api_key.setPlainText(self._stored_key)
            self.btn_show.setText("Hide")
        else:
            self._stored_key = self.textedit_api_key.toPlainText().replace("\n", "").strip()
            self.textedit_api_key.setPlainText(_MASK_CHAR * len(self._stored_key))
            self.textedit_api_key.setReadOnly(True)
            self.btn_show.setText("Show")

    def _load_key(self):
        key = control._def.ANTHROPIC_API_KEY or ""
        self._stored_key = key
        # Start masked and read-only
        self.textedit_api_key.setPlainText(_MASK_CHAR * len(key))
        self.textedit_api_key.setReadOnly(True)

    def _save_key(self):
        """Save the API key to runtime config and cache file."""
        if self._is_visible:
            self._stored_key = self.textedit_api_key.toPlainText().replace("\n", "").strip()
        key = self._stored_key or None

        data = {"api_key": key}
        try:
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            fd = os.open(CACHE_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                yaml.dump(data, f, default_flow_style=False)
            control._def.ANTHROPIC_API_KEY = key
            self.label_status.setText("Saved" if key else "Cleared")
            self.label_status.setStyleSheet("color: green;")
            log.info("Anthropic API key %s in cache", "saved" if key else "cleared")
        except (OSError, yaml.YAMLError) as e:
            self.label_status.setText(f"Failed to save: {e}")
            self.label_status.setStyleSheet("color: red;")
            log.error(f"Failed to save Anthropic API key: {e}")

    def _clear_key(self):
        self._stored_key = ""
        self.textedit_api_key.clear()
        self._save_key()
