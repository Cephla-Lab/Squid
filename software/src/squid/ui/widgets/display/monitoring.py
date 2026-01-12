"""Monitoring widgets for RAM usage and backpressure status.

These widgets are designed to be added to a status bar or toolbar
for real-time monitoring during acquisition.

Ported from upstream commits:
- c28b372b: RAM usage monitoring
- 97f85d1b: Backpressure status bar widget
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qtpy.QtCore import QTimer
from qtpy.QtWidgets import QLabel, QWidget, QHBoxLayout

if TYPE_CHECKING:
    from squid.backend.controllers.multipoint.backpressure import BackpressureStats


class RAMMonitorWidget(QWidget):
    """Widget displaying current and peak RAM usage.

    Shows: "RAM: X.XX GB | peak: X.XX GB"

    Updates automatically via a timer at the specified interval.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        update_interval_ms: int = 1000,
    ) -> None:
        """Initialize RAM monitor widget.

        Args:
            parent: Parent widget.
            update_interval_ms: How often to update the display in milliseconds.
        """
        super().__init__(parent)
        self._peak_mb: float = 0.0

        self._label = QLabel("RAM: --")
        layout = QHBoxLayout()
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(self._label)
        self.setLayout(layout)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_display)
        self._timer.start(update_interval_ms)

        # Initial update
        self._update_display()

    def _update_display(self) -> None:
        """Update the RAM display with current and peak values."""
        try:
            from squid.backend.processing.memory_profiler import get_process_memory_mb

            current_mb = get_process_memory_mb()
            if current_mb > self._peak_mb:
                self._peak_mb = current_mb

            current_gb = current_mb / 1024
            peak_gb = self._peak_mb / 1024
            self._label.setText(f"RAM: {current_gb:.2f} GB | peak: {peak_gb:.2f} GB")
        except Exception:
            self._label.setText("RAM: --")

    def reset_peak(self) -> None:
        """Reset the peak memory tracking."""
        self._peak_mb = 0.0
        self._update_display()


class BackpressureMonitorWidget(QWidget):
    """Widget displaying backpressure queue status.

    Shows: "Queue: X/Y jobs | X.X/Y.Y MB [THROTTLED]"

    The widget shows pending job count, pending MB, and a throttled indicator.
    Updates are driven externally via update_stats().
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize backpressure monitor widget."""
        super().__init__(parent)

        self._label = QLabel("Queue: --")
        layout = QHBoxLayout()
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(self._label)
        self.setLayout(layout)

        # Style for throttled state
        self._normal_style = ""
        self._throttled_style = "color: orange; font-weight: bold;"

    def update_stats(self, stats: BackpressureStats | None) -> None:
        """Update the display with new backpressure statistics.

        Args:
            stats: BackpressureStats from BackpressureController, or None to clear.
        """
        if stats is None:
            self._label.setText("Queue: disabled")
            self._label.setStyleSheet(self._normal_style)
            return

        throttled_text = " [THROTTLED]" if stats.is_throttled else ""
        self._label.setText(
            f"Queue: {stats.pending_jobs}/{stats.max_pending_jobs} jobs | "
            f"{stats.pending_bytes_mb:.1f}/{stats.max_pending_mb:.1f} MB{throttled_text}"
        )

        if stats.is_throttled:
            self._label.setStyleSheet(self._throttled_style)
        else:
            self._label.setStyleSheet(self._normal_style)

    def clear(self) -> None:
        """Clear the display."""
        self._label.setText("Queue: --")
        self._label.setStyleSheet(self._normal_style)
