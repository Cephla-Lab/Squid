"""The Sipper group of the Fluidics display tab, on a plate-sipper rig: where the tip is, the robot
moves an operator needs around a run -- Go to well, Retract, Home, Reset fault, Stop -- and the
priming and clean-up wells.

Teaching (A1, dip depth, travel height) stays in the standalone fluidics GUI's Robot tab. Each move
is a manual job on the fluidics session, through the library's ManualOperations, so it waits for an
idle rig like every other job and Stop aborts only a move started here. Retract matters most: after
a run the tip rests wet in its last well, and the Reservoirs page refuses to reset that plate until
the tip is out of it. Reset fault clears a drive fault and re-enables the drives (Home afterwards
if the fault stopped a homing); a fault that persists stops the next move again.

"Do not lower tip" (on when the group is built) makes Go to well stop retracted over the well, so a
taught position can be checked without dipping. It is the library's switch, but here it shapes only
this group's Go to well: the library refuses every run and sipper draw while the switch is on, so it is
set for the one move and put back after. Runs and Prime/Clean always lower the tip.

The priming and clean-up wells are where Prime / Clean and priming / clean-up rows fill the sipper's
line, unless a row names its own. "Set as priming well" makes the well in the picker the priming
well through the library's set_dedicated_well (which refuses a well that holds less than the line
takes), then writes that one field to the fluidics config file, read fresh, so the standalone GUI
reads it too. Not the live config saved whole: that would also write back whatever the file gained
since Initialize (a hand edit, a volume the library wrote to the file but not to the live config).
"""

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

import squid.logging
from fluidics.control.config import gantry, load_config, save_config
from fluidics.manual_operations import DEDICATED_WELLS
from fluidics.qt.support import WellCombo
from fluidics.robot.labware import well_key
from fluidics.vocabulary import sipper_vocabulary

# The session's `source` for the jobs this group starts (the library labels each manual job).
SIPPER_JOB_SOURCE = "squid sipper"

# What each dedicated well is for, in the confirmation before it changes.
_DEDICATED_USE = {
    "priming": "Prime and priming rows fill the sipper's line from it",
    "clean-up": "Clean and clean-up rows fill the sipper's line from it",
}


