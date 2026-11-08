"""Set Z-range in the Wellplate tab follows the "Piezo Z-Stack" checkbox (the Flexible tab has no Set Z-range).

Checked (always, on a piezo-only scope): the entries are piezo positions in µm, limited to the piezo's
travel, and the stepper holds where it is. Unchecked: stepper z in µm, as before.

With laser AF checked, Set Range is relative to the laser AF reference plane: the entries are µm from the
reference, measured (Set) and replayed (Go To) through the laser AF controller's anchor rule.

The widget methods run on a stand-in object with real Qt spin boxes and a real PiezoStage.
"""

import functools
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from qtpy.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QLabel, QSpinBox, QWidget

import control._def
import control.acquisition_yaml_loader
import control.widgets
import squid.abc
from control.core.laser_auto_focus_controller import LOCK_ON_FAILED_MESSAGE, RelativeZError, RelativeZMeasurement
from control.piezo import PiezoStage
from control.widgets import WellplateMultiPointWidget

WIDGETS = [WellplateMultiPointWidget]
SOFTWARE_POS_LIMIT_Z_UM = (
    control._def.SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000,
    control._def.SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000,
)


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
        self.laserAutoFocusController = None
        self.z_range = None
        self.piezo_z_range_um = None
        self.laser_af_relative_range_um = None

    def set_z_range(self, minZ, maxZ):
        self.z_range = (minZ, maxZ)

    def set_piezo_z_range_um(self, piezo_z_range_um):
        self.piezo_z_range_um = piezo_z_range_um

    def set_laser_af_relative_range_um(self, rng):
        self.laser_af_relative_range_um = rng


def _bind(host, cls, *names):
    """Give the stand-in the real widget methods, bound to it."""
    for name in names:
        setattr(host, name, functools.partial(getattr(cls, name), host))


def _z_mode_box():
    box = QComboBox()
    box.addItems(list(control.widgets.WELLPLATE_Z_MODES))
    return box


def _fake(cls, use_piezo, stage_z_mm=1.0, piezo_um=20.0):
    piezo = PiezoStage(MagicMock(), {"OBJECTIVE_PIEZO_RANGE_UM": 300})
    piezo.move_to(piezo_um)
    fake = SimpleNamespace(
        stage=_FakeStage(stage_z_mm),
        multipointController=_Controller(piezo),
        checkbox_usePiezo=QCheckBox(),
        checkbox_withReflectionAutofocus=QCheckBox(),  # unchecked: absolute Set Range
        checkbox_z=QCheckBox(),
        combobox_z_mode=_z_mode_box(),
        entry_minZ=QDoubleSpinBox(),
        entry_maxZ=QDoubleSpinBox(),
    )
    fake.checkbox_usePiezo.setChecked(use_piezo)
    _bind(fake, cls, "_stack_z_um", "_reset_z_range_entries", "_push_set_z_range", "_move_stack_z_to_um")
    _bind(fake, cls, "_set_range_shown", "_relative_mode")
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


def test_wellplate_start_with_z_unchecked_ignores_a_remembered_set_range():
    """Unchecking Z leaves the Z mode alone (Nz = 1): a "Set Range" still in the dropdown must not reach the
    controller, or laser AF on a single-plane run would be refused as Set Range."""
    fake = MagicMock()
    fake.checkbox_z.isChecked.return_value = False
    fake.combobox_z_mode.currentText.return_value = "Set Range"
    fake.checkbox_usePiezo.isChecked.return_value = True
    fake.checkbox_useFocusMap.isChecked.return_value = False
    fake.stage.get_pos.return_value.z_mm = 1.0
    fake.entry_deltaZ.value.return_value = 1.5
    fake.entry_NZ.value.return_value = 1

    WellplateMultiPointWidget.configure_controller_from_ui(fake)

    fake._push_set_z_range.assert_not_called()
    fake.multipointController.set_piezo_z_range_um.assert_called_once_with(None)
    fake.multipointController.set_z_range.assert_called_once_with(1.0, 1.0)


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


