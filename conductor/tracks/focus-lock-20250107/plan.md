# Focus Lock Implementation Plan

## Summary

Implement continuous closed-loop focus lock with:
- Mode-based operation (Off / Always On / Auto Lock)
- Gain-scheduled control algorithm
- Dockable status widget visible alongside live imaging
- Comprehensive quality metrics
- Piezo-only Z control

## Phase 1: Core Events and Data Structures

### 1.1 Add New Events

**File: `software/src/squid/core/events.py`**

Add the following events:

```python
# Focus Lock Status Events
@dataclass(frozen=True)
class FocusLockModeChanged(Event):
    """Focus lock mode changed."""
    mode: str  # "off" | "always_on" | "auto_lock"

@dataclass(frozen=True)
class FocusLockStatusChanged(Event):
    """Lock status changed."""
    is_locked: bool
    status: str  # "locked" | "searching" | "lost" | "disabled" | "paused"
    lock_buffer_fill: int = 0
    lock_buffer_length: int = 5

@dataclass(frozen=True)
class FocusLockMetricsUpdated(Event):
    """Real-time metrics update."""
    z_error_um: float
    z_position_um: float
    spot_snr: float
    spot_intensity: float
    is_good_reading: bool
    correlation: float = 0.0
    z_error_rms_um: float = 0.0
    drift_rate_um_per_s: float = 0.0

@dataclass(frozen=True)
class FocusLockWarning(Event):
    """Warning condition detected."""
    warning_type: str
    message: str

# Focus Lock Commands
@dataclass(frozen=True)
class SetFocusLockModeCommand(Event):
    """Set focus lock mode."""
    mode: str

@dataclass(frozen=True)
class StartFocusLockCommand(Event):
    """Start focus lock."""
    target_um: float = 0.0

@dataclass(frozen=True)
class StopFocusLockCommand(Event):
    """Stop focus lock."""
    pass

@dataclass(frozen=True)
class PauseFocusLockCommand(Event):
    """Temporarily pause focus lock."""
    pass

@dataclass(frozen=True)
class ResumeFocusLockCommand(Event):
    """Resume paused focus lock."""
    pass
```

### 1.2 Add Configuration Model

**File: `software/src/squid/core/config/focus_lock.py`** (new file)

```python
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class FocusLockConfig:
    """Configuration for continuous focus lock."""

    # Control parameters
    lock_gain: float = 0.5
    lock_gain_max: float = 0.7
    lock_buffer_length: int = 5
    offset_threshold_um: float = 0.5
    min_spot_snr: float = 5.0

    # Timing
    loop_rate_hz: float = 30.0
    metrics_publish_rate_hz: float = 10.0

    # Piezo limits
    piezo_warning_margin_um: float = 20.0

    # Default mode
    default_mode: Literal["off", "always_on", "auto_lock"] = "off"
```

---

## Phase 2: Focus Lock Controller

### 2.1 Create ContinuousFocusLockController

**File: `software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py`** (new file)

