"""The hardware control panel (joystick and focus wheel) is locked while one reason or more is held.

Several features lock the panel: the loading position, a homing run, an agent session. Each adds its reason when it
starts and removes it when it ends. The panel unlocks only when no reason is left, so the end of one feature does
not unlock the panel under another.
"""

from typing import Callable, FrozenSet, Set

PANEL_LOCK_LOADING = "loading position"
PANEL_LOCK_HOMING = "stage homing"
PANEL_LOCK_AGENT = "agent session"


class PanelLockReasons:
    def __init__(self, apply: Callable[[bool], None]) -> None:
        """apply(True) locks the panel, apply(False) unlocks it."""
        self._apply = apply
        self._reasons: Set[str] = set()

    def add(self, reason: str) -> None:
        was_free = not self._reasons
        self._reasons.add(reason)
        if was_free:
            self._apply(True)

    def remove(self, reason: str) -> None:
        if reason not in self._reasons:
            return
        self._reasons.discard(reason)
        if not self._reasons:
            self._apply(False)

    def held(self) -> FrozenSet[str]:
        return frozenset(self._reasons)
