"""Tests for the "Relative" z-stack mode -- a stack centred on the focus plane.

Covers both halves of the feature:
  * the GUI arithmetic and control enablement (widgets.py), and
  * the worker's "FROM CENTER" stack geometry (multi_point_worker.py), which the GUI
    reaches via MultiPointController.set_z_stacking_config(1).
"""

import pytest

import control._def
import control.gui_hcs
import control.microscope
import control.widgets
from control.core.multi_point_worker import MultiPointWorker
from control.widgets import (
    Z_MODE_FROM_BOTTOM,
    Z_MODE_RELATIVE,
    Z_MODE_SET_RANGE,
    relative_stack_Nz,
    z_mode_to_stacking_index,
)
from qtpy.QtWidgets import QMessageBox


# ---------------------------------------------------------------------------
# Pure arithmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "z_range_um, dz_um, expected_nz",
    [
        (20.0, 3.0, 9),  # ceil(20/6)=4 -> 9 slices, covering 24 um
        (20.0, 5.0, 5),  # ceil(20/10)=2 -> 5 slices, covering exactly 20 um
        (20.0, 10.0, 3),
        (0.1, 10.0, 3),  # a range far below one step still yields the focus plane +/- 1
        (10.0, 1.0, 11),
    ],
)
def test_relative_stack_Nz(z_range_um, dz_um, expected_nz):
    assert relative_stack_Nz(z_range_um, dz_um) == expected_nz


def test_relative_stack_Nz_is_always_odd():
    """An odd Nz is what makes the stack symmetric about the focus plane."""
    for z_range_um in (0.5, 1.0, 7.3, 20.0, 101.7):
        for dz_um in (0.3, 1.0, 1.5, 3.0, 25.0):
            assert relative_stack_Nz(z_range_um, dz_um) % 2 == 1


def test_relative_stack_Nz_covers_at_least_the_requested_range():
    """Up to a tenth of a step of slack -- see _RELATIVE_STACK_RATIO_TOLERANCE."""
    for z_range_um in (0.5, 7.3, 20.0, 101.7):
        for dz_um in (0.3, 1.5, 3.0):
            nz = relative_stack_Nz(z_range_um, dz_um)
            assert (nz - 1) * dz_um >= z_range_um - 0.1 * dz_um


def test_microstep_quantized_dz_does_not_add_a_slice_pair():
    """set_deltaZ snaps dz to the Z microstep grid; 5.000 um becomes ~4.969.

    A naive ceil() would turn a 20 um / 5 um stack into 7 slices covering 29.8 um.
    """
    assert relative_stack_Nz(20.0, 5.0) == 5
    assert relative_stack_Nz(20.0, 4.968594731414643) == 5


def test_relative_stack_Nz_guards_zero_dz():
    """entry_deltaZ has a minimum of 0, so a mid-edit 0 must not raise."""
    assert relative_stack_Nz(20.0, 0.0) == 1


def test_z_mode_to_stacking_index():
    # 1 == "FROM CENTER", 0 == "FROM BOTTOM" in control._def.Z_STACKING_CONFIG_MAP
    assert control._def.Z_STACKING_CONFIG_MAP[z_mode_to_stacking_index(Z_MODE_RELATIVE)] == "FROM CENTER"
    assert control._def.Z_STACKING_CONFIG_MAP[z_mode_to_stacking_index(Z_MODE_FROM_BOTTOM)] == "FROM BOTTOM"
    assert control._def.Z_STACKING_CONFIG_MAP[z_mode_to_stacking_index(Z_MODE_SET_RANGE)] == "FROM BOTTOM"


# ---------------------------------------------------------------------------
# GUI behaviour
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gui(qapp):
    """One simulated GUI for the whole module.

    Deliberately module-scoped: each HighContentScreeningGui brings up simulated cameras
    and their streaming threads, and building a dozen of them in one pytest process is
    enough to tip Windows into a heap fault.  Every test below sets the z mode it needs
    up-front, so sharing the instance is safe.
    """
    with pytest.MonkeyPatch.context() as mp:

        def confirm_exit(parent, title, text, *args, **kwargs):
            if title == "Confirm Exit":
                return QMessageBox.Yes
            raise RuntimeError(f"Unexpected QMessageBox: {title} - {text}")

        mp.setattr(QMessageBox, "question", confirm_exit)
        # Switching to "Set Range" with laser AF checked re-references the laser AF, which
        # fails (and pops a modal) on simulated hardware.  Pre-existing behavior; a modal
        # here would hang the run, so swallow it.
        mp.setattr(control.widgets, "error_dialog", lambda *args, **kwargs: None)

        scope = control.microscope.Microscope.build_from_global_config(True)
        window = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
        try:
            yield window
        finally:
            window.close()