```python
"""
Continuous closed-loop focus lock controller.

Uses the existing LaserAutofocusController for displacement measurement
and PiezoService for Z corrections. Implements gain-scheduled control
with buffer-based lock quality assessment.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional

import numpy as np

import squid.logging
from squid.core.events import (
    EventBus,
    FocusLockModeChanged,
    FocusLockStatusChanged,
    FocusLockMetricsUpdated,
    FocusLockWarning,
    SetFocusLockModeCommand,
    StartFocusLockCommand,
    StopFocusLockCommand,
    PauseFocusLockCommand,
    ResumeFocusLockCommand,
)

if TYPE_CHECKING:
    from squid.backend.controllers.autofocus import LaserAutofocusController
    from squid.backend.services import PiezoService
    from squid.core.config.focus_lock import FocusLockConfig


@dataclass
class FocusLockState:
    """Immutable state for focus lock."""
    mode: str = "off"
    status: str = "disabled"
    is_locked: bool = False
    lock_buffer_fill: int = 0


class MetricsTracker:
    """Track focus lock metrics over time."""

    def __init__(self, buffer_length: int = 50):
        self.z_errors = deque(maxlen=buffer_length)
        self.timestamps = deque(maxlen=buffer_length)

    def update(self, z_error_um: float) -> dict:
        timestamp = time.perf_counter()
        self.z_errors.append(z_error_um)
        self.timestamps.append(timestamp)

        # RMS error
        if len(self.z_errors) > 0:
            z_error_rms = float(np.sqrt(np.mean(np.array(self.z_errors) ** 2)))
        else:
            z_error_rms = 0.0

        # Drift rate (linear regression slope)
        drift_rate = 0.0
        if len(self.z_errors) >= 10:
            times = np.array(self.timestamps) - self.timestamps[0]
            errors = np.array(self.z_errors)
            try:
                slope, _ = np.polyfit(times, errors, 1)
                drift_rate = float(slope)
            except Exception:
                pass

        return {
            "z_error_rms_um": z_error_rms,
            "drift_rate_um_per_s": drift_rate,
        }

    def reset(self):
        self.z_errors.clear()
        self.timestamps.clear()


class ContinuousFocusLockController:
    """
    Continuous closed-loop focus lock using piezo Z stage.

    Modes:
    - off: No continuous lock, single-shot AF only
    - always_on: Continuous lock, user toggleable
    - auto_lock: Lock active only during acquisition
    """

    def __init__(
        self,
        laser_af_controller: "LaserAutofocusController",
        piezo_service: "PiezoService",
        event_bus: EventBus,
        config: Optional["FocusLockConfig"] = None,
    ):
        self._log = squid.logging.get_logger(__name__)
        self._laser_af = laser_af_controller
        self._piezo = piezo_service
        self._event_bus = event_bus

        # Configuration
        if config is None:
            from squid.core.config.focus_lock import FocusLockConfig
            config = FocusLockConfig()
        self._config = config

        # State
        self._mode: Literal["off", "always_on", "auto_lock"] = config.default_mode
        self._is_running = False
        self._is_paused = False
        self._lock_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Lock target
        self._target_um = 0.0

        # Lock quality buffer
        self._lock_buffer = np.zeros(config.lock_buffer_length, dtype=np.uint8)
        self._lock_buffer_idx = 0

        # Metrics tracking
        self._metrics_tracker = MetricsTracker()
        self._last_metrics_time = 0.0

        # Subscribe to commands
        self._event_bus.subscribe(SetFocusLockModeCommand, self._on_set_mode)
        self._event_bus.subscribe(StartFocusLockCommand, self._on_start)
        self._event_bus.subscribe(StopFocusLockCommand, self._on_stop)
        self._event_bus.subscribe(PauseFocusLockCommand, self._on_pause)
        self._event_bus.subscribe(ResumeFocusLockCommand, self._on_resume)

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_running(self) -> bool:
        return self._is_running and not self._is_paused

    @property
    def is_locked(self) -> bool:
        return int(np.sum(self._lock_buffer)) == len(self._lock_buffer)

    @property
    def state(self) -> FocusLockState:
        if not self._is_running:
            status = "disabled"
        elif self._is_paused:
            status = "paused"
        elif self.is_locked:
            status = "locked"
        else:
            status = "searching"

        return FocusLockState(
            mode=self._mode,
            status=status,
            is_locked=self.is_locked,
            lock_buffer_fill=int(np.sum(self._lock_buffer)),
        )

    # =========================================================================
    # Public Methods
    # =========================================================================

    def set_mode(self, mode: str) -> None:
        """Set focus lock mode."""
        if mode not in ("off", "always_on", "auto_lock"):
            self._log.error(f"Invalid focus lock mode: {mode}")
            return

        old_mode = self._mode
        self._mode = mode

        if mode == "off" and self._is_running:
            self.stop()

        self._event_bus.publish(FocusLockModeChanged(mode=mode))
        self._log.info(f"Focus lock mode changed: {old_mode} -> {mode}")

    def start(self, target_um: float = 0.0) -> bool:
        """Start continuous focus lock."""
        if self._mode == "off":
            self._log.warning("Cannot start focus lock in 'off' mode")
            return False

        if not self._laser_af.is_initialized:
            self._log.error("Laser AF not initialized")
            return False

        if not self._laser_af.laser_af_properties.has_reference:
            self._log.error("No reference set for laser AF")
            return False

        if self._is_running:
            self._log.warning("Focus lock already running")
            return True

        self._target_um = target_um
        self._is_running = True
        self._is_paused = False
        self._stop_event.clear()
        self._reset_lock_buffer()
        self._metrics_tracker.reset()

        self._lock_thread = threading.Thread(
            target=self._focus_lock_loop,
            name="FocusLock",
            daemon=True,
        )
        self._lock_thread.start()

        self._publish_status()
        self._log.info(f"Focus lock started (target: {target_um:.2f} μm)")
        return True

    def stop(self) -> None:
        """Stop focus lock."""
        if not self._is_running:
            return

        self._stop_event.set()
        self._is_running = False

        if self._lock_thread and self._lock_thread.is_alive():
            self._lock_thread.join(timeout=2.0)

        self._lock_thread = None
        self._reset_lock_buffer()

        self._publish_status()
        self._log.info("Focus lock stopped")

    def pause(self) -> None:
        """Temporarily pause focus lock (e.g., during Z-stack)."""
        if self._is_running and not self._is_paused:
            self._is_paused = True
            self._publish_status()
            self._log.info("Focus lock paused")

    def resume(self) -> None:
        """Resume paused focus lock."""
        if self._is_running and self._is_paused:
            self._is_paused = False
            self._reset_lock_buffer()
            self._publish_status()
            self._log.info("Focus lock resumed")

    def wait_for_lock(self, timeout_s: float = 5.0) -> bool:
        """Wait for focus lock to stabilize."""
        if not self._is_running:
            return False

        start = time.perf_counter()
        while time.perf_counter() - start < timeout_s:
            if self.is_locked:
                return True
            time.sleep(0.05)

        self._log.warning(f"Focus lock did not stabilize within {timeout_s}s")
        return False

    # =========================================================================
    # Control Loop
    # =========================================================================

    def _focus_lock_loop(self) -> None:
        """Main focus lock control loop."""
        loop_period = 1.0 / self._config.loop_rate_hz

        while not self._stop_event.is_set():
            loop_start = time.perf_counter()

            if self._is_paused:
                time.sleep(0.05)
                continue

            try:
                self._focus_lock_iteration()
            except Exception as e:
                self._log.exception(f"Error in focus lock loop: {e}")

            # Rate limiting
            elapsed = time.perf_counter() - loop_start
            sleep_time = max(0, loop_period - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _focus_lock_iteration(self) -> None:
        """Single iteration of focus lock."""
        # 1. Measure displacement
        displacement_um = self._laser_af.measure_displacement()

        if math.isnan(displacement_um):
            self._update_lock_buffer(False)
            self._publish_metrics_if_due(
                z_error_um=float('nan'),
                z_position_um=self._piezo.get_position(),
                spot_snr=0.0,
                spot_intensity=0.0,
                is_good_reading=False,
            )
            self._check_signal_lost()
            return

        # 2. Compute error
        error_um = displacement_um - self._target_um

        # 3. Get current Z position
        current_z = self._piezo.get_position()

        # 4. Compute correction
        correction_um = self._control_fn(error_um)

        # 5. Check piezo range
        new_z = current_z + correction_um
        piezo_min, piezo_max = self._piezo.get_range()

        if not self._check_piezo_range(new_z, piezo_min, piezo_max):
            # Clamp to safe range
            new_z = max(piezo_min + 1, min(piezo_max - 1, new_z))

        # 6. Apply correction
        self._piezo.move_to_fast(new_z)

        # 7. Update lock status
        is_good = abs(error_um) < self._config.offset_threshold_um
        self._update_lock_buffer(is_good)

        # 8. Track metrics
        temporal_metrics = self._metrics_tracker.update(error_um)

        # 9. Publish metrics (throttled)
        self._publish_metrics_if_due(
            z_error_um=error_um,
            z_position_um=new_z,
            spot_snr=10.0,  # TODO: Get from spot detection
            spot_intensity=100.0,  # TODO: Get from spot detection
            is_good_reading=True,
            **temporal_metrics,
        )

        # 10. Check for status change
        self._publish_status_if_changed()

    def _control_fn(self, error_um: float) -> float:
        """
        Gain-scheduled proportional control.

        Uses exponential gain scheduling:
        - Near target: low gain for stability
        - Far from target: high gain for fast recovery
        """
        sigma = 0.5  # μm, transition width
        dx = error_um ** 2 / sigma
        scale = self._config.lock_gain_max - self._config.lock_gain
        p_term = self._config.lock_gain_max - scale * math.exp(-dx)
        return -p_term * error_um

    # =========================================================================
    # Lock Quality
    # =========================================================================

    def _update_lock_buffer(self, is_good: bool) -> None:
        """Update circular buffer of lock quality."""
        self._lock_buffer[self._lock_buffer_idx] = 1 if is_good else 0
        self._lock_buffer_idx = (self._lock_buffer_idx + 1) % len(self._lock_buffer)

    def _reset_lock_buffer(self) -> None:
        """Reset lock buffer to all zeros."""
        self._lock_buffer.fill(0)
        self._lock_buffer_idx = 0

    # =========================================================================
    # Range Checking
    # =========================================================================

    def _check_piezo_range(self, z_um: float, z_min: float, z_max: float) -> bool:
        """Check if Z position is within safe range."""
        margin = self._config.piezo_warning_margin_um

        if z_um < z_min + margin:
            self._event_bus.publish(FocusLockWarning(
                warning_type="piezo_low",
                message=f"Piezo near lower limit ({z_um:.1f} μm)",
            ))
            return z_um >= z_min

        if z_um > z_max - margin:
            self._event_bus.publish(FocusLockWarning(
                warning_type="piezo_high",
                message=f"Piezo near upper limit ({z_um:.1f} μm)",
            ))
            return z_um <= z_max

        return True

    def _check_signal_lost(self) -> None:
        """Check if signal has been lost for too long."""
        if int(np.sum(self._lock_buffer)) == 0:
            self._event_bus.publish(FocusLockWarning(
                warning_type="signal_lost",
                message="Focus lock signal lost",
            ))

    # =========================================================================
    # Event Publishing
    # =========================================================================

    def _publish_status(self) -> None:
        """Publish current lock status."""
        state = self.state
        self._event_bus.publish(FocusLockStatusChanged(
            is_locked=state.is_locked,
            status=state.status,
            lock_buffer_fill=state.lock_buffer_fill,
            lock_buffer_length=len(self._lock_buffer),
        ))

    def _publish_status_if_changed(self) -> None:
        """Publish status only if it changed."""
        # Simple implementation - could be optimized with state tracking
        self._publish_status()

    def _publish_metrics_if_due(self, **metrics) -> None:
        """Publish metrics at throttled rate."""
        now = time.perf_counter()
        period = 1.0 / self._config.metrics_publish_rate_hz

        if now - self._last_metrics_time >= period:
            self._last_metrics_time = now
            self._event_bus.publish(FocusLockMetricsUpdated(
                z_error_um=metrics.get("z_error_um", 0.0),
                z_position_um=metrics.get("z_position_um", 0.0),
                spot_snr=metrics.get("spot_snr", 0.0),
                spot_intensity=metrics.get("spot_intensity", 0.0),
                is_good_reading=metrics.get("is_good_reading", False),
                correlation=metrics.get("correlation", 0.0),
                z_error_rms_um=metrics.get("z_error_rms_um", 0.0),
                drift_rate_um_per_s=metrics.get("drift_rate_um_per_s", 0.0),
            ))

    # =========================================================================
    # Command Handlers
    # =========================================================================

    def _on_set_mode(self, cmd: SetFocusLockModeCommand) -> None:
        self.set_mode(cmd.mode)

    def _on_start(self, cmd: StartFocusLockCommand) -> None:
        self.start(target_um=cmd.target_um)

    def _on_stop(self, cmd: StopFocusLockCommand) -> None:
        self.stop()

    def _on_pause(self, cmd: PauseFocusLockCommand) -> None:
        self.pause()

    def _on_resume(self, cmd: ResumeFocusLockCommand) -> None:
        self.resume()
```

