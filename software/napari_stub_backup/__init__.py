"""
Lightweight stub of napari used for headless testing.

Only provides the minimal surface required for imports in GUI modules.
"""

from typing import Any


class _Dims:
    def __init__(self):
        self.axis_labels = []


class _Window:
    def __init__(self):
        self._qt_window = object()

    def add_dock_widget(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        return object()


class Viewer:
    def __init__(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - stub
        self.window = _Window()
        self.dims = _Dims()


from .layers import Layer  # noqa: E402,F401
from . import layers  # noqa: E402,F401
from . import utils  # noqa: E402,F401

__all__ = ["Viewer", "Layer", "layers", "utils"]