@pytest.fixture(params=["wellplate", "flexible"])
def z_widget(request, gui):
    """Both multipoint tabs offer Relative and must behave identically."""
    if request.param == "wellplate":
        widget = gui.wellplateMultiPointWidget
        widget.checkbox_z.setChecked(True)
    else:
        widget = gui.flexibleMultiPointWidget
    # Shared GUI: start every test from a known mode rather than the previous test's.
    widget.combobox_z_mode.setCurrentText(Z_MODE_FROM_BOTTOM)
    widget.checkbox_withReflectionAutofocus.setChecked(False)
    return widget


def test_relative_mode_is_offered(z_widget):
    modes = [z_widget.combobox_z_mode.itemText(i) for i in range(z_widget.combobox_z_mode.count())]
    assert modes == [Z_MODE_FROM_BOTTOM, Z_MODE_SET_RANGE, Z_MODE_RELATIVE]


def test_relative_mode_computes_nz_and_keeps_it_read_only(z_widget):
    z_widget.combobox_z_mode.setCurrentText(Z_MODE_RELATIVE)
    z_widget.entry_zRange.setValue(20.0)
    z_widget.entry_deltaZ.setValue(3.0)

    assert z_widget.entry_NZ.value() == 9
    assert not z_widget.entry_NZ.isEnabled()

    # Nz tracks dz changes without the user touching it
    z_widget.entry_deltaZ.setValue(5.0)
    assert z_widget.entry_NZ.value() == 5

    # ...and range changes too
    z_widget.entry_zRange.setValue(40.0)
    assert z_widget.entry_NZ.value() == 9


def test_relative_mode_shows_the_range_entry_only_in_relative(z_widget):
    z_widget.combobox_z_mode.setCurrentText(Z_MODE_RELATIVE)
    assert z_widget.entry_zRange.isVisibleTo(z_widget)

    z_widget.combobox_z_mode.setCurrentText(Z_MODE_FROM_BOTTOM)
    assert not z_widget.entry_zRange.isVisibleTo(z_widget)
    # Nz goes back to being the user's to set
    assert z_widget.entry_NZ.isEnabled()


def test_relative_mode_keeps_laser_af_available(z_widget):
    """The whole point of Relative is a mid-sample laser AF reference.

    "Set Range" force-unchecks and disables laser AF; Relative must not.
    """
    z_widget.checkbox_withReflectionAutofocus.setChecked(True)

    # (Only the wellplate tab also force-unchecks it; both disable it.)
    z_widget.combobox_z_mode.setCurrentText(Z_MODE_SET_RANGE)
    assert not z_widget.checkbox_withReflectionAutofocus.isEnabled()

    z_widget.checkbox_withReflectionAutofocus.setChecked(True)
    z_widget.combobox_z_mode.setCurrentText(Z_MODE_RELATIVE)
    assert z_widget.checkbox_withReflectionAutofocus.isEnabled()
    assert z_widget.checkbox_withReflectionAutofocus.isChecked()


def test_zero_dz_does_not_raise_in_relative_mode(z_widget):
    z_widget.combobox_z_mode.setCurrentText(Z_MODE_RELATIVE)
    z_widget.entry_deltaZ.setValue(0.0)  # must not ZeroDivisionError
    z_widget.entry_zRange.setValue(15.0)


def test_switching_modes_repeatedly_does_not_stack_connections(z_widget):
    """Duplicate valueChanged connections would recompute Nz N times per edit."""
    for _ in range(4):
        z_widget.combobox_z_mode.setCurrentText(Z_MODE_RELATIVE)
        z_widget.combobox_z_mode.setCurrentText(Z_MODE_FROM_BOTTOM)

    z_widget.combobox_z_mode.setCurrentText(Z_MODE_RELATIVE)
    z_widget.entry_zRange.setValue(20.0)
    z_widget.entry_deltaZ.setValue(3.0)
    assert z_widget.entry_NZ.value() == 9


def test_from_bottom_mode_leaves_nz_editable(z_widget):
    z_widget.combobox_z_mode.setCurrentText(Z_MODE_FROM_BOTTOM)
    assert z_widget.entry_NZ.isEnabled()
    z_widget.entry_NZ.setValue(7)
    assert z_widget.entry_NZ.value() == 7


