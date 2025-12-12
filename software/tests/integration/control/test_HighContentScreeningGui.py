import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import _def

# Skip this GUI integration in headless/offscreen environments to avoid Qt aborts.
if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
    pytest.skip("Skipping GUI integration in offscreen/headless mode", allow_module_level=True)

import ui.main_window
from PyQt5.QtWidgets import QMessageBox

import mcs.microscope
from squid.application import ApplicationContext


def test_create_simulated_hcs_with_or_without_piezo(qtbot, monkeypatch):
    # This just tests to make sure we can successfully create a simulated hcs gui with or without
    # the piezo objective.

    # We need to close the dialog shown on GUI shut down or it will hang forever.
    def confirm_exit(parent, title, text, *args, **kwargs):
        if title == "Confirm Exit":
            return QMessageBox.Yes
        raise RuntimeError(f"Unexpected QMessageBox: {title} - {text}")

    monkeypatch.setattr(QMessageBox, "question", confirm_exit)

    original_has_objective_piezo = control._def.HAS_OBJECTIVE_PIEZO
    contexts = []

    def build_gui_with_context(has_piezo: bool):
        control._def.HAS_OBJECTIVE_PIEZO = has_piezo
        ctx = ApplicationContext(simulation=True)
        gui = ctx.create_gui()
        qtbot.add_widget(gui)
        contexts.append(ctx)

    try:
        build_gui_with_context(True)
        build_gui_with_context(False)
    finally:
        control._def.HAS_OBJECTIVE_PIEZO = original_has_objective_piezo
        for ctx in contexts:
            ctx.shutdown()
