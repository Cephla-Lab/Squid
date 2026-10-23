"""Dialogs for the fluidics protocol: Add rounds, imaging-source pickers, pre-flight, crash recovery."""

import html
import os
import re
import time
from typing import List, Optional

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from control.models.fluidics_protocol import (
    IMAGING_TYPE,
    ProtocolFile,
    expand_rounds,
    format_well_list,
    imaging_folder,
    next_wells,
    parse_port_list,
    parse_well_list,
    well_rows,
)

_NO_PORT_ROW = "— none —"


def drop_help_button(dialog: QDialog) -> None:
    """Remove the "?" Windows puts in a dialog's title bar. It opens Qt's What's This mode, which
    shows nothing unless a widget has What's This text; these dialogs explain themselves in their
    own text and tooltips instead."""
    dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)


def _tip(text: str) -> str:
    """A tooltip that wraps: Qt wraps rich text, not plain text."""
    return f"<p>{html.escape(text, quote=False)}</p>"


def _next_round_number(labels: List[str]) -> int:
    numbers = [int(m.group(1)) for label in labels for m in [re.search(r"(\d+)$", label or "")] if m]
    return max(numbers) + 1 if numbers else 2


class AddRoundsDialog(QDialog):
    """Copy one round N times with new labels, ports, wells and folders (see models.expand_rounds).

    Wells: shown when the round has rows that draw from a well (a plate-sipper protocol). Each new
    round moves every such row to the next well of the list, on its own plate. Prefilled with the
    wells after the round's own, along the row (A1 -> A2, A3, ...), until the operator types."""

    def __init__(self, protocol: ProtocolFile, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add rounds")
        drop_help_button(self)
        self.protocol = protocol
        self.result_kwargs: Optional[dict] = None

        labels = protocol.round_labels()
        self.template_combo = QComboBox()
        self.template_combo.addItems(labels)
        self.template_combo.setToolTip(
            _tip(
                "The round whose rows are copied. The copies go right after it; rows after it (a final clean-up) stay last."
            )
        )
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 999)
        self.count_spin.setToolTip(_tip("How many new rounds to add."))
        self.pattern_edit = QLineEdit("R{n:02d}")
        self.pattern_edit.setToolTip(_tip("The label of each new round; {n} is its number. R{n:02d} gives R02, R03, …"))
        self.start_spin = QSpinBox()
        self.start_spin.setRange(1, 9999)
        self.start_spin.setValue(_next_round_number(labels))
        self.start_spin.setToolTip(_tip("The number of the first new round."))
        self.port_row_combo = QComboBox()
        self.port_row_combo.setToolTip(
            _tip(
                "The row whose port changes each round, e.g. a probe row with one bottle per round. — none — "
                "keeps every row's port. On a sipper rig, rows that draw from wells change wells instead (below)."
            )
        )
        self.ports_edit = QLineEdit()
        self.ports_edit.setPlaceholderText("2-12,14-25")
        self.ports_edit.setToolTip(
            _tip("One port per new round for the row picked above, as a list or ranges: 2-12, 14-25.")
        )
        wells_tip = _tip(
            "One well per new round: along a row (A2-A12), down a column (A1-H1), or listed (A2, A5, B1). "
            "In each new round, every row that draws from a well moves to that well on its own plate: hyb "
            "A1 → A2, push ssct/A1 → ssct/A2. Filled in with the wells after the round's own until you type "
            "here. Blank: every round reuses the same wells."
        )
        self.wells_label = QLabel("Wells")
        self.wells_label.setToolTip(wells_tip)
        self.wells_edit = QLineEdit()  # isModified(): the operator typed, so the prefill stops
        self.wells_edit.setPlaceholderText("A2-A12 (blank: every round keeps the same wells)")
        self.wells_edit.setToolTip(wells_tip)
        self._first_well: Optional[str] = None  # the template's first well row's well, without its plate
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.help_label = QLabel(
            "Copies a round right after itself. Each copy gets the next label and its own imaging folder, "
            "and can get the next port and the next well. Hover over a field for details."
        )
        self.help_label.setWordWrap(True)
        self.help_label.setStyleSheet("color: gray;")

        form = QFormLayout()
        form.addRow("Copy round", self.template_combo)
        form.addRow("How many", self.count_spin)
        form.addRow("Label pattern", self.pattern_edit)
        form.addRow("Starting n", self.start_spin)
        form.addRow("Port for row", self.port_row_combo)
        form.addRow("Ports", self.ports_edit)
        form.addRow(self.wells_label, self.wells_edit)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(self.help_label)
        layout.addLayout(form)
        layout.addWidget(QLabel("Preview:"))
        layout.addWidget(self.preview)
        layout.addWidget(self.buttons)
        self.setLayout(layout)

        self.template_combo.currentTextChanged.connect(self._template_changed)
        for signal in (
            self.count_spin.valueChanged,
            self.pattern_edit.textChanged,
            self.start_spin.valueChanged,
            self.ports_edit.textChanged,
            self.wells_edit.textChanged,
        ):
            signal.connect(self._update_preview)
        self.count_spin.valueChanged.connect(self._prefill_wells)
        self.port_row_combo.currentTextChanged.connect(self._update_preview)
        self._template_changed()

    def _template_changed(self) -> None:
        template = self.template_combo.currentText()
        rows = [r for r in self.protocol.sequences if r.get("round") == template]
        names = list(dict.fromkeys((r.get("name") or r["type"]) for r in rows if "fluidic_port" in r))
        self.port_row_combo.blockSignals(True)
        self.port_row_combo.clear()
        self.port_row_combo.addItems([_NO_PORT_ROW] + names)
        self.port_row_combo.blockSignals(False)
        drawing = well_rows(rows)
        self._first_well = drawing[0]["well"].rpartition("/")[2] if drawing else None
        self.wells_label.setVisible(bool(drawing))
        self.wells_edit.setVisible(bool(drawing))
        self.wells_edit.setModified(False)  # a new round: its own wells are proposed again
        self._prefill_wells()
        self._update_preview()

    def _prefill_wells(self) -> None:
        """The wells after the round's own, one per new round, until the operator types a list."""
        if self.wells_edit.isModified():
            return
        text = ""
        if self._first_well is not None:
            try:
                text = format_well_list(next_wells(self._first_well, int(self.count_spin.value())))
            except ValueError:  # the round's own well is not one: _wells says so
                pass
        self.wells_edit.setText(text)

    def _current_kwargs(self) -> dict:
        port_row = self.port_row_combo.currentText()
        use_ports = port_row not in ("", _NO_PORT_ROW)
        return dict(
            template_round=self.template_combo.currentText(),
            count=int(self.count_spin.value()),
            label_pattern=self.pattern_edit.text() or "R{n:02d}",
            start=int(self.start_spin.value()),
            port_row_name=port_row if use_ports else None,
            ports=parse_port_list(self.ports_edit.text()) if use_ports else None,
            wells=self._wells(),
        )

    def _wells(self) -> Optional[List[str]]:
        """The Wells box as expand_rounds takes it. A blank box means every round keeps its wells
        only when the operator cleared it: the blank an automatic proposal leaves (the plate has no
        well after the round's own, H12; or the round's well is not one) is refused, never read as
        reuse."""
        if self._first_well is None:
            return None
        text = self.wells_edit.text().strip()
        if text:
            return parse_well_list(text)
        if self.wells_edit.isModified():
            return None
        try:  # why the proposal came out blank
            next_wells(self._first_well, 1)
        except ValueError as e:
            raise ValueError(f"Wells: {e}: fix the round's well row, or type the wells for the new rounds") from None
        raise ValueError(f"Wells: no wells after {self._first_well} on its plate: type the wells for the new rounds")

    def _update_preview(self) -> None:
        try:
            kwargs = self._current_kwargs()
            out = expand_rounds(self.protocol, **kwargs)
            old_ids = {id(r) for r in self.protocol.sequences}
            added = [r for r in out.sequences if id(r) not in old_ids]
            lines = []
            for label in dict.fromkeys(r.get("round") for r in added):
                rows = [r for r in added if r.get("round") == label]
                port = next(
                    (r["fluidic_port"] for r in rows if (r.get("name") or r["type"]) == kwargs["port_row_name"]), None
                )
                folders = [imaging_folder(label, r.get("folder")) for r in rows if r.get("type") == IMAGING_TYPE]
                wells = list(dict.fromkeys(r["well"] for r in well_rows(rows)))
                parts = [str(label)]
                if port is not None:
                    parts.append(f"port {port}")
                if wells:
                    parts.append(", ".join(wells))
                if folders:
                    parts.append(", ".join(str(f) for f in folders))
                lines.append(" · ".join(parts))
            self.preview.setPlainText("\n".join(lines))
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)
        except Exception as e:
            self.preview.setPlainText(f"✗ {e}")
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)

    def accept(self) -> None:
        try:
            self.result_kwargs = self._current_kwargs()
            expand_rounds(self.protocol, **self.result_kwargs)  # final validation
        except Exception as e:
            self.preview.setPlainText(f"✗ {e}")
            return
        super().accept()


