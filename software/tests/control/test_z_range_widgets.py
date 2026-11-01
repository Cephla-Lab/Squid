"""Set Z-range in the Wellplate tab follows the "Piezo Z-Stack" checkbox (the Flexible tab has no Set Z-range).

Checked (always, on a piezo-only scope): the entries are piezo positions in µm, limited to the piezo's
travel, and the stepper holds where it is. Unchecked: stepper z in µm, as before.

The widget methods run on a stand-in object with real Qt spin boxes and a real PiezoStage.
"""

import functools
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from qtpy.QtWidgets import QCheckBox, QDoubleSpinBox

import control._def
import squid.abc
from control.piezo import PiezoStage
from control.widgets import WellplateMultiPointWidget

WIDGETS = [WellplateMultiPointWidget]


class _FakeStage:
    def __init__(self, z_mm):
        self.z_mm = z_mm

    def get_pos(self):
        return squid.abc.Pos(x_mm=10.0, y_mm=20.0, z_mm=self.z_mm, theta_rad=None)

    def move_z_to(self, z_mm):
        self.z_mm = z_mm


class _Controller:
    def __init__(self, piezo):
        self.piezo = piezo
        self.z_range = None
        self.piezo_z_range_um = None

    def set_z_range(self, minZ, maxZ):
        self.z_range = (minZ, maxZ)

    def set_piezo_z_range_um(self, piezo_z_range_um):
        self.piezo_z_range_um = piezo_z_range_um


def _fake(cls, use_piezo, stage_z_mm=1.0, piezo_um=20.0):
    piezo = PiezoStage(MagicMock(), {"OBJECTIVE_PIEZO_RANGE_UM": 300})
    piezo.move_to(piezo_um)
    fake = SimpleNamespace(
        stage=_FakeStage(stage_z_mm),
        multipointController=_Controller(piezo),
        checkbox_usePiezo=QCheckBox(),
        entry_minZ=QDoubleSpinBox(),
        entry_maxZ=QDoubleSpinBox(),
        _reset_reflection_af_reference=MagicMock(),
    )
    fake.checkbox_usePiezo.setChecked(use_piezo)
    for name in ("_stack_z_um", "_reset_z_range_entries", "_push_set_z_range", "_move_stack_z_to_um"):
        setattr(fake, name, functools.partial(getattr(cls, name), fake))
    fake._reset_z_range_entries()  # what the widget does when it builds the entries
    return fake


@pytest.mark.parametrize("cls", WIDGETS)
@pytest.mark.parametrize("use_piezo, expected_um", [(True, (20.0, 26.0)), (False, (1000.0, 1004.0))])
def test_set_min_and_max_read_the_motor_the_stack_runs_on(qtbot, cls, use_piezo, expected_um):
    fake = _fake(cls, use_piezo)

    cls.set_z_min(fake)
    fake.multipointController.piezo.move_to(26.0)  # focus up with the piezo...
    fake.stage.z_mm = 1.004  # ...or with the stepper
    cls.set_z_max(fake)

    assert (fake.entry_minZ.value(), fake.entry_maxZ.value()) == expected_um


@pytest.mark.parametrize("cls", WIDGETS)
def test_piezo_entries_are_limited_to_the_piezo_travel(qtbot, cls):
    fake = _fake(cls, use_piezo=True)
    for entry in (fake.entry_minZ, fake.entry_maxZ):
        assert (entry.minimum(), entry.maximum()) == (0.0, 300.0)


@pytest.mark.parametrize("cls", WIDGETS)
def test_stepper_entries_keep_the_stepper_software_limits(qtbot, cls):
    fake = _fake(cls, use_piezo=False)
    limits = control._def.SOFTWARE_POS_LIMIT
    for entry in (fake.entry_minZ, fake.entry_maxZ):
        assert (entry.minimum(), entry.maximum()) == (limits.Z_NEGATIVE * 1000, limits.Z_POSITIVE * 1000)


@pytest.mark.parametrize("cls", WIDGETS)
def test_switching_to_the_piezo_restarts_the_range_at_the_piezo_position(qtbot, cls):
    fake = _fake(cls, use_piezo=False)
    assert fake.entry_minZ.value() == 1000.0  # a stepper value

    fake.checkbox_usePiezo.setChecked(True)
    fake._reset_z_range_entries()  # connected to the checkbox's toggled signal

    assert (fake.entry_minZ.value(), fake.entry_maxZ.value()) == (20.0, 20.0)


