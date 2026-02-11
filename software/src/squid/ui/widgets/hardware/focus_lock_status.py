from __future__ import annotations

import math
from typing import Optional, TYPE_CHECKING, Tuple

import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from qtpy.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import _def
from squid.core.config.focus_lock import FocusLockConfig
from squid.core.events import (
    handles,
    FocusLockFrameUpdated,
    FocusLockMetricsUpdated,
    FocusLockModeChanged,
    FocusLockSearchProgress,
    FocusLockStatusChanged,
    FocusLockWarning,
    AdjustFocusLockTargetCommand,
    PauseFocusLockCommand,
    ReleaseFocusLockReferenceCommand,
    ResumeFocusLockCommand,
    SetFocusLockAutoSearchCommand,
    SetFocusLockParamsCommand,
    SetFocusLockReferenceCommand,
    SetPiezoPositionCommand,
    StartFocusLockCommand,
    StopFocusLockCommand,
)
from squid.ui.widgets.base import EventBusFrame

if TYPE_CHECKING:
    from squid.ui.ui_event_bus import UIEventBus


_STATUS_COLORS = {
    "disabled": "#808080",  # Gray
    "paused": "#7a7a7a",    # Dark gray
    "ready": "#3498db",     # Blue - running but not locked
    "locked": "#28a745",    # Green - actively maintaining lock
    "recovering": "#ffc107",  # Yellow/amber - trying to recover
    "searching": "#17a2b8",   # Cyan - scanning for focus
    "lost": "#dc3545",      # Red - lost lock signal
}


class HorizontalBar(QWidget):
    """Base class for horizontal indicator bars."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(12)
        self.setMinimumWidth(80)

    def _draw_background(self, painter: QPainter, w: int, h: int) -> None:
        painter.fillRect(0, 0, w, h, QColor("#e0e0e0"))

    def _draw_border(self, painter: QPainter, w: int, h: int) -> None:
        painter.setPen(QPen(QColor("#999999")))
        painter.drawRect(0, 0, w - 1, h - 1)


class HorizontalLockBar(HorizontalBar):
    """Horizontal bar showing lock quality (buffer fill level)."""

    def __init__(self, parent: Optional[QWidget] = None, max_value: int = 5) -> None:
        super().__init__(parent)
        self._value = 0
        self._max_value = max(1, max_value)
        self._is_locked = False

    def set_value(self, value: int, max_value: int, is_locked: bool) -> None:
        self._value = max(0, min(value, max_value))
        self._max_value = max(1, max_value)
        self._is_locked = is_locked
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        w, h = self.width(), self.height()

        self._draw_background(painter, w, h)

        if self._max_value > 0:
            fill_ratio = self._value / self._max_value
            fill_width = int(fill_ratio * (w - 4))

            color = QColor(0, 200, 0) if self._is_locked else QColor(100, 100, 100)
            painter.fillRect(2, 2, fill_width, h - 4, color)

        self._draw_border(painter, w, h)


class HorizontalQualityBar(HorizontalBar):
    """Horizontal bar showing smoothed lock quality (0-100%)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._quality = 1.0  # 0-1

    def set_quality(self, quality: float) -> None:
        self._quality = max(0.0, min(1.0, quality))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        w, h = self.width(), self.height()

        self._draw_background(painter, w, h)

        fill_width = int(self._quality * (w - 4))

        # Color gradient: green (good) -> yellow -> red (bad)
        if self._quality > 0.7:
            color = QColor(0, 200, 0)  # Green
        elif self._quality > 0.4:
            color = QColor(255, 200, 0)  # Yellow
        else:
            color = QColor(255, 80, 80)  # Red

        painter.fillRect(2, 2, fill_width, h - 4, color)
        self._draw_border(painter, w, h)