@pytest.mark.parametrize("z_mode", ["Set Range", "From Bottom"])
def test_laser_af_stays_enabled_in_set_range(qtbot, z_mode):
    # Laser AF picks the Set Range mode (checked: relative to the laser AF reference), so it stays available:
    # after an acquisition setEnabled_all(True) re-enables every child, and nothing disables it again.
    from qtpy.QtWidgets import QProgressBar, QPushButton

    host = QWidget()
    qtbot.addWidget(host)
    host.btn_startAcquisition, host.progress_bar = QPushButton(host), QProgressBar(host)
    host.progress_label, host.eta_label = QLabel(host), QLabel(host)
    host.checkbox_xy, host.checkbox_z = QCheckBox(host), QCheckBox(host)
    host.combobox_xy_mode, host.combobox_z_mode = QComboBox(host), QComboBox(host)
    host.combobox_z_mode.addItems(["From Bottom", "Set Range"])
    host.entry_NZ, host.entry_well_coverage = QSpinBox(host), QDoubleSpinBox(host)
    host.checkbox_withReflectionAutofocus = QCheckBox(host)
    host.scanCoordinates = SimpleNamespace(format="96 well plate")
    host.update_scan_control_ui = MagicMock()
    host._update_laser_af_per_stack_state = MagicMock()  # the Z First "AF per stack" rule, tested in test_widgets.py
    host.checkbox_z.setChecked(True)
    host.combobox_z_mode.setCurrentText(z_mode)

    WellplateMultiPointWidget.setEnabled_all(host, False)  # acquisition running
    WellplateMultiPointWidget.setEnabled_all(host, True)  # acquisition finished

    assert host.checkbox_withReflectionAutofocus.isEnabled()
    assert host.entry_NZ.isEnabled() is (z_mode != "Set Range")  # Nz still comes from the range


def test_entering_set_range_keeps_laser_af_checked_and_enabled(qtbot):
    host = MagicMock()
    host.checkbox_withReflectionAutofocus = QCheckBox()
    host.checkbox_withReflectionAutofocus.setChecked(True)
    host.z_min_layout.count.return_value = 0
    host.z_max_layout.count.return_value = 0

    WellplateMultiPointWidget.toggle_z_range_controls(host, True)

    assert host.checkbox_withReflectionAutofocus.isChecked()
    assert host.checkbox_withReflectionAutofocus.isEnabled()
    host._restart_z_range.assert_called_once_with()  # the entries start over in the mode laser AF picks


# --- Set Range relative to the laser AF reference (laser AF checked) ---------------------------------------


IDENTITY = ("profile", "20x", 512.0, "crop-sha1")


class _FakeLive:
    def __init__(self, calls, is_live):
        self.calls, self.is_live = calls, is_live

    def stop_live(self):
        self.calls.append("stop_live")
        self.is_live = False

    def start_live(self):
        self.calls.append("start_live")
        self.is_live = True


class _FakeLaserAF:
    """LaserAutofocusController's relative-z API on the stand-in's piezo, recording calls in order."""

    def __init__(self, piezo, calls, measurements, anchor_um):
        self.piezo, self.calls = piezo, calls
        self.is_initialized = True
        self.laser_af_properties = SimpleNamespace(has_reference=True)
        self.reference_identity = IDENTITY
        self.measurements = list(measurements)
        self.anchor_um = anchor_um
        self.anchor_error = None
        self.set_reference = MagicMock(return_value=True)

    def measure_relative_z_um(self):
        self.calls.append("measure")
        result = self.measurements.pop(0)
        if isinstance(result, Exception):
            raise result
        return RelativeZMeasurement(z_rel_um=result, anchor_reused=False)

    def find_anchor_um(self):
        self.calls.append("find_anchor")
        self.piezo.move_to(self.anchor_um)  # to the last anchor first; a fresh lock-on leaves it at the anchor
        if self.anchor_error is not None:
            raise self.anchor_error
        return self.anchor_um, False

    def actuator_position_um(self):
        return self.piezo.position

    def move_actuator_to_um(self, um):
        self.calls.append(("move", um))
        self.piezo.move_to(um)


