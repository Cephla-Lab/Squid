"""Conftest for UI tests — ensures offscreen Qt before any widget imports."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