class HorizontalDisplacementBar(HorizontalBar):
    """Horizontal centered bar showing displacement from target."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        range_um: float = 2.0,
        threshold_um: float = 0.5,
    ) -> None:
        super().__init__(parent)
        self._displacement_um = 0.0
        self._range_um = range_um
        self._threshold_um = threshold_um
        self._is_good = False

    def set_displacement(self, displacement_um: float, is_good: bool) -> None:
        self._displacement_um = displacement_um
        self._is_good = is_good
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        w, h = self.width(), self.height()
        center_x = w // 2

        self._draw_background(painter, w, h)

        # Center line
        painter.setPen(QPen(QColor(100, 100, 100), 1))
        painter.drawLine(center_x, 0, center_x, h)

        # Position indicator
        clamped = max(-self._range_um, min(self._displacement_um, self._range_um))
        pos_ratio = clamped / self._range_um
        pos_x = center_x + int(pos_ratio * (w // 2 - 4))

        color = QColor(0, 200, 0) if self._is_good else QColor(200, 150, 0)
        painter.fillRect(pos_x - 2, 2, 5, h - 4, color)

        self._draw_border(painter, w, h)


class HorizontalPiezoBar(HorizontalBar):
    """Horizontal bar showing piezo position within range with lock reference."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        range_um: Tuple[float, float] = (0.0, 300.0),
        warning_margin_um: float = 20.0,
    ) -> None:
        super().__init__(parent)
        self._position_um = range_um[0]
        self._range_um = range_um
        self._warning_margin_um = warning_margin_um
        self._lock_reference_um: Optional[float] = None  # Position when locked

    def set_position(self, position_um: float) -> None:
        self._position_um = float(position_um)
        self.update()

    def set_lock_reference(self, position_um: Optional[float]) -> None:
        """Set the lock reference position (or None to clear)."""
        self._lock_reference_um = position_um
        self.update()

    def is_in_warning(self) -> bool:
        min_um, max_um = self._range_um
        margin = self._warning_margin_um
        return self._position_um < min_um + margin or self._position_um > max_um - margin

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        w, h = self.width(), self.height()

        min_um, max_um = self._range_um
        span = max(1e-6, max_um - min_um)
        margin = min(self._warning_margin_um, span / 2.0)
        margin_px = int((margin / span) * w)

        # Background with warning zones
        self._draw_background(painter, w, h)
        warning_color = QColor(255, 180, 180)
        painter.fillRect(0, 0, margin_px, h, warning_color)  # Left (low)
        painter.fillRect(w - margin_px, 0, margin_px, h, warning_color)  # Right (high)

        # Draw lock reference line (if set)
        if self._lock_reference_um is not None:
            ref_pos = min(max(self._lock_reference_um, min_um), max_um)
            ref_ratio = (ref_pos - min_um) / span
            ref_x = int(ref_ratio * (w - 4)) + 2
            painter.setPen(QPen(QColor(100, 100, 255), 2))  # Blue line
            painter.drawLine(ref_x, 1, ref_x, h - 1)

        # Position indicator
        pos = min(max(self._position_um, min_um), max_um)
        pos_ratio = (pos - min_um) / span
        pos_x = int(pos_ratio * (w - 4)) + 2

        in_warning = self.is_in_warning()
        color = QColor(255, 0, 0) if in_warning else QColor(0, 150, 0)
        painter.fillRect(pos_x - 2, 2, 5, h - 4, color)

        self._draw_border(painter, w, h)


