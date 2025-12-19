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


class _DummyQtViewer:
    def __init__(self) -> None:
        self.layerButtons = _DummyLayerButtons()


class _DummyWindow:
    def __init__(self) -> None:
        self._qt_window = QWidget()
        self._qt_viewer = _DummyQtViewer()

    def add_dock_widget(self, *args: Any, **kwargs: Any) -> _DummyDock:
        return _DummyDock()


class _DummyDims:
    def __init__(self) -> None:
        self.axis_labels: list[str] = []


class _DummyGrid:
    def __init__(self) -> None:
        self.enabled = False


class Viewer:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.window = _DummyWindow()
        self.dims = _DummyDims()
        self.grid = _DummyGrid()
        self.layers: list[Any] = []


__all__ = ["Viewer"]
