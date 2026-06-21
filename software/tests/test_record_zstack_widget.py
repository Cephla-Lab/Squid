"""Tests for RecordZStackMultiPointWidget (Task E1).

Two test layers:
1. Pure validation helper (_validate_record_zstack_params) — no Qt needed.
2. Full widget instantiation using qtbot (pytest-qt manages QApplication).

The validation rules are extracted into a pure helper so they can be tested
without any Qt machinery. The widget tests verify that the widget reads its
fields correctly and delegates to the same rules.

NOTE on testability (as required by the task brief):
The validation logic was factored into _validate_record_zstack_params() in
widgets.py so that all constraint rules can be exercised without instantiating
QWidget.  Creating a QApplication manually via PyQt5.QtWidgets.QApplication
aborts because pytest-qt (loaded by napari's conftest) creates a PyQt6
QApplication before the test body runs.  Using qtbot solves this — it is
available in the test suite and is the pattern used by all other widget tests
in tests/control/.
"""

import sys
from typing import List, Optional
from unittest.mock import MagicMock

import pytest

from control.widgets import _validate_record_zstack_params


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_channel(name: str = "BF LED matrix full"):
    """Return a minimal AcquisitionChannel with the given name."""
    from control.models.acquisition_config import AcquisitionChannel, CameraSettings, IlluminationSettings

    return AcquisitionChannel(
        name=name,
        camera_settings=CameraSettings(exposure_time_ms=50.0, gain_mode=0.0),
        illumination_settings=IlluminationSettings(intensity=50.0),
    )


def _base_params(**overrides):
    """Return a dict of valid _validate_record_zstack_params kwargs, with optional overrides."""
    defaults = dict(
        base_path="/tmp/test",
        selected_well_count=1,
        recording_enabled=False,
        fps=10.0,
        duration_s=1.0,
        recording_channel_name="BF LED matrix full",
        zstack_enabled=True,
        z_min=-3.0,
        z_max=3.0,
        step=1.0,
        zstack_channel_names=["BF LED matrix full"],
        use_laser_af=False,
        laser_af_has_reference=False,
    )
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Pure-function validation tests (no Qt required)
# ---------------------------------------------------------------------------


def test_validate_helper_no_base_path():
    params = _base_params(base_path="")
    assert _validate_record_zstack_params(**params) is not None


def test_validate_helper_no_wells():
    params = _base_params(selected_well_count=0)
    assert _validate_record_zstack_params(**params) is not None


def test_validate_helper_no_phases_enabled():
    params = _base_params(recording_enabled=False, zstack_enabled=False)
    assert _validate_record_zstack_params(**params) is not None


def test_validate_helper_bad_zstack_range():
    params = _base_params(z_min=3.0, z_max=-3.0)
    assert _validate_record_zstack_params(**params) is not None


def test_validate_helper_bad_zstack_step():
    params = _base_params(step=0.0)
    assert _validate_record_zstack_params(**params) is not None


def test_validate_helper_zstack_no_channels():
    params = _base_params(zstack_channel_names=[])
    assert _validate_record_zstack_params(**params) is not None


def test_validate_helper_recording_no_channel():
    params = _base_params(recording_enabled=True, recording_channel_name=None, zstack_enabled=False)
    assert _validate_record_zstack_params(**params) is not None


def test_validate_helper_recording_bad_fps():
    params = _base_params(recording_enabled=True, fps=0.0, zstack_enabled=False)
    assert _validate_record_zstack_params(**params) is not None


def test_validate_helper_laser_af_no_reference():
    params = _base_params(use_laser_af=True, laser_af_has_reference=False)
    assert _validate_record_zstack_params(**params) is not None


def test_validate_helper_laser_af_with_reference():
    params = _base_params(use_laser_af=True, laser_af_has_reference=True)
    assert _validate_record_zstack_params(**params) is None


def test_validate_helper_valid_zstack_only():
    params = _base_params()
    assert _validate_record_zstack_params(**params) is None


def test_validate_helper_valid_recording_only():
    params = _base_params(
        recording_enabled=True,
        fps=10.0,
        duration_s=1.0,
        recording_channel_name="BF LED matrix full",
        zstack_enabled=False,
    )
    assert _validate_record_zstack_params(**params) is None


def test_validate_helper_valid_both_phases():
    params = _base_params(
        recording_enabled=True,
        fps=10.0,
        duration_s=1.0,
        recording_channel_name="BF LED matrix full",
        zstack_enabled=True,
    )
    assert _validate_record_zstack_params(**params) is None


