"""The Repeatability (factory) group of the calibration dialog (spec C §8): gated by the ini key, N
cycles with a report, cancel after k cycles and a failed restore still write it, Apply saves the mean."""

import csv
import re
from pathlib import Path

import pytest

from control._def import FocusMeasureOperator
from control.core.config.repository import ConfigRepository
from control.utils import calculate_focus_measure
from control.widgets_objective_calibration import (
    APPLY_MEAN_TEXT,
    APPLY_TEXT,
    REPEAT_CYCLES_DEFAULT,
    FactoryTools,
    ObjectiveCalibrationDialog,
)
from squid.objective_calibration.engine import ObjectiveSpec
from squid.objective_calibration.report import CYCLE_COLUMNS
from squid.objective_calibration.synthetic import FakeCalibrationHardware, FakeObjective, FakeScene

SPECS = [ObjectiveSpec("4x", 4, 0.13, 1.625), ObjectiveSpec("10x", 10, 0.3, 0.65), ObjectiveSpec("20x", 20, 0.8, 0.325)]
DECLARED = {
    "4x": {"magnification": 4.0, "na": 0.13, "tube_lens_f_mm": 180.0, "model": "", "serial": ""},
    "10x": {"magnification": 10.0, "na": 0.3, "tube_lens_f_mm": 180.0, "model": "", "serial": ""},
    "20x": {"magnification": 20.0, "na": 0.8, "tube_lens_f_mm": 180.0, "model": "", "serial": ""},
}
MOUNTING = {"4x": ("nimotion_turret", 1), "10x": ("nimotion_turret", 2), "20x": ("nimotion_turret", 3)}
TRUE_PX = {"4x": 1.625 * 1.02, "10x": 0.65 * 0.99, "20x": 0.325 * 1.01}
PARCENTRIC = {"4x": (0.0, 0.0), "10x": (12.0, -7.0), "20x": (-9.0, 6.0)}
Z_FOCUS = {"4x": 0.0, "10x": 5.0, "20x": -3.0}
START_XY = (1000.0, 1000.0)
EXPOSURES_MS = {"4x": 5.0, "10x": 8.0, "20x": 20.0}
REPORT_FILES = ("cycles.csv", "focus_curves.csv", "summary.csv", "report.html")


def lape(crop):
    return float(calculate_focus_measure(crop, FocusMeasureOperator.LAPE))


def _fake(**kw):
    objectives = {
        spec.name: FakeObjective(
            spec.name,
            spec.magnification,
            spec.na,
            pixel_um=TRUE_PX[spec.name],
            z_focus_um=Z_FOCUS[spec.name],
            parcentric_um=PARCENTRIC[spec.name],
        )
        for spec in SPECS
    }
    return FakeCalibrationHardware(
        objectives,
        FakeScene.random(),
        shape=(160, 200),
        start_objective="10x",
        start_xy_um=START_XY,
        start_z_um=2.0,
        positioning_noise_um=0.1,
        microstep_um=0.4,
        **kw,
    )


@pytest.fixture
def make_dialog(qtbot, tmp_path):
    def make(hw=None, factory_tools=True, cycles=2):
        hw = hw or _fake()
        factory = (
            FactoryTools(
                ini_name="configuration_test",
                saving_path=str(tmp_path / "saving"),
                squid_commit="deadbeef (dirty=False)",
                get_exposure_ms=lambda objective, channel: EXPOSURES_MS[objective] if channel == "BF" else None,
            )
            if factory_tools
            else None
        )
        dialog = ObjectiveCalibrationDialog(
            hw,
            SPECS,
            ["BF"],
            ConfigRepository(base_path=tmp_path),
            tube_lens_mm=180.0,
            get_declared=lambda name: DECLARED[name],
            fine_metric=lape,
            get_mounting=lambda name: MOUNTING[name],
            factory=factory,
        )
        qtbot.addWidget(dialog)
        for box in dialog.checkboxes.values():
            box.setChecked(True)
        dialog.spin_range.setValue(20.0)
        if factory is not None:
            dialog.spin_repeat_cycles.setValue(cycles)
        return dialog, hw

    return make


def _wait(qtbot, dialog):
    qtbot.waitUntil(lambda: not dialog._running(), timeout=300_000)


def _report_folder(dialog) -> Path:
    """The group's default: {DEFAULT_SAVING_PATH}/objective_calibration/<ini-name>_<timestamp> (spec C §8.2)."""
    folder = dialog.edit_report_folder.text()
    assert re.fullmatch(r".*/saving/objective_calibration/configuration_test_\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d", folder)
    return Path(folder)


