"""
Minimal Dock/DockArea stubs for tests.
"""

from typing import Any


class Dock:
    def __init__(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - stub
        pass

    def showTitleBar(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        pass

    def addWidget(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        pass

    def setStretch(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        pass

    def setFixedWidth(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        pass


class DockArea:
    def addDock(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - stub
        pass
