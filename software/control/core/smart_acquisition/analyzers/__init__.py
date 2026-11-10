"""Analysis plugins. Importing this package registers the built-in analyzers."""

from .base import Analyzer, BaseAnalyzer, available, create, defaults_for, primary_params_for, register

# Import for side effect: each module registers itself.
from . import focus_score, ring_grid  # noqa: F401,E402

__all__ = [
    "Analyzer",
    "BaseAnalyzer",
    "available",
    "create",
    "defaults_for",
    "primary_params_for",
    "register",
]
