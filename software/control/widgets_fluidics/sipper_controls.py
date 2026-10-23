"""The Sipper group of the Fluidics display tab, on a plate-sipper rig: where the tip is, and the
robot moves an operator needs around a run -- Go to well, Retract, Home, Stop.

Teaching (A1, dip depth, travel height, the priming and clean-up wells) stays in the standalone
fluidics GUI's Robot tab. Each move is a manual job on the fluidics session, through the library's
ManualOperations, so it waits for an idle rig like every other job and Stop aborts only a move
started here. Retract matters most: after a run the tip rests wet in its last well, and the
Reservoirs page refuses to reset that plate until the tip is out of it.
"""

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QComboBox, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

import squid.logging
from fluidics.qt.support import WellCombo
from fluidics.vocabulary import sipper_vocabulary

# The session's `source` for the jobs this group starts (the library labels each manual job).
SIPPER_JOB_SOURCE = "squid sipper"


class SipperControls(QGroupBox):
    _failed = Signal(str)  # a move's error, from the job thread to this one

    def __init__(self, system, parent=None):
        super().__init__("Sipper", parent)
        self._log = squid.logging.get_logger(__name__)
        self.system = system
        self.robot = system.devices.robot
        self._vocabulary = sipper_vocabulary(system.devices.config)
        self._allowed = True
        self._failed.connect(self._show_failure)

        self.where_label = QLabel("—")
        self.reservoir_combo = QComboBox()
        for name in sorted(self._vocabulary.reservoirs):
            self.reservoir_combo.addItem(name, name)
        self.reservoir_combo.setCurrentIndex(self.reservoir_combo.findData(self._vocabulary.default_reservoir))
        self.well_combo = WellCombo()
        self.reservoir_combo.currentIndexChanged.connect(self._fill_wells)
        self._fill_wells()
        self.go_button = QPushButton("Go to well")
        self.go_button.clicked.connect(self.go_to_well)
        self.retract_button = QPushButton("Retract")
        self.retract_button.clicked.connect(lambda: self._run(self.system.manual.retract, "Retract"))
        self.home_button = QPushButton("Home")
        self.home_button.clicked.connect(lambda: self._run(self.system.manual.home_robot, "Home"))
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop)

        where_row = QHBoxLayout()
        where_row.addWidget(QLabel("Tip:"))
        where_row.addWidget(self.where_label, 1)
        move_row = QHBoxLayout()
        move_row.addWidget(self.reservoir_combo)
        move_row.addWidget(self.well_combo, 1)
        for button in (self.go_button, self.retract_button, self.home_button, self.stop_button):
            move_row.addWidget(button)
        layout = QVBoxLayout()
        layout.addLayout(where_row)
        layout.addLayout(move_row)
        self.setLayout(layout)
        self.refresh()

    def set_allowed(self, allowed: bool) -> None:
        """False while a protocol run owns the rig (and is not held): the moves go dead."""
        self._allowed = bool(allowed)
        self.refresh()

    def _fill_wells(self) -> None:
        self.well_combo.fill(self._vocabulary.well_names(self.reservoir_combo.currentData()))

    def _owns_job(self) -> bool:
        session = self.system.session
        return session.kind == "manual" and session.source == SIPPER_JOB_SOURCE

    def refresh(self) -> None:
        """Paint where the tip is (cached state, no I/O) and which moves are live."""
        key = self.robot.current_well_key()
        if key is not None:
            where = f"in {key}"
        elif self.robot.is_retracted:
            where = "retracted"
        else:
            where = "not known to be retracted (Retract or Home before reloading a plate)"
        moving = self._owns_job()
        self.where_label.setText(where + (" · moving…" if moving else ""))
        free = self._allowed and not self.system.busy
        for widget in (self.reservoir_combo, self.well_combo, self.go_button, self.retract_button, self.home_button):
            widget.setEnabled(free)
        self.stop_button.setEnabled(moving)

    def reference(self) -> str:
        """The well the pickers name: the typed or picked well on the chosen plate, or as typed
        when it names its plate (ssct/A1)."""
        text = self.well_combo.value()
        return text if "/" in text else f"{self.reservoir_combo.currentData()}/{text}"

    def go_to_well(self) -> None:
        reference = self.reference()
        try:
            self._vocabulary.resolve(reference)  # a typo is refused here, before any job
        except ValueError as e:
            QMessageBox.warning(self, "Go to well", str(e))
            return
        self._run(lambda: self.system.manual.go_to_well(reference), f"Go to {reference}")

    def stop(self) -> None:
        if self._owns_job():
            self.system.abort()

    def _run(self, verb, what: str) -> None:
        try:
            self.system.run_manual(
                verb,
                callbacks={
                    "on_error": lambda message: self._failed.emit(f"{what} failed: {message}"),
                    "on_stopped": lambda: self._log.info(f"Sipper: {what} stopped"),
                    "on_finished": lambda: self._log.info(f"Sipper: {what} done"),
                },
                source=SIPPER_JOB_SOURCE,
            )
        except RuntimeError as e:  # another job holds the session
            QMessageBox.warning(self, "Sipper", f"{what} was not started: {e}")
            return
        self._log.info(f"Sipper: {what}")
        self.refresh()

    def _show_failure(self, message: str) -> None:
        self._log.error(message)
        QMessageBox.critical(self, "Sipper", message)