### 2.2 Update Controller Package

**File: `software/src/squid/backend/controllers/autofocus/__init__.py`**

Add export:
```python
from squid.backend.controllers.autofocus.continuous_focus_lock import ContinuousFocusLockController
```

---

## Phase 3: UI Widget

### 3.1 Create FocusLockStatusWidget

**File: `software/src/squid/ui/widgets/hardware/focus_lock_status.py`** (new file)

```python
"""
Dockable focus lock status widget.

Displays AF camera preview, lock status, and metrics.
Can be docked alongside any image display tab.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QFrame, QProgressBar, QSizePolicy,
    QGroupBox, QGridLayout,
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QColor, QPalette, QPixmap, QImage
import numpy as np

from squid.core.events import (
    FocusLockModeChanged,
    FocusLockStatusChanged,
    FocusLockMetricsUpdated,
    FocusLockWarning,
    SetFocusLockModeCommand,
    StartFocusLockCommand,
    StopFocusLockCommand,
)


class StatusLED(QWidget):
    """Simple LED status indicator."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._color = "gray"

    def set_color(self, color: str):
        """Set LED color: 'green', 'yellow', 'red', 'gray'."""
        self._color = color
        self.update()

    def paintEvent(self, event):
        from PyQt5.QtGui import QPainter, QBrush
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        colors = {
            "green": QColor(0, 200, 0),
            "yellow": QColor(255, 200, 0),
            "red": QColor(255, 50, 50),
            "gray": QColor(128, 128, 128),
        }
        color = colors.get(self._color, colors["gray"])

        painter.setBrush(QBrush(color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(2, 2, 12, 12)


class AFCameraPreview(QLabel):
    """Small preview of AF camera image."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(150, 100)
        self.setStyleSheet("background-color: black; border: 1px solid gray;")
        self.setAlignment(Qt.AlignCenter)
        self.setText("AF Camera")
        self._spot_x = None
        self._spot_y = None

    def update_image(self, image: np.ndarray, spot_x: float = None, spot_y: float = None):
        """Update preview with new image and optional spot location."""
        if image is None:
            return

        self._spot_x = spot_x
        self._spot_y = spot_y

        # Convert to QImage
        if image.dtype != np.uint8:
            image = (image / image.max() * 255).astype(np.uint8)

        h, w = image.shape[:2]
        if len(image.shape) == 2:
            qimg = QImage(image.data, w, h, w, QImage.Format_Grayscale8)
        else:
            qimg = QImage(image.data, w, h, w * 3, QImage.Format_RGB888)

        # Scale to widget size
        pixmap = QPixmap.fromImage(qimg).scaled(
            self.width(), self.height(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation
        )

        self.setPixmap(pixmap)


class ErrorBar(QWidget):
    """Vertical bar showing focus error."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(20)
        self.setMinimumHeight(60)
        self._value = 0.0  # -1.0 to 1.0
        self._is_locked = False

    def set_value(self, error_um: float, max_error_um: float = 2.0):
        """Set error value (clamped to -1..1 range)."""
        self._value = max(-1.0, min(1.0, error_um / max_error_um))
        self.update()

    def set_locked(self, is_locked: bool):
        self._is_locked = is_locked
        self.update()

    def paintEvent(self, event):
        from PyQt5.QtGui import QPainter, QBrush, QPen
        painter = QPainter(self)

        # Background
        painter.fillRect(0, 0, self.width(), self.height(), QColor(240, 240, 240))

        # Center line
        center_y = self.height() // 2
        painter.setPen(QPen(QColor(100, 100, 100), 1))
        painter.drawLine(0, center_y, self.width(), center_y)

        # Error bar
        bar_color = QColor(0, 200, 0) if self._is_locked else QColor(50, 50, 50)
        painter.setBrush(QBrush(bar_color))
        painter.setPen(Qt.NoPen)

        bar_height = int(abs(self._value) * (self.height() // 2 - 2))
        if self._value >= 0:
            painter.drawRect(4, center_y - bar_height, self.width() - 8, bar_height)
        else:
            painter.drawRect(4, center_y, self.width() - 8, bar_height)


class FocusLockStatusWidget(QWidget):
    """
    Compact focus lock status panel.

    Shows:
    - AF camera preview (small)
    - Lock status LED
    - Z position and error
    - Mode selector
    - Lock/Unlock button
    """

    def __init__(self, event_bus, parent=None):
        super().__init__(parent)
        self._event_bus = event_bus
        self._is_collapsed = False

        self._setup_ui()
        self._connect_events()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Header with collapse button
        header = QHBoxLayout()
        self._collapse_btn = QPushButton("−")
        self._collapse_btn.setFixedSize(20, 20)
        self._collapse_btn.clicked.connect(self._toggle_collapse)
        header.addWidget(QLabel("Focus Lock"))
        header.addStretch()
        header.addWidget(self._collapse_btn)
        layout.addLayout(header)

        # Collapsible content
        self._content = QWidget()
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(4)

        # AF Camera preview
        self._camera_preview = AFCameraPreview()
        content_layout.addWidget(self._camera_preview)

        # Status row
        status_row = QHBoxLayout()
        self._status_led = StatusLED()
        self._status_label = QLabel("OFF")
        self._status_label.setStyleSheet("font-weight: bold;")
        status_row.addWidget(self._status_led)
        status_row.addWidget(self._status_label)
        status_row.addStretch()
        content_layout.addLayout(status_row)

        # Metrics
        metrics_grid = QGridLayout()
        metrics_grid.setSpacing(2)

        metrics_grid.addWidget(QLabel("Z:"), 0, 0)
        self._z_value = QLabel("--.- μm")
        metrics_grid.addWidget(self._z_value, 0, 1)

        metrics_grid.addWidget(QLabel("Err:"), 1, 0)
        self._error_value = QLabel("--.- μm")
        metrics_grid.addWidget(self._error_value, 1, 1)

        metrics_grid.addWidget(QLabel("RMS:"), 2, 0)
        self._rms_value = QLabel("--.- μm")
        metrics_grid.addWidget(self._rms_value, 2, 1)

        content_layout.addLayout(metrics_grid)

        # Error bar
        bar_row = QHBoxLayout()
        bar_row.addWidget(QLabel("Error"))
        self._error_bar = ErrorBar()
        bar_row.addWidget(self._error_bar)
        bar_row.addStretch()
        content_layout.addLayout(bar_row)

        # Lock buffer indicator
        self._buffer_bar = QProgressBar()
        self._buffer_bar.setMaximum(5)
        self._buffer_bar.setValue(0)
        self._buffer_bar.setTextVisible(True)
        self._buffer_bar.setFormat("%v/%m")
        self._buffer_bar.setFixedHeight(16)
        content_layout.addWidget(self._buffer_bar)

        # Mode selector
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["Off", "Always On", "Auto Lock"])
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self._mode_combo)
        content_layout.addLayout(mode_row)

        # Lock button
        self._lock_btn = QPushButton("Start Lock")
        self._lock_btn.setCheckable(True)
        self._lock_btn.clicked.connect(self._on_lock_clicked)
        content_layout.addWidget(self._lock_btn)

        layout.addWidget(self._content)

        # Set fixed width
        self.setFixedWidth(170)

    def _connect_events(self):
        """Subscribe to events."""
        self._event_bus.subscribe(FocusLockStatusChanged, self._on_status_changed)
        self._event_bus.subscribe(FocusLockMetricsUpdated, self._on_metrics_updated)
        self._event_bus.subscribe(FocusLockModeChanged, self._on_mode_event)
        self._event_bus.subscribe(FocusLockWarning, self._on_warning)

    def _toggle_collapse(self):
        self._is_collapsed = not self._is_collapsed
        self._content.setVisible(not self._is_collapsed)
        self._collapse_btn.setText("+" if self._is_collapsed else "−")

    def _on_mode_changed(self, index):
        modes = ["off", "always_on", "auto_lock"]
        if 0 <= index < len(modes):
            self._event_bus.publish(SetFocusLockModeCommand(mode=modes[index]))

    def _on_lock_clicked(self, checked):
        if checked:
            self._event_bus.publish(StartFocusLockCommand(target_um=0.0))
        else:
            self._event_bus.publish(StopFocusLockCommand())

    def _on_status_changed(self, event: FocusLockStatusChanged):
        # Update LED
        status_colors = {
            "locked": "green",
            "searching": "yellow",
            "lost": "red",
            "disabled": "gray",
            "paused": "yellow",
        }
        self._status_led.set_color(status_colors.get(event.status, "gray"))
        self._status_label.setText(event.status.upper())

        # Update buffer bar
        self._buffer_bar.setMaximum(event.lock_buffer_length)
        self._buffer_bar.setValue(event.lock_buffer_fill)

        # Update error bar lock state
        self._error_bar.set_locked(event.is_locked)

        # Update button state
        self._lock_btn.setChecked(event.status not in ("disabled", "off"))
        self._lock_btn.setText("Stop Lock" if event.status != "disabled" else "Start Lock")

    def _on_metrics_updated(self, event: FocusLockMetricsUpdated):
        self._z_value.setText(f"{event.z_position_um:.1f} μm")

        if event.is_good_reading:
            self._error_value.setText(f"{event.z_error_um:.2f} μm")
            self._error_bar.set_value(event.z_error_um)
        else:
            self._error_value.setText("-- μm")

        self._rms_value.setText(f"{event.z_error_rms_um:.2f} μm")

    def _on_mode_event(self, event: FocusLockModeChanged):
        mode_indices = {"off": 0, "always_on": 1, "auto_lock": 2}
        index = mode_indices.get(event.mode, 0)
        self._mode_combo.blockSignals(True)
        self._mode_combo.setCurrentIndex(index)
        self._mode_combo.blockSignals(False)

    def _on_warning(self, event: FocusLockWarning):
        # Could show tooltip or flash LED
        self._status_label.setToolTip(event.message)

    def update_af_image(self, image: np.ndarray, spot_x: float = None, spot_y: float = None):
        """Update AF camera preview."""
        self._camera_preview.update_image(image, spot_x, spot_y)
```