class SpotPreviewWidget(QWidget):
    """Widget to display AF spot preview with crosshair overlay.

    Designed for the laser AF camera's 6:1 aspect ratio (1536x256).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._frame: Optional[np.ndarray] = None
        self._frame_bytes: Optional[bytes] = None
        self._spot_x: float = 0.0
        self._spot_y: float = 0.0
        self._spot_valid: bool = False
        self._pixmap: Optional[QPixmap] = None

        # Expand horizontally to fill available width
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(70)  # Taller for better visibility
        self.setStyleSheet("background-color: #000;")

    def set_frame(
        self, frame: np.ndarray, spot_x: float, spot_y: float, spot_valid: bool = True
    ) -> None:
        """Update the displayed frame and spot position."""
        self._frame = frame
        self._spot_x = spot_x
        self._spot_y = spot_y
        self._spot_valid = spot_valid
        self._update_pixmap()
        self.update()

    def _update_pixmap(self) -> None:
        """Convert numpy array to QPixmap."""
        if self._frame is None:
            self._pixmap = None
            self._frame_bytes = None
            return

        frame = np.asarray(self._frame)
        if frame.dtype != np.uint8:
            frame = frame.astype(np.uint8, copy=False)
        frame = np.ascontiguousarray(frame)
        h, w = frame.shape[:2]

        # Handle grayscale
        if len(frame.shape) == 2:
            bytes_per_line = w
            self._frame_bytes = frame.tobytes()
            qimg = QImage(
                self._frame_bytes, w, h, bytes_per_line, QImage.Format_Grayscale8
            )
        else:
            # RGB image
            bytes_per_line = 3 * w
            self._frame_bytes = frame.tobytes()
            qimg = QImage(
                self._frame_bytes, w, h, bytes_per_line, QImage.Format_RGB888
            )

        self._pixmap = QPixmap.fromImage(qimg)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        widget_w = self.width()
        widget_h = self.height()
        painter.fillRect(0, 0, widget_w, widget_h, QColor(0, 0, 0))

        if self._pixmap is not None and not self._pixmap.isNull():
            # Keep camera geometry intact: show full frame without distortion.
            scaled = self._pixmap.scaled(
                widget_w, widget_h, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            x_offset = (widget_w - scaled.width()) // 2
            y_offset = (widget_h - scaled.height()) // 2
            painter.drawPixmap(x_offset, y_offset, scaled)

            # Draw small circle at spot centroid position (only if spot detection was valid)
            if self._frame is not None and self._spot_valid:
                frame_h, frame_w = self._frame.shape[:2]
                scale_x = scaled.width() / frame_w
                scale_y = scaled.height() / frame_h
                spot_x = int(x_offset + self._spot_x * scale_x)
                spot_y = int(y_offset + self._spot_y * scale_y)

                # Draw green circle at spot center
                painter.setPen(QPen(QColor(0, 255, 0), 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(spot_x - 6, spot_y - 6, 12, 12)
        else:
            # Draw placeholder text
            painter.setPen(QPen(QColor(85, 85, 85)))
            painter.drawText(
                0, 0, widget_w, widget_h, Qt.AlignCenter, "AF Spot Preview"
            )

    def clear(self) -> None:
        """Clear the preview."""
        self._frame = None
        self._pixmap = None
        self._frame_bytes = None
        self.update()


class FocusLockStatusWidget(EventBusFrame):
    """Focus lock widget with vertical bars and space for laser spot view."""

    def __init__(self, event_bus: "UIEventBus", parent: Optional[QWidget] = None) -> None:
        super().__init__(event_bus, parent)
        self._config = FocusLockConfig()
        self._mode: str = self._config.default_mode
        self._status: str = "disabled"
        self._is_running = False
        self._lock_buffer_fill = 0
        self._lock_buffer_length = self._config.buffer_length
        self._is_good_reading = True
        self._z_position_um: Optional[float] = None
        self._z_error_um: Optional[float] = None
        self._spot_snr: Optional[float] = None
        self._z_error_rms_um: Optional[float] = None
        self._drift_rate_um_per_s: Optional[float] = None
        self._collapsed = False

        self._setup_ui()
        self._connect_events()
        self._sync_mode_ui()
        self._sync_status_ui()

    def _setup_ui(self) -> None:
        self.setFrameStyle(QFrame.StyledPanel)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Status bar (compact - no duplicate title since dock has one)
        header = QHBoxLayout()
        header.setSpacing(4)
        header.setContentsMargins(0, 0, 0, 0)

        self._collapse_btn = QToolButton()
        self._collapse_btn.setArrowType(Qt.DownArrow)
        self._collapse_btn.setFixedSize(16, 16)
        self._collapse_btn.clicked.connect(self._toggle_collapsed)

        self._led = QFrame()
        self._led.setFixedSize(10, 10)
        self._led.setStyleSheet("background-color: #808080; border-radius: 5px;")

        self._status_label = QLabel("DISABLED")
        self._status_label.setStyleSheet("font-size: 10px; color: #888;")

        header.addWidget(self._collapse_btn)
        header.addWidget(self._led)
        header.addWidget(self._status_label)
        header.addStretch()

        # Content - vertical layout with grouped sections
        self._content = QWidget()
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 4, 0, 4)
        content_layout.setSpacing(6)

        # AF Spot Preview - no frame, just the widget
        self._spot_preview = SpotPreviewWidget()
        content_layout.addWidget(self._spot_preview)

        # Status bars - grouped in a frame
        bars_frame = QFrame()
        bars_frame.setFrameStyle(QFrame.StyledPanel)
        bars_layout = QVBoxLayout(bars_frame)
        bars_layout.setContentsMargins(8, 8, 8, 8)
        bars_layout.setSpacing(14)  # More space between bar rows

        # Create bars
        self._offset_bar = HorizontalDisplacementBar(
            range_um=100.0,  # Large range to handle big offsets
            threshold_um=self._config.offset_threshold_um,
        )
        self._lock_bar = HorizontalLockBar(max_value=self._lock_buffer_length)
        self._quality_bar = HorizontalQualityBar()
        self._piezo_bar = HorizontalPiezoBar(
            range_um=(0.0, float(_def.OBJECTIVE_PIEZO_RANGE_UM)),
            warning_margin_um=self._config.piezo_warning_margin_um,
        )

        label_width = 50  # Wide enough for "Quality:"
        value_width = 180  # Wide enough for values like "+0.00 um (+50.0 px)"

        # Error row
        offset_row = QHBoxLayout()
        offset_row.setSpacing(4)
        offset_lbl = QLabel("Error:")
        offset_lbl.setFixedWidth(label_width)
        offset_row.addWidget(offset_lbl)
        offset_row.addWidget(self._offset_bar, 1)
        self._err_label = QLabel("--")
        self._err_label.setFixedWidth(value_width)
        self._err_label.setAlignment(Qt.AlignLeft)
        offset_row.addWidget(self._err_label)
        bars_layout.addLayout(offset_row)

        # Lock row
        lock_row = QHBoxLayout()
        lock_row.setSpacing(4)
        lock_lbl = QLabel("Lock:")
        lock_lbl.setFixedWidth(label_width)
        lock_row.addWidget(lock_lbl)
        lock_row.addWidget(self._lock_bar, 1)
        self._lock_label = QLabel("0/5")
        self._lock_label.setFixedWidth(value_width)
        self._lock_label.setAlignment(Qt.AlignLeft)
        lock_row.addWidget(self._lock_label)
        bars_layout.addLayout(lock_row)

        # Quality row
        quality_row = QHBoxLayout()
        quality_row.setSpacing(4)
        quality_lbl = QLabel("Quality:")
        quality_lbl.setFixedWidth(label_width)
        quality_row.addWidget(quality_lbl)
        quality_row.addWidget(self._quality_bar, 1)
        self._quality_label = QLabel("--")
        self._quality_label.setFixedWidth(value_width)
        self._quality_label.setAlignment(Qt.AlignLeft)
        quality_row.addWidget(self._quality_label)
        bars_layout.addLayout(quality_row)

        # Piezo row
        self._piezo_range_um = float(_def.OBJECTIVE_PIEZO_RANGE_UM)
        piezo_row = QHBoxLayout()
        piezo_row.setSpacing(4)
        piezo_lbl = QLabel("Piezo:")
        piezo_lbl.setFixedWidth(label_width)
        piezo_row.addWidget(piezo_lbl)
        piezo_row.addWidget(self._piezo_bar, 1)
        self._z_label = QLabel(f"-- / {self._piezo_range_um:.0f} um")
        self._z_label.setFixedWidth(value_width)
        self._z_label.setAlignment(Qt.AlignLeft)
        piezo_row.addWidget(self._z_label)
        bars_layout.addLayout(piezo_row)

        # Compact metrics row - all four metrics on one line (inside bars frame)
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(8)
        self._delta_label = QLabel("D: --")
        self._delta_label.setToolTip("Piezo delta from lock position (um)")
        metrics_row.addWidget(self._delta_label)
        self._snr_label = QLabel("SNR: --")
        self._snr_label.setToolTip("Spot signal-to-noise ratio")
        metrics_row.addWidget(self._snr_label)
        self._rms_label = QLabel("RMS: --")
        self._rms_label.setToolTip("RMS error (um) - lower = more stable")
        metrics_row.addWidget(self._rms_label)
        self._drift_label = QLabel("Dft: --")
        self._drift_label.setToolTip("Drift rate (um/s)")
        metrics_row.addWidget(self._drift_label)
        metrics_row.addStretch()
        bars_layout.addLayout(metrics_row)

        content_layout.addWidget(bars_frame)

        # Controls section with grid layout for better alignment
        controls_frame = QFrame()
        controls_frame.setFrameStyle(QFrame.StyledPanel)
        controls_grid = QGridLayout(controls_frame)
        controls_grid.setContentsMargins(6, 6, 6, 6)
        controls_grid.setSpacing(6)

        # Row 0: Start/Stop and Lock/Release buttons (same width)
        self._action_btn = QPushButton("Start")
        self._action_btn.setFixedWidth(65)
        self._action_btn.clicked.connect(self._on_start_stop)
        controls_grid.addWidget(self._action_btn, 0, 0)

        self._lock_btn = QPushButton("Lock")
        self._lock_btn.setFixedWidth(65)
        self._lock_btn.clicked.connect(self._on_lock_release)
        controls_grid.addWidget(self._lock_btn, 0, 1)

        # Auto-recover checkbox
        self._auto_recover_checkbox = QCheckBox("Auto-recover")
        self._auto_recover_checkbox.setChecked(self._config.auto_search_enabled)
        self._auto_recover_checkbox.setToolTip(
            "Automatically search to recover focus when lock is lost"
        )
        self._auto_recover_checkbox.toggled.connect(self._on_auto_recover_toggled)
        controls_grid.addWidget(self._auto_recover_checkbox, 0, 2, 1, 2)

        # Row 1: Adjust target (nudge) controls
        adjust_label = QLabel("Adjust target:")
        adjust_label.setToolTip("Nudge lock target position while locked")
        controls_grid.addWidget(adjust_label, 1, 0, 1, 2)

        nudge_container = QHBoxLayout()
        nudge_container.setSpacing(2)
        self._nudge_down = QPushButton("-")
        self._nudge_down.setFixedWidth(28)
        self._nudge_down.clicked.connect(lambda: self._nudge(-1.0))
        nudge_container.addWidget(self._nudge_down)

        self._nudge_step = QDoubleSpinBox()
        self._nudge_step.setRange(0.01, 10.0)
        self._nudge_step.setDecimals(2)
        self._nudge_step.setValue(0.5)
        self._nudge_step.setSuffix(" um")
        self._nudge_step.setFixedWidth(75)
        self._nudge_step.setKeyboardTracking(False)
        nudge_container.addWidget(self._nudge_step)

        self._nudge_up = QPushButton("+")
        self._nudge_up.setFixedWidth(28)
        self._nudge_up.clicked.connect(lambda: self._nudge(1.0))
        nudge_container.addWidget(self._nudge_up)
        nudge_container.addStretch()

        controls_grid.addLayout(nudge_container, 1, 2, 1, 2)

        # Row 2: Piezo position controls
        piezo_label = QLabel("Move piezo:")
        piezo_label.setToolTip("Move piezo to absolute position (disabled while locked)")
        controls_grid.addWidget(piezo_label, 2, 0, 1, 2)

        piezo_container = QHBoxLayout()
        piezo_container.setSpacing(2)

        self._jump_spinbox = QDoubleSpinBox()
        self._jump_spinbox.setRange(-150.0, 150.0)
        self._jump_spinbox.setDecimals(1)
        self._jump_spinbox.setValue(0.0)
        self._jump_spinbox.setSuffix(" um")
        self._jump_spinbox.setFixedWidth(80)
        self._jump_spinbox.setToolTip("Offset from piezo center (150 um)")
        self._jump_spinbox.setKeyboardTracking(False)
        piezo_container.addWidget(self._jump_spinbox)

        self._jump_btn = QPushButton("Go")
        self._jump_btn.setFixedWidth(50)
        self._jump_btn.setToolTip("Move piezo to center + offset")
        self._jump_btn.clicked.connect(self._on_jump_clicked)
        piezo_container.addWidget(self._jump_btn)

        self._center_btn = QPushButton("Center")
        self._center_btn.setFixedWidth(50)
        self._center_btn.setToolTip("Move piezo to center of range (150 um)")
        self._center_btn.clicked.connect(self._on_center_clicked)
        piezo_container.addWidget(self._center_btn)
        piezo_container.addStretch()

        controls_grid.addLayout(piezo_container, 2, 2, 1, 2)

        content_layout.addWidget(controls_frame)

        # Collapsed row - shows summary of all variables
        self._collapsed_row = QWidget()
        collapsed_layout = QHBoxLayout(self._collapsed_row)
        collapsed_layout.setContentsMargins(20, 2, 0, 2)
        collapsed_layout.setSpacing(8)

        # Summary labels: Z | Delta | Offset | Lock | SNR
        self._collapsed_z = QLabel("Z: --")
        self._collapsed_z.setStyleSheet("font-size: 10px;")
        self._collapsed_delta = QLabel("D: --")
        self._collapsed_delta.setStyleSheet("font-size: 10px;")
        self._collapsed_offset = QLabel("Err: --")
        self._collapsed_offset.setStyleSheet("font-size: 10px;")
        self._collapsed_lock = QLabel("Lock: 0/5")
        self._collapsed_lock.setStyleSheet("font-size: 10px;")
        self._collapsed_snr = QLabel("SNR: --")
        self._collapsed_snr.setStyleSheet("font-size: 10px;")

        collapsed_layout.addWidget(self._collapsed_z)
        collapsed_layout.addWidget(self._collapsed_delta)
        collapsed_layout.addWidget(self._collapsed_offset)
        collapsed_layout.addWidget(self._collapsed_lock)
        collapsed_layout.addWidget(self._collapsed_snr)
        collapsed_layout.addStretch()

        self._collapsed_btn = QPushButton("Start")
        self._collapsed_btn.setFixedSize(50, 22)
        self._collapsed_btn.clicked.connect(self._on_start_stop)
        collapsed_layout.addWidget(self._collapsed_btn)

        self._collapsed_lock_btn = QPushButton("Lock")
        self._collapsed_lock_btn.setFixedSize(50, 22)
        self._collapsed_lock_btn.clicked.connect(self._on_lock_release)
        collapsed_layout.addWidget(self._collapsed_lock_btn)

        main_layout.addLayout(header)
        main_layout.addWidget(self._content)
        main_layout.addWidget(self._collapsed_row)

        self._update_collapsed_ui()

    def _connect_events(self) -> None:
        # Subscriptions are now handled automatically by EventBusFrame base class
        pass

    def _toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self._update_collapsed_ui()

    def _update_collapsed_ui(self) -> None:
        self._collapse_btn.setArrowType(Qt.RightArrow if self._collapsed else Qt.DownArrow)
        self._content.setVisible(not self._collapsed)
        self._collapsed_row.setVisible(self._collapsed)
        # Notify parent layouts that size hint changed
        self.updateGeometry()
        self.adjustSize()

    def sizeHint(self) -> "QSize":
        """Return appropriate size hint based on collapsed state."""
        from qtpy.QtCore import QSize

        if self._collapsed:
            # When collapsed, only need space for header + collapsed row
            return QSize(480, 60)
        else:
            # When expanded, use default size calculation
            return super().sizeHint()

    def minimumSizeHint(self) -> "QSize":
        """Return minimum size based on collapsed state."""
        from qtpy.QtCore import QSize

        if self._collapsed:
            return QSize(300, 50)
        else:
            return super().minimumSizeHint()

    def _sync_mode_ui(self) -> None:
        self._update_buttons()

    def _sync_status_ui(self) -> None:
        color = _STATUS_COLORS.get(self._status, "#808080")
        self._led.setStyleSheet(f"background-color: {color}; border-radius: 5px;")
        self._status_label.setText(self._status.upper())
        self._update_buttons()
        self._update_nudge_enabled()
        self._update_piezo_controls_enabled()
        self._update_lock_bar()

    def _update_buttons(self) -> None:
        self._action_btn.setEnabled(True)
        self._collapsed_btn.setEnabled(True)

        label = "Stop" if self._is_running else "Start"
        self._action_btn.setText(label)
        self._collapsed_btn.setText(label)

        # Lock/Release button - only enabled when running
        lock_enabled = self._is_running
        self._lock_btn.setEnabled(lock_enabled)
        self._collapsed_lock_btn.setEnabled(lock_enabled)

        # Show "Release" when locked or recovering (still trying to maintain lock)
        if self._status in ("locked", "recovering"):
            lock_label = "Release"
        else:
            lock_label = "Lock"

        self._lock_btn.setText(lock_label)
        self._collapsed_lock_btn.setText(lock_label)

    def _on_start_stop(self) -> None:
        if self._status == "paused":
            self._publish(ResumeFocusLockCommand())
        elif self._is_running:
            self._publish(StopFocusLockCommand())
        else:
            self._publish(StartFocusLockCommand())

    def _on_lock_release(self) -> None:
        if self._status in ("locked", "recovering"):
            self._publish(ReleaseFocusLockReferenceCommand())
        else:
            self._publish(SetFocusLockReferenceCommand())

    def _nudge(self, direction: float) -> None:
        delta = direction * float(self._nudge_step.value())
        if delta != 0.0:
            self._publish(AdjustFocusLockTargetCommand(delta_um=delta))

    @handles(FocusLockModeChanged)
    def _on_mode_changed(self, event: FocusLockModeChanged) -> None:
        self._mode = event.mode
        self._sync_mode_ui()

    @handles(FocusLockStatusChanged)
    def _on_status_changed(self, event: FocusLockStatusChanged) -> None:
        self._status = event.status
        self._is_running = event.status in ("ready", "locked", "recovering", "searching", "lost", "paused")
        self._lock_buffer_fill = event.lock_buffer_fill
        self._lock_buffer_length = event.lock_buffer_length
        self._sync_status_ui()

    @handles(SetFocusLockParamsCommand)
    def _on_focus_lock_params_changed(self, event: SetFocusLockParamsCommand) -> None:
        updates = {}
        if event.buffer_length is not None:
            updates["buffer_length"] = event.buffer_length
        if event.recovery_attempts is not None:
            updates["recovery_attempts"] = event.recovery_attempts
        if event.min_spot_snr is not None:
            updates["min_spot_snr"] = event.min_spot_snr
        if event.acquire_threshold_um is not None:
            updates["acquire_threshold_um"] = event.acquire_threshold_um
        if event.maintain_threshold_um is not None:
            updates["maintain_threshold_um"] = event.maintain_threshold_um
        if updates:
            self._config = self._config.model_copy(update=updates)

    @handles(FocusLockMetricsUpdated)
    def _on_metrics_updated(self, event: FocusLockMetricsUpdated) -> None:
        self._z_position_um = event.z_position_um
        self._z_error_um = event.z_error_um
        self._spot_snr = event.spot_snr
        self._z_error_rms_um = event.z_error_rms_um
        self._drift_rate_um_per_s = event.drift_rate_um_per_s
        self._is_good_reading = event.is_good_reading

        # Update lock buffer from metrics (for continuous bar updates)
        self._lock_buffer_fill = event.lock_buffer_fill
        self._lock_buffer_length = event.lock_buffer_length
        self._update_lock_bar()

        # Update labels (values only - titles are above bars)
        if self._z_position_um is not None:
            self._z_label.setText(f"{self._z_position_um:.1f} / {self._piezo_range_um:.0f} um")
            self._collapsed_z.setText(f"Z: {self._z_position_um:.1f}")
            self._piezo_bar.set_position(self._z_position_um)

        # Show offset value regardless of is_good_reading (use styling to indicate quality)
        if self._z_error_um is not None and not math.isnan(self._z_error_um):
            # Show offset in um and pixels (if available)
            spot_px = event.spot_offset_px
            if not math.isnan(spot_px):
                err_text = f"{self._z_error_um:+.2f} um ({spot_px:+.1f} px)"
            else:
                err_text = f"{self._z_error_um:+.2f} um"
            self._err_label.setText(err_text)
            self._collapsed_offset.setText(f"Err: {self._z_error_um:+.2f} um")
        else:
            self._err_label.setText("--")
            self._collapsed_offset.setText("Err: --")

        # Show SNR with color coding (red when < 5)
        if self._spot_snr is not None and not math.isnan(self._spot_snr):
            self._snr_label.setText(f"SNR: {self._spot_snr:.0f}")
            self._collapsed_snr.setText(f"SNR: {self._spot_snr:.0f}")
            if self._spot_snr < self._config.min_spot_snr:
                self._snr_label.setStyleSheet("color: #ff6666;")
            else:
                self._snr_label.setStyleSheet("")
        else:
            self._snr_label.setText("SNR:--")
            self._snr_label.setStyleSheet("color: #999;")
            self._collapsed_snr.setText("SNR:--")

        # Show RMS with color coding (red when > 0.1 um)
        if self._z_error_rms_um is not None and not math.isnan(self._z_error_rms_um):
            self._rms_label.setText(f"RMS: {self._z_error_rms_um:.2f} um")
            if self._z_error_rms_um > 0.1:
                self._rms_label.setStyleSheet("color: #ff6666;")
            else:
                self._rms_label.setStyleSheet("")
        else:
            self._rms_label.setText("RMS:--")
            self._rms_label.setStyleSheet("color: #999;")

        # Show drift with color coding (red when > 0.5 um/s)
        if self._drift_rate_um_per_s is not None and not math.isnan(self._drift_rate_um_per_s):
            self._drift_label.setText(f"Dft: {self._drift_rate_um_per_s:+.2f} um/s")
            if abs(self._drift_rate_um_per_s) > 0.5:
                self._drift_label.setStyleSheet("color: #ff6666;")
            else:
                self._drift_label.setStyleSheet("")
        else:
            self._drift_label.setText("Dft:--")
            self._drift_label.setStyleSheet("color: #999;")

        # Error label styling
        if self._is_good_reading:
            self._err_label.setStyleSheet("")
        else:
            self._err_label.setStyleSheet("color: #999;")

        # Update piezo delta (movement from locked position)
        piezo_delta = event.piezo_delta_um
        if not math.isnan(piezo_delta):
            self._delta_label.setText(f"D: {piezo_delta:+.1f}")
            self._collapsed_delta.setText(f"D: {piezo_delta:+.1f}")
            # Calculate and show lock reference on piezo bar
            if self._z_position_um is not None:
                lock_ref = self._z_position_um - piezo_delta
                self._piezo_bar.set_lock_reference(lock_ref)
        else:
            self._delta_label.setText("D: --")
            self._collapsed_delta.setText("D: --")
            self._piezo_bar.set_lock_reference(None)

        # Update displacement bar
        error = self._z_error_um if self._z_error_um is not None else 0.0
        if math.isnan(error):
            error = 0.0
        self._offset_bar.set_displacement(error, self._is_good_reading)

        # Update quality bar (smoothed RMS-based quality 0-1)
        quality = event.lock_quality
        if not math.isnan(quality):
            self._quality_bar.set_quality(quality)
            self._quality_label.setText(f"{quality * 100:.0f}%")
        else:
            self._quality_bar.set_quality(0.0)
            self._quality_label.setText("--")

    def _update_lock_bar(self) -> None:
        is_locked = self._status == "locked"
        self._lock_bar.set_value(self._lock_buffer_fill, self._lock_buffer_length, is_locked)
        lock_text = f"{self._lock_buffer_fill}/{self._lock_buffer_length}"
        self._lock_label.setText(lock_text)
        self._collapsed_lock.setText(f"Lock: {lock_text}")

    @handles(FocusLockFrameUpdated)
    def _on_frame_updated(self, event: FocusLockFrameUpdated) -> None:
        """Handle frame update events from focus lock controller."""
        self._spot_preview.set_frame(
            event.frame, event.spot_x_px, event.spot_y_px, event.spot_valid
        )

    def _update_nudge_enabled(self) -> None:
        # Adjust only works when locked (adjusts target displacement)
        enabled = self._status == "locked"
        self._nudge_down.setEnabled(enabled)
        self._nudge_up.setEnabled(enabled)
        self._nudge_step.setEnabled(enabled)

    def _update_piezo_controls_enabled(self) -> None:
        # Piezo Set/Reset works when focus lock is not engaged
        # (disabled when locked/recovering/searching as control loop overrides manual moves)
        enabled = self._status in ("disabled", "ready", "paused", "lost")
        self._jump_spinbox.setEnabled(enabled)
        self._jump_btn.setEnabled(enabled)
        self._center_btn.setEnabled(enabled)
        if not enabled:
            tooltip = "Disabled while Focus Lock is engaged"
            self._jump_spinbox.setToolTip(tooltip)
            self._jump_btn.setToolTip(tooltip)
            self._center_btn.setToolTip(tooltip)
        else:
            self._jump_spinbox.setToolTip("Offset from piezo center")
            self._jump_btn.setToolTip("Move piezo to center + offset")
            self._center_btn.setToolTip("Move piezo to center of range")

    @handles(FocusLockWarning)
    def _on_warning(self, event: FocusLockWarning) -> None:
        # Log warnings from backend but no separate indicator
        pass

    def _on_auto_recover_toggled(self, checked: bool) -> None:
        """Handle auto-recover checkbox toggle."""
        self._publish(SetFocusLockAutoSearchCommand(enabled=checked))

    def _on_jump_clicked(self) -> None:
        """Jump piezo to center + offset."""
        offset = self._jump_spinbox.value()
        center = self._piezo_range_um / 2.0
        target = center + offset
        # Clamp to valid range
        target = max(0.0, min(self._piezo_range_um, target))
        self._publish(SetPiezoPositionCommand(position_um=target))

    def _on_center_clicked(self) -> None:
        """Move piezo to center of range."""
        center = self._piezo_range_um / 2.0
        self._publish(SetPiezoPositionCommand(position_um=center))

    @handles(FocusLockSearchProgress)
    def _on_search_progress(self, event: FocusLockSearchProgress) -> None:
        """Handle search progress events."""
        # Update status label to show search progress
        if event.phase == "last_position":
            self._status_label.setText("SEARCH: last pos")
        else:
            # Show progress through sweep
            span = event.search_max_um - event.search_min_um
            progress = (event.current_position_um - event.search_min_um) / span if span > 0 else 0.0
            self._status_label.setText(f"SEARCH: {progress * 100:.0f}%")

    def pause(self) -> None:
        self._publish(PauseFocusLockCommand())

    def resume(self) -> None:
        self._publish(ResumeFocusLockCommand())
