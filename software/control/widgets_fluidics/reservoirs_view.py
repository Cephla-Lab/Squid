"""The Reservoirs page of the Fluidics display tab: the Squid-Fluidics library's Reservoirs window,
embedded as a page instead of its floating tool window.

It is volume tracking's one view: per reservoir (each bottle, each well of each plate) what it has
given since its last reset, what is left and what was loaded, with the resets (Reset selected,
Reset <plate>, Reset all) and the loaded-volume settings (Set selected…, Set <plate>…, a double
click on a Loaded cell). Each reset or setting is a manual job on the fluidics session, so the
library already greys its buttons while any job runs; Squid adds one rule on top
(set_edits_allowed): no edits while a protocol run owns the rig, except while it is held.
"""

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QPushButton

from fluidics.qt.reservoirs_window import ReservoirsWindow

# The session's `source` for the jobs this page starts (the library labels each manual job).
RESERVOIRS_JOB_SOURCE = "squid reservoirs"


class ReservoirsView(ReservoirsWindow):
    def __init__(self, config, system, parent=None):
        self._edits_allowed = True  # read by the paint the library's __init__ may already trigger
        super().__init__(config, system, RESERVOIRS_JOB_SOURCE, parent)
        self.setWindowFlags(Qt.Widget)  # a page in a tab, not the library's floating tool window

    def set_edits_allowed(self, allowed: bool) -> None:
        self._edits_allowed = bool(allowed)
        self._paintButtons()

    # The library repaints its buttons from the session on every tick, selection change and job
    # boundary through this one method, so the rule goes here; the table itself stays live.
    def _paintButtons(self):
        super()._paintButtons()
        if not self._edits_allowed:
            for button in self.findChildren(QPushButton):
                button.setEnabled(False)

    def _onCellDoubleClicked(self, row, column):
        if self._edits_allowed:
            super()._onCellDoubleClicked(row, column)