### 3.2 Update Widget Package

**File: `software/src/squid/ui/widgets/__init__.py`**

Add import:
```python
from squid.ui.widgets.hardware.focus_lock_status import FocusLockStatusWidget
```

---

## Phase 4: Main Window Integration

### 4.1 Add FocusLockStatusWidget to Main Window

**File: `software/src/squid/ui/main_window.py`**

Changes needed:

1. Import the widget
2. Create instance during widget creation
3. Add as docked panel to image display area
4. Connect AF camera stream to widget preview

```python
# In imports
from squid.ui.widgets.hardware.focus_lock_status import FocusLockStatusWidget

# In __init__ or widget creation
if SUPPORT_LASER_AUTOFOCUS:
    self.focusLockStatusWidget = FocusLockStatusWidget(
        event_bus=self._ui_event_bus,
        parent=self,
    )

# In layout setup - add to right side of image display area
# Create a splitter or dock arrangement that persists across tabs
```

### 4.2 Create Controller in Application Context

**File: `software/src/squid/application.py`**

Add controller creation:

```python
from squid.backend.controllers.autofocus import ContinuousFocusLockController

# In controller creation section
if self.laser_autofocus_controller and self.piezo_service:
    self.continuous_focus_lock_controller = ContinuousFocusLockController(
        laser_af_controller=self.laser_autofocus_controller,
        piezo_service=self.piezo_service,
        event_bus=self.event_bus,
    )
```