_RELATIVE_METHODS = (
    "_restart_z_range",
    "_reset_relative_z_range_entries",
    "_actuator_travel_um",
    "_laser_af",
    "_laser_af_reference_identity",
    "_ready_laser_af",
    "_with_live_paused",
    "_set_relative_z",
    "_goto_relative_z",
    "_set_z_range_status",
    "_on_laser_af_toggled",
    "_on_laser_af_reference_changed",
    "_remember_relative_range_reference",
    "_on_stack_motor_changed",
    "update_Nz",
    "update_z_min",
    "update_z_max",
    "set_z_min",
    "set_z_max",
    "goto_z_min",
    "goto_z_max",
    "init_z",
)


def _relative_fake(
    measurements=(), anchor_um=100.0, is_live=True, piezo_um=20.0, stepper_only=False, has_reference=True
):
    """A Wellplate stand-in in Set Range with laser AF checked (relative mode), live running by default."""
    fake = _fake(WellplateMultiPointWidget, use_piezo=not stepper_only, piezo_um=piezo_um)
    piezo = fake.multipointController.piezo
    if stepper_only:
        fake.multipointController.piezo = None
    fake.calls = []
    fake.liveController = _FakeLive(fake.calls, is_live)
    fake.laser_af = _FakeLaserAF(piezo, fake.calls, measurements, anchor_um)
    if not has_reference:
        fake.laser_af.laser_af_properties.has_reference = False
        fake.laser_af.reference_identity = None
    fake.multipointController.laserAutoFocusController = fake.laser_af
    fake.label_parent = QWidget()  # never shown, so showing the status line opens no window
    fake.label_z_range_status = QLabel(fake.label_parent)
    fake.entry_NZ, fake.entry_deltaZ = QSpinBox(), QDoubleSpinBox()
    fake.entry_NZ.setRange(1, 2000)
    fake.entry_deltaZ.setValue(1.0)
    fake.stored_z_params = dict(WellplateMultiPointWidget.INITIAL_STORED_Z_PARAMS)
    fake._log = MagicMock()
    fake._relative_range_identity = None
    _bind(fake, WellplateMultiPointWidget, *_RELATIVE_METHODS)
    for entry in (fake.entry_minZ, fake.entry_maxZ):  # as the widget connects them
        entry.valueChanged.connect(fake._remember_relative_range_reference)
    fake.checkbox_z.setChecked(True)
    fake.combobox_z_mode.setCurrentText("Set Range")
    fake.checkbox_withReflectionAutofocus.setChecked(True)
    fake._restart_z_range()  # what toggle_z_range_controls does on entering Set Range with laser AF checked
    return fake


@pytest.fixture
def dialogs(monkeypatch):
    shown = []
    monkeypatch.setattr(control.widgets, "error_dialog", lambda message, *a, **kw: shown.append(message))
    return shown


@pytest.mark.parametrize("stepper_only", [False, True])
def test_toggling_laser_af_in_set_range_clears_the_range_and_switches_units(qtbot, stepper_only):
    fake = _relative_fake(stepper_only=stepper_only)
    limits = control._def.SOFTWARE_POS_LIMIT
    travel_um = (limits.Z_POSITIVE - limits.Z_NEGATIVE) * 1000 if stepper_only else 300.0

    # Laser AF checked (as _relative_fake leaves it): 0 / 0 from the reference, within ± the actuator travel
    for entry in (fake.entry_minZ, fake.entry_maxZ):
        assert entry.value() == 0.0
        assert (entry.minimum(), entry.maximum()) == (-travel_um, travel_um)
        assert "from reference" in entry.suffix()

    fake.entry_minZ.setValue(-5.0)
    fake.entry_maxZ.setValue(10.0)
    fake.checkbox_withReflectionAutofocus.setChecked(False)
    fake._on_laser_af_toggled(False)  # connected to the checkbox's toggled signal

    # Unchecked: absolute again, as _reset_z_range_entries does - the stack motor's travel and position
    z_um = 1000.0 if stepper_only else 20.0
    low_um, high_um = SOFTWARE_POS_LIMIT_Z_UM if stepper_only else (0.0, 300.0)
    for entry in (fake.entry_minZ, fake.entry_maxZ):
        assert entry.value() == z_um
        assert (entry.minimum(), entry.maximum()) == (low_um, high_um)
        assert "from reference" not in entry.suffix()

    fake.checkbox_withReflectionAutofocus.setChecked(True)
    fake._on_laser_af_toggled(True)
    assert (fake.entry_minZ.value(), fake.entry_maxZ.value()) == (0.0, 0.0)


