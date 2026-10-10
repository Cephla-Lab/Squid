"""ObjectivesWidget applies the saved focus offset on a switch (spec C §7.2, tests of §9 "Z on switch"):
a stub changer and stage, the step formula with and without the Xeryon frame, path independence,
the three-state outcome, and refusals that never clamp."""

import threading
import types
from unittest.mock import patch

import pytest
from qtpy.QtWidgets import QMessageBox

import control._def
from control.core.objective_store import ObjectiveStore
from control.widgets import ObjectivesWidget, parfocal_adjusted_z_mm
from squid.abc import Pos
from tests.control.test_objective_store_offsets import CAMERA, OBJECTIVES, TURRET, TURRET_CONFIG, XERYON, _config

Z_MIN_MM, Z_MAX_MM = 0.0, 6.0  # a Squid+-like SOFTWARE_POS_LIMIT.Z range
Z_START_MM = 3.0


class _Stage:
    """Records every absolute Z command. Like squid.stage.cephla.CephlaStage, move_z_to would clamp to the
    limits, so the tests assert on the commands themselves: a refused step is one that never arrives."""

    def __init__(self, z_mm=Z_START_MM, fail=None):
        self.z_mm = z_mm
        self.fail = fail
        self.z_targets = []

    def get_pos(self):
        return Pos(x_mm=1.0, y_mm=2.0, z_mm=self.z_mm, theta_rad=None)

    def get_config(self):
        return types.SimpleNamespace(Z_AXIS=types.SimpleNamespace(MIN_POSITION=Z_MIN_MM, MAX_POSITION=Z_MAX_MM))

    def move_z_to(self, abs_mm, blocking=True):
        if self.fail is not None:
            raise self.fail
        self.z_targets.append(abs_mm)
        self.z_mm = abs_mm


class _Changer:
    """A turret-like changer: restores the same Z it started from, so it leaves Z alone. With
    `z_by_objective` it emulates the Xeryon's own mechanical Z, as FakeCalibrationHardware does: a
    relative, unchecked move of the stage by the difference of the two positions' parking Z."""

    def __init__(self, stage, fail_with=None, z_by_objective=None, current="4x"):
        self.stage = stage
        self.fail_with = fail_with
        self.z_by_objective = z_by_objective or {}
        self.current = current
        self.calls = []
        self.started = threading.Event()

    def move_to_objective(self, objective_name):
        self.calls.append(objective_name)
        self.started.set()
        if self.fail_with is not None:
            raise self.fail_with
        self.stage.z_mm += self.z_by_objective.get(objective_name, 0.0) - self.z_by_objective.get(self.current, 0.0)
        self.current = objective_name


def _store(config=TURRET_CONFIG, mountings=TURRET, pos2_mm=0.0):
    store = ObjectiveStore(objectives_dict=OBJECTIVES, default_objective="4x")
    store.set_offset_calibration(config, mountings, CAMERA, pos2_mm)
    return store


def _switch(qtbot, widget, name):
    with qtbot.waitSignal(widget.signal_objective_changed, timeout=3000):
        widget.dropdown.setCurrentText(name)
    qtbot.waitUntil(widget.dropdown.isEnabled, timeout=3000)


@pytest.fixture
def no_warning(monkeypatch):
    calls = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: calls.append(args))
    return calls


class TestStepWithAChanger:
    def test_turret_switch_moves_z_by_the_calibrated_residual(self, qtbot, no_warning):
        store, stage = _store(), _Stage()
        widget = ObjectivesWidget(store, _Changer(stage), stage)
        qtbot.addWidget(widget)
        _switch(qtbot, widget, "20x")
        # 20x focuses 6 um above 4x (dz_um=+6): Z goes UP by 6 um from where the turret restored it.
        assert stage.z_targets == [pytest.approx(Z_START_MM + 0.006)]
        assert store.current_objective == "20x"
        assert no_warning == []

    def test_path_independence_with_an_uncalibrated_objective(self, qtbot, no_warning):
        store, stage = _store(), _Stage()
        widget = ObjectivesWidget(store, _Changer(stage), stage)
        qtbot.addWidget(widget)
        for name in ("20x", "40x", "4x"):
            _switch(qtbot, widget, name)
        # 4x -> 20x: +6 um; 20x -> 40x (uncalibrated): -6 um; 40x -> 4x: 0, no command at all.
        assert stage.z_targets == [pytest.approx(Z_START_MM + 0.006), pytest.approx(Z_START_MM)]
        assert stage.z_mm == pytest.approx(Z_START_MM)

    def test_xeryon_step_is_the_residual_not_the_frame(self, qtbot, no_warning):
        """Stored dz is raw (the 2 mm the changer does is in it); the switch adds only the 6 um it did not."""
        config = _config("4x", XERYON["4x"], {"20x": (XERYON["20x"], 0.0, 0.0, -2000.0 + 6.0)})
        store, stage = _store(config, XERYON, pos2_mm=2.0), _Stage()
        changer = _Changer(stage, z_by_objective={"20x": -2.0, "40x": -2.0})  # position 2 parks 2 mm lower
        widget = ObjectivesWidget(store, changer, stage)
        qtbot.addWidget(widget)
        _switch(qtbot, widget, "20x")
        assert stage.z_targets == [pytest.approx(Z_START_MM - 2.0 + 0.006)]
        _switch(qtbot, widget, "40x")  # uncalibrated position-2 objective: the changer moves nothing, the step is -6 um
        assert stage.z_targets[-1] == pytest.approx(Z_START_MM - 2.0)
        _switch(qtbot, widget, "4x")  # back up 2 mm by the changer; residual 0 -> no step command
        assert len(stage.z_targets) == 2
        assert stage.z_mm == pytest.approx(Z_START_MM)

    def test_no_calibration_means_no_z_command(self, qtbot, no_warning):
        store, stage = _store(config=None), _Stage()
        widget = ObjectivesWidget(store, _Changer(stage), stage)
        qtbot.addWidget(widget)
        _switch(qtbot, widget, "20x")
        assert stage.z_targets == []
        assert store.current_objective == "20x"


