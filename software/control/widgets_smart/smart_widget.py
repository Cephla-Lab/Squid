"""Qt face for smart acquisition.

All the work happens in the Qt-free ``control.core.smart_acquisition`` package;
this widget only collects parameters, runs the analysis off the GUI thread, and
hands the result to the acquisition tab the template belongs to: Record + Z-Stack
(Load Coordinates) for a ``record_zstack`` template, Flexible Multipoint for
anything else.
"""

import os
from typing import Any, Dict, List, Optional

from qtpy.QtCore import Qt, QThread, Signal
from qtpy.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

import squid.logging
from control.core.smart_acquisition import analyzers, emit, pipeline
from control.core.smart_acquisition.targets import Target


class _AnalysisWorker(QThread):
    """Runs the analysis pipeline off the GUI thread."""

    progress = Signal(int, int)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, kwargs: Dict[str, Any], parent=None):
        super().__init__(parent)
        self._kwargs = kwargs

    def run(self):
        try:
            result = pipeline.run_analysis(progress_cb=lambda i, n: self.progress.emit(i, n), **self._kwargs)
            self.finished_ok.emit(result)
        except Exception as e:  # surfaced in the GUI, not swallowed
            self.failed.emit(str(e))


class SmartAcquisitionWidget(QFrame):
    """Analyse a finished survey and turn what it finds into new stage coordinates."""

    #: Emitted with the generated acquisition.yaml path once the user accepts a result.
    signal_targets_ready = Signal(str)

    #: Analyzer selected when the tab first opens.
    DEFAULT_ANALYZER = "ring_grid"

    def __init__(
        self,
        multipointController=None,
        flexibleMultiPointWidget=None,
        tab_widget=None,
        parent=None,
        recordZStackWidget=None,
    ):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.multipointController = multipointController
        self.flexibleMultiPointWidget = flexibleMultiPointWidget
        self.recordZStackWidget = recordZStackWidget
        self.tab_widget = tab_widget

        self._worker: Optional[_AnalysisWorker] = None
        self._result = None
        self._targets: List[Target] = []
        self._param_widgets: Dict[str, Any] = {}

        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self._on_analyzer_changed(self.combobox_analyzer.currentText())

    # ------------------------------------------------------------------ UI

    def add_components(self):
        # This panel is taller than the space a narrow control column has to
        # give. The record tab bar (widgets.ScrollingTabWidget) scrolls every
        # page rather than squashing it, so no scroll area of our own here.
        layout = QVBoxLayout()

        # --- source -----------------------------------------------------
        source_box = QGroupBox("Survey acquisition")
        source_layout = QGridLayout()
        self.lineedit_input = QLineEdit()
        self.lineedit_input.setPlaceholderText("Folder of a completed low-magnification acquisition")
        self.btn_browse_input = QPushButton("Browse...")
        self.btn_browse_input.clicked.connect(self._browse_input)
        self.btn_use_last = QPushButton("Use Last")
        self.btn_use_last.setToolTip("Use the acquisition this session most recently finished")
        self.btn_use_last.clicked.connect(self._use_last_acquisition)
        source_layout.addWidget(QLabel("Folder:"), 0, 0)
        source_layout.addWidget(self.lineedit_input, 0, 1)
        source_layout.addWidget(self.btn_browse_input, 0, 2)
        source_layout.addWidget(self.btn_use_last, 0, 3)
        source_box.setLayout(source_layout)
        layout.addWidget(source_box)

        # --- analyzer ---------------------------------------------------
        analyzer_box = QGroupBox("Analysis")
        analyzer_layout = QVBoxLayout()
        top = QHBoxLayout()
        top.addWidget(QLabel("Analyzer:"))
        self.combobox_analyzer = QComboBox()
        self.combobox_analyzer.addItems(analyzers.available())
        # Alphabetical order would open on focus_score; ring_grid is the one
        # people actually come here for.
        if self.combobox_analyzer.findText(self.DEFAULT_ANALYZER) >= 0:
            self.combobox_analyzer.setCurrentText(self.DEFAULT_ANALYZER)
        self.combobox_analyzer.currentTextChanged.connect(self._on_analyzer_changed)
        top.addWidget(self.combobox_analyzer, 1)
        analyzer_layout.addLayout(top)

        z_row = QHBoxLayout()
        self.checkbox_sweep_z = QCheckBox("Find best Z in stack")
        self.checkbox_sweep_z.setToolTip(
            "Run the analyzer on every plane of each z-stack and keep the best-focus one.\n"
            "Without this only the middle plane of a stack is examined."
        )
        self.checkbox_sweep_z.toggled.connect(self._on_sweep_z_toggled)
        self.checkbox_interpolate_z = QCheckBox("Interpolate")
        self.checkbox_interpolate_z.setChecked(True)
        self.checkbox_interpolate_z.setEnabled(False)
        self.checkbox_interpolate_z.setToolTip(
            "Fit a parabola across the focus peak to refine Z below the z-step,\n"
            "instead of snapping to the nearest acquired plane."
        )
        self.checkbox_carry_z = QCheckBox("Carry survey Z")
        self.checkbox_carry_z.setToolTip(
            "Off (recommended): leave Z to autofocus, since the survey focus is not the high-magnification focus."
        )
        z_row.addWidget(self.checkbox_sweep_z)
        z_row.addWidget(self.checkbox_interpolate_z)
        z_row.addWidget(self.checkbox_carry_z)
        z_row.addStretch(1)
        analyzer_layout.addLayout(z_row)

        # The handful of parameters that describe the sample.
        self.params_form = QFormLayout()
        self.params_host = QFrame()
        self.params_host.setLayout(self.params_form)
        analyzer_layout.addWidget(self.params_host)

        # Detector tuning, collapsed by default so the panel stays short.
        self.advanced_box = QGroupBox("Advanced")
        self.advanced_box.setCheckable(True)
        self.advanced_box.setChecked(False)
        self.advanced_form = QFormLayout()
        self.advanced_box.setLayout(self.advanced_form)
        self.advanced_box.toggled.connect(self._on_advanced_toggled)
        analyzer_layout.addWidget(self.advanced_box)

        analyzer_box.setLayout(analyzer_layout)
        layout.addWidget(analyzer_box)

        # --- run --------------------------------------------------------
        run_row = QHBoxLayout()
        self.btn_analyze = QPushButton("Analyze")
        self.btn_analyze.setCheckable(False)
        self.btn_analyze.clicked.connect(self._start_analysis)
        self.checkbox_overlay = QCheckBox("Write overlays")
        self.checkbox_overlay.setChecked(True)
        self.checkbox_overlay.setToolTip("Save annotated PNGs so you can check what was detected")
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        run_row.addWidget(self.btn_analyze)
        run_row.addWidget(self.checkbox_overlay)
        run_row.addWidget(self.progress, 1)
        layout.addLayout(run_row)

        # --- results ----------------------------------------------------
        self.table_targets = QTableWidget(0, 6)
        self.table_targets.setHorizontalHeaderLabels(["Region", "x (mm)", "y (mm)", "z (mm)", "Score", "Source"])
        self.table_targets.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_targets.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_targets.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_targets.setMinimumHeight(120)
        layout.addWidget(self.table_targets, 1)

        self.label_summary = QLabel("No analysis run yet.")
        self.label_summary.setWordWrap(True)
        layout.addWidget(self.label_summary)

        # --- output -----------------------------------------------------
        out_box = QGroupBox("Emit acquisition")
        out_layout = QGridLayout()
        self.lineedit_template = QLineEdit()
        self.lineedit_template.setPlaceholderText("acquisition.yaml supplying channels, Z-stack and autofocus")
        self.lineedit_template.setToolTip(
            "Any existing acquisition.yaml. The new run takes its coordinates from the\n"
            "analysis above and everything else -- channels, exposure, z planes,\n"
            "autofocus, recording -- from this file. Its widget type picks the tab the\n"
            "targets are sent to: a Record + Z-Stack file goes to Record + Z-Stack (Load\n"
            "Coordinates), a wellplate or flexible file to Flexible Multipoint.\n\n"
            "To make one: set up the acquisition you want in a multipoint tab or in\n"
            "Record + Z-Stack (XY = Current Position, the recording and z-stack\n"
            "settings, Laser AF, Nt/dt), run it once, and use the\n"
            "acquisition.yaml it writes. Defaults to the survey's own."
        )
        self.btn_browse_template = QPushButton("Browse...")
        self.btn_browse_template.clicked.connect(self._browse_template)
        self.lineedit_out = QLineEdit()
        self.lineedit_out.setPlaceholderText("Folder to write the new acquisition into")
        self.lineedit_out.setToolTip(
            "Where the generated acquisition.yaml and coordinates.csv are written.\n"
            "Point a Workflow Runner acquisition step at that acquisition.yaml to\n"
            "run these targets unattended."
        )
        self.btn_browse_out = QPushButton("Browse...")
        self.btn_browse_out.clicked.connect(self._browse_out)
        out_layout.addWidget(QLabel("Template:"), 0, 0)
        out_layout.addWidget(self.lineedit_template, 0, 1)
        out_layout.addWidget(self.btn_browse_template, 0, 2)
        out_layout.addWidget(QLabel("Output:"), 1, 0)
        out_layout.addWidget(self.lineedit_out, 1, 1)
        out_layout.addWidget(self.btn_browse_out, 1, 2)

        button_row = QHBoxLayout()
        self.btn_save = QPushButton("Save")
        self.btn_save.clicked.connect(self._save_targets)
        self.btn_send = QPushButton("Send to Flexible Multipoint")
        self.btn_send.clicked.connect(self._send_to_acquisition_tab)
        # The label names the tab the current template belongs to.
        self.lineedit_template.textChanged.connect(self._update_send_button)
        button_row.addWidget(self.btn_save)
        button_row.addWidget(self.btn_send)
        out_layout.addLayout(button_row, 2, 0, 1, 3)
        out_box.setLayout(out_layout)
        layout.addWidget(out_box)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(48)
        self.log_view.setMaximumHeight(90)
        layout.addWidget(self.log_view)

        self.setLayout(layout)
        self._set_outputs_enabled(False)
        self._update_send_button()

    @staticmethod
    def _editor_for(default):
        """Build the right editor for a parameter's default value."""
        if isinstance(default, bool):
            widget = QCheckBox()
            widget.setChecked(default)
        elif isinstance(default, int):
            widget = QSpinBox()
            widget.setRange(0, 100000)
            widget.setValue(default)
        elif isinstance(default, float):
            widget = QDoubleSpinBox()
            widget.setRange(0.0, 1e6)
            widget.setDecimals(3)
            widget.setValue(default)
        else:
            # Tuples and anything else stay read-only; change them via the CLI.
            widget = QLineEdit(str(default))
            widget.setReadOnly(True)
            widget.setToolTip("Not editable here; use scripts/analyze_to_targets.py --param")
        return widget

    def _on_analyzer_changed(self, name: str):
        """Rebuild the parameter forms for the selected analyzer."""
        for form in (self.params_form, self.advanced_form):
            while form.rowCount():
                form.removeRow(0)
        self._param_widgets.clear()
        if not name:
            return

        defaults = analyzers.defaults_for(name)
        primary = analyzers.primary_params_for(name)
        advanced = sorted(set(defaults) - set(primary))

        for key in primary:
            widget = self._editor_for(defaults[key])
            self._param_widgets[key] = widget
            self.params_form.addRow(key.replace("_", " ") + ":", widget)

        for key in advanced:
            widget = self._editor_for(defaults[key])
            self._param_widgets[key] = widget
            self.advanced_form.addRow(key.replace("_", " ") + ":", widget)

        self.advanced_box.setVisible(bool(advanced))
        self.advanced_box.setTitle(f"Advanced ({len(advanced)})" if advanced else "Advanced")
        self._on_advanced_toggled(self.advanced_box.isChecked())

    def _on_sweep_z_toggled(self, checked: bool):
        """Sweeping the stack determines Z, so 'carry survey Z' is implied and moot."""
        self.checkbox_carry_z.setEnabled(not checked)
        self.checkbox_interpolate_z.setEnabled(checked)
        if checked:
            self.checkbox_carry_z.setChecked(True)

    def _on_advanced_toggled(self, checked: bool):
        """Collapse the advanced section by hiding its fields, not just greying them."""
        for row in range(self.advanced_form.rowCount()):
            for role in (QFormLayout.LabelRole, QFormLayout.FieldRole):
                item = self.advanced_form.itemAt(row, role)
                if item is not None and item.widget() is not None:
                    item.widget().setVisible(checked)
        self.advanced_box.setFlat(not checked)
        self.advanced_box.updateGeometry()

    def _collect_params(self) -> Dict[str, Any]:
        params = {}
        for key, widget in self._param_widgets.items():
            if isinstance(widget, QCheckBox):
                params[key] = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                params[key] = widget.value()
            elif isinstance(widget, QDoubleSpinBox):
                params[key] = widget.value()
        return params

    # ------------------------------------------------------- file pickers

    def _browse_input(self):
        path = QFileDialog.getExistingDirectory(self, "Select survey acquisition folder", self.lineedit_input.text())
        if path:
            self.lineedit_input.setText(path)
            self._suggest_outputs()

    def _suggest_outputs(self):
        """Pre-fill Template and Output from the chosen survey.

        Both were blank with only placeholder text, which left no clue that the
        template is just another acquisition.yaml -- and the survey's own is
        usually the right one, since it already has the channels and exposure
        for this sample. Only fills empty fields, so a deliberate choice stands.
        """
        survey = self.lineedit_input.text().strip()
        if not survey or not os.path.isdir(survey):
            return
        if not self.lineedit_template.text().strip():
            candidate = os.path.join(survey, "acquisition.yaml")
            if os.path.isfile(candidate):
                self.lineedit_template.setText(candidate)
                self._log_line(f"Template defaulted to the survey's own settings: {candidate}")
        if not self.lineedit_out.text().strip():
            self.lineedit_out.setText(os.path.join(survey, "smart_targets"))

    def _browse_template(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select high-magnification acquisition.yaml", self.lineedit_template.text(), "YAML (*.yaml *.yml)"
        )
        if path:
            self.lineedit_template.setText(path)

    def _browse_out(self):
        path = QFileDialog.getExistingDirectory(self, "Select output folder", self.lineedit_out.text())
        if path:
            self.lineedit_out.setText(path)

    def _use_last_acquisition(self):
        controller = self.multipointController
        base = getattr(controller, "base_path", None)
        experiment = getattr(controller, "experiment_ID", None)
        if base and experiment:
            self.lineedit_input.setText(os.path.join(base, experiment))
            self._suggest_outputs()
        else:
            QMessageBox.information(self, "No Acquisition", "No acquisition has been run in this session yet.")

    # ---------------------------------------------------------- analysis

    def _log_line(self, text: str):
        self.log_view.appendPlainText(text)

    def _set_outputs_enabled(self, enabled: bool):
        self.btn_save.setEnabled(enabled)
        self.btn_send.setEnabled(enabled)

    def _start_analysis(self):
        acq_dir = self.lineedit_input.text().strip()
        if not acq_dir or not os.path.isdir(acq_dir):
            QMessageBox.warning(self, "No Survey", "Select a completed acquisition folder first.")
            return
        if self._worker is not None and self._worker.isRunning():
            return

        self._set_outputs_enabled(False)
        self.table_targets.setRowCount(0)
        self.btn_analyze.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.label_summary.setText("Analysing...")
        self._log_line(f"Analysing {acq_dir} with {self.combobox_analyzer.currentText()}")

        self._worker = _AnalysisWorker(
            {
                "acq_dir": acq_dir,
                "analyzer_name": self.combobox_analyzer.currentText(),
                "params": self._collect_params(),
                "carry_z": self.checkbox_carry_z.isChecked(),
                "sweep_z": self.checkbox_sweep_z.isChecked(),
                "interpolate_z": self.checkbox_interpolate_z.isChecked(),
            },
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_analysis_done)
        self._worker.failed.connect(self._on_analysis_failed)
        self._worker.start()

    def _on_progress(self, done: int, total: int):
        self.progress.setRange(0, total)
        self.progress.setValue(done)

    def _on_analysis_failed(self, message: str):
        self.progress.setVisible(False)
        self.btn_analyze.setEnabled(True)
        self.label_summary.setText("Analysis failed.")
        self._log_line(f"ERROR: {message}")
        QMessageBox.critical(self, "Analysis Failed", message)

    def _on_analysis_done(self, result):
        self.progress.setVisible(False)
        self.btn_analyze.setEnabled(True)
        self._result = result
        self._targets = list(result.targets)

        self.table_targets.setRowCount(len(self._targets))
        for row, t in enumerate(self._targets):
            source = t.source_region
            best_z = t.metrics.get("best_z_level")
            if best_z is not None:
                source += f"  z{best_z}"
                if t.metrics.get("z_interpolated"):
                    source += "*"
            if not t.measured:
                source += "  (inferred)"
            cells = [
                t.label or f"{row}",
                f"{t.x_mm:.4f}",
                f"{t.y_mm:.4f}",
                "-" if t.z_mm is None else f"{t.z_mm:.4f}",
                f"{t.score:.4g}",
                source,
            ]
            for col, text in enumerate(cells):
                self.table_targets.setItem(row, col, QTableWidgetItem(text))

        inferred = sum(1 for t in self._targets if not t.measured)
        summary = (
            f"{len(result.fovs)} FOV(s) analysed -> {len(self._targets)} target(s), "
            f"{result.measured_count} measured, {inferred} inferred."
        )
        if self.checkbox_sweep_z.isChecked() and self._targets:
            planes = self._targets[0].metrics.get("planes_examined")
            levels = sorted({t.metrics.get("best_z_level") for t in self._targets if "best_z_level" in t.metrics})
            if planes:
                interpolated = sum(1 for t in self._targets if t.metrics.get("z_interpolated"))
                summary += (
                    f" Best focus chosen from {planes} plane(s); z levels used: {levels}; "
                    f"{interpolated} of {len(self._targets)} Z values interpolated between planes (*)."
                )
        self.label_summary.setText(summary)
        for message in result.errors:
            self._log_line(f"warning: {message}")

        if self.checkbox_overlay.isChecked() and self._targets:
            out_base = self.lineedit_out.text().strip() or os.path.join(
                self.lineedit_input.text().strip(), "smart_analysis"
            )
            try:
                written = pipeline.save_overlays(
                    self.lineedit_input.text().strip(), result, os.path.join(out_base, "overlays")
                )
                self._log_line(f"Wrote {len(written)} overlay(s) to {os.path.join(out_base, 'overlays')}")
            except Exception as e:
                self._log_line(f"warning: could not write overlays: {e}")

        self._suggest_outputs()
        self._set_outputs_enabled(bool(self._targets))

    # ------------------------------------------------------------ output

    def _write_targets(self) -> Optional[Dict[str, Any]]:
        if not self._targets:
            QMessageBox.warning(self, "No Targets", "Run an analysis first.")
            return None
        template = self.lineedit_template.text().strip()
        out_dir = self.lineedit_out.text().strip()
        if not template or not os.path.isfile(template):
            QMessageBox.warning(
                self,
                "No Template",
                "Choose a template acquisition.yaml.\n\n"
                "It supplies the channels, exposure, Z-stack and autofocus for the new "
                "acquisition; only the coordinates come from the analysis.\n\n"
                "Any previous acquisition's acquisition.yaml will do -- including the "
                "survey you just analysed, if you want to re-image it with the same settings.",
            )
            return None
        if not out_dir:
            QMessageBox.warning(self, "No Output Folder", "Select a folder to write the acquisition into.")
            return None
        try:
            summary = emit.write_targets(self._targets, template, out_dir)
        except Exception as e:
            QMessageBox.critical(self, "Could Not Write Targets", str(e))
            return None

        self._log_line(f"Wrote {summary['written']} position(s) to {summary['acquisition_yaml']}")
        if summary["dropped_out_of_limits"]:
            self._log_line(f"warning: dropped {summary['dropped_out_of_limits']} target(s) outside the stage limits")
        for message in summary.get("notes", []):
            self._log_line(f"note: {message}")
        return summary

    def _save_targets(self):
        summary = self._write_targets()
        if summary:
            QMessageBox.information(
                self,
                "Targets Saved",
                f"Wrote {summary['written']} position(s) to:\n{summary['acquisition_yaml']}\n"
                f"{summary['coordinates_csv']}",
            )

    def _destination(self):
        """``(widget_type, widget, tab name)`` the current template's targets are sent to.

        The template decides (``emit.template_widget_type``): a ``record_zstack``
        file runs in Record + Z-Stack's Load Coordinates mode, everything else in
        Flexible Multipoint. A blank or unreadable path counts as flexible.
        """
        path = self.lineedit_template.text().strip()
        widget_type = "flexible"
        if path:
            try:
                widget_type = emit.template_widget_type(emit.load_template(path))
            except Exception:
                widget_type = "flexible"
        if widget_type == "record_zstack":
            return widget_type, self.recordZStackWidget, "Record + Z-Stack"
        return "flexible", self.flexibleMultiPointWidget, "Flexible Multipoint"

    def _update_send_button(self, *_args):
        _widget_type, _widget, name = self._destination()
        self.btn_send.setText(f"Send to {name}")
        self.btn_send.setToolTip(
            f"Write the acquisition and load it into the {name} tab.\n"
            "The template's widget type picks the tab: a Record + Z-Stack acquisition.yaml\n"
            "goes to Record + Z-Stack (Load Coordinates), anything else to Flexible Multipoint."
        )

    def _send_to_acquisition_tab(self):
        """Write the acquisition, then load it into the tab its template belongs to."""
        _widget_type, widget, name = self._destination()
        if widget is None:
            QMessageBox.warning(self, "Unavailable", f"{name} is not enabled in this configuration.")
            return

        summary = self._write_targets()
        if not summary:
            return

        # Switch to the acquisition tab before loading. The main window routes
        # acquisition start/stop through recordTabWidget.currentWidget(), so
        # leaving this tab focused would break a subsequent workflow run.
        if self.tab_widget is not None:
            index = self.tab_widget.indexOf(widget)
            if index >= 0:
                self.tab_widget.setCurrentIndex(index)

        loader = getattr(widget, "_load_acquisition_yaml", None)
        if loader is None:
            QMessageBox.warning(self, "Unavailable", f"This build's {name} cannot load acquisition YAML.")
            return

        if loader(summary["acquisition_yaml"]):
            self.signal_targets_ready.emit(summary["acquisition_yaml"])
            self._log_line(f"Loaded {summary['written']} position(s) into {name}.")
        else:
            self._log_line(f"{name} declined the generated acquisition.yaml.")

    # Former name, kept for callers that predate the type-aware routing.
    _send_to_flexible = _send_to_acquisition_tab

    # ----------------------------------------------------------- cleanup

    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(5000)
        super().closeEvent(event)