def test_toggling_laser_af_outside_set_range_leaves_the_entries_alone(qtbot):
    fake = _relative_fake()
    fake.combobox_z_mode.setCurrentText("From Bottom")
    fake.entry_minZ.setValue(-5.0)

    fake.checkbox_withReflectionAutofocus.setChecked(False)
    fake._on_laser_af_toggled(False)

    assert fake.entry_minZ.value() == -5.0


def test_relative_set_min_and_max_store_measurements_and_pause_live(qtbot, dialogs):
    fake = _relative_fake(measurements=[12.5, 30.0])

    WellplateMultiPointWidget.set_z_min(fake)
    WellplateMultiPointWidget.set_z_max(fake)

    assert (fake.entry_minZ.value(), fake.entry_maxZ.value()) == (12.5, 30.0)
    assert fake.calls == ["stop_live", "measure", "start_live"] * 2
    assert fake.liveController.is_live
    assert dialogs == []


def test_relative_set_min_with_live_off_leaves_live_off(qtbot):
    fake = _relative_fake(measurements=[12.5], is_live=False)

    WellplateMultiPointWidget.set_z_min(fake)

    assert fake.entry_minZ.value() == 12.5
    assert fake.calls == ["measure"]


def test_relative_set_min_failure_shows_the_reason_and_keeps_the_entry(qtbot, dialogs):
    fake = _relative_fake(measurements=[RelativeZError(LOCK_ON_FAILED_MESSAGE)])
    fake.entry_minZ.setValue(-3.0)

    WellplateMultiPointWidget.set_z_min(fake)

    assert fake.entry_minZ.value() == -3.0
    assert dialogs == [LOCK_ON_FAILED_MESSAGE]
    assert fake.calls == ["stop_live", "measure", "start_live"]  # live resumed after the failure


@pytest.mark.parametrize("initialized, has_reference", [(False, True), (True, False)])
def test_relative_set_min_without_a_reference_refuses(qtbot, dialogs, initialized, has_reference):
    fake = _relative_fake(measurements=[12.5])
    fake.laser_af.is_initialized = initialized
    fake.laser_af.laser_af_properties.has_reference = has_reference

    WellplateMultiPointWidget.set_z_min(fake)
    WellplateMultiPointWidget.goto_z_min(fake)

    assert dialogs == ["Set the laser AF reference first."] * 2
    assert fake.calls == []  # refused before live was paused or anything was measured or moved
    assert fake.entry_minZ.value() == 0.0


def test_relative_go_to_locks_on_then_moves_to_anchor_plus_value(qtbot, dialogs):
    fake = _relative_fake(anchor_um=100.0)
    fake.entry_minZ.setValue(-12.5)
    fake.entry_maxZ.setValue(30.0)

    WellplateMultiPointWidget.goto_z_min(fake)
    assert fake.multipointController.piezo.position == 87.5
    WellplateMultiPointWidget.goto_z_max(fake)

    assert fake.multipointController.piezo.position == 130.0
    assert fake.calls == ["stop_live", "find_anchor", ("move", 87.5), "start_live"] + [
        "stop_live",
        "find_anchor",
        ("move", 130.0),
        "start_live",
    ]
    assert fake.stage.z_mm == 1.0  # the stepper holds: laser AF drives the piezo
    assert dialogs == []


def test_relative_go_to_failed_lock_on_returns_z_and_explains(qtbot, dialogs):
    fake = _relative_fake(anchor_um=100.0, piezo_um=40.0)
    fake.laser_af.anchor_error = RelativeZError(LOCK_ON_FAILED_MESSAGE)
    fake.entry_minZ.setValue(-12.5)

    WellplateMultiPointWidget.goto_z_min(fake)

    assert dialogs == [LOCK_ON_FAILED_MESSAGE]
    assert fake.multipointController.piezo.position == 40.0  # back where the Go To started
    assert fake.calls[0] == "stop_live" and fake.calls[-1] == "start_live"


