"""The Fluidics display tab on a plate-sipper rig: the Sipper group and the Reservoirs page, over
the simulated rig the library builds from the sample plate-sipper config."""

import threading

import pytest

pytest.importorskip("fluidics")

from qtpy.QtWidgets import QMessageBox


@pytest.fixture
def dialogs(monkeypatch):
    """Record the message boxes instead of opening them (a modal would hang the offscreen run)."""
    import fluidics.qt.reservoirs_window as reservoirs_window

    shown = []
    for kind in ("warning", "critical", "information"):
        monkeypatch.setattr(
            QMessageBox, kind, staticmethod(lambda _parent, title, text, *a, k=kind: shown.append(text))
        )
    monkeypatch.setattr(reservoirs_window, "_ask_yes_no", lambda *a, **k: True)  # "Reset …?" -> yes
    return shown


@pytest.fixture
def sipper_tab(initialized_display_tab, dialogs):
    from tests.control.fluidics_test_config import SIPPER_CONFIG_YAML

    return initialized_display_tab(SIPPER_CONFIG_YAML)


def _settle(qtbot, tab):
    qtbot.waitUntil(lambda: not tab.service.system.busy, timeout=10000)
    tab.sipper_controls.refresh()


def _go_to(qtbot, tab, plate, well, lower=True):
    controls = tab.sipper_controls
    controls.lower_tip_box.setChecked(not lower)
    controls.reservoir_combo.setCurrentIndex(controls.reservoir_combo.findData(plate))
    controls.well_combo.setEditText(well)
    controls.go_button.click()
    _settle(qtbot, tab)


def test_a_sipper_rig_gets_the_sipper_group_and_the_reservoirs_page(sipper_tab):
    tab = sipper_tab
    assert [tab.tabs.tabText(i) for i in range(tab.tabs.count())] == ["Log", "Flow", "Reservoirs"]
    assert tab.sipper_controls is not None
    assert tab.sipper_controls.where_label.text() == "retracted"  # homed at Initialize

    tab.show()
    tab.tabs.setCurrentWidget(tab.reservoirs_tab)  # the page paints while it is shown
    table = tab.reservoirs_tab.table
    keys = [table.item(row, 0).text() for row in range(table.rowCount())]
    assert keys[:5] == ["port_2", "port_3", "port_4", "port_5", "port_6"]  # the bottles, the sipper's port left out
    assert "probes/A1" in keys and "ssct/H12" in keys  # every well of both plates
    loaded = {table.item(row, 0).text(): table.item(row, 4).text() for row in range(table.rowCount())}
    assert loaded["probes/A1"] == "350" and loaded["ssct/A1"] == "1800" and loaded["port_4"] == "2000"


def test_no_sipper_group_on_a_rig_without_one(initialized_display_tab, dialogs):
    from tests.control.fluidics_test_config import CONFIG_YAML

    tab = initialized_display_tab(CONFIG_YAML)
    assert tab.sipper_controls is None
    assert tab.reservoirs_tab is not None  # volume tracking is built on every rig


def test_go_to_well_and_retract_move_the_sipper(sipper_tab, qtbot):
    _go_to(qtbot, sipper_tab, "ssct", "B3")
    assert sipper_tab.sipper_controls.where_label.text() == "in ssct/B3"
    assert sipper_tab.service.system.devices.robot.current_well_key() == "ssct/B3"

    sipper_tab.sipper_controls.retract_button.click()
    _settle(qtbot, sipper_tab)
    assert sipper_tab.sipper_controls.where_label.text() == "retracted"


def test_do_not_lower_tip_starts_on_and_shapes_only_this_go_to_well(sipper_tab, qtbot, dialogs):
    controls, system = sipper_tab.sipper_controls, sipper_tab.service.system
    robot = system.devices.robot
    assert controls.lower_tip_box.isChecked()  # a first Go to well never dips

    controls.reservoir_combo.setCurrentIndex(controls.reservoir_combo.findData("ssct"))
    controls.well_combo.setEditText("B3")
    controls.go_button.click()
    _settle(qtbot, sipper_tab)
    x, y, _z = robot.position()
    assert (x, y) == pytest.approx(robot.placements["ssct"].xy_mm("B3"))  # over the well...
    assert robot.current_well_key() is None and controls.where_label.text() == "retracted"  # ...not in it
    assert robot.lower_tip is True  # put back: runs and Prime/Clean still dip
    assert dialogs == []

    _go_to(qtbot, sipper_tab, "ssct", "B3", lower=True)
    assert robot.current_well_key() == "ssct/B3" and robot.lower_tip is True


