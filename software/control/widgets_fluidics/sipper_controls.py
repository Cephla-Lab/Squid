"""The Sipper group of the Fluidics display tab, on a plate-sipper rig: where the tip is, the robot
moves an operator needs around a run (Go to well, Retract, Home, Reset fault, Stop), and the priming
and clean-up wells. Teaching (A1, dip depth, travel height) stays in the standalone fluidics GUI's
Robot tab.

Each move is a manual job on the fluidics session, started through the display tab's job starter, so
it waits for an idle rig like every other job, and Stop aborts only a move started here. Retract
matters most: after a run the tip rests wet in its last well, and the Reservoirs page refuses to
reset that plate until the tip is out of it.

"Do not lower tip" is the library's switch, but here it shapes only this group's Go to well: the
library refuses every run and sipper draw while the switch is on, so it is set for the one move and
put back after.

A dedicated well is set through the library's set_dedicated_well, then that one field is written to
the config file read fresh, not the live config saved whole: that would also write back whatever the
file gained since Initialize (a hand edit, a volume the library wrote to the file only).
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

from control.widgets_fluidics.system_panel import NOT_RETRACTED, tip_location
from fluidics.control.config import gantry, load_config, save_config
from fluidics.manual_operations import DEDICATED_WELLS, dedicated_wells
from fluidics.qt.robot_control import ROBOT_JOB_SOURCE, is_robot_job
from fluidics.qt.support import WellCombo
from fluidics.robot.labware import well_key
from fluidics.vocabulary import sipper_vocabulary

# The session's `source` for the jobs this group starts: the library's Robot tab label, so its
# is_robot_job rule reads them (the two never share a rig).
SIPPER_JOB_SOURCE = ROBOT_JOB_SOURCE

# What each dedicated well is for: its button's tooltip and the confirmation before it changes.
_DEDICATED_USE = {
    "priming": "Prime and priming rows fill the sipper's line from it, unless a row names its own",
    "clean-up": "Clean and clean-up rows fill the sipper's line from it, unless a row names its own",
}


def dedicated_well_key(config, role: str) -> str:
    """The priming or clean-up well as the library reads `config` (reservoir/well)."""
    return well_key(dedicated_wells(config)[role])


class SipperControls(QGroupBox):
    _failed = Signal(str)  # a job's error, from the job thread to this one, for the dialog

    def __init__(self, system, start_job, parent=None):
        """`start_job`: FluidicsDisplayTab.start_manual_job, which every Squid manual job goes through."""
        super().__init__("Sipper", parent)
        self.system = system
        self.robot = system.devices.robot
        self._start_job = start_job
        self._vocabulary = sipper_vocabulary(system.devices.config)
        self._allowed = True
        self._failed.connect(lambda message: QMessageBox.critical(self, "Sipper", message))

        self.where_label = QLabel("—")
        self.lower_tip_box = QCheckBox("Do not lower tip")
        self.lower_tip_box.setChecked(True)
        self.lower_tip_box.setToolTip(
            "Go to well travels over the well and stops retracted: check a taught position without "
            "dipping. Applies to this Go to well only; runs and Prime/Clean always lower the tip."
        )
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
        self.home_button.clicked.connect(lambda: self._run(self.system.manual.home_robot, "Home", clears_fault=True))
        self.reset_fault_button = QPushButton("Reset fault")
        self.reset_fault_button.setToolTip(
            "Clear a drive fault and enable the drives again. Home afterwards if the fault stopped a "
            "homing. A fault that persists stops the next move again."
        )
        self.reset_fault_button.clicked.connect(
            lambda: self._run(self.system.manual.reset_robot_faults, "Reset fault", clears_fault=True)
        )
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop)

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
        self.dedicated_labels = {}
        self.dedicated_buttons = {}
        for role in DEDICATED_WELLS:
            label = self.dedicated_labels[role] = QLabel("—")
            button = self.dedicated_buttons[role] = QPushButton(f"Set as {role} well")
            button.setToolTip(
                f"Make the well picked above the {role} well. {_DEDICATED_USE[role]}. Saved to the fluidics config file."
            )
            button.clicked.connect(lambda _checked=False, role=role: self.set_dedicated_well(role))
            dedicated_row.addWidget(QLabel(f"{role.capitalize()} well:"))
            dedicated_row.addWidget(label)
            dedicated_row.addWidget(button)
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

    def dedicated_well(self, role: str) -> str:
        """The priming or clean-up well as the library reads the config (reservoir/well)."""
        return dedicated_well_key(self.system.devices.config, role)

    def refresh(self) -> None:
        """Paint where the tip is and the dedicated wells (cached state, no I/O), and which moves
        are live."""
        where = tip_location(self.robot)
        if where == NOT_RETRACTED:
            where += " (Retract or Home before reloading a plate)"
        moving = is_robot_job(self.system.session)
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

    def _picked(self, title: str):
        """The picked well as reservoir/well, or None with the operator told why: a typo is
        refused here, before any job."""
        try:
            return well_key(self._vocabulary.resolve(self.reference()))
        except ValueError as e:
            QMessageBox.warning(self, title, str(e))
            return None

    def go_to_well(self) -> None:
        key = self._picked("Go to well")
        if key is None:
            return
        lower = not self.lower_tip_box.isChecked()
        self._run(lambda: self._go_to_well(key, lower), f"Go to {key}" + ("" if lower else " (tip not lowered)"))

    def _go_to_well(self, key: str, lower: bool) -> None:
        """On the job thread; the switch is put back even when the move fails or is stopped."""
        manual = self.system.manual
        was = self.robot.lower_tip
        manual.set_lower_tip(lower)
        try:
            manual.go_to_well(key)
        finally:
            manual.set_lower_tip(was)

    def set_dedicated_well(self, role: str) -> None:
        title = f"{role.capitalize()} well"
        key = self._picked(title)
        if key is None or key == self.dedicated_well(role):
            return
        answer = QMessageBox.question(
            self,
            title,
            f"Make {key} the {role} well (now {self.dedicated_well(role)})?\n\n{_DEDICATED_USE[role]}. "
            f"Saved to the fluidics config file:\n{self.system.devices.config.source_path}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._run(lambda: self._set_and_save(role, key), f"Set {role} well to {key}")

    def _set_and_save(self, role: str, key: str) -> None:
        """On the job thread: the live config first (a refusal changes nothing), then the file."""
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
        if is_robot_job(self.system.session):
            self.system.abort()

    def _run(self, verb, what: str, clears_fault: bool = False) -> None:
        refusal = self._start_job(
            verb,
            f"Sipper: {what}",
            source=SIPPER_JOB_SOURCE,
            on_error=lambda message: self._failed.emit(f"{what} failed: {message}"),
            clears_fault=clears_fault,
        )
        if refusal is not None:
            QMessageBox.warning(self, "Sipper", f"{what} was not started: {refusal}")
            return
        self.refresh()