def test_relative_mode_sets_from_center_on_the_controller(z_widget, monkeypatch):
    """The whole feature hinges on this call -- nothing else ever set the stacking config."""
    recorded = {}
    monkeypatch.setattr(
        z_widget.multipointController,
        "set_z_stacking_config",
        lambda index: recorded.update(index=index),
    )

    z_widget.combobox_z_mode.setCurrentText(Z_MODE_RELATIVE)
    z_widget.multipointController.set_z_stacking_config(z_mode_to_stacking_index(Z_MODE_RELATIVE))
    assert control._def.Z_STACKING_CONFIG_MAP[recorded["index"]] == "FROM CENTER"


# ---------------------------------------------------------------------------
# Worker stack geometry ("FROM CENTER")
# ---------------------------------------------------------------------------


class _RecordingStage:
    """Records absolute z after each relative move, in mm."""

    def __init__(self, z_mm=0.0):
        self.z_mm = z_mm
        self.moves = []

    def move_z(self, delta_mm):
        self.z_mm += delta_mm
        self.moves.append(delta_mm)


class _ZStackStub:
    """MultiPointWorker-shaped stub exercising the stack geometry in isolation."""

    def __init__(self, NZ, deltaZ_mm, z_stacking_config, start_z_mm=5.0):
        self.NZ = NZ
        self.deltaZ = deltaZ_mm
        self.z_stacking_config = z_stacking_config
        self.use_piezo = False
        self.stage = _RecordingStage(start_z_mm)

    def _sleep(self, _seconds):
        pass

    _center_offset_steps = MultiPointWorker._center_offset_steps
    _move_z_for_stack_actuator = MultiPointWorker._move_z_for_stack_actuator
    prepare_z_stack = MultiPointWorker.prepare_z_stack
    move_z_for_stack = MultiPointWorker.move_z_for_stack
    move_z_back_after_stack = MultiPointWorker.move_z_back_after_stack


def _run_stack(NZ, deltaZ_mm, z_stacking_config, start_z_mm=5.0):
    """Walk a full stack the way acquire_at_position does; return the z of each slice."""
    worker = _ZStackStub(NZ, deltaZ_mm, z_stacking_config, start_z_mm)
    if NZ > 1:
        worker.prepare_z_stack()
    slice_zs = []
    for z_level in range(NZ):
        slice_zs.append(worker.stage.z_mm)
        if z_level < NZ - 1:
            worker.move_z_for_stack()
    if NZ > 1:
        worker.move_z_back_after_stack()
    return slice_zs, worker.stage.z_mm


def test_from_center_stack_is_symmetric_about_the_focus_plane():
    focus_z = 5.0
    dz = 0.003  # mm
    slice_zs, _ = _run_stack(NZ=5, deltaZ_mm=dz, z_stacking_config="FROM CENTER", start_z_mm=focus_z)

    assert len(slice_zs) == 5
    assert slice_zs[0] == pytest.approx(focus_z - 2 * dz)
    assert slice_zs[2] == pytest.approx(focus_z)  # the middle slice IS the focus plane
    assert slice_zs[-1] == pytest.approx(focus_z + 2 * dz)


def test_from_center_stack_returns_to_the_focus_plane():
    """Any residual here accumulates across every FOV of the acquisition."""
    focus_z = 5.0
    dz = 0.003
    for nz in (3, 5, 9, 21):
        _, final_z = _run_stack(NZ=nz, deltaZ_mm=dz, z_stacking_config="FROM CENTER", start_z_mm=focus_z)
        assert final_z == pytest.approx(focus_z), f"drift after NZ={nz}"


def test_from_bottom_stack_is_unchanged():
    """FROM BOTTOM must still start at the focus plane and run upward."""
    focus_z = 5.0
    dz = 0.003
    slice_zs, final_z = _run_stack(NZ=5, deltaZ_mm=dz, z_stacking_config="FROM BOTTOM", start_z_mm=focus_z)

    assert slice_zs[0] == pytest.approx(focus_z)
    assert slice_zs[-1] == pytest.approx(focus_z + 4 * dz)
    assert final_z == pytest.approx(focus_z)


def test_center_offset_steps():
    stub = _ZStackStub(NZ=9, deltaZ_mm=0.003, z_stacking_config="FROM CENTER")
    assert stub._center_offset_steps() == 4

    stub = _ZStackStub(NZ=9, deltaZ_mm=0.003, z_stacking_config="FROM BOTTOM")
    assert stub._center_offset_steps() == 0
