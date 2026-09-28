"""Settings > Advanced > Objectives...: edit machine_configs/objectives.yaml (spec A §5)."""

import os
from typing import Callable, List, Optional, Set

from qtpy.QtCore import Qt
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

import control._def
import control.objectives_config as oc
import squid.logging

_COLUMNS = ["Name", "Magnification", "NA", "Tube lens (mm)", "Slot", "Model", "Serial", "Copy settings from"]
_COL_NAME, _COL_MAG, _COL_NA, _COL_TUBE, _COL_SLOT, _COL_MODEL, _COL_SERIAL, _COL_COPY = range(len(_COLUMNS))
_CONFLICT = QColor(255, 210, 210)


def _float_or_none(text: str) -> Optional[float]:
    try:
        return float(text)
    except ValueError:
        return None


class ObjectivesEditorDialog(QDialog):
    def __init__(
        self,
        config_repo,
        *,
        use_xeryon: bool,
        use_turret: bool,
        catalog,
        turret_positions,
        xeryon_pos_1,
        xeryon_pos_2,
        on_restart: Optional[Callable[[], None]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._repo = config_repo
        self._use_xeryon = use_xeryon
        self._use_turret = use_turret
        self._kind = oc.changer_kind_for_flags(use_xeryon, use_turret)
        self._catalog = catalog
        self._on_restart = on_restart
        self._copied = set()  # (source, target) pairs already copied by an earlier Save
        self.setWindowTitle("Objectives")
        self.setMinimumSize(900, 360)
        self._build_ui()
        existing = config_repo.get_objectives_config()
        if existing is not None:
            rows = oc.config_to_rows(existing)
        else:
            rows = oc.seed_rows(
                self._kind,
                catalog=catalog,
                turret_positions=turret_positions,
                xeryon_pos_1=xeryon_pos_1,
                xeryon_pos_2=xeryon_pos_2,
            )
        for row in rows:
            self.add_row(row)

    @classmethod
    def for_current_machine(cls, config_repo, on_restart=None, parent=None):
        catalog = control._def.read_objectives_csv(os.path.join("objective_and_sample_formats", "objectives.csv"))
        return cls(
            config_repo,
            use_xeryon=control._def.USE_XERYON,
            use_turret=control._def.USE_OBJECTIVE_TURRET,
            catalog=catalog,
            # Passed straight through (no dict()/list() wrapping): the shipped Xeryon ini loads
            # these as Python-literal strings (e.g. "['10x', '20x']"), and seed_rows() handles
            # both the string and the real dict/list forms. list()/dict() on a string would
            # corrupt it (list() splits into characters; dict() raises).
            turret_positions=control._def.OBJECTIVE_TURRET_POSITIONS,
            xeryon_pos_1=control._def.XERYON_OBJECTIVE_SWITCHER_POS_1,
            xeryon_pos_2=control._def.XERYON_OBJECTIVE_SWITCHER_POS_2,
            on_restart=on_restart,
            parent=parent,
        )

    # --- UI ---

    def _build_ui(self):
        layout = QVBoxLayout(self)
        note = QLabel(
            "The objectives mounted on this microscope. Changes take effect after a restart. "
            "Changing an objective's optics, serial or slot invalidates calibrations measured with the "
            "old values (Objective Calibration shows which). Removing an objective keeps its files."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setColumnHidden(_COL_SLOT, self._kind is oc.ChangerKind.NONE)
        layout.addWidget(self._table)
        buttons = QHBoxLayout()
        for text, slot in (
            ("Add from catalog...", self._add_from_catalog),
            ("Add custom", self._add_custom),
            ("Remove", self._remove_selected),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch()
        save = QPushButton("Save")
        save.clicked.connect(self.save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(save)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def _slot_combo(self, slot: Optional[int]) -> QComboBox:
        combo = QComboBox()
        for s in range(1, oc.SLOT_COUNTS[self._kind] + 1):
            combo.addItem(str(s), s)
        if slot is not None and combo.findData(slot) >= 0:
            combo.setCurrentIndex(combo.findData(slot))
        combo.currentIndexChanged.connect(self._refresh_highlight)
        return combo

    def add_row(self, row: oc.EditorRow) -> None:
        r = self._table.rowCount()
        self._table.insertRow(r)
        texts = {
            _COL_NAME: row.name,
            _COL_MAG: "" if row.magnification is None else f"{row.magnification:g}",
            _COL_NA: "" if row.na is None else f"{row.na:g}",
            _COL_TUBE: "" if row.tube_lens_f_mm is None else f"{row.tube_lens_f_mm:g}",
            _COL_MODEL: row.model,
            _COL_SERIAL: row.serial,
        }
        for col, text in texts.items():
            self._table.setItem(r, col, QTableWidgetItem(text))
        if self._kind is not oc.ChangerKind.NONE:
            self._table.setCellWidget(r, _COL_SLOT, self._slot_combo(row.slot))
        copy_item = QTableWidgetItem(row.copy_from or "")
        copy_item.setFlags(copy_item.flags() & ~Qt.ItemIsEditable)
        self._table.setItem(r, _COL_COPY, copy_item)
        self._refresh_highlight()

    def remove_row(self, index: int) -> None:
        self._table.removeRow(index)
        self._refresh_highlight()

    def rows(self) -> List[oc.EditorRow]:
        rows = []
        for r in range(self._table.rowCount()):
            combo = self._table.cellWidget(r, _COL_SLOT)
            rows.append(
                oc.EditorRow(
                    name=self._table.item(r, _COL_NAME).text(),
                    magnification=_float_or_none(self._table.item(r, _COL_MAG).text()),
                    na=_float_or_none(self._table.item(r, _COL_NA).text()),
                    tube_lens_f_mm=_float_or_none(self._table.item(r, _COL_TUBE).text()),
                    slot=combo.currentData() if combo is not None else None,
                    model=self._table.item(r, _COL_MODEL).text(),
                    serial=self._table.item(r, _COL_SERIAL).text(),
                    copy_from=self._table.item(r, _COL_COPY).text() or None,
                )
            )
        return rows

    def highlighted_rows(self) -> Set[int]:
        return oc.slot_conflicts(self.rows())

    def _refresh_highlight(self, *_):
        conflicts = self.highlighted_rows()
        for r in range(self._table.rowCount()):
            item = self._table.item(r, _COL_NAME)
            if item is not None:
                item.setBackground(_CONFLICT if r in conflicts else QColor(0, 0, 0, 0))

    # --- actions ---

    def _new_row(self, name, magnification, na, tube_lens_f_mm) -> oc.EditorRow:
        rows = self.rows()
        used = {r.slot for r in rows}
        free = [s for s in range(1, oc.SLOT_COUNTS[self._kind] + 1) if s not in used]
        return oc.EditorRow(
            name,
            magnification,
            na,
            tube_lens_f_mm,
            free[0] if free else None,
            copy_from=oc.nearest_by_magnification(magnification, rows),
        )

    def _add_from_catalog(self):
        name, ok = QInputDialog.getItem(self, "Add from catalog", "Objective:", list(self._catalog), 0, False)
        if ok:
            optics = self._catalog[name]
            self.add_row(self._new_row(name, optics["magnification"], optics["NA"], optics["tube_lens_f_mm"]))

    def _add_custom(self):
        name, ok = QInputDialog.getText(self, "Add custom objective", "Name:")
        if ok:
            self.add_row(self._new_row(name, None, None, None))

    def _remove_selected(self):
        for index in sorted({i.row() for i in self._table.selectedIndexes()}, reverse=True):
            self.remove_row(index)

    def save(self) -> bool:
        rows = self.rows()
        try:
            config = oc.rows_to_config(self._kind, rows)
            oc.validate_objectives_config(config, use_xeryon=self._use_xeryon, use_turret=self._use_turret)
        except oc.ObjectivesConfigError as e:
            QMessageBox.warning(self, "Objectives", f"{e.field}: {e.reason}")
            return False
        self._repo.save_objectives_config(config)
        for row in rows:
            if row.copy_from and (row.copy_from, row.name) not in self._copied:
                profiles = self._repo.copy_objective_channel_configs(row.copy_from, row.name)
                self._copied.add((row.copy_from, row.name))
                self._log.info(f"copied channel settings {row.copy_from} -> {row.name} in profiles {profiles}")
        answer = QMessageBox.question(
            self,
            "Restart to apply",
            "Saved. The new objective list takes effect after a restart. Restart now?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer == QMessageBox.Yes and self._on_restart is not None:
            self._on_restart()
        return True