def _read_csv(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _assert_restored(hw):
    assert hw.current_objective() == "10x"
    assert hw.get_xy_um() == pytest.approx(START_XY)
    assert hw.get_z_um() == pytest.approx(2.0)


def test_the_group_exists_only_with_the_ini_key(make_dialog):
    dialog, _ = make_dialog(factory_tools=False)
    assert dialog.factory_group is None and not hasattr(dialog, "button_repeat_start")
    assert dialog.button_apply.text() == APPLY_TEXT
    dialog, _ = make_dialog(cycles=REPEAT_CYCLES_DEFAULT)
    assert dialog.factory_group is not None and dialog.factory_group.title() == "Repeatability (factory)"
    assert dialog.spin_repeat_cycles.value() == 10  # spec C §8.2: N defaults to 10
    _report_folder(dialog)  # {DEFAULT_SAVING_PATH}/objective_calibration/<ini-name>_<timestamp>
    assert dialog.button_repeat_start.isEnabled() and not dialog.button_repeat_cancel.isEnabled()


def test_n_cycles_write_the_report_and_apply_saves_the_mean(qtbot, tmp_path, make_dialog):
    dialog, hw = make_dialog(cycles=2)
    folder = _report_folder(dialog)
    dialog.button_repeat_start.click()
    assert dialog._running() and not dialog.button_repeat_start.isEnabled() and dialog.button_repeat_cancel.isEnabled()
    _wait(qtbot, dialog)
    _assert_restored(hw)
    text = dialog.label_result.text()
    assert "Repeatability run completed; report written to" in text and text.endswith("report.html.")
    for name in REPORT_FILES:
        assert (folder / name).is_file(), name
    rows = _read_csv(folder / "cycles.csv")
    assert list(rows[0]) == CYCLE_COLUMNS and len(rows) == 6 and all(r["status"] == "ok" for r in rows)
    page = (folder / "report.html").read_text(encoding="utf-8")
    for text in ("configuration_test", "deadbeef (dirty=False)", "4x 5 ms, 10x 8 ms, 20x 20 ms", "2 completed of 2"):
        assert text in page, text
    assert sorted(p.name for p in (folder / "images").iterdir()) == [
        "cycle_01_10x.tiff",
        "cycle_01_20x.tiff",
        "cycle_01_4x.tiff",
    ]
    # the one-shot dialog state is the same as after a Calibrate run: the tables, and Apply, now named for N cycles
    assert dialog.table_offsets.rowCount() == 3 and dialog.table.rowCount() == 3
    assert dialog.button_apply.text() == APPLY_MEAN_TEXT and dialog.button_apply.isEnabled()
    assert ConfigRepository(base_path=tmp_path).get_objective_calibration() is None  # nothing saved automatically
    dialog.button_apply.click()
    saved = ConfigRepository(base_path=tmp_path).get_objective_calibration()
    assert saved.offset_calibration.cycles == 2
    for name in ("10x", "20x"):
        block = saved.objectives[name].offset
        assert block.std_dz_um is not None and block.std_dx_um is not None
        assert block.dz_um == pytest.approx(Z_FOCUS[name] - Z_FOCUS["4x"], abs=1.0)
        assert saved.objectives[name].pixel_size.cycles == 2
    assert "Saved" in dialog.label_result.text()
    # the next one-shot run names the button for what it writes again
    dialog.spin_cycles.setValue(1)
    dialog.button_calibrate.click()
    assert dialog.button_apply.text() == APPLY_TEXT
    _wait(qtbot, dialog)


def test_cancel_after_k_cycles_still_writes_the_report(qtbot, tmp_path, make_dialog):
    dialog, hw = make_dialog(cycles=5)
    folder = _report_folder(dialog)
    original = hw.switch_objective
    switches = []

    def switch(name):
        switches.append(name)
        if len(switches) == 5:  # cycle 1: 4x, 10x, 20x and the restore to 10x; the fifth switch starts cycle 2
            dialog.worker.cancel()
        original(name)

    hw.switch_objective = switch
    dialog.button_repeat_start.click()
    _wait(qtbot, dialog)
    _assert_restored(hw)
    assert "cancelled after 1 cycles; report written to" in dialog.label_result.text()
    for name in REPORT_FILES:
        assert (folder / name).is_file(), name
    rows = _read_csv(folder / "cycles.csv")
    assert [(r["cycle"], r["status"]) for r in rows] == [("1", "ok")] * 3 + [("2", "interrupted")]
    page = (folder / "report.html").read_text(encoding="utf-8")
    assert "1 completed of 5 requested (cancelled after 1 cycles)" in page and "cycle 2: cancelled" in page
    assert not dialog.button_apply.isEnabled()  # a cancelled run is not applied, as in C1


def test_a_failed_restore_stops_the_run_and_still_writes_the_report(qtbot, tmp_path, make_dialog):
    dialog, hw = make_dialog(cycles=3)
    folder = _report_folder(dialog)
    original = hw.move_xy_to_um

    def move(x, y):
        # The offsets phase appends its result just before the restore: the first XY move after that is
        # the restore's, and pass 2 has already switched back to 10x, so the restore moves only XY and Z.
        if dialog.phase is not None and dialog.phase.results:
            raise RuntimeError("XY stage fault")
        original(x, y)

    hw.move_xy_to_um = move
    dialog.button_repeat_start.click()
    _wait(qtbot, dialog)
    text = dialog.label_result.text()
    assert text.startswith("Repeatability run stopped after 1 cycles: Restoring 10x") and "report written to" in text
    assert "XY: XY stage fault" in text
    assert "reselect the objective in the main window" in dialog.log_view.toPlainText()
    assert (folder / "report.html").is_file() and (folder / "cycles.csv").is_file()
    rows = _read_csv(folder / "cycles.csv")
    assert [(r["cycle"], r["status"]) for r in rows] == [("1", "ok")] * 3  # nothing further moved: no cycle 2
    page = (folder / "report.html").read_text(encoding="utf-8")
    assert "1 completed of 3 requested (stopped after 1 cycles: Restoring 10x" in page
    assert not dialog.button_apply.isEnabled()


def test_the_run_refuses_a_non_empty_folder_and_fewer_than_two_objectives(tmp_path, make_dialog):
    dialog, _ = make_dialog()
    dialog.checkboxes["4x"].setChecked(False)
    dialog.checkboxes["20x"].setChecked(False)
    dialog.button_repeat_start.click()
    assert "at least two objectives" in dialog.label_result.text() and not dialog._running()
    dialog.checkboxes["4x"].setChecked(True)
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "old.csv").write_text("x")
    dialog.edit_report_folder.setText(str(busy))
    dialog.button_repeat_start.click()
    assert "is not empty" in dialog.label_result.text() and not dialog._running()
