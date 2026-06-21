"""Tests for RecordZStackMultiPointWidget (Tasks E1 + E2).

Covers: widget skeleton, validation, inline editors, Copy-from-Live, computed labels.

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


# ---------------------------------------------------------------------------
# E2: Copy-from-Live, dynamic z-stack rows, computed labels
# ---------------------------------------------------------------------------


def _make_live_channel(name: str, exposure: float, gain: float, intensity: float):
    """Return an AcquisitionChannel stub with specific settings."""
    from control.models.acquisition_config import AcquisitionChannel, CameraSettings, IlluminationSettings

    return AcquisitionChannel(
        name=name,
        camera_settings=CameraSettings(exposure_time_ms=exposure, gain_mode=gain),
        illumination_settings=IlluminationSettings(intensity=intensity),
    )


def test_copy_from_live_populates_recording_fields(qtbot, simulated_widget_deps):
    """Copy-from-Live reads currentConfiguration and sets recording fields."""
    from control.widgets import RecordZStackMultiPointWidget

    live_ch = _make_live_channel("Fluorescence 488 nm Ex", exposure=33.0, gain=2.5, intensity=75.0)
    simulated_widget_deps["liveController"].currentConfiguration = live_ch

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    # Enable the recording group so its child widgets are interactive
    w.checkbox_recording.setChecked(True)
    w.btn_copy_from_live.click()

    # Channel dropdown should be updated to the live channel name
    assert w.combobox_recording_channel.currentText() == "Fluorescence 488 nm Ex"
    # Exposure, gain, intensity spinboxes should reflect live channel values
    assert w.entry_recording_exposure.value() == pytest.approx(33.0)
    assert w.entry_recording_gain.value() == pytest.approx(2.5)
    assert w.entry_recording_illumination.value() == pytest.approx(75.0)


def test_add_remove_zstack_channel_row_syncs_list_and_table(qtbot, simulated_widget_deps):
    """_add_zstack_channel_row and _remove_zstack_channel_row keep list+table in sync."""
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    assert w.zstack_channel_table.rowCount() == 0
    assert len(w._zstack_channel_names) == 0

    w._add_zstack_channel_row("BF LED matrix full")
    assert w.zstack_channel_table.rowCount() == 1
    assert len(w._zstack_channel_names) == 1

    w._add_zstack_channel_row("Fluorescence 488 nm Ex")
    assert w.zstack_channel_table.rowCount() == 2
    assert len(w._zstack_channel_names) == 2

    w._remove_zstack_channel_row("BF LED matrix full")
    assert w.zstack_channel_table.rowCount() == 1
    assert len(w._zstack_channel_names) == 1
    assert "BF LED matrix full" not in w._zstack_channel_names
    assert "Fluorescence 488 nm Ex" in w._zstack_channel_names


def test_computed_frame_label_updates_on_spinbox_change(qtbot, simulated_widget_deps):
    """label_recording_frames updates to '→ N frames' when fps/duration change."""
    from control.core.record_zstack_controller import frame_count
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    w.entry_fps.setValue(5.0)
    w.entry_duration.setValue(4.0)
    expected = frame_count(5.0, 4.0)  # 20
    assert str(expected) in w.label_recording_frames.text()


def test_computed_plane_label_updates_on_spinbox_change(qtbot, simulated_widget_deps):
    """label_zstack_planes updates to '→ N planes' when zmin/zmax/step change."""
    from control.core.record_zstack_controller import zstack_plane_count
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    w.entry_zmin.setValue(-2.0)
    w.entry_zmax.setValue(2.0)
    w.entry_step.setValue(1.0)
    expected = zstack_plane_count(-2.0, 2.0, 1.0)  # 5
    assert str(expected) in w.label_zstack_planes.text()


def test_computed_plane_label_degrades_gracefully_on_invalid_range(qtbot, simulated_widget_deps):
    """label_zstack_planes shows '—' or '--' when z_max < z_min (invalid range)."""
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    w.entry_zmin.setValue(3.0)
    w.entry_zmax.setValue(-3.0)  # invalid: max < min
    # Label should show a placeholder, not crash
    text = w.label_zstack_planes.text()
    assert text == "-- planes"


def test_build_parameters_uses_inline_editor_values(qtbot, simulated_widget_deps):
    """build_parameters() reflects inline editor values, not just channel name lookup."""
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)
    w.lineEdit_savingDir.setText("/tmp/test")
    w.checkbox_recording.setChecked(True)
    w.checkbox_zstack.setChecked(False)

    # Set inline editor values
    w.entry_recording_exposure.setValue(99.0)
    w.entry_recording_gain.setValue(1.5)
    w.entry_recording_illumination.setValue(60.0)

    params = w.build_parameters()
    assert params.recording_channel is not None
    assert params.recording_channel.exposure_time == pytest.approx(99.0)
    assert params.recording_channel.analog_gain == pytest.approx(1.5)
    assert params.recording_channel.illumination_intensity == pytest.approx(60.0)


def test_zstack_row_inline_editors_reflected_in_build_parameters(qtbot, simulated_widget_deps):
    """Z-stack row inline editor values are used when build_parameters() is called."""
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)
    w.lineEdit_savingDir.setText("/tmp/test")
    w.checkbox_zstack.setChecked(True)
    w.checkbox_recording.setChecked(False)
    w.entry_zmin.setValue(-1.0)
    w.entry_zmax.setValue(1.0)
    w.entry_step.setValue(1.0)

    w._add_zstack_channel_row("BF LED matrix full")
    # Set inline editor values for the row
    w._set_zstack_row_values("BF LED matrix full", exposure=77.0, gain=3.0, illumination=45.0)

    params = w.build_parameters()
    assert len(params.zstack_channels) == 1
    ch = params.zstack_channels[0]
    assert ch.exposure_time == pytest.approx(77.0)
    assert ch.analog_gain == pytest.approx(3.0)
    assert ch.illumination_intensity == pytest.approx(45.0)
