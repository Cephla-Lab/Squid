"""Utils > Illumination Power Calibration...: refuses to start without a meter or while busy, runs and saves in
simulation, asks before saving a failed calibration, cannot be closed mid-run, and turns the test beam off on close."""

from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from qtpy.QtWidgets import QMessageBox

import control.widgets_intensity_calibration as dialog_module
from control.core.config import ConfigRepository
from control.widgets_intensity_calibration import IntensityCalibrationDialog
from squid.intensity_calibration import load_calibration
from squid.intensity_calibration_run import CalibrationSession, ChannelResult
from squid.power_meter import PowerMeterError, PowerMeterOverrange
from tests.tools import get_test_microcontroller

YAML = """\
version: 1
controller_port_mapping:
  D1: 11
  D5: 15
channels:
  - name: Fluorescence 405 nm Ex
    type: epi_illumination
    controller_port: D1
    wavelength_nm: 405
  - name: Fluorescence 730 nm Ex
    type: epi_illumination
    controller_port: D5
    wavelength_nm: 730
"""


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(YAML)
    return ConfigRepository(base_path=tmp_path)


@pytest.fixture
def session(repo):
    s = CalibrationSession(get_test_microcontroller(), repo)
    s.settle_s = 0.0
    s.hold_s = 0.0
    s.rest_s = 0.0
    return s


@pytest.fixture
def answers(monkeypatch):
    """Answer the safety confirmation and the save question; tests change answers["question"] as needed."""
    replies = {"warning": QMessageBox.Yes, "question": QMessageBox.Yes}
    monkeypatch.setattr(dialog_module.QMessageBox, "warning", lambda *a, **k: replies["warning"])
    monkeypatch.setattr(dialog_module.QMessageBox, "question", lambda *a, **k: replies["question"])
    return replies


def _dialog(qtbot, session, busy_reason=None):
    dialog = IntensityCalibrationDialog(session, "widefield", busy_reason=busy_reason)
    qtbot.addWidget(dialog)
    return dialog


def _run_405_only(qtbot, dialog):
    dialog.button_connect.click()
    dialog.table.cellWidget(1, dialog.COL_USE).setChecked(False)
    dialog.button_run.click()
    assert dialog.worker is not None, dialog.label_result.text()
    qtbot.waitUntil(lambda: dialog.worker is None, timeout=120000)


def test_run_refused_without_a_meter(qtbot, session, answers):
    dialog = _dialog(qtbot, session)
    dialog.button_run.click()
    assert dialog.worker is None and "Connect a power meter" in dialog.label_result.text()


def test_run_refused_while_busy(qtbot, session, answers):
    dialog = _dialog(qtbot, session, busy_reason=lambda: "Live view is running.")
    dialog.button_connect.click()
    dialog.button_run.click()
    assert dialog.worker is None and "Live view is running." in dialog.label_result.text()


def test_run_and_save_in_simulation(qtbot, session, repo, answers):
    dialog = _dialog(qtbot, session)
    _run_405_only(qtbot, dialog)
    assert dialog.table.item(0, dialog.COL_RUN).text().startswith("✓")
    assert dialog.button_save.isEnabled()

    dialog.button_save.click()
    path = session.calibrations_dir() / "405nm_D1.csv"
    assert path.is_file() and path.with_suffix(".png").is_file()
    assert load_calibration(path).verification == "pass"
    assert repo.get_illumination_config().channels[0].intensity_calibration_file == "405nm_D1.csv"
    assert (
        dialog.table.item(0, dialog.COL_CURRENT).text()
        == "405nm_D1.csv: calibrated " + load_calibration(path).calibrated_at[:10]
    )


def test_failed_verification_asks_before_saving(qtbot, session, answers):
    dialog = _dialog(qtbot, session)
    _run_405_only(qtbot, dialog)
    result = dialog.results["Fluorescence 405 nm Ex"]
    dialog.results["Fluorescence 405 nm Ex"] = ChannelResult(replace(result.calibration, verification="fail"))
    answers["question"] = QMessageBox.No
    dialog.button_save.click()
    assert not (session.calibrations_dir() / "405nm_D1.csv").exists()
    answers["question"] = QMessageBox.Yes
    dialog.button_save.click()
    assert load_calibration(session.calibrations_dir() / "405nm_D1.csv").verification == "fail"


def test_close_refused_while_running(qtbot, session, answers):
    dialog = _dialog(qtbot, session)
    dialog.show()
    dialog.worker = MagicMock()
    dialog.worker.isRunning.return_value = True
    dialog.close()
    assert dialog.isVisible() and "in progress" in dialog.label_result.text()
    dialog.worker = None


