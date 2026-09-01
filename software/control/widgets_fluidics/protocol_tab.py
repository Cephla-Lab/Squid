"""The Protocol editor: a round-grouped tree over a ProtocolFile, with imaging-source assignment
(Apply current settings / Capture current coordinates / From file…), folder rules, Add rounds and
live validation. Built on the fluidics library's own logic helpers (get_fields_for_type,
SEQUENCE_TYPE_LABELS, sequence_type_problem, sequence_port_problems, SequenceListAdapter) — the
widget skin is Squid's, the sequence semantics are the library's."""

import datetime
import os
import time
from typing import Callable, Dict, List, Optional, Tuple

from qtpy.QtCore import QSignalBlocker, Qt, QTimer, Signal
from qtpy.QtGui import QBrush, QColor
from qtpy.QtWidgets import (
    QDialog,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import squid.logging
from control.models.fluidics_protocol import (
    DEFAULT_FOLDER_PATTERN,
    IMAGING_TYPE,
    CoordinatesBlock,
    ImagingRow,
    ProtocolFile,
    SettingsBlock,
    expand_rounds,
    folder_problems,
    load_protocol,
    render_folder,
    save_protocol,
)
from control.widgets_fluidics import state
from control.widgets_fluidics.dialogs import AddRoundsDialog, pick_coordinates_source, pick_settings_source

try:  # the editor degrades gracefully when the fluidics library is not installed
    from fluidics.sequences import (
        SEQUENCE_TYPE_LABELS,
        SequenceListAdapter,
        get_fields_for_type,
        sequence_port_problems,
        sequence_type_problem,
    )
except ImportError:
    SEQUENCE_TYPE_LABELS = SequenceListAdapter = None
    get_fields_for_type = sequence_port_problems = sequence_type_problem = None

_SCOPE_ALL = "all imaging rows"
_SCOPE_SELECTED = "selected rows"
_STRING_FIELDS = ("name", "round", "folder", "settings", "coordinates")
_HIGHLIGHT = QColor(255, 244, 180)
_INVALID = QColor(255, 205, 205)


def _port_limit(service) -> Optional[int]:
    if service is None or not getattr(service, "initialized", False):
        return None
    from fluidics.control.config import available_port_count  # importable once the service is up

    return available_port_count(service.config)


def _application(service) -> str:
    if service is not None and getattr(service, "initialized", False):
        return service.config.application
    return "Flow Cell"


class ProtocolTab(QWidget):
    signal_protocol_changed = Signal()

    def __init__(
        self, service, current_source: Optional[Callable[[], Tuple[Optional[str], dict, dict]]] = None, parent=None
    ):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.service = service
        self._current_source = current_source
        self._protocol = ProtocolFile()
        self.protocol_path: Optional[str] = None
        self._problems: Dict[int, str] = {}
        self._open_groups: set = set()
        self._highlight_row: Optional[int] = None
        self._run_locked = False
        self._dirty = False

        self._build_ui()
        self._render()

    # ---------- public surface ----------

    @property
    def protocol(self) -> ProtocolFile:
        return self._protocol

    def set_protocol(self, protocol: ProtocolFile, path: Optional[str] = None) -> None:
        self._protocol = protocol
        self.protocol_path = path
        self._open_groups = set()
        self._highlight_row = None
        self._dirty = False
        self._render()
        self.signal_protocol_changed.emit()

    def load(self, path: str) -> bool:
        try:
            protocol = load_protocol(path)
        except Exception as e:
            QMessageBox.warning(self, "Open failed", f"Could not open the protocol:\n{e}")
            return False
        self.set_protocol(protocol, path)
        state.save_ui_state(protocol_path=path)
        return True

    def save(self) -> bool:
        if not self.protocol_path:
            return self.save_as()
        try:
            self._drop_unreferenced_blocks()
            save_protocol(self._protocol, self.protocol_path)
        except Exception as e:
            QMessageBox.warning(self, "Save failed", f"Could not save the protocol:\n{e}")
            return False
        self._dirty = False
        self._render_file_row()
        return True

    def save_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save protocol", self.protocol_path or "protocol.yaml", "YAML (*.yaml *.yml)"
        )
        if not path:
            return False
        self.protocol_path = path
        state.save_ui_state(protocol_path=path)
        return self.save()

    def new(self) -> None:
        self.set_protocol(ProtocolFile(), None)

    def set_run_locked(self, locked: bool) -> None:
        """The structure is frozen while a run rides it (the tree stays viewable)."""
        self._run_locked = locked
        for widget in self._lockable:
            widget.setEnabled(not locked)
        self._render()  # re-renders the checkable flags and rebuilds the field editor

    def imaging_ready(self) -> Optional[str]:
        """None when every included imaging row has both sources; else a one-line hint."""
        missing = 0
        for _i, row in self._protocol.imaging_rows():
            if row.include and (not row.settings or not row.coordinates):
                missing += 1
        if missing:
            return f"{missing} imaging row(s) have no settings or coordinates"
        if not self._protocol.sequences:
            return "the protocol is empty"
        return None

    def highlight_row(self, row_index: Optional[int]) -> None:
        self._highlight_row = row_index
        if row_index is not None:
            round_label = (
                self._protocol.sequences[row_index].get("round")
                if 0 <= row_index < len(self._protocol.sequences)
                else None
            )
            self._open_groups.add(round_label)
        self._render()

    # ---------- UI construction ----------

    def _build_ui(self) -> None:
        self.name_label = QLabel("(new protocol)")
        self.new_button = QPushButton("New")
        self.open_button = QPushButton("Open…")
        self.save_button = QPushButton("Save")
        self.save_as_button = QPushButton("Save as…")
        self.new_button.clicked.connect(self._confirm_discard_then(self.new))
        self.open_button.clicked.connect(self._open_clicked)
        self.save_button.clicked.connect(self.save)
        self.save_as_button.clicked.connect(self.save_as)
        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("File:"))
        file_row.addWidget(self.name_label, 1)
        for b in (self.new_button, self.open_button, self.save_button, self.save_as_button):
            file_row.addWidget(b)

        self.scope_combo = QComboBox()
        self.scope_combo.addItems([_SCOPE_ALL, _SCOPE_SELECTED])
        self.apply_settings_button = QPushButton("Apply current settings")
        self.settings_file_button = QPushButton("From file…")
        self.settings_summary = QLabel("—")
        self.apply_settings_button.clicked.connect(lambda: self._apply_current("settings"))
        self.settings_file_button.clicked.connect(lambda: self._assign_from_file("settings"))
        settings_row = QHBoxLayout()
        settings_row.addWidget(QLabel("Settings"))
        settings_row.addWidget(QLabel("for"))
        settings_row.addWidget(self.scope_combo)
        settings_row.addWidget(self.apply_settings_button)
        settings_row.addWidget(self.settings_file_button)
        settings_row.addWidget(self.settings_summary, 1)

        self.capture_button = QPushButton("Capture current coordinates")
        self.coordinates_file_button = QPushButton("From file…")
        self.coordinates_summary = QLabel("—")
        self.capture_button.clicked.connect(lambda: self._apply_current("coordinates"))
        self.coordinates_file_button.clicked.connect(lambda: self._assign_from_file("coordinates"))
        coordinates_row = QHBoxLayout()
        coordinates_row.addWidget(QLabel("Coordinates"))
        coordinates_row.addWidget(self.capture_button)
        coordinates_row.addWidget(self.coordinates_file_button)
        coordinates_row.addWidget(self.coordinates_summary, 1)

        self.pattern_edit = QLineEdit(DEFAULT_FOLDER_PATTERN)
        self.pattern_edit.editingFinished.connect(self._pattern_changed)
        self.validation_label = QLabel("—")
        folders_row = QHBoxLayout()
        folders_row.addWidget(QLabel("Folders"))
        folders_row.addWidget(self.pattern_edit)
        folders_row.addWidget(self.validation_label, 1)

        self.add_step_button = QPushButton("+ Step")
        self.add_imaging_button = QPushButton("+ Imaging")
        self.duplicate_button = QPushButton("Duplicate")
        self.up_button = QPushButton("↑")
        self.down_button = QPushButton("↓")
        self.remove_button = QPushButton("Remove")
        self.add_rounds_button = QPushButton("Add rounds…")
        self.expand_button = QPushButton("Expand all")
        self.collapse_button = QPushButton("Collapse all")
        self.add_step_button.clicked.connect(self._add_step)
        self.add_imaging_button.clicked.connect(self._add_imaging)
        self.duplicate_button.clicked.connect(self._duplicate_row)
        self.up_button.clicked.connect(lambda: self._move_row(-1))
        self.down_button.clicked.connect(lambda: self._move_row(+1))
        self.remove_button.clicked.connect(self._remove_row)
        self.add_rounds_button.clicked.connect(self._add_rounds)
        self.expand_button.clicked.connect(lambda: self._expand_all(True))
        self.collapse_button.clicked.connect(lambda: self._expand_all(False))
        toolbar = QHBoxLayout()
        for b in (
            self.add_step_button,
            self.add_imaging_button,
            self.duplicate_button,
            self.up_button,
            self.down_button,
            self.remove_button,
            self.add_rounds_button,
        ):
            toolbar.addWidget(b)
        toolbar.addStretch(1)
        toolbar.addWidget(self.expand_button)
        toolbar.addWidget(self.collapse_button)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(7)
        self.tree.setHeaderLabels(["Step", "Type", "Port", "Vol", "Rate", "Inc", "Status"])
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemSelectionChanged.connect(self._rebuild_field_editor)
        self.tree.itemExpanded.connect(lambda item: self._note_open(item, True))
        self.tree.itemCollapsed.connect(lambda item: self._note_open(item, False))

        self.field_group = QGroupBox("Selected row")
        self.field_form = QFormLayout()
        self.field_group.setLayout(self.field_form)

        layout = QVBoxLayout()
        layout.addLayout(file_row)
        layout.addLayout(settings_row)
        layout.addLayout(coordinates_row)
        layout.addLayout(folders_row)
        layout.addLayout(toolbar)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.field_group)
        self.setLayout(layout)

        self._lockable = [
            self.new_button,
            self.open_button,
            self.apply_settings_button,
            self.settings_file_button,
            self.capture_button,
            self.coordinates_file_button,
            self.pattern_edit,
            self.add_step_button,
            self.add_imaging_button,
            self.duplicate_button,
            self.up_button,
            self.down_button,
            self.remove_button,
            self.add_rounds_button,
        ]

    # ---------- model helpers ----------

    def _mark_changed(self) -> None:
        self._dirty = True
        self._render()  # renders and re-validates
        self.signal_protocol_changed.emit()

    def _selected_row_index(self) -> Optional[int]:
        items = self.tree.selectedItems()
        if not items:
            return None
        data = items[0].data(0, Qt.UserRole)
        return int(data) if data is not None else None

    def _selected_imaging_rows(self) -> List[int]:
        if self.scope_combo.currentText() == _SCOPE_ALL:
            return [i for i, _r in self._protocol.imaging_rows()]
        index = self._selected_row_index()
        rows = []
        if index is not None and self._protocol.sequences[index].get("type") == IMAGING_TYPE:
            rows.append(index)
        return rows

    def _drop_unreferenced_blocks(self) -> None:
        used_settings = {r.get("settings") for r in self._protocol.sequences if r.get("type") == IMAGING_TYPE}
        used_coordinates = {r.get("coordinates") for r in self._protocol.sequences if r.get("type") == IMAGING_TYPE}
        header = self._protocol.imaging
        header.settings = {k: v for k, v in header.settings.items() if k in used_settings}
        header.coordinates = {k: v for k, v in header.coordinates.items() if k in used_coordinates}

    def _confirm_discard_then(self, action):
        def run():
            if self._dirty:
                reply = QMessageBox.question(
                    self,
                    "Discard changes?",
                    "The protocol has unsaved changes. Discard them?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply == QMessageBox.No:
                    return
            action()

        return run

    # ---------- sources ----------

    def _fresh_block_key(self, header_map: dict) -> str:
        key = f"current_{time.strftime('%H%M')}"
        suffix = 2
        base = key
        while key in header_map:
            key = f"{base}_{suffix}"
            suffix += 1
        return key

    def _confirm_replace(self, rows: List[int], field: str, new_ref: str) -> bool:
        existing = {self._protocol.sequences[i].get(field) for i in rows} - {None, new_ref}
        if not existing:
            return True
        reply = QMessageBox.question(
            self,
            "Replace sources?",
            f"{len(rows)} imaging row(s) already have {field} assigned. Replace them?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def _apply_current(self, kind: str, rows: Optional[List[int]] = None) -> None:
        if self._current_source is None:
            QMessageBox.information(self, "Not available", "The Wellplate Multipoint tab is not available.")
            return
        rows = rows if rows is not None else self._selected_imaging_rows()
        if not rows:
            QMessageBox.information(self, "No imaging rows", "There are no imaging rows in this scope.")
            return
        error, settings, coordinates = self._current_source()
        if error:
            QMessageBox.warning(self, "Cannot read the current settings", error)
            return
        now = datetime.datetime.now().isoformat(timespec="seconds")
        if kind == "settings":
            block = SettingsBlock.model_validate({**settings, "applied_at": now, "source": "Wellplate Multipoint"})
            header = self._protocol.imaging.settings
        else:
            block = CoordinatesBlock.model_validate(
                {**coordinates, "captured_at": now, "source": "Wellplate Multipoint"}
            )
            if block.fov_count == 0:
                QMessageBox.warning(
                    self,
                    "No coordinates",
                    "The Wellplate tab has no FOVs to capture — select wells or load coordinates first.",
                )
                return
            header = self._protocol.imaging.coordinates
        if not self._confirm_replace(rows, kind, ""):
            return
        key = self._fresh_block_key(header)
        header[key] = block
        for i in rows:
            self._protocol.sequences[i][kind] = key
        self._mark_changed()

    def _assign_from_file(self, kind: str, rows: Optional[List[int]] = None) -> None:
        rows = rows if rows is not None else self._selected_imaging_rows()
        if not rows:
            QMessageBox.information(self, "No imaging rows", "There are no imaging rows in this scope.")
            return
        path = pick_settings_source(self) if kind == "settings" else pick_coordinates_source(self)
        if not path:
            return
        if kind == "settings" and path.endswith("acquisition.yaml"):
            path = os.path.dirname(path)  # a saved acquisition folder is the settings source
        base = os.path.dirname(os.path.abspath(self.protocol_path)) if self.protocol_path else None
        ref = os.path.relpath(path, base) if base and os.path.abspath(path).startswith(base + os.sep) else path
        if not self._confirm_replace(rows, kind, ref):
            return
        for i in rows:
            self._protocol.sequences[i][kind] = ref
        self._mark_changed()

    # ---------- toolbar actions ----------

    def _selected_round(self) -> Optional[str]:
        index = self._selected_row_index()
        if index is not None:
            return self._protocol.sequences[index].get("round")
        return self._protocol.sequences[-1].get("round") if self._protocol.sequences else None

    def _insert_after_selection(self, row: dict) -> None:
        index = self._selected_row_index()
        at = index + 1 if index is not None else len(self._protocol.sequences)
        self._protocol.sequences.insert(at, row)
        self._mark_changed()

    def _add_step(self) -> None:
        try:
            from fluidics.qt.sequence_editor import AddSequenceDialog
        except ImportError:
            QMessageBox.warning(self, "Not available", "Adding steps needs the updated fluidics library (fluidics.qt).")
            return
        limit = _port_limit(self.service)
        port_names = [f"Port {i}" for i in range(1, (limit or 24) + 1)]
        if self.service is not None and getattr(self.service, "initialized", False):
            try:
                port_names = self.service.system.devices.selector_valves.get_port_names()
            except Exception:
                pass
        dialog = AddSequenceDialog(self, _application(self.service), port_names)
        if dialog.exec_() == QDialog.Accepted and dialog.result_dict:
            row = dict(dialog.result_dict)
            if row.get("type") == IMAGING_TYPE:
                self._add_imaging()
                return
            row["round"] = self._selected_round()
            self._insert_after_selection(row)

    def _imaging_ordinal_before(self, at: int) -> int:
        return sum(1 for r in self._protocol.sequences[:at] if r.get("type") == IMAGING_TYPE and r.get("include", True))

    def _add_imaging(self) -> None:
        index = self._selected_row_index()
        at = index + 1 if index is not None else len(self._protocol.sequences)
        round_label = self._selected_round()
        folder = render_folder(
            self._protocol.imaging.folder_pattern,
            round_label=round_label,
            step_name="image",
            index=self._imaging_ordinal_before(at) + 1,
        )
        self._protocol.sequences.insert(
            at, {"type": IMAGING_TYPE, "name": "image", "round": round_label, "folder": folder}
        )
        self._mark_changed()

    def _duplicate_row(self) -> None:
        index = self._selected_row_index()
        if index is None:
            return
        self._protocol.sequences.insert(index + 1, dict(self._protocol.sequences[index]))
        self._mark_changed()

    def _move_row(self, delta: int) -> None:
        index = self._selected_row_index()
        if index is None:
            return
        target = index + delta
        if not 0 <= target < len(self._protocol.sequences):
            return
        rows = self._protocol.sequences
        rows[index], rows[target] = rows[target], rows[index]
        self._mark_changed()
        self._select_row(target)

    def _remove_row(self) -> None:
        index = self._selected_row_index()
        if index is None:
            return
        del self._protocol.sequences[index]
        self._mark_changed()

    def _add_rounds(self) -> None:
        if not any(r.get("round") for r in self._protocol.sequences):
            QMessageBox.information(self, "No rounds", "Label at least one row with a round first.")
            return
        dialog = AddRoundsDialog(self._protocol, self)
        if dialog.exec_() == QDialog.Accepted and dialog.result_kwargs:
            self._protocol = expand_rounds(self._protocol, **dialog.result_kwargs)
            self._mark_changed()

    def _pattern_changed(self) -> None:
        pattern = self.pattern_edit.text().strip() or DEFAULT_FOLDER_PATTERN
        if pattern != self._protocol.imaging.folder_pattern:
            self._protocol.imaging.folder_pattern = pattern
            self._mark_changed()

    def _expand_all(self, expand: bool) -> None:
        self.tree.expandAll() if expand else self.tree.collapseAll()

    def _open_clicked(self) -> None:
        def open_file():
            start = self.protocol_path or state.load_ui_state().get("protocol_path") or ""
            path, _ = QFileDialog.getOpenFileName(self, "Open protocol", start, "YAML (*.yaml *.yml)")
            if path:
                self.load(path)

        self._confirm_discard_then(open_file)()

    # ---------- validation ----------

    def _validate(self) -> None:
        problems: Dict[int, str] = {}
        folder_map: Dict[str, str] = {}
        for message in folder_problems(self._protocol):
            folder_map.setdefault(message.split(":", 1)[0], message)
        application = _application(self.service)
        limit = _port_limit(self.service)
        base = os.path.dirname(os.path.abspath(self.protocol_path)) if self.protocol_path else None
        for i, row in enumerate(self._protocol.sequences):
            if row.get("type") == IMAGING_TYPE:
                try:
                    imaging = ImagingRow.model_validate(row)
                except Exception as e:
                    problems[i] = str(e)
                    continue
                if not imaging.include:
                    continue
                label = imaging.name or f"row {i + 1}"
                if label in folder_map:
                    problems[i] = folder_map[label]
                    continue
                for field in ("settings", "coordinates"):
                    ref = getattr(imaging, field)
                    if not ref:
                        problems[i] = f"no {field}"
                        break
                    header = getattr(self._protocol.imaging, field)
                    if ref in header:
                        continue
                    candidate = os.path.join(base, ref) if base else ref
                    if not os.path.exists(candidate):
                        problems[i] = f"{field} '{ref}' is neither a header block nor a file"
                        break
                continue
            if SequenceListAdapter is None:
                continue
            try:
                type_problem = sequence_type_problem(row, application)
                if type_problem:
                    problems[i] = type_problem
                    continue
                coerced = SequenceListAdapter.validate_python([row])[0].model_dump()
                if limit is not None:
                    port_problems = sequence_port_problems(coerced, limit)
                    if port_problems:
                        problems[i] = "; ".join(port_problems)
            except Exception as e:
                problems[i] = str(e)
        self._problems = problems

    # ---------- rendering ----------

    def _note_open(self, item: QTreeWidgetItem, is_open: bool) -> None:
        if item.parent() is None:
            label = item.data(0, Qt.UserRole + 1)
            if is_open:
                self._open_groups.add(label)
            else:
                self._open_groups.discard(label)

    def _render_file_row(self) -> None:
        name = self._protocol.name or (os.path.basename(self.protocol_path) if self.protocol_path else "(new protocol)")
        self.name_label.setText(name + (" *" if self._dirty else ""))

    def _render_summaries(self) -> None:
        imaging = [r for _i, r in self._protocol.imaging_rows()]
        by_ref = lambda field: {getattr(r, field) for r in imaging if getattr(r, field)}  # noqa: E731
        self.settings_summary.setText(
            f"{sum(1 for r in imaging if r.settings)}/{len(imaging)} rows · {len(by_ref('settings'))} source(s)"
            if imaging
            else "—"
        )
        total_fovs = 0
        for r in imaging:
            block = self._protocol.imaging.coordinates.get(r.coordinates or "")
            if block is not None:
                total_fovs += block.fov_count
        self.coordinates_summary.setText(
            f"{sum(1 for r in imaging if r.coordinates)}/{len(imaging)} rows · {total_fovs} FOVs in blocks"
            if imaging
            else "—"
        )
        if self._problems:
            self.validation_label.setText(f"✗ {len(self._problems)} problem(s)")
            self.validation_label.setStyleSheet("color: #b00020;")
        else:
            self.validation_label.setText(f"✓ valid · {self._protocol.summary_line()}")
            self.validation_label.setStyleSheet("color: #2e7d32;")

    def _render(self) -> None:
        self._validate()
        selected = self._selected_row_index()
        with QSignalBlocker(self.tree):
            self.tree.clear()
            group_item = None
            current_label = object()  # sentinel unequal to any real label
            for i, row in enumerate(self._protocol.sequences):
                label = row.get("round")
                if label != current_label:
                    current_label = label
                    group_item = QTreeWidgetItem([label or "—", "", "", "", "", "", ""])
                    group_item.setData(0, Qt.UserRole + 1, label)
                    self.tree.addTopLevelItem(group_item)
                    group_item.setExpanded(label in self._open_groups)
                group_item.addChild(self._render_row(i, row))
            for gi in range(self.tree.topLevelItemCount()):
                group = self.tree.topLevelItem(gi)
                group.setText(1, f"{group.childCount()} step(s)")
        self._render_file_row()
        self._render_summaries()
        if selected is not None:
            self._select_row(selected)
        # The rebuild ran via itemSelectionChanged only if a row is still selected;
        # when the selected row vanished, clear the stale editor explicitly.
        self._rebuild_field_editor()

    def _render_row(self, i: int, row: dict) -> QTreeWidgetItem:
        seq_type = row.get("type", "")
        if seq_type == IMAGING_TYPE:
            type_label = "Imaging"
        elif SEQUENCE_TYPE_LABELS is not None:
            type_label = SEQUENCE_TYPE_LABELS.get(seq_type, seq_type)
        else:
            type_label = seq_type
        name = row.get("name") or type_label
        if seq_type == IMAGING_TYPE:
            name = f"{name} → {row.get('folder') or '?'}"
            port = vol = rate = inc = ""
        else:
            port = str(row.get("fluidic_port", "") or "")
            vol = str(row.get("volume", "") or "")
            rate = str(row.get("flow_rate", "") or "")
            inc = str(row.get("incubation_time", "") or "")
        status = "✗" if i in self._problems else ""
        item = QTreeWidgetItem([name, type_label, port, vol, rate, inc, status])
        item.setData(0, Qt.UserRole, i)
        if self._run_locked:  # a running protocol's structure is frozen, checkboxes included
            item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
        else:
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked if row.get("include", True) else Qt.Unchecked)
        if i in self._problems:
            item.setToolTip(6, self._problems[i])
            for column in range(7):
                item.setBackground(column, QBrush(_INVALID))
        elif i == self._highlight_row:
            for column in range(7):
                item.setBackground(column, QBrush(_HIGHLIGHT))
        return item

    def _select_row(self, index: int) -> None:
        for gi in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(gi)
            for ci in range(group.childCount()):
                child = group.child(ci)
                if child.data(0, Qt.UserRole) == index:
                    self.tree.setCurrentItem(child)
                    return

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return
        data = item.data(0, Qt.UserRole)
        if data is None:
            return
        if self._run_locked:
            row = self._protocol.sequences[int(data)]
            with QSignalBlocker(self.tree):
                item.setCheckState(0, Qt.Checked if row.get("include", True) else Qt.Unchecked)
            return
        index = int(data)
        included = item.checkState(0) == Qt.Checked
        row = self._protocol.sequences[index]
        if bool(row.get("include", True)) == included:
            return
        row["include"] = included
        self._dirty = True
        self._validate()
        self._render_file_row()
        self._render_summaries()
        self.signal_protocol_changed.emit()

    # ---------- field editor ----------

    def _clear_field_editor(self) -> None:
        while self.field_form.rowCount():
            self.field_form.removeRow(0)
        self._apply_all_checkbox = None

    def _rebuild_field_editor(self) -> None:
        self._clear_field_editor()
        index = self._selected_row_index()
        if index is None or self._run_locked:
            self.field_group.setTitle("Selected row")
            return
        row = self._protocol.sequences[index]
        if row.get("type") == IMAGING_TYPE:
            self._build_imaging_editor(index, row)
        else:
            self._build_fluidics_editor(index, row)

    def _line_edit(self, index: int, field: str, value) -> QLineEdit:
        edit = QLineEdit("" if value is None else str(value))
        edit.editingFinished.connect(lambda e=edit, f=field: self._field_edited(index, f, e))
        return edit

    def _build_fluidics_editor(self, index: int, row: dict) -> None:
        self.field_group.setTitle(f"Selected row — {row.get('name') or row.get('type')}")
        try:
            fields = get_fields_for_type(row.get("type")) if get_fields_for_type is not None else {}
        except Exception:
            fields = {}
        for fname, finfo in fields.items():
            if fname == "include":
                continue
            value = row.get(fname, getattr(finfo, "default", None))
            if fname in _STRING_FIELDS:
                widget = self._line_edit(index, fname, value)
            else:
                is_float = fname in ("temperature", "incubation_time")
                widget = QDoubleSpinBox() if is_float else QSpinBox()
                widget.setRange(0, 100000)
                if is_float:
                    widget.setDecimals(2)
                if value is not None:
                    try:
                        widget.setValue(float(value) if is_float else int(value))
                    except (TypeError, ValueError):
                        pass
                widget.editingFinished.connect(lambda w=widget, f=fname: self._field_edited(index, f, w))
            self.field_form.addRow(fname, widget)
        same_named = sum(
            1
            for other in self._protocol.sequences
            if (other.get("name"), other.get("type")) == (row.get("name"), row.get("type"))
        )
        self._apply_all_checkbox = QCheckBox(
            f"apply this edit to all rows named {row.get('name') or row.get('type')} ({same_named} rows)"
        )
        self.field_form.addRow("", self._apply_all_checkbox)

    def _build_imaging_editor(self, index: int, row: dict) -> None:
        self.field_group.setTitle("Selected row — imaging")
        for fname in ("name", "round", "folder"):
            self.field_form.addRow(fname, self._line_edit(index, fname, row.get(fname)))
        reset = QPushButton("Reset folder to pattern")
        reset.clicked.connect(lambda: self._reset_folder(index))
        self.field_form.addRow("", reset)
        for kind in ("settings", "coordinates"):
            ref = QLabel(str(row.get(kind) or "—"))
            buttons = QHBoxLayout()
            current = QPushButton("Apply current settings" if kind == "settings" else "Capture current coordinates")
            current.clicked.connect(lambda _=False, k=kind: self._apply_current(k, rows=[index]))
            from_file = QPushButton("From file…")
            from_file.clicked.connect(lambda _=False, k=kind: self._assign_from_file(k, rows=[index]))
            buttons.addWidget(ref, 1)
            buttons.addWidget(current)
            buttons.addWidget(from_file)
            container = QWidget()
            container.setLayout(buttons)
            self.field_form.addRow(kind, container)

    def _reset_folder(self, index: int) -> None:
        row = self._protocol.sequences[index]
        row["folder"] = render_folder(
            self._protocol.imaging.folder_pattern,
            round_label=row.get("round"),
            step_name=row.get("name"),
            index=self._imaging_ordinal_before(index) + 1,
        )
        self._mark_changed()

    def _field_edited(self, index: int, field: str, widget) -> None:
        if self._run_locked or not 0 <= index < len(self._protocol.sequences):
            return
        if isinstance(widget, QLineEdit):
            text = widget.text().strip()
            value = text or None
        elif isinstance(widget, QDoubleSpinBox):
            value = float(widget.value())
        else:
            value = int(widget.value())
        row = self._protocol.sequences[index]
        if row.get(field) == value:
            return
        apply_all = (
            self._apply_all_checkbox is not None
            and self._apply_all_checkbox.isChecked()
            and field not in ("name", "round")
        )
        key = (row.get("name"), row.get("type"))
        row[field] = value
        if apply_all:
            for other in self._protocol.sequences:
                if other is not row and (other.get("name"), other.get("type")) == key:
                    other[field] = value
        # Re-render on the next event-loop turn: destroying the editor inside its own signal is unsafe.
        QTimer.singleShot(0, self._mark_changed)