---

## Phase 5: Acquisition Integration

### 5.1 Update MultiPointWorker

**File: `software/src/squid/backend/controllers/multipoint/multi_point_worker.py`**

Modify `perform_autofocus` method:

```python
def perform_autofocus(self, region_id: str, fov: int) -> bool:
    if self.do_reflection_af:
        # Check if continuous focus lock is active
        if (self.continuous_focus_lock_controller and
            self.continuous_focus_lock_controller.mode in ("always_on", "auto_lock") and
            self.continuous_focus_lock_controller.is_running):
            # Continuous mode: wait for lock to stabilize
            return self.continuous_focus_lock_controller.wait_for_lock(timeout_s=5.0)
        else:
            # Single-shot mode: existing behavior
            return self.laser_auto_focus_controller.move_to_target(0)
    return True
```

### 5.2 Add Z-Stack Pause/Resume

In Z-stack acquisition code, add pause/resume calls:

```python
def _acquire_z_stack(self, ...):
    # Pause focus lock during Z-stack
    if self.continuous_focus_lock_controller:
        self.continuous_focus_lock_controller.pause()

    try:
        # Existing Z-stack code...
        pass
    finally:
        # Resume focus lock
        if self.continuous_focus_lock_controller:
            self.continuous_focus_lock_controller.resume()
```