def test_relative_go_to_outside_the_travel_does_not_move_there(qtbot, dialogs):
    fake = _relative_fake(anchor_um=290.0, piezo_um=40.0)
    fake.entry_maxZ.setValue(20.0)  # 310 µm on a 300 µm piezo

    WellplateMultiPointWidget.goto_z_max(fake)

    assert len(dialogs) == 1 and "outside the travel" in dialogs[0]
    assert ("move", 310.0) not in fake.calls
    assert fake.multipointController.piezo.position == 40.0
    assert fake.calls[-1] == "start_live"


def test_relative_nz_comes_from_the_range_and_dz(qtbot):
    fake = _relative_fake()
    fake.entry_deltaZ.setValue(0.2)
    fake.entry_minZ.setValue(0.0)
    fake.entry_maxZ.setValue(0.6)

    fake.update_Nz()
    assert fake.entry_NZ.value() == 4  # nz_for_range: 0.6 / 0.2 does not add a plane

    fake.entry_deltaZ.setValue(0.0)
    fake.update_Nz()  # no ZeroDivisionError while dz is being typed
    assert fake.entry_NZ.value() == 1


def test_relative_range_goes_to_the_controller_with_the_stepper_held(qtbot):
    fake = _relative_fake()
    fake.multipointController.piezo_z_range_um = (20.0, 26.0)  # from an earlier absolute run
    fake.entry_minZ.setValue(-5.0)
    fake.entry_maxZ.setValue(20.0)

    fake._push_set_z_range()

    assert fake.multipointController.laser_af_relative_range_um == (-5.0, 20.0)
    assert fake.multipointController.piezo_z_range_um is None
    assert fake.multipointController.z_range == (1.0, 1.0)


@pytest.mark.parametrize("use_piezo", [True, False])
def test_an_absolute_range_clears_a_relative_range(qtbot, use_piezo):
    fake = _fake(WellplateMultiPointWidget, use_piezo)
    fake.multipointController.laser_af_relative_range_um = (-5.0, 20.0)  # from an earlier relative run

    fake._push_set_z_range()

    assert fake.multipointController.laser_af_relative_range_um is None


def test_start_without_set_range_and_snap_clear_a_relative_range():
    fake = MagicMock()
    fake.combobox_z_mode.currentText.return_value = "From Bottom"
    fake.checkbox_useFocusMap.isChecked.return_value = False
    WellplateMultiPointWidget.configure_controller_from_ui(fake)
    fake.multipointController.set_laser_af_relative_range_um.assert_called_once_with(None)

    for snap in (WellplateMultiPointWidget.on_snap_images, control.widgets.FlexibleMultiPointWidget.on_snap_images):
        fake = MagicMock()
        snap(fake)
        fake.multipointController.set_laser_af_relative_range_um.assert_called_once_with(None)


def test_reference_identity_change_clears_the_relative_range_but_a_benign_reemit_does_not(qtbot):
    fake = _relative_fake(measurements=[12.5, 30.0])
    WellplateMultiPointWidget.set_z_min(fake)
    WellplateMultiPointWidget.set_z_max(fake)

    fake._on_laser_af_reference_changed(True)  # a config reload re-emits with nothing changed
    assert (fake.entry_minZ.value(), fake.entry_maxZ.value()) == (12.5, 30.0)
    assert fake.label_z_range_status.text() == ""

    fake.laser_af.reference_identity = IDENTITY[:-1] + ("another-crop",)  # e.g. the reference was set again
    fake._on_laser_af_reference_changed(True)
    assert (fake.entry_minZ.value(), fake.entry_maxZ.value()) == (0.0, 0.0)
    assert fake.label_z_range_status.text() == "Laser AF reference changed — Z-range cleared, set it again."

    fake.entry_minZ.setValue(-4.0)  # typed against the new reference
    fake._on_laser_af_reference_changed(True)  # and its benign re-emit
    assert fake.entry_minZ.value() == -4.0


