"""
Drag-and-drop mixin for loading acquisition YAML files.

This mixin provides drag-and-drop functionality for multipoint widgets,
allowing users to restore acquisition settings from previously saved YAML files.

Ported from upstream commit 88db4da8.

Usage:
    class MyMultiPointWidget(AcquisitionYAMLDropMixin, QFrame):
        def __init__(self):
            super().__init__()
            self.setAcceptDrops(True)

        def _get_expected_widget_type(self) -> str:
            return "wellplate"  # or "flexible"

        def _apply_yaml_settings(self, yaml_data: AcquisitionYAMLData) -> None:
            # Apply settings to widget controls
            ...
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStyle,
    QVBoxLayout,
)

from squid.backend.io.acquisition_yaml import (
    AcquisitionYAMLData,
    ValidationResult,
    parse_acquisition_yaml,
    validate_hardware,
)
import squid.core.logging

if TYPE_CHECKING:
    from PyQt5.QtCore import QEvent
    from PyQt5.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent


_log = squid.core.logging.get_logger(__name__)


class AcquisitionYAMLDropMixin:
    """Mixin class providing drag-and-drop functionality for loading acquisition YAML files.

    Widgets using this mixin must:
    1. Call `self.setAcceptDrops(True)` in __init__
    2. Have `self._log` attribute (or use module-level _log)
    3. Have access to `objectiveStore` and `multipointController` (or camera service)
    4. Implement `_get_expected_widget_type()` returning "wellplate" or "flexible"
    5. Implement `_apply_yaml_settings(yaml_data)` to apply settings to the widget
    """

    # Store original stylesheet for restore after drag
    _original_stylesheet: str = ""

    def _is_valid_yaml_drop(self, file_path: str) -> bool:
        """Check if the path is a valid YAML file or a folder containing acquisition.yaml."""
        if file_path.endswith(".yaml") or file_path.endswith(".yml"):
            return True
        # Check if it's a directory containing acquisition.yaml
        if os.path.isdir(file_path):
            yaml_path = os.path.join(file_path, "acquisition.yaml")
            if os.path.isfile(yaml_path):
                return True
        return False

    def _resolve_yaml_path(self, file_path: str) -> str:
        """Resolve the actual YAML file path from a file or folder."""
        if file_path.endswith(".yaml") or file_path.endswith(".yml"):
            return file_path
        # Check if it's a directory containing acquisition.yaml
        if os.path.isdir(file_path):
            yaml_path = os.path.join(file_path, "acquisition.yaml")
            if os.path.isfile(yaml_path):
                return yaml_path
        return file_path

    def dragEnterEvent(self, event: "QDragEnterEvent") -> None:
        """Handle drag enter event for YAML file or folder drops."""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                file_path = url.toLocalFile()
                if self._is_valid_yaml_drop(file_path):
                    event.accept()
                    # Visual feedback - dashed border (store original for restore)
                    if not hasattr(self, "_original_stylesheet") or not self._original_stylesheet:
                        self._original_stylesheet = self.styleSheet()
                    class_name = self.__class__.__name__
                    self.setStyleSheet(
                        self._original_stylesheet + f" {class_name} {{ border: 3px dashed #4a90d9; }}"
                    )
                    return
        event.ignore()

    def dragLeaveEvent(self, event: "QDragLeaveEvent") -> None:
        """Handle drag leave event."""
        if hasattr(self, "_original_stylesheet"):
            self.setStyleSheet(self._original_stylesheet)
        event.accept()

    def dropEvent(self, event: "QDropEvent") -> None:
        """Handle drop event for YAML file or folder."""
        if hasattr(self, "_original_stylesheet"):
            self.setStyleSheet(self._original_stylesheet)

        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        yaml_paths = [self._resolve_yaml_path(p) for p in paths if self._is_valid_yaml_drop(p)]

        if yaml_paths:
            if len(yaml_paths) > 1:
                log = getattr(self, "_log", _log)
                log.warning(
                    "Multiple YAML files/folders dropped (%d). Only loading the first: %s",
                    len(yaml_paths),
                    yaml_paths[0],
                )
            self._load_acquisition_yaml(yaml_paths[0])
        event.accept()

    def _get_expected_widget_type(self) -> str:
        """Return the expected widget_type for this widget. Override in subclass."""
        raise NotImplementedError("Subclass must implement _get_expected_widget_type()")

    def _get_other_widget_name(self) -> str:
        """Return the name of the other widget type for error messages."""
        if self._get_expected_widget_type() == "wellplate":
            return "Flexible Multipoint"
        return "Wellplate Multipoint"

    def _get_current_objective(self) -> str:
        """Get current objective name. Override if objectiveStore not available."""
        if hasattr(self, "objectiveStore") and self.objectiveStore:
            return self.objectiveStore.current_objective
        return ""

    def _get_current_binning(self) -> tuple:
        """Get current camera binning. Override if needed."""
        # Try multipointController.camera first
        if hasattr(self, "multipointController") and self.multipointController:
            controller = self.multipointController
            if hasattr(controller, "_camera_service") and controller._camera_service:
                try:
                    return tuple(controller._camera_service.get_binning())
                except Exception:
                    pass
        # Fall back to camera attribute
        if hasattr(self, "camera") and self.camera:
            try:
                return tuple(self.camera.get_binning())
            except Exception:
                pass
        return (1, 1)

    def _load_acquisition_yaml(self, file_path: str) -> bool:
        """Load acquisition settings from YAML file.

        Returns:
            True if settings were loaded successfully, False otherwise.
        """
        log = getattr(self, "_log", _log)

        try:
            yaml_data = parse_acquisition_yaml(file_path)
        except Exception as e:
            log.error(f"Failed to parse YAML file: {e}")
            QMessageBox.warning(self, "Load Error", f"Failed to parse YAML file:\n{e}")
            return False

        # Check widget type
        expected_type = self._get_expected_widget_type()
        if yaml_data.widget_type != expected_type:
            QMessageBox.warning(
                self,
                "Widget Type Mismatch",
                f"This YAML is for '{yaml_data.widget_type}' mode.\n"
                f"Please drop this file on the {self._get_other_widget_name()} widget instead.",
            )
            return False

        # Validate hardware
        current_objective = self._get_current_objective()
        current_binning = self._get_current_binning()

        validation = validate_hardware(yaml_data, current_objective, current_binning)

        if not validation.is_valid:
            dialog = AcquisitionYAMLMismatchDialog(validation, self)
            dialog.exec_()
            return False

        # Apply settings with signal blocking
        self._apply_yaml_settings(yaml_data)
        log.info(f"Loaded acquisition settings from: {file_path}")
        return True

    def _apply_yaml_settings(self, yaml_data: AcquisitionYAMLData) -> None:
        """Apply parsed YAML settings to widget controls. Override in subclass."""
        raise NotImplementedError("Subclass must implement _apply_yaml_settings()")


class AcquisitionYAMLMismatchDialog(QDialog):
    """Dialog shown when hardware configuration doesn't match loaded YAML settings."""

    def __init__(self, validation_result: ValidationResult, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cannot Load Settings")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        # Warning icon and title
        title_layout = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setPixmap(self.style().standardIcon(QStyle.SP_MessageBoxWarning).pixmap(32, 32))
        title_layout.addWidget(icon_label)
        title_label = QLabel("<b>Hardware Configuration Mismatch</b>")
        title_label.setStyleSheet("font-size: 14px;")
        title_layout.addWidget(title_label)
        title_layout.addStretch()
        layout.addLayout(title_layout)

        layout.addSpacing(10)

        # Mismatch details
        message_label = QLabel(validation_result.message)
        message_label.setWordWrap(True)
        message_label.setStyleSheet("background-color: #fff3cd; padding: 10px; border-radius: 4px;")
        layout.addWidget(message_label)

        layout.addSpacing(10)

        # Instructions
        instruction_label = QLabel(
            "Please update your hardware settings to match the YAML file, then drag and drop again."
        )
        instruction_label.setWordWrap(True)
        instruction_label.setStyleSheet("color: #666;")
        layout.addWidget(instruction_label)

        layout.addSpacing(15)

        # OK button
        button_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        button_layout.addStretch()
        button_layout.addWidget(ok_btn)
        layout.addLayout(button_layout)
