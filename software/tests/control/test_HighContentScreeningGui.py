import pytest

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


def _build_hcs_gui(qtbot, monkeypatch):
    """Build a simulated HCS GUI, auto-confirming the exit dialog on teardown."""

    def confirm_exit(parent, title, text, *args, **kwargs):
        if title == "Confirm Exit":
            return QMessageBox.Yes
        raise RuntimeError(f"Unexpected QMessageBox: {title} - {text}")

    monkeypatch.setattr(QMessageBox, "question", confirm_exit)

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    return win


def test_wellplate_widget_loads_v2_wells_coverage_yaml(qtbot, monkeypatch):
    """Schema v2: a wells+coverage yaml selects the named wells (range-expanded)
    and applies the coverage scan settings to the wellplate widget controls."""
    from control.acquisition_yaml_loader import AcquisitionYAMLData

    win = _build_hcs_gui(qtbot, monkeypatch)
    widget = win.wellplateMultiPointWidget
    assert widget.well_selection_widget is not None

    yaml_data = AcquisitionYAMLData(
        widget_type="wellplate",
        wells="A1:A2",
        overlap_percent=10.0,
        fov_pattern={
            "type": "coverage",
            "scan_size_mm": 2.5,
            "overlap_percent": 15.0,
            "shape": "Circle",
        },
    )

    widget._apply_yaml_settings(yaml_data)

    assert widget.entry_scan_size.value() == pytest.approx(2.5)
    # overlap comes from the pattern (15.0), not the flat default (10.0)
    assert widget.entry_overlap.value() == pytest.approx(15.0)
    assert widget.combobox_shape.currentText() == "Circle"
    assert widget.checkbox_xy.isChecked()
    # "A1:A2" range-expands to two wells -> two selected cells
    assert len(widget.well_selection_widget.selectedItems()) == 2


def test_wellplate_widget_rejects_noncoverage_pattern(qtbot, monkeypatch):
    """Schema v2: a non-coverage fov_pattern is not representable in the GUI yet,
    so the load is aborted with a clear warning and no wells are selected."""
    from control.acquisition_yaml_loader import AcquisitionYAMLData

    warnings = []

    def capture_warning(parent, title, text, *args, **kwargs):
        warnings.append((title, text))
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "warning", capture_warning)

    win = _build_hcs_gui(qtbot, monkeypatch)
    widget = win.wellplateMultiPointWidget
    assert widget.well_selection_widget is not None
    widget.well_selection_widget.clearSelection()

    yaml_data = AcquisitionYAMLData(
        widget_type="wellplate",
        wells="A1",
        fov_pattern={"type": "centered_grid", "nx": 3, "ny": 3, "overlap_percent": 10.0},
    )

    widget._apply_yaml_settings(yaml_data)

    assert len(warnings) == 1
    assert warnings[0][0] == "Pattern Not Supported in GUI"
    assert "centered_grid" in warnings[0][1]
    # Load aborted before selecting wells
    assert len(widget.well_selection_widget.selectedItems()) == 0


def test_load_acquisition_yaml_noncoverage_returns_false(qtbot, monkeypatch, tmp_path):
    """Full-path regression: a non-coverage v2 yaml aborts inside _apply_yaml_settings,
    and _load_acquisition_yaml must propagate that abort as False so workflow-runner
    callers do not act on a false success."""
    warnings = []

    def capture_warning(parent, title, text, *args, **kwargs):
        warnings.append((title, text))
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "warning", capture_warning)

    win = _build_hcs_gui(qtbot, monkeypatch)
    widget = win.wellplateMultiPointWidget

    yaml_path = tmp_path / "noncoverage.yaml"
    yaml_path.write_text(
        "acquisition:\n"
        "  widget_type: wellplate\n"
        "wellplate_scan:\n"
        '  wells: "A1"\n'
        "  fov_pattern:\n"
        "    type: centered_grid\n"
        "    nx: 3\n"
        "    ny: 3\n"
        "    overlap_percent: 10.0\n"
    )

    assert widget._load_acquisition_yaml(str(yaml_path)) is False
    assert len(warnings) == 1
    assert warnings[0][0] == "Pattern Not Supported in GUI"


def test_load_acquisition_yaml_coverage_returns_true(qtbot, monkeypatch, tmp_path):
    """Full-path regression: a wells+coverage v2 yaml loads successfully via the full
    _load_acquisition_yaml path and returns True."""

    def fail_on_warning(parent, title, text, *args, **kwargs):
        raise RuntimeError(f"Unexpected QMessageBox.warning: {title} - {text}")

    monkeypatch.setattr(QMessageBox, "warning", fail_on_warning)

    win = _build_hcs_gui(qtbot, monkeypatch)
    widget = win.wellplateMultiPointWidget

    yaml_path = tmp_path / "coverage.yaml"
    yaml_path.write_text(
        "acquisition:\n"
        "  widget_type: wellplate\n"
        "wellplate_scan:\n"
        '  wells: "A1:A2"\n'
        "  overlap_percent: 10.0\n"
        "  fov_pattern:\n"
        "    type: coverage\n"
        "    scan_size_mm: 2.5\n"
        "    overlap_percent: 15.0\n"
        "    shape: Circle\n"
    )

    assert widget._load_acquisition_yaml(str(yaml_path)) is True
    assert widget.checkbox_xy.isChecked()
    assert len(widget.well_selection_widget.selectedItems()) == 2
