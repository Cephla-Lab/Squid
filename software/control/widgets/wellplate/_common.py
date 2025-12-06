# Common imports for wellplate widgets
from __future__ import annotations


try:
    import pyqtgraph as pg
except ImportError:
    pg = None

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None  # type: ignore[misc, assignment]
    ImageDraw = None  # type: ignore[misc, assignment]
    ImageFont = None  # type: ignore[misc, assignment]
