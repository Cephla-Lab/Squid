"""Qt-thread bridge for GUI side effects. Headless-safe: no-ops without a GUI."""

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