---

## Phase 6: Testing

### 6.1 Unit Tests

**File: `software/tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py`**

```python
import pytest
import numpy as np
from unittest.mock import Mock, MagicMock

from squid.backend.controllers.autofocus.continuous_focus_lock import (
    ContinuousFocusLockController,
    MetricsTracker,
)
from squid.core.config.focus_lock import FocusLockConfig


class TestMetricsTracker:
    def test_rms_calculation(self):
        tracker = MetricsTracker(buffer_length=10)
        for error in [0.1, -0.1, 0.2, -0.2, 0.1]:
            metrics = tracker.update(error)
        assert metrics["z_error_rms_um"] > 0

    def test_drift_rate(self):
        tracker = MetricsTracker(buffer_length=20)
        # Simulate constant drift
        for i in range(15):
            tracker.update(i * 0.1)
        metrics = tracker.update(15 * 0.1)
        assert metrics["drift_rate_um_per_s"] > 0


class TestContinuousFocusLockController:
    @pytest.fixture
    def mock_laser_af(self):
        mock = Mock()
        mock.is_initialized = True
        mock.laser_af_properties.has_reference = True
        mock.measure_displacement.return_value = 0.1
        return mock

    @pytest.fixture
    def mock_piezo(self):
        mock = Mock()
        mock.get_position.return_value = 150.0
        mock.get_range.return_value = (0.0, 300.0)
        return mock

    @pytest.fixture
    def mock_event_bus(self):
        mock = Mock()
        mock.subscribe = Mock()
        mock.publish = Mock()
        return mock

    def test_control_fn_gain_scheduling(self, mock_laser_af, mock_piezo, mock_event_bus):
        controller = ContinuousFocusLockController(
            laser_af_controller=mock_laser_af,
            piezo_service=mock_piezo,
            event_bus=mock_event_bus,
        )

        # Small error: lower gain
        small_correction = controller._control_fn(0.1)

        # Large error: higher gain
        large_correction = controller._control_fn(2.0)

        # Ratio should reflect gain scheduling
        assert abs(large_correction / 2.0) > abs(small_correction / 0.1)

    def test_lock_buffer(self, mock_laser_af, mock_piezo, mock_event_bus):
        controller = ContinuousFocusLockController(
            laser_af_controller=mock_laser_af,
            piezo_service=mock_piezo,
            event_bus=mock_event_bus,
        )

        # Initially not locked
        assert not controller.is_locked

        # Fill buffer with good readings
        for _ in range(5):
            controller._update_lock_buffer(True)

        assert controller.is_locked

        # One bad reading breaks lock
        controller._update_lock_buffer(False)
        assert not controller.is_locked
```