@pytest.mark.parametrize("cls", WIDGETS)
def test_a_piezo_range_goes_to_the_controller_with_the_stepper_held(qtbot, cls):
    fake = _fake(cls, use_piezo=True)
    fake.entry_minZ.setValue(20.0)
    fake.entry_maxZ.setValue(26.0)

    fake._push_set_z_range()

    assert fake.multipointController.piezo_z_range_um == (20.0, 26.0)
    assert fake.multipointController.z_range == (1.0, 1.0)


@pytest.mark.parametrize("cls", WIDGETS)
def test_a_stepper_range_goes_to_the_controller_in_mm_and_clears_a_piezo_range(qtbot, cls):
    fake = _fake(cls, use_piezo=False)
    fake.multipointController.piezo_z_range_um = (20.0, 26.0)  # from an earlier piezo run
    fake.entry_minZ.setValue(1000.0)
    fake.entry_maxZ.setValue(1004.0)

    fake._push_set_z_range()

    assert fake.multipointController.z_range == pytest.approx((1.0, 1.004))
    assert fake.multipointController.piezo_z_range_um is None


@pytest.mark.parametrize("use_piezo", [True, False])
def test_wellplate_go_to_moves_the_motor_the_stack_runs_on(qtbot, use_piezo):
    fake = _fake(WellplateMultiPointWidget, use_piezo)
    fake.entry_minZ.setValue(40.0 if use_piezo else 1500.0)

    WellplateMultiPointWidget.goto_z_min(fake)

    if use_piezo:
        assert fake.multipointController.piezo.position == 40.0
        assert fake.stage.z_mm == 1.0
    else:
        assert fake.stage.z_mm == pytest.approx(1.5)
        assert fake.multipointController.piezo.position == 20.0


@pytest.mark.parametrize("cls", WIDGETS)
def test_snap_clears_a_piezo_range_left_by_an_earlier_acquisition(cls):
    # Snap runs on the stepper (use_piezo False); a leftover piezo range would make the start check refuse it.
    fake = MagicMock()
    cls.on_snap_images(fake)
    fake.multipointController.set_piezo_z_range_um.assert_called_once_with(None)


@pytest.mark.parametrize("cls, range_off", [(WellplateMultiPointWidget, "wp")])
def test_start_without_set_z_range_clears_a_piezo_range(cls, range_off):
    fake = MagicMock()
    if range_off == "flexible":
        fake.checkbox_set_z_range.isChecked.return_value = False
    else:
        fake.combobox_z_mode.currentText.return_value = "From Bottom"
    fake.checkbox_useFocusMap.isChecked.return_value = False

    cls.configure_controller_from_ui(fake)

    fake.multipointController.set_piezo_z_range_um.assert_called_once_with(None)


def test_wellplate_does_not_restore_a_range_recorded_on_the_other_motor(qtbot):
    from qtpy.QtWidgets import QComboBox, QSpinBox

    fake = _fake(WellplateMultiPointWidget, use_piezo=True)
    fake.entry_deltaZ, fake.entry_NZ, fake.combobox_z_mode = QDoubleSpinBox(), QSpinBox(), QComboBox()
    fake.combobox_z_mode.addItems(["From Bottom", "Set Range"])
    fake.stored_z_params = {"dz": None, "nz": None, "z_min": None, "z_max": None, "z_mode": "From Bottom"}
    fake.entry_minZ.setValue(20.0)
    fake.entry_maxZ.setValue(26.0)
    WellplateMultiPointWidget.store_z_parameters(fake)  # Z unchecked with a piezo range

    fake.checkbox_usePiezo.setChecked(False)  # the stack moves to the stepper
    WellplateMultiPointWidget._on_stack_motor_changed(fake)  # connected to the checkbox's toggled signal
    WellplateMultiPointWidget.restore_z_parameters(fake)  # Z checked again

    assert (fake.entry_minZ.value(), fake.entry_maxZ.value()) == (1000.0, 1000.0)
