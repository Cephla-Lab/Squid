"""
Fallback shim for pyqtgraph.

If a real pyqtgraph installation is available outside this repository, load and
re-export it. Otherwise provide a minimal stub so imports succeed in headless
tests.
"""

from pathlib import Path
from types import ModuleType
from typing import Any, Optional
import importlib.machinery
import importlib.util
import sys


def _load_real_pyqtgraph() -> Optional[ModuleType]:
    """Attempt to load pyqtgraph from outside this repository."""
    current_dir = Path(__file__).resolve().parent
    search_paths = [
        str(Path(p or ".").resolve())
        for p in sys.path
        if Path(p or ".").resolve() not in {current_dir, current_dir.parent}
    ]
    spec = importlib.machinery.PathFinder.find_spec(__name__, search_paths)
    if spec is None or spec.origin is None:
        return None

    resolved_origin = Path(spec.origin).resolve()
    if resolved_origin == Path(__file__).resolve():
        return None

    loader = spec.loader
    if loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[__name__] = module
    loader.exec_module(module)  # type: ignore[arg-type]
    return module


_real_pyqtgraph = _load_real_pyqtgraph()

if _real_pyqtgraph:
    globals().update(_real_pyqtgraph.__dict__)
    __all__ = getattr(_real_pyqtgraph, "__all__", [])
else:

    def setConfigOptions(*args: Any, **kwargs: Any) -> None:  # pragma: no cover - stub
        """Accept configuration options in headless mode."""
        pass

    class _Signal:
        def connect(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - stub
            pass

    class _Region:
        def __init__(self):
            self.sigRegionChanged = _Signal()
            self.sigRegionChangeFinished = _Signal()

        def setRegion(
            self, *args: Any, **kwargs: Any
        ) -> None:  # pragma: no cover - stub
            pass

    class _Gradient:
        def setColorMap(
            self, *args: Any, **kwargs: Any
        ) -> None:  # pragma: no cover - stub
            pass

    class ImageItem:
        def setImage(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - stub
            pass

    class ColorMap:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - stub
            pass

    class HistogramLUTWidget:
        def __init__(self, image: Any = None):
            self.image = image
            self.region = _Region()
            self.gradient = _Gradient()

        def setFixedWidth(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
            pass

        def setLevels(
            self, *args: Any, **kwargs: Any
        ) -> None:  # pragma: no cover - stub
            pass

        def setHistogramRange(
            self, *args: Any, **kwargs: Any
        ) -> None:  # pragma: no cover - stub
            pass

    class PlotItem:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
            pass

        def plot(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - stub
            pass

        def addLegend(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
            pass

    class DateAxisItem:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
            pass

    def mkPen(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - stub
        return object()

    class GraphicsLayoutWidget:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
            pass

        def addPlot(self, *args: Any, **kwargs: Any) -> PlotItem:  # pragma: no cover
            return PlotItem()

    from .dockarea import Dock, DockArea  # noqa: E402

    __all__ = ["Dock", "DockArea", "ImageItem", "HistogramLUTWidget", "ColorMap"]