def test_closing_with_test_beam_on_turns_the_light_off(qtbot, session, answers):
    dialog = _dialog(qtbot, session)
    dialog.show()
    dialog.button_connect.click()
    dialog.table.selectRow(0)
    dialog.button_test_beam.setChecked(True)
    assert session.source_state()[0] is True
    dialog.close()
    assert session.source_state() == (False, 0.0)
    assert session.meter is None


def test_closing_after_driving_the_light_restores_the_live_channel_once(qtbot, session, answers):
    restore = MagicMock()
    dialog = IntensityCalibrationDialog(session, "widefield", restore_illumination=restore)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.button_connect.click()
    dialog.table.selectRow(0)
    dialog.button_test_beam.setChecked(True)
    dialog.button_test_beam.setChecked(False)
    dialog.close()  # closeEvent, then QDialog's own reject()
    restore.assert_called_once()


def test_closing_without_driving_the_light_restores_nothing(qtbot, session, answers):
    restore = MagicMock()
    dialog = IntensityCalibrationDialog(session, "widefield", restore_illumination=restore)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.close()
    restore.assert_not_called()


def test_sensor_limit_comes_from_the_sensor_and_can_only_be_lowered(qtbot, session, answers):
    dialog = _dialog(qtbot, session)
    dialog.button_connect.click()
    assert dialog.spin_sensor_limit.value() == 500.0 and dialog.spin_sensor_limit.maximum() == 500.0
    dialog.spin_sensor_limit.setValue(0)
    dialog.button_run.click()
    assert dialog.worker is None and "sensor limit" in dialog.label_result.text()


def test_test_beam_feeds_the_watchdog_and_turns_itself_off(qtbot, session, answers):
    dialog = _dialog(qtbot, session)
    dialog.button_connect.click()
    dialog.table.selectRow(0)
    session.feed_watchdog = MagicMock()
    dialog.button_test_beam.setChecked(True)
    assert dialog.test_beam_timer.isActive()
    dialog._update_reading()
    session.feed_watchdog.assert_called_once()
    dialog._test_beam_timed_out()
    assert not dialog.button_test_beam.isChecked() and session.source_state() == (False, 0.0)


def test_test_beam_over_the_sensor_limit_turns_off(qtbot, session, answers):
    dialog = _dialog(qtbot, session)
    dialog.button_connect.click()
    dialog.table.selectRow(0)
    dialog.spin_sensor_limit.setValue(1.0)
    dialog.spin_test_beam.setValue(50)
    dialog.button_test_beam.setChecked(True)
    dialog._update_reading()
    assert not dialog.button_test_beam.isChecked() and "above the sensor limit" in dialog.label_result.text()


def test_software_light_source_has_nothing_to_calibrate(qtbot, repo, answers):
    session = CalibrationSession(get_test_microcontroller(), repo, dac_driven=False)
    dialog = _dialog(qtbot, session)
    assert dialog.table.rowCount() == 0
    assert "light source itself" in dialog.label_note.text()


def test_test_beam_needs_a_connected_meter(qtbot, session, answers):
    # without a meter nothing reads the beam or feeds the watchdog the session takes over
    dialog = _dialog(qtbot, session)
    dialog.table.selectRow(0)
    dialog.button_test_beam.setChecked(True)
    assert not dialog.button_test_beam.isChecked() and session.source_state() == (False, 0.0)
    assert "Connect a power meter first" in dialog.label_result.text()


def test_connect_turns_the_test_beam_off_first(qtbot, session, answers):
    dialog = _dialog(qtbot, session)
    dialog.button_connect.click()
    dialog.table.selectRow(0)
    dialog.button_test_beam.setChecked(True)
    assert session.source_state()[0] is True
    dialog.button_connect.click()
    assert not dialog.button_test_beam.isChecked() and session.source_state() == (False, 0.0)


@pytest.mark.parametrize("error", [PowerMeterOverrange("meter overrange"), PowerMeterError("USB disconnected")])
def test_a_failed_reading_turns_the_test_beam_off(qtbot, session, answers, error):
    # no reading means no sensor-limit check; an overrange means the sensor is already past its range
    dialog = _dialog(qtbot, session)
    dialog.button_connect.click()
    dialog.table.selectRow(0)
    dialog.button_test_beam.setChecked(True)
    session.read_mw = MagicMock(side_effect=error)
    dialog._update_reading()
    assert not dialog.button_test_beam.isChecked() and session.source_state() == (False, 0.0)
    assert "Test beam turned off" in dialog.label_result.text()