class SipperControls(QGroupBox):
    _failed = Signal(str)  # a job's error, from the job thread to this one, for the dialog
    robot_failed = Signal(str)  # the library's error text of a failed job (Device status reads faults from it)
    robot_recovered = Signal()  # Reset fault or Home finished without an error

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
        self.home_button.clicked.connect(lambda: self._run(self.system.manual.home_robot, "Home", recovers=True))
        self.reset_fault_button = QPushButton("Reset fault")
        self.reset_fault_button.setToolTip(
            "Clear a drive fault and enable the drives again. Home afterwards if the fault stopped a "
            "homing. A fault that persists stops the next move again."
        )
        self.reset_fault_button.clicked.connect(
            lambda: self._run(self.system.manual.reset_robot_faults, "Reset fault", recovers=True)
        )
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop)
        self.lower_tip_box = QCheckBox("Do not lower tip")
        self.lower_tip_box.setChecked(True)
        self.lower_tip_box.setToolTip(
            "Go to well travels over the well and stops retracted: check a taught position without "
            "dipping. Applies to this Go to well only; runs and Prime/Clean always lower the tip."
        )

        self.dedicated_labels = {}
        self.dedicated_buttons = {}
        for role in DEDICATED_WELLS:
            self.dedicated_labels[role] = QLabel("—")
            button = QPushButton(f"Set as {role} well")
            button.setToolTip(
                f"Make the well picked above the {role} well. {_DEDICATED_USE[role]}, unless a row "
                "names its own. Saved to the fluidics config file."
            )
            button.clicked.connect(lambda _checked=False, role=role: self.set_dedicated_well(role))
            self.dedicated_buttons[role] = button

        where_row = QHBoxLayout()
        where_row.addWidget(QLabel("Tip:"))
        where_row.addWidget(self.where_label, 1)
        where_row.addWidget(self.lower_tip_box)
        move_row = QHBoxLayout()
        move_row.addWidget(self.reservoir_combo)
        move_row.addWidget(self.well_combo, 1)
        for button in (self.go_button, self.retract_button, self.home_button, self.reset_fault_button):
            move_row.addWidget(button)
        move_row.addWidget(self.stop_button)
        dedicated_row = QHBoxLayout()
        for role in DEDICATED_WELLS:
            dedicated_row.addWidget(QLabel(f"{role.capitalize()} well:"))
            dedicated_row.addWidget(self.dedicated_labels[role])
            dedicated_row.addWidget(self.dedicated_buttons[role])
            dedicated_row.addSpacing(12)
        dedicated_row.addStretch(1)
        layout = QVBoxLayout()
        layout.addLayout(where_row)
        layout.addLayout(move_row)
        layout.addLayout(dedicated_row)
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

    def dedicated_well(self, role: str) -> str:
        """The config's priming or clean-up well as it stands (reservoir/well)."""
        return getattr(gantry(self.system.devices.config), DEDICATED_WELLS[role])

    def refresh(self) -> None:
        """Paint where the tip is and the dedicated wells (cached state, no I/O), and which moves
        are live."""
        key = self.robot.current_well_key()
        if key is not None:
            where = f"in {key}"
        elif self.robot.is_retracted:
            where = "retracted"
        else:
            where = "not known to be retracted (Retract or Home before reloading a plate)"
        moving = self._owns_job()
        self.where_label.setText(where + (" · moving…" if moving else ""))
        for role, label in self.dedicated_labels.items():
            label.setText(self.dedicated_well(role))
        free = self._allowed and not self.system.busy
        widgets = [self.reservoir_combo, self.well_combo, self.go_button, self.retract_button, self.home_button]
        widgets += [self.reset_fault_button, *self.dedicated_buttons.values()]
        for widget in widgets:
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
        lower = not self.lower_tip_box.isChecked()
        what = f"Go to {reference}" + ("" if lower else " (tip not lowered)")
        self._run(lambda: self._go_to_well(reference, lower), what)

    def _go_to_well(self, reference: str, lower: bool) -> None:
        """The move, on the job thread. The robot's switch is read by every run and draw, so it is
        set for this move only and put back after, even when the move fails or is stopped."""
        manual = self.system.manual
        was = self.robot.lower_tip
        manual.set_lower_tip(lower)
        try:
            manual.go_to_well(reference)
        finally:
            manual.set_lower_tip(was)

    def set_dedicated_well(self, role: str) -> None:
        """Make the picked well the priming or the clean-up well, once the operator confirms; the
        library refuses a well that holds less than the sipper's line takes."""
        title = f"{role.capitalize()} well"
        try:
            key = well_key(self._vocabulary.resolve(self.reference()))  # a typo is refused before any job
        except ValueError as e:
            QMessageBox.warning(self, title, str(e))
            return
        if key == self.dedicated_well(role):
            return
        path = self.system.devices.config.source_path
        answer = QMessageBox.question(
            self,
            title,
            f"Make {key} the {role} well (now {self.dedicated_well(role)})?\n\n"
            f"{_DEDICATED_USE[role]}, unless a row names its own. Saved to the fluidics config file:\n{path}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._run(lambda: self._set_and_save(role, key), f"Set {role} well to {key}")

    def _set_and_save(self, role: str, key: str) -> None:
        """On the job thread: the live config first (refused, nothing changes), then that one field
        of the file, read fresh -- the library's save_loaded_volumes pattern."""
        self.system.manual.set_dedicated_well(role, key)
        try:
            fresh = load_config(self.system.devices.config.source_path)
            setattr(gantry(fresh), DEDICATED_WELLS[role], key)
            save_config(fresh)
        except Exception as e:
            raise RuntimeError(
                f"{key} is the {role} well for this session, but saving the config file failed: {e}"
            ) from e

    def stop(self) -> None:
        if self._owns_job():
            self.system.abort()

    def _run(self, verb, what: str, recovers: bool = False) -> None:
        """Start `verb` as a manual job. `recovers`: a clean finish says the drives are healthy
        again (Reset fault, Home)."""
        ended = []  # how it ended early, if it did: the callbacks run on the job thread, in order

        def on_error(message):
            ended.append("failed")
            self.robot_failed.emit(message)
            self._failed.emit(f"{what} failed: {message}")

        def on_stopped():
            ended.append("stopped")
            self._log.info(f"Sipper: {what} stopped")

        def on_finished():
            if ended:
                return
            self._log.info(f"Sipper: {what} done")
            if recovers:
                self.robot_recovered.emit()

        try:
            self.system.run_manual(
                verb,
                callbacks={"on_error": on_error, "on_stopped": on_stopped, "on_finished": on_finished},
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