def test_reference_cleared_clears_the_relative_range(qtbot):
    fake = _relative_fake(measurements=[12.5])
    WellplateMultiPointWidget.set_z_min(fake)

    fake.laser_af.reference_identity = None  # e.g. initialize_auto cleared the reference
    fake._on_laser_af_reference_changed(False)

    assert (fake.entry_minZ.value(), fake.entry_maxZ.value()) == (0.0, 0.0)
    assert fake.label_z_range_status.text() == "Laser AF reference changed — Z-range cleared, set it again."


def test_a_range_typed_without_a_reference_is_kept_when_the_first_reference_appears(qtbot):
    fake = _relative_fake(has_reference=False)
    fake.entry_minZ.setValue(-5.0)  # typed while no reference exists
    fake.entry_maxZ.setValue(20.0)

    fake.laser_af.reference_identity = IDENTITY  # Set Reference
    fake._on_laser_af_reference_changed(True)

    assert (fake.entry_minZ.value(), fake.entry_maxZ.value()) == (-5.0, 20.0)
    assert fake.label_z_range_status.text() == ""

    fake.laser_af.reference_identity = IDENTITY[:-1] + ("another-crop",)  # a real change after that
    fake._on_laser_af_reference_changed(True)
    assert (fake.entry_minZ.value(), fake.entry_maxZ.value()) == (0.0, 0.0)
    assert fake.label_z_range_status.text() == "Laser AF reference changed — Z-range cleared, set it again."


def test_typing_remembers_the_reference_it_is_relative_to(qtbot):
    fake = _relative_fake()
    fake._relative_range_identity = None

    fake.entry_minZ.setValue(-5.0)  # typed (valueChanged)

    assert fake._relative_range_identity == IDENTITY


def test_reference_identity_change_in_absolute_set_range_changes_nothing(qtbot):
    fake = _relative_fake()
    fake.checkbox_withReflectionAutofocus.setChecked(False)
    fake._on_laser_af_toggled(False)  # absolute: piezo positions
    fake.entry_minZ.setValue(25.0)

    fake.laser_af.reference_identity = None  # e.g. initialize_auto cleared the reference
    fake._on_laser_af_reference_changed(False)

    assert fake.entry_minZ.value() == 25.0
    assert fake.label_z_range_status.text() == ""


def test_set_min_no_longer_resets_the_laser_af_reference(qtbot):
    fake = _relative_fake(measurements=[12.5])
    fake.entry_maxZ.setValue(30.0)

    WellplateMultiPointWidget.set_z_min(fake)
    WellplateMultiPointWidget.update_z_min(fake, -2.0)  # Z-max typed below Z-min drags Z-min down

    fake.laser_af.set_reference.assert_not_called()
    assert not hasattr(WellplateMultiPointWidget, "_reset_reflection_af_reference")


def test_relative_entries_survive_init_z_and_a_stack_motor_change(qtbot):
    # Both write motor positions into the entries in absolute mode; relative values are not positions.
    fake = _relative_fake()
    fake.entry_minZ.setValue(-5.0)
    fake.entry_maxZ.setValue(20.0)

    fake.init_z()  # the GUI restores the cached z at start-up
    fake.checkbox_usePiezo.setChecked(False)
    fake._on_stack_motor_changed()  # the actuator is still the one laser AF drives

    assert (fake.entry_minZ.value(), fake.entry_maxZ.value()) == (-5.0, 20.0)
    assert "from reference" in fake.entry_minZ.suffix()