### 6.2 Integration Tests

**File: `software/tests/integration/test_focus_lock_integration.py`**

Test with simulated hardware:
- Mode switching
- Lock acquisition
- Pause/resume during Z-stack
- Event publishing

---

## Implementation Order

1. **Phase 1**: Events and config (foundation)
2. **Phase 2**: Controller implementation (core logic)
3. **Phase 3**: UI widget (user interface)
4. **Phase 4**: Main window integration (wiring)
5. **Phase 5**: Acquisition integration (workflow)
6. **Phase 6**: Testing (validation)

## Files to Create

| File | Purpose |
|------|---------|
| `squid/core/config/focus_lock.py` | Configuration dataclass |
| `squid/backend/controllers/autofocus/continuous_focus_lock.py` | Main controller |
| `squid/ui/widgets/hardware/focus_lock_status.py` | Status widget |
| `tests/unit/.../test_continuous_focus_lock.py` | Unit tests |
| `tests/integration/test_focus_lock_integration.py` | Integration tests |

## Files to Modify

| File | Changes |
|------|---------|
| `squid/core/events.py` | Add focus lock events |
| `squid/backend/controllers/autofocus/__init__.py` | Export new controller |
| `squid/ui/widgets/__init__.py` | Export new widget |
| `squid/ui/main_window.py` | Add widget to layout |
| `squid/application.py` | Create controller instance |
| `squid/backend/controllers/multipoint/multi_point_worker.py` | Integration |

## Dependencies

- Existing `LaserAutofocusController`
- Existing `PiezoService`
- Existing `EventBus`
- PyQt5

## Risk Mitigation

1. **Performance**: Control loop runs in separate thread; metrics throttled
2. **Thread Safety**: Uses existing service patterns with locks
3. **Backwards Compatibility**: Mode="off" preserves existing behavior
4. **Piezo Limits**: Warnings and clamping prevent damage
