import control._def

import control.gui_hcs
from qtpy.QtWidgets import QMessageBox

import control.microscope


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
    scope_with = control.microscope.Microscope.build_from_global_config(True)
    with_piezo = control.gui_hcs.HighContentScreeningGui(microscope=scope_with, is_simulation=True)
    qtbot.add_widget(with_piezo)

    control._def.HAS_OBJECTIVE_PIEZO = False
    scope_without = control.microscope.Microscope.build_from_global_config(True)
    without_piezo = control.gui_hcs.HighContentScreeningGui(microscope=scope_without, is_simulation=True)
    qtbot.add_widget(without_piezo)


def test_image_display_signals_connected_once(qtbot, monkeypatch):
    """Regression: make_connections and makeNapariConnections both used to wire the
    non-Napari image-display signals, causing slots to fire twice per click/scroll."""

    def confirm_exit(parent, title, text, *args, **kwargs):
        if title == "Confirm Exit":
            return QMessageBox.Yes
        raise RuntimeError(f"Unexpected QMessageBox: {title} - {text}")

    monkeypatch.setattr(QMessageBox, "question", confirm_exit)

    # Patch slots at the class level *before* construction so signal-slot bindings
    # made inside __init__ resolve to these counters.
    z_calls = []
    click_calls = []
    monkeypatch.setattr(
        control.gui_hcs.HighContentScreeningGui, "move_z_from_scroll", lambda self, delta_um: z_calls.append(delta_um)
    )
    monkeypatch.setattr(
        control.gui_hcs.HighContentScreeningGui,
        "move_from_click_image",
        lambda self, *args, **kwargs: click_calls.append(args),
    )

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    win.imageDisplayWindow.signal_z_um_delta.emit(1.0)
    win.imageDisplayWindow.image_click_coordinates.emit(0.0, 0.0, 0, 0)

    assert len(z_calls) == 1, f"signal_z_um_delta wired {len(z_calls)} times, expected 1"
    assert len(click_calls) == 1, f"image_click_coordinates wired {len(click_calls)} times, expected 1"


def test_record_zstack_tab_keeps_well_selector_visible(qtbot, monkeypatch):
    """Switching to the Record + Z-Stack tab must not hide the well selector dock.

    The tab's own validation requires selected wells, so onTabChanged has to
    treat it like Wellplate Multipoint when deciding well-selector visibility.
    Also exercises the tab switch end-to-end, which duck-calls
    emit_selected_channels() on the widget.
    """

    def confirm_exit(parent, title, text, *args, **kwargs):
        if title == "Confirm Exit":
            return QMessageBox.Yes
        raise RuntimeError(f"Unexpected QMessageBox: {title} - {text}")

    monkeypatch.setattr(QMessageBox, "question", confirm_exit)
    # The tab is gated by ENABLE_RECORDING; force it on regardless of the local INI.
    monkeypatch.setattr(control.gui_hcs, "ENABLE_RECORDING", True)

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    tabw = win.recordTabWidget
    labels = [tabw.tabText(i) for i in range(tabw.count())]
    assert "Record + Z-Stack" in labels

    tabw.setCurrentIndex(labels.index("Record + Z-Stack"))

    assert not win.dock_wellSelection.isHidden(), "well selector dock must stay available on the Record + Z-Stack tab"


def test_record_zstack_acquisition_finish_keeps_well_selector(qtbot, monkeypatch):
    """R6: toggleAcquisitionStart(False) after a Record + Z-Stack acquisition
    must not hide the well selector dock (only the wellplate branch kept it)."""

    def confirm_exit(parent, title, text, *args, **kwargs):
        if title == "Confirm Exit":
            return QMessageBox.Yes
        raise RuntimeError(f"Unexpected QMessageBox: {title} - {text}")

    monkeypatch.setattr(QMessageBox, "question", confirm_exit)
    monkeypatch.setattr(control.gui_hcs, "ENABLE_RECORDING", True)

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    tabw = win.recordTabWidget
    labels = [tabw.tabText(i) for i in range(tabw.count())]
    tabw.setCurrentIndex(labels.index("Record + Z-Stack"))
    assert not win.dock_wellSelection.isHidden()

    win.toggleAcquisitionStart(True)  # acquisition starts: selector hides
    assert win.dock_wellSelection.isHidden()
    win.toggleAcquisitionStart(False)  # acquisition ends: selector must return
    assert not win.dock_wellSelection.isHidden(), "well selector not restored after Record+Z-Stack acquisition"