def test_the_switch_is_put_back_when_the_move_fails(sipper_tab, qtbot, dialogs, monkeypatch):
    robot = sipper_tab.service.system.devices.robot

    def jammed(ref):
        assert robot.lower_tip is False  # the move ran with the switch on
        raise RuntimeError("Y axis fault")

    monkeypatch.setattr(robot, "go_to_well", jammed)
    sipper_tab.sipper_controls.go_button.click()
    _settle(qtbot, sipper_tab)
    qtbot.waitUntil(lambda: bool(dialogs), timeout=5000)
    assert "Y axis fault" in dialogs[-1]
    assert robot.lower_tip is True


def _status(tab, name):
    tab.device_status.refresh()
    return tab.device_status.labels[name].text()


def test_device_status_shows_where_the_robot_is(sipper_tab, qtbot):
    assert not sipper_tab.device_status.labels["Robot"].isHidden()
    assert _status(sipper_tab, "Robot").endswith("mm · retracted")  # homed at Initialize
    assert _status(sipper_tab, "Robot fault") == "none"

    _go_to(qtbot, sipper_tab, "ssct", "B3")
    x, y, z = sipper_tab.service.system.devices.robot.position()
    assert _status(sipper_tab, "Robot") == f"x {x:.2f}  y {y:.2f}  z {z:.2f} mm · in ssct/B3"


def test_a_drive_fault_shows_in_device_status_until_reset_fault(sipper_tab, qtbot, dialogs):
    tab, robot = sipper_tab, sipper_tab.service.system.devices.robot
    stage = robot._stage
    stage._service.fault(stage._y, after_polls=1, error_code=0x7306)  # the simulated drive faults mid-move
    _go_to(qtbot, tab, "ssct", "B3")
    qtbot.waitUntil(lambda: bool(dialogs), timeout=5000)
    assert "drive fault, error code 0x7306" in dialogs[-1]
    qtbot.waitUntil(lambda: "0x7306" in _status(tab, "Robot fault"), timeout=5000)
    assert "Reset fault" in _status(tab, "Robot fault")

    tab.sipper_controls.reset_fault_button.click()
    _settle(qtbot, tab)
    qtbot.waitUntil(lambda: _status(tab, "Robot fault") == "none", timeout=5000)
    shown = len(dialogs)
    _go_to(qtbot, tab, "ssct", "B3")
    assert robot.current_well_key() == "ssct/B3" and len(dialogs) == shown  # the drives move again


def test_a_failed_run_naming_a_drive_fault_shows_it_and_other_failures_do_not(sipper_tab):
    from fluidics.events import RunEnded

    status = sipper_tab.device_status
    status._on_run_event(RunEnded("run-1", "failed", "the flow sensor read no flow", 1.0, 0))
    assert _status(sipper_tab, "Robot fault") == "none"
    status._on_run_event(RunEnded("run-2", "failed", "Error in flow_reagent: axis X1: drive is not enabled", 1.0, 0))
    assert _status(sipper_tab, "Robot fault").startswith("axis X1: drive is not enabled")


@pytest.mark.parametrize(
    "message, fault",
    [
        ("axis Y2: drive fault, error code 0x7306", "axis Y2: drive fault, error code 0x7306"),
        ("Error in push: axis X1: drive fault during homing", "axis X1: drive fault during homing"),
        # the servo driver's own wording, when Home or Reset fault meets a fault that will not clear
        (
            "axis Z3: homing failed: Motor fault: error code 0x7306",
            "axis Z3: homing failed: Motor fault: error code 0x7306",
        ),
        (
            "axis Y2: fault reset failed: Motor fault: error code 0x7306",
            "axis Y2: fault reset failed: Motor fault: error code 0x7306",
        ),
        (
            "axis Y2: enable failed: Motor fault: error code 0x7306",
            "axis Y2: enable failed: Motor fault: error code 0x7306",
        ),
        ("axis X1: move timed out", None),
        ("ssct/A1 cannot supply 710 uL", None),
    ],
)
def test_drive_fault_reads_every_wording_of_a_fault(message, fault):
    from control.widgets_fluidics.system_panel import drive_fault

    assert drive_fault(message) == fault


def test_the_robot_row_reads_the_stage_once_the_rig_is_idle(sipper_tab):
    """A Home or a fault reset that fails can drop the stage's position without publishing it: the
    row must not go on saying retracted."""
    stage = sipper_tab.service.system.devices.robot._stage
    assert _status(sipper_tab, "Robot").endswith("· retracted")
    stage._homed = False  # as a Home that failed leaves it
    assert _status(sipper_tab, "Robot").endswith("· not known to be retracted")
    stage._position = None
    assert _status(sipper_tab, "Robot") == "position unknown (Home the robot)"