class TestOutcomes:
    def test_failed_changer_reverts_the_dropdown_and_moves_no_z(self, qtbot):
        store, stage = _store(), _Stage()
        widget = ObjectivesWidget(store, _Changer(stage, fail_with=RuntimeError("Motion did not finish")), stage)
        qtbot.addWidget(widget)
        with patch.object(QMessageBox, "warning") as warning:
            widget.dropdown.setCurrentText("20x")
            qtbot.waitUntil(widget.dropdown.isEnabled, timeout=3000)
        assert store.current_objective == "4x"
        assert widget.dropdown.currentText() == "4x"
        assert stage.z_targets == []
        warning.assert_called_once()
        assert "Objective Change Failed" in warning.call_args.args[1]

    def test_over_cap_step_is_refused_not_clamped_and_the_objective_stands(self, qtbot, monkeypatch):
        monkeypatch.setattr(control._def, "MAX_OBJECTIVE_Z_STEP_MM", 0.5)
        # A bypassed save check: 20x "calibrated" 0.6 mm above 4x.
        config = _config("4x", TURRET["4x"], {"20x": (TURRET["20x"], 0.0, 0.0, 600.0)})
        store, stage = _store(config), _Stage()
        widget = ObjectivesWidget(store, _Changer(stage), stage)
        qtbot.addWidget(widget)
        with patch.object(QMessageBox, "warning") as warning:
            _switch(qtbot, widget, "20x")
        assert stage.z_targets == []  # never clamped to the cap
        assert store.current_objective == "20x"  # the changer did move
        assert widget.dropdown.currentText() == "20x"
        warning.assert_called_once()
        assert "MAX_OBJECTIVE_Z_STEP_MM" in warning.call_args.args[2]

    def test_target_outside_the_z_limits_is_refused_not_clamped(self, qtbot):
        store, stage = _store(), _Stage(z_mm=Z_MAX_MM - 0.001)  # 1 um below the top; the step is +6 um
        widget = ObjectivesWidget(store, _Changer(stage), stage)
        qtbot.addWidget(widget)
        with patch.object(QMessageBox, "warning") as warning:
            _switch(qtbot, widget, "20x")
        assert stage.z_targets == []
        assert stage.z_mm == pytest.approx(Z_MAX_MM - 0.001)
        assert store.current_objective == "20x"
        warning.assert_called_once()
        assert "outside the Z limits" in warning.call_args.args[2]

    def test_failed_z_move_keeps_the_new_objective_and_warns(self, qtbot):
        store, stage = _store(), _Stage(fail=RuntimeError("Z timed out"))
        widget = ObjectivesWidget(store, _Changer(stage), stage)
        qtbot.addWidget(widget)
        with patch.object(QMessageBox, "warning") as warning:
            _switch(qtbot, widget, "20x")
        assert store.current_objective == "20x"
        assert widget.dropdown.currentText() == "20x"
        warning.assert_called_once()
        assert "Z timed out" in warning.call_args.args[2]


class TestNoChanger:
    def test_no_calibration_keeps_the_synchronous_path(self, qtbot, no_warning):
        store, stage = _store(config=None), _Stage()
        widget = ObjectivesWidget(store, None, stage)
        qtbot.addWidget(widget)
        emitted = []
        widget.signal_objective_changed.connect(lambda: emitted.append(widget.dropdown.isEnabled()))
        widget.dropdown.setCurrentText("20x")
        assert emitted == [True]  # emitted before setCurrentText returned, with the dropdown never disabled
        assert store.current_objective == "20x"
        assert stage.z_targets == []

    def test_a_calibrated_step_runs_through_the_helper_thread(self, qtbot, no_warning):
        store, stage = _store(), _Stage()
        widget = ObjectivesWidget(store, None, stage)
        qtbot.addWidget(widget)
        emitted = []
        widget.signal_objective_changed.connect(lambda: emitted.append(True))
        widget.dropdown.setCurrentText("20x")
        assert not widget.dropdown.isEnabled()  # the re-entry guard while the step runs off the GUI thread
        assert emitted == []
        qtbot.waitUntil(widget.dropdown.isEnabled, timeout=3000)
        assert emitted == [True]
        assert stage.z_targets == [pytest.approx(Z_START_MM + 0.006)]

    def test_a_calibrated_step_without_a_stage_is_refused_with_a_warning(self, qtbot):
        store = _store()
        widget = ObjectivesWidget(store, None, None)
        qtbot.addWidget(widget)
        with patch.object(QMessageBox, "warning") as warning:
            _switch(qtbot, widget, "20x")
        assert store.current_objective == "20x"
        warning.assert_called_once()


def test_parfocal_adjusted_z_mm_uses_the_stores_z_frame():
    store = _store()
    assert parfocal_adjusted_z_mm("4x", "20x", 3.0, store) == pytest.approx(3.006)
    assert parfocal_adjusted_z_mm("20x", "4x", 3.006, store) == pytest.approx(3.0)
    assert parfocal_adjusted_z_mm("20x", "40x", 3.006, store) == pytest.approx(3.0)  # 40x uncalibrated: frame 0
    # Without a store, today's rule (no Xeryon here): unchanged.
    assert parfocal_adjusted_z_mm("4x", "20x", 3.0) == pytest.approx(3.0)
