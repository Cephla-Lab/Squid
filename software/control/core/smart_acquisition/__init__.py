"""Analysis-driven acquisition: let the content of images shape the experiment.

Qt-free by design, mirroring the fluidics protocol engine. The GUI layer lives
in ``control/widgets_smart`` and the command line entry point is
``scripts/analyze_to_targets.py``.

Pipeline: a finished acquisition folder -> :mod:`source` -> an
:mod:`analyzers` plugin (pixel space) -> :mod:`geometry` (stage space) ->
:mod:`emit` -> a new acquisition.yaml + coordinates.csv.
"""

from .analyzers import available as available_analyzers, create as create_analyzer, defaults_for
from .pipeline import AnalysisResult, run_analysis, save_overlays
from .targets import FovRecord, PixelDetection, Target

__all__ = [
    "AnalysisResult",
    "FovRecord",
    "PixelDetection",
    "Target",
    "available_analyzers",
    "create_analyzer",
    "defaults_for",
    "run_analysis",
    "save_overlays",
]
