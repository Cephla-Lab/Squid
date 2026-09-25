"""Dialogs for the fluidics protocol: Add rounds, imaging-source pickers, pre-flight, crash recovery."""

import os
import re
import time
from typing import List, Optional

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QCheckBox,
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

from control.models.fluidics_protocol import IMAGING_TYPE, ProtocolFile, expand_rounds, imaging_folder, parse_port_list

_NO_PORT_ROW = "— none —"


def _next_round_number(labels: List[str]) -> int:
    numbers = [int(m.group(1)) for label in labels for m in [re.search(r"(\d+)$", label or "")] if m]
    return max(numbers) + 1 if numbers else 2


class AddRoundsDialog(QDialog):
    """Copy one round N times with new labels, ports and folders (see models.expand_rounds)."""

    def __init__(self, protocol: ProtocolFile, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add rounds")
        self.protocol = protocol
        self.result_kwargs: Optional[dict] = None

        labels = protocol.round_labels()
        self.template_combo = QComboBox()
        self.template_combo.addItems(labels)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 999)
        self.pattern_edit = QLineEdit("R{n:02d}")
        self.start_spin = QSpinBox()
        self.start_spin.setRange(1, 9999)
        self.start_spin.setValue(_next_round_number(labels))
        self.port_row_combo = QComboBox()
        self.ports_edit = QLineEdit()
        self.ports_edit.setPlaceholderText("2-12,14-25")
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)

        form = QFormLayout()
        form.addRow("Copy round", self.template_combo)
        form.addRow("How many", self.count_spin)
        form.addRow("Label pattern", self.pattern_edit)
        form.addRow("Starting n", self.start_spin)
        form.addRow("Port for row", self.port_row_combo)
        form.addRow("Ports", self.ports_edit)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
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
        ):
            signal.connect(self._update_preview)
        self.port_row_combo.currentTextChanged.connect(self._update_preview)
        self._template_changed()

    def _template_changed(self) -> None:
        template = self.template_combo.currentText()
        rows = [r for r in self.protocol.sequences if r.get("round") == template and "fluidic_port" in r]
        names = list(dict.fromkeys((r.get("name") or r["type"]) for r in rows))
        self.port_row_combo.blockSignals(True)
        self.port_row_combo.clear()
        self.port_row_combo.addItems([_NO_PORT_ROW] + names)
        self.port_row_combo.blockSignals(False)
        self._update_preview()

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
        )

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
                parts = [str(label)]
                if port is not None:
                    parts.append(f"port {port}")
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

    def __init__(self, problems: List[str], summary_lines: List[str], parent=None, tec_option: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Start run")
        self.disable_tec_checkbox = None
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

        if tec_option and not problems:
            self.disable_tec_checkbox = QCheckBox("Turn off temperature control (TEC) when the run finishes")
            layout.addWidget(self.disable_tec_checkbox)

        self.buttons = QDialogButtonBox()
        self.start_button = QPushButton("Start")
        self.buttons.addButton(self.start_button, QDialogButtonBox.AcceptRole)
        self.buttons.addButton(QDialogButtonBox.Cancel)
        self.start_button.setEnabled(not problems)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.setLayout(layout)

    def disable_tec_at_end(self) -> bool:
        return self.disable_tec_checkbox is not None and self.disable_tec_checkbox.isChecked()


class RecoveryDialog(QDialog):
    """Offers to reopen an unfinished run found at start-up (or via Resume unfinished run…)."""

    def __init__(self, manifest, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unfinished fluidics run")
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