# ---------------------------------------------------------------------------
# Widget-level fixtures
# ---------------------------------------------------------------------------


def _make_stub_live_controller():
    """Stub liveController whose get_channels() returns two fake AcquisitionChannels."""
    ctrl = MagicMock()
    ctrl.get_channels.return_value = [
        _make_channel("BF LED matrix full"),
        _make_channel("Fluorescence 488 nm Ex"),
    ]
    ctrl.currentConfiguration = _make_channel("BF LED matrix full")
    return ctrl


def _make_stub_objective_store():
    store = MagicMock()
    store.current_objective = "10x"
    return store


def _make_stub_scan_coordinates():
    """Stub scanCoordinates reporting 1 selected well."""
    sc = MagicMock()
    sc.get_selected_wells.return_value = ["A1"]
    return sc


@pytest.fixture
def simulated_widget_deps(tmp_path):
    """Provide lightweight stub dependencies for RecordZStackMultiPointWidget."""
    stage = MagicMock()
    stage.get_pos.return_value = MagicMock(z_mm=0.0)

    return dict(
        stage=stage,
        navigationViewer=MagicMock(),
        recordZStackController=MagicMock(),
        liveController=_make_stub_live_controller(),
        objectiveStore=_make_stub_objective_store(),
        scanCoordinates=_make_stub_scan_coordinates(),
        well_selection_widget=None,
        tab_widget=None,
    )


# ---------------------------------------------------------------------------
# Widget-level tests — use qtbot (pytest-qt) for QApplication management.
# ---------------------------------------------------------------------------


def test_validate_rejects_bad_zstack_and_builds_params(qtbot, simulated_widget_deps):
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    # Must set base path so validation can reach the z-stack checks
    w.lineEdit_savingDir.setText("/tmp/test")

    w.checkbox_zstack.setChecked(True)
    w.entry_zmin.setValue(3.0)
    w.entry_zmax.setValue(-3.0)  # invalid: max < min
    assert w.validate() is not None  # returns an error string

    w.entry_zmin.setValue(-3.0)
    w.entry_zmax.setValue(3.0)
    w.entry_step.setValue(1.0)
    # Add one z-stack channel row so the phase is valid
    w._add_zstack_channel_row(w.liveController.get_channels(w.objectiveStore.current_objective)[0].name)
    assert w.validate() is None

    params = w.build_parameters()
    assert params.zstack_enabled and len(params.zstack_channels) == 1


def test_validate_requires_base_path(qtbot, simulated_widget_deps):
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)
    w.lineEdit_savingDir.setText("")
    w.checkbox_zstack.setChecked(True)
    w.entry_zmin.setValue(-3.0)
    w.entry_zmax.setValue(3.0)
    w.entry_step.setValue(1.0)
    w._add_zstack_channel_row("BF LED matrix full")
    assert w.validate() is not None


def test_validate_requires_phase_enabled(qtbot, simulated_widget_deps):
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)
    w.lineEdit_savingDir.setText("/tmp/test")
    w.checkbox_zstack.setChecked(False)
    w.checkbox_recording.setChecked(False)
    assert w.validate() is not None


def test_build_parameters_recording_phase(qtbot, simulated_widget_deps):
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)
    w.lineEdit_savingDir.setText("/tmp/test")
    w.lineEdit_experimentID.setText("my_exp")
    w.checkbox_recording.setChecked(True)
    w.entry_fps.setValue(20.0)
    w.entry_duration.setValue(5.0)
    w.checkbox_zstack.setChecked(False)

    params = w.build_parameters()
    assert params.recording_enabled is True
    assert params.fps == pytest.approx(20.0)
    assert params.duration_s == pytest.approx(5.0)
    assert params.experiment_id == "my_exp"
    assert params.zstack_enabled is False
    assert params.recording_channel is not None


def test_add_zstack_channel_row_deduplicates(qtbot, simulated_widget_deps):
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)
    w._add_zstack_channel_row("BF LED matrix full")
    w._add_zstack_channel_row("BF LED matrix full")  # duplicate
    # Both the internal list AND the table must stay at 1 entry after dedup.
    assert w._zstack_channel_names.count("BF LED matrix full") == 1
    assert w.zstack_channel_table.rowCount() == 1


def test_validate_helper_recording_bad_duration():
    params = _base_params(recording_enabled=True, duration_s=0.0, zstack_enabled=False)
    assert _validate_record_zstack_params(**params) is not None
