from __future__ import annotations

from typing import Any

try:
    from qtpy.QtWidgets import QWidget
except Exception:  # pragma: no cover - Qt not available in some environments
    QWidget = object  # type: ignore[assignment]


class _DummyDock:
    def setFeatures(self, *args: Any, **kwargs: Any) -> None:
        pass

    def setTitleBarWidget(self, *args: Any, **kwargs: Any) -> None:
        pass


class _DummyLayerButtons:
    def hide(self) -> None:
        pass


class _DummyCanvasNative:
    """Mock canvas native widget with wheelEvent support."""

    def wheelEvent(self, event: Any) -> None:
        pass


class _DummyCanvas:
    """Mock canvas with native widget."""

    def __init__(self) -> None:
        self.native = _DummyCanvasNative()


class _DummyQtViewer:
    def __init__(self) -> None:
        self.layerButtons = _DummyLayerButtons()
        self.canvas = _DummyCanvas()


class _DummyWindow:
    def __init__(self) -> None:
        self._qt_window = QWidget()
        self._qt_viewer = _DummyQtViewer()
        self.main_menu = _DummyMenu()

    def add_dock_widget(self, *args: Any, **kwargs: Any) -> _DummyDock:
        return _DummyDock()


class _DummyMenu:
    def clear(self) -> None:
        pass


class _DummyDims:
    def __init__(self) -> None:
        self.axis_labels: list[str] = []


class _DummyGrid:
    def __init__(self) -> None:
        self.enabled = False


class _DummyEvent:
    """Mock event that supports connect/disconnect for callbacks."""

    def __init__(self) -> None:
        self._handlers: list[Any] = []

    def connect(self, handler: Any) -> None:
        self._handlers.append(handler)

    def disconnect(self, handler: Any) -> None:
        if handler in self._handlers:
            self._handlers.remove(handler)


class _DummyCameraEvents:
    """Mock camera events with zoom event."""

    def __init__(self) -> None:
        self.zoom = _DummyEvent()


class _DummyCamera:
    """Mock camera with zoom and center properties."""

    def __init__(self) -> None:
        self.events = _DummyCameraEvents()
        self.zoom = 1.0
        self.center = (0.0, 0.0)


class Viewer:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.window = _DummyWindow()
        self.dims = _DummyDims()
        self.grid = _DummyGrid()
        self.camera = _DummyCamera()
        self.layers: list[Any] = []

    def bind_key(self, key: str, callback: Any) -> None:
        """Mock key binding - does nothing in test mode."""
        pass


__all__ = ["Viewer"]
