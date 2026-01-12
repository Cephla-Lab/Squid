# Stage widgets package
from squid.ui.widgets.stage.utils import StageUtils
from squid.ui.widgets.stage.piezo import PiezoWidget
from squid.ui.widgets.stage.navigation import NavigationWidget
from squid.ui.widgets.stage.autofocus import AutoFocusWidget

__all__ = [
    "StageUtils",
    "PiezoWidget",
    "NavigationWidget",
    "AutoFocusWidget",
    "AlignmentWidget",
]


def __getattr__(name: str):
    """Lazy import AlignmentWidget to avoid napari import at module load."""
    if name == "AlignmentWidget":
        from squid.ui.widgets.stage.alignment_widget import AlignmentWidget as _AlignmentWidget

        return _AlignmentWidget
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
