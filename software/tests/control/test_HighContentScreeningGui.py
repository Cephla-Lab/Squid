import control._def

import control.gui_hcs
from PyQt5.QtWidgets import QMessageBox


def test_create_simulated_hcs_with_or_without_piezo(qtbot, monkeypatch):
    # This just tests to make sure we can successfully create a simulated hcs gui with or without
    # the piezo objective.

    # We need to close the dialog shown on GUI shut down or it will hang forever.
    def confirm_exit(parent, title, text, *args, **kwargs):
        if title == "Confirm Exit":
            return QMessageBox.Yes
        raise RuntimeError(f"Unexpected QMessageBox: {title} - {text}")

    monkeypatch.setattr(QMessageBox, "question", confirm_exit)

    control._def.HAS_OBJECTIVE_PIEZO = True
    with_piezo = control.gui_hcs.HighContentScreeningGui(is_simulation=True)
    qtbot.add_widget(with_piezo)

    control._def.HAS_OBJECTIVE_PIEZO = False
    without_piezo = control.gui_hcs.HighContentScreeningGui(is_simulation=True)
    qtbot.add_widget(without_piezo)