def _yaml(use_piezo, piezo_z_range_um=None, nz=4, delta_z_um=2.0, laser_af=False, laser_af_relative_range_um=None):
    """The fields WellplateMultiPointWidget._apply_yaml_settings reads from a parsed acquisition.yaml."""
    return SimpleNamespace(
        nz=nz,
        delta_z_um=delta_z_um,
        z_stack_order="CHANNEL FIRST",
        z_stacking_config="FROM BOTTOM",  # what Set Range saves
        use_piezo=use_piezo,
        piezo_z_range_um=piezo_z_range_um,
        laser_af_relative_range_um=laser_af_relative_range_um,
        nt=1,
        delta_t_s=0.0,
        overlap_percent=10.0,
        scan_size_mm=None,
        scan_shape=None,
        channel_names=[],
        contrast_af=False,
        laser_af=laser_af,
        laser_af_per_stack=False,
        xy_mode="Select Wells",
        wellplate_regions=None,
    )


def _wellplate_for_yaml(use_piezo, stage_z_mm=1.0, piezo_um=50.0):
    """A Wellplate stand-in for _apply_yaml_settings: real z entries, motor checkbox, Z checkbox and Z mode."""
    from qtpy.QtWidgets import QComboBox, QSpinBox

    fake = _fake(WellplateMultiPointWidget, use_piezo, stage_z_mm=stage_z_mm, piezo_um=piezo_um)
    host = MagicMock()
    for name, value in vars(fake).items():
        setattr(host, name, value)
    host.entry_NZ, host.entry_deltaZ, host.checkbox_z = QSpinBox(), QDoubleSpinBox(), QCheckBox()
    host.entry_NZ.setRange(1, 1000)
    host.combobox_z_mode = QComboBox()
    host.combobox_z_mode.addItems(list(control.widgets.WELLPLATE_Z_MODES))
    host.stored_z_params = dict(WellplateMultiPointWidget.INITIAL_STORED_Z_PARAMS)
    _bind(
        host,
        WellplateMultiPointWidget,
        "_stack_z_um",
        "_reset_z_range_entries",
        "_on_stack_motor_changed",
        "_set_range_shown",
        "_relative_mode",
    )
    return host


def test_wellplate_yaml_switching_to_the_stepper_gives_the_entries_the_stepper_limits(qtbot):
    host = _wellplate_for_yaml(use_piezo=True, stage_z_mm=1.0)  # entries limited to the piezo's 0..300 µm
    WellplateMultiPointWidget._apply_yaml_settings(host, _yaml(use_piezo=False))

    assert host.entry_minZ.maximum() == SOFTWARE_POS_LIMIT_Z_UM[1]
    host.entry_minZ.setValue(1000.0)  # Set Min at a 1,000 µm stage z
    assert host.entry_minZ.value() == 1000.0


def test_wellplate_yaml_with_a_piezo_range_restores_set_range_and_both_ends(qtbot):
    host = _wellplate_for_yaml(use_piezo=True, piezo_um=50.0)
    WellplateMultiPointWidget._apply_yaml_settings(host, _yaml(use_piezo=True, piezo_z_range_um=(20.0, 26.0)))

    assert host.combobox_z_mode.currentText() == "Set Range"
    assert (host.entry_minZ.value(), host.entry_maxZ.value()) == (20.0, 26.0)


def test_wellplate_yaml_without_a_piezo_range_keeps_its_z_mode(qtbot):
    host = _wellplate_for_yaml(use_piezo=True)
    WellplateMultiPointWidget._apply_yaml_settings(host, _yaml(use_piezo=True))
    assert host.combobox_z_mode.currentText() == "From Bottom"


@pytest.mark.parametrize("nz", [14, 1])  # Nz is read-only in the file; a 0 / 0 range is one plane
def test_yaml_with_a_relative_range_selects_set_range_checks_laser_af_and_fills_entries(qtbot, nz):
    host = _wellplate_for_yaml(use_piezo=True, piezo_um=50.0)
    host.checkbox_withReflectionAutofocus.setChecked(False)
    host.multipointController.laserAutoFocusController = SimpleNamespace(reference_identity=IDENTITY)
    host.label_parent = QWidget()
    host.label_z_range_status = QLabel(host.label_parent)
    _bind(
        host,
        WellplateMultiPointWidget,
        "_reset_relative_z_range_entries",
        "_actuator_travel_um",
        "_laser_af",
        "_laser_af_reference_identity",
        "_set_z_range_status",
    )
    rng = (-5.0, 20.0) if nz > 1 else (0.0, 0.0)

    WellplateMultiPointWidget._apply_yaml_settings(
        host, _yaml(use_piezo=True, nz=nz, laser_af=True, laser_af_relative_range_um=rng)
    )

    assert host.combobox_z_mode.currentText() == "Set Range"
    assert host.checkbox_z.isChecked()
    assert host.checkbox_withReflectionAutofocus.isChecked()
    assert (host.entry_minZ.value(), host.entry_maxZ.value()) == rng
    assert "from reference" in host.entry_minZ.suffix()
    assert host._relative_range_identity == IDENTITY  # a later real reference change clears these