def pick_settings_source(parent) -> Optional[str]:
    path, _ = QFileDialog.getOpenFileName(
        parent, "Imaging settings from a saved acquisition", "", "Acquisition (acquisition.yaml *.yaml *.yml)"
    )
    return path or None


def pick_coordinates_source(parent) -> Optional[str]:
    path, _ = QFileDialog.getOpenFileName(
        parent,
        "Coordinates from a saved acquisition or CSV",
        "",
        "Acquisition or coordinates (acquisition.yaml coordinates.csv *.yaml *.yml *.csv)",
    )
    return path or None


class PreflightDialog(QDialog):
    """The one gate before a run: problems block Start; otherwise the summary asks for confirmation."""

    def __init__(self, problems: List[str], summary_lines: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Start run")
        drop_help_button(self)
        layout = QVBoxLayout()
        if problems:
            headline = QLabel(f"{len(problems)} problem(s) must be fixed before this protocol can run:")
            headline.setStyleSheet("color: #b00020; font-weight: bold;")
            layout.addWidget(headline)
            text = QPlainTextEdit("\n".join(f"✗ {p}" for p in problems))
            text.setStyleSheet("color: #b00020;")
        else:
            layout.addWidget(QLabel("Ready to start:"))
            text = QPlainTextEdit("\n".join(summary_lines))
        text.setReadOnly(True)
        layout.addWidget(text)

        self.buttons = QDialogButtonBox()
        self.start_button = QPushButton("Start")
        self.buttons.addButton(self.start_button, QDialogButtonBox.AcceptRole)
        self.buttons.addButton(QDialogButtonBox.Cancel)
        self.start_button.setEnabled(not problems)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.setLayout(layout)


class RecoveryDialog(QDialog):
    """Offers to reopen an unfinished run found at start-up (or via Resume unfinished run…)."""

    def __init__(self, manifest, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unfinished fluidics run")
        drop_help_button(self)
        cursor = manifest.cursor
        step_line = "—"
        if cursor.step is not None and 0 <= cursor.step < len(manifest.steps):
            record = manifest.steps[cursor.step]
            step_line = f"step {cursor.step + 1}/{len(manifest.steps)} ({record.kind} {record.label})"
        heartbeat = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(manifest.heartbeat_at))
        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"Run '{manifest.run_name}' was {manifest.status.upper()} at {step_line}."))
        layout.addWidget(QLabel(f"Last written {heartbeat} · {os.path.basename(manifest.run_dir)}"))
        warning = QLabel("Pump and valve state is unknown — check before continuing.")
        warning.setStyleSheet("color: #b00020;")
        layout.addWidget(warning)
        self.buttons = QDialogButtonBox()
        open_button = QPushButton("Open recovery")
        self.buttons.addButton(open_button, QDialogButtonBox.AcceptRole)
        not_now = QPushButton("Not now")
        self.buttons.addButton(not_now, QDialogButtonBox.RejectRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.setLayout(layout)
