"""Qt-thread bridge for GUI side effects. Headless-safe: no-ops without a GUI."""

from typing import Optional

import squid.logging

try:
    from qtpy.QtCore import Q_ARG, QMetaObject, Qt, QTimer

    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


class GuiBridge:
    def __init__(self, gui=None):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._gui = gui

    @property
    def has_gui(self) -> bool:
        return self._gui is not None

    def _widget_for_type(self, widget_type: str):
        if self._gui is None:
            return None
        if widget_type == "wellplate":
            return getattr(self._gui, "wellplateMultiPointWidget", None)
        if widget_type == "flexible":
            return getattr(self._gui, "flexibleMultiPointWidget", None)
        return None

    def sync_yaml_to_widgets(self, yaml_data, yaml_path: str) -> None:
        """Fire-and-forget widget refresh; completion is not required before acquisition."""
        if not QT_AVAILABLE or self._gui is None:
            return
        widget = self._widget_for_type(yaml_data.widget_type)
        if widget is None or not hasattr(widget, "_load_acquisition_yaml"):
            return

        def update():
            try:
                widget._load_acquisition_yaml(yaml_path)
            except Exception as e:
                self._log.error(f"GUI YAML sync failed: {e}")

        QTimer.singleShot(0, update)

    def set_acquisition_state(self, yaml_data, running: bool) -> None:
        """MUST complete before run_acquisition() (napari layer scale race, PR #463)."""
        if not QT_AVAILABLE or self._gui is None:
            return
        widget = self._widget_for_type(yaml_data.widget_type)
        if widget is None or not hasattr(widget, "set_acquisition_running_state"):
            return
        try:
            ok = QMetaObject.invokeMethod(
                widget,
                "set_acquisition_running_state",
                Qt.BlockingQueuedConnection,
                Q_ARG(bool, running),
                Q_ARG(int, yaml_data.nz),
                Q_ARG(float, yaml_data.delta_z_um),
            )
            if not ok:
                self._log.error("invokeMethod(set_acquisition_running_state) failed")
        except Exception as e:
            self._log.error(f"GUI acquisition-state update failed: {e}")

    def get_performance_mode(self) -> Optional[bool]:
        """None when headless (no GUI attached) -- callers surface this as null."""
        if self._gui is None:
            return None
        return bool(getattr(self._gui, "performance_mode", False))

    def set_performance_mode(self, enabled: bool) -> None:
        """Mirrors the legacy TCP `_cmd_set_performance_mode`. Fire-and-forget
        (CLAUDE.md-approved pattern here: no caller needs to wait for completion) --
        schedules the toggle on the Qt main thread and returns immediately."""
        if not QT_AVAILABLE or self._gui is None:
            return
        if not hasattr(self._gui, "performanceModeToggle"):
            self._log.error("performanceModeToggle not available on GUI")
            return

        def update():
            try:
                self._gui.performanceModeToggle.setChecked(enabled)
            except Exception as e:
                self._log.error(f"GUI performance-mode toggle failed: {e}")

        QTimer.singleShot(0, update)
