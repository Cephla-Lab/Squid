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


def _go_to(qtbot, tab, plate, well):
    controls = tab.sipper_controls
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