def test_the_priming_and_clean_up_wells_are_set_from_the_picker_and_saved(sipper_tab, qtbot, dialogs, monkeypatch):
    from fluidics.control.config import gantry, load_config

    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda _p, _title, text, *a: asked.append(text) or QMessageBox.Yes)
    )
    controls = sipper_tab.sipper_controls
    path = sipper_tab.service.system.devices.config.source_path
    assert [controls.dedicated_labels[role].text() for role in ("priming", "clean-up")] == ["ssct/H11", "ssct/H12"]
    with open(path, encoding="utf-8") as f:  # the file changes after Initialize (a hand edit, say)
        text = f.read()
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.replace("port_1: sipper", "port_1: sipper tip", 1))

    controls.reservoir_combo.setCurrentIndex(controls.reservoir_combo.findData("ssct"))
    controls.well_combo.setEditText("H10")
    controls.dedicated_buttons["priming"].click()
    _settle(qtbot, sipper_tab)
    assert "Make ssct/H10 the priming well (now ssct/H11)?" in asked[-1] and path in asked[-1]
    assert controls.dedicated_labels["priming"].text() == "ssct/H10"
    saved = load_config(path)
    assert (gantry(saved).priming_well, gantry(saved).clean_up_well) == ("ssct/H10", "ssct/H12")
    with open(path, encoding="utf-8") as f:
        assert "port_1: sipper tip" in f.read()  # only the well was written: the edit stands
    assert dialogs == []

    # a probes well holds 350 uL, less than the 460 the sipper's line takes to prime: refused
    controls.reservoir_combo.setCurrentIndex(controls.reservoir_combo.findData("probes"))
    controls.well_combo.setEditText("A1")
    controls.dedicated_buttons["clean-up"].click()
    _settle(qtbot, sipper_tab)
    qtbot.waitUntil(lambda: bool(dialogs), timeout=5000)
    assert "pick a fuller well" in dialogs[-1]
    assert gantry(load_config(path)).clean_up_well == "ssct/H12"
    assert controls.dedicated_labels["clean-up"].text() == "ssct/H12"

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a: QMessageBox.No))  # changed their mind
    controls.reservoir_combo.setCurrentIndex(controls.reservoir_combo.findData("ssct"))
    controls.well_combo.setEditText("G12")
    controls.dedicated_buttons["clean-up"].click()
    assert not sipper_tab.service.system.busy and controls.dedicated_labels["clean-up"].text() == "ssct/H12"


def test_a_well_the_rig_lacks_is_refused_before_any_move(sipper_tab, dialogs):
    controls = sipper_tab.sipper_controls
    controls.well_combo.setEditText("Z99")
    controls.go_button.click()
    assert dialogs and "Z99" in dialogs[-1]
    assert not sipper_tab.service.system.busy
    assert sipper_tab.service.system.devices.robot.current_well_key() is None


def test_a_plate_with_the_tip_in_it_is_reset_only_after_retract(sipper_tab, qtbot, dialogs, monkeypatch):
    manual = sipper_tab.service.system.manual
    refilled = []
    refill = manual.refill
    monkeypatch.setattr(manual, "refill", lambda *keys: (refilled.append(keys), refill(*keys)))
    _go_to(qtbot, sipper_tab, "probes", "A1")

    sipper_tab.reservoirs_tab.resetPlate("probes")
    assert "Retract" in dialogs[-1] and refilled == []  # refused: lifting the plate would bend the tip

    sipper_tab.sipper_controls.retract_button.click()
    _settle(qtbot, sipper_tab)
    shown = len(dialogs)
    sipper_tab.reservoirs_tab.resetPlate("probes")
    _settle(qtbot, sipper_tab)
    assert refilled == [("probes",)] and len(dialogs) == shown


def test_a_protocol_run_owns_the_sipper_and_the_resets_until_it_is_held(sipper_tab):
    controls, reservoirs = sipper_tab.sipper_controls, sipper_tab.reservoirs_tab

    def live():
        return controls.go_button.isEnabled(), reservoirs.resetPlateButtons["probes"].isEnabled()

    assert live() == (True, True)
    sipper_tab.set_run_active(True)
    assert live() == (False, False)
    sipper_tab.set_run_held(True)  # held: Retract, reload, Reset, then Restart step
    assert live() == (True, True)
    sipper_tab.set_run_held(False)  # the run goes on
    assert live() == (False, False)
    sipper_tab.set_run_active(False)
    assert live() == (True, True)


def test_any_job_on_the_fluidics_session_blocks_a_protocol_start(sipper_tab, qtbot):
    release = threading.Event()
    sipper_tab.service.system.run_manual(lambda: release.wait(5))
    try:
        assert sipper_tab.manual_job_active()
        sipper_tab.sipper_controls.refresh()
        assert not sipper_tab.sipper_controls.go_button.isEnabled()  # the library session is busy
    finally:
        release.set()
    qtbot.waitUntil(lambda: not sipper_tab.manual_job_active(), timeout=5000)