def test_yaml_relative_range_loaded_without_a_reference_is_kept_when_one_appears(qtbot):
    # e.g. a protocol YAML, or the control server loading a YAML into the tab, before Set Reference
    host = _wellplate_for_yaml(use_piezo=True, piezo_um=50.0)
    laser_af = SimpleNamespace(reference_identity=None)
    host.multipointController.laserAutoFocusController = laser_af
    host.label_parent = QWidget()
    host.label_z_range_status = QLabel(host.label_parent)
    _bind(
        host,
        WellplateMultiPointWidget,
        "_reset_relative_z_range_entries",
        "_actuator_travel_um",
        "_laser_af",
        "_laser_af_reference_identity",
        "_set_z_range_status",
        "_restart_z_range",
        "_on_laser_af_reference_changed",
    )
    WellplateMultiPointWidget._apply_yaml_settings(
        host, _yaml(use_piezo=True, nz=14, laser_af=True, laser_af_relative_range_um=(-5.0, 20.0))
    )
    assert host._relative_range_identity is None

    laser_af.reference_identity = IDENTITY  # Set Reference
    host._on_laser_af_reference_changed(True)
    assert (host.entry_minZ.value(), host.entry_maxZ.value()) == (-5.0, 20.0)
    assert host.label_z_range_status.text() == ""

    host._on_laser_af_reference_changed(True)  # benign re-emit
    assert (host.entry_minZ.value(), host.entry_maxZ.value()) == (-5.0, 20.0)

    laser_af.reference_identity = IDENTITY[:-1] + ("another-crop",)  # a real change after that
    host._on_laser_af_reference_changed(True)
    assert (host.entry_minZ.value(), host.entry_maxZ.value()) == (0.0, 0.0)
    assert host.label_z_range_status.text() == "Laser AF reference changed — Z-range cleared, set it again."


@pytest.mark.parametrize(
    "piezo_travel_um, piezo_z_range_um, loads",
    [
        (None, None, False),  # piezo stack, no piezo on this microscope
        (None, (20.0, 26.0), False),
        (300, (250.0, 350.0), False),  # saved on a piezo with longer travel
        (300, (20.0, 26.0), True),
    ],
)
def test_dropping_a_yaml_refuses_piezo_settings_this_microscope_cannot_run(
    monkeypatch, piezo_travel_um, piezo_z_range_um, loads
):
    from control.acquisition_yaml_loader import AcquisitionYAMLData

    yaml_data = AcquisitionYAMLData(widget_type="wellplate", use_piezo=True, piezo_z_range_um=piezo_z_range_um)
    monkeypatch.setattr(control.acquisition_yaml_loader, "parse_acquisition_yaml", lambda path: yaml_data)
    mismatch_dialog = MagicMock()
    monkeypatch.setattr(control.widgets, "AcquisitionYAMLMismatchDialog", mismatch_dialog)
    piezo = None
    if piezo_travel_um is not None:
        piezo = PiezoStage(MagicMock(), {"OBJECTIVE_PIEZO_RANGE_UM": piezo_travel_um})
    host = MagicMock(multipointController=_Controller(piezo))
    host.objectiveStore.current_objective = "20x"
    host._get_expected_widget_type.return_value = "wellplate"

    assert control.widgets.AcquisitionYAMLDropMixin._load_acquisition_yaml(host, "acquisition.yaml") is loads
    # Refused before any control changes, with the mismatch dialog
    assert host._apply_yaml_settings.called is loads
    assert mismatch_dialog.called is not loads
