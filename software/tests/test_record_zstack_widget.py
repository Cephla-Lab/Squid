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
from unittest.mock import MagicMock, patch

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
    """Copy-from-Live reads currentConfiguration and sets the recording table row."""
    from control.widgets import RecordZStackMultiPointWidget

    live_ch = _make_live_channel("Fluorescence 488 nm Ex", exposure=33.0, gain=2.5, intensity=75.0)
    simulated_widget_deps["liveController"].currentConfiguration = live_ch

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    # Enable the recording group so its child widgets are interactive
    w.checkbox_recording.setChecked(True)
    w.btn_copy_from_live.click()

    # Channel combo in table row should be updated to the live channel name
    assert w._recording_channel_name() == "Fluorescence 488 nm Ex"
    # Spinboxes in table row should reflect live channel values
    assert w._recording_exposure() == pytest.approx(33.0)
    assert w._recording_gain() == pytest.approx(2.5)
    assert w._recording_illumination() == pytest.approx(75.0)


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
    """build_parameters() reflects recording table row values, not just channel name lookup."""
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)
    w.lineEdit_savingDir.setText("/tmp/test")
    w.checkbox_recording.setChecked(True)
    w.checkbox_zstack.setChecked(False)

    # Set values via the table's spinbox cell widgets
    w._recording_exp_spin.setValue(99.0)
    w._recording_gain_spin.setValue(1.5)
    w._recording_illum_spin.setValue(60.0)

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


# ---------------------------------------------------------------------------
# E3: Start/Stop handoff to RecordZStackController
# ---------------------------------------------------------------------------


def _make_stub_controller():
    """Stub RecordZStackController that records calls and reports not-in-progress."""
    ctrl = MagicMock()
    ctrl.acquisition_in_progress.return_value = False
    return ctrl


def _make_valid_widget(qtbot, deps):
    """Return a widget pre-configured for a valid z-stack-only acquisition."""
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**deps)
    qtbot.addWidget(w)
    w.lineEdit_savingDir.setText("/tmp/test_e3")
    w.checkbox_zstack.setChecked(True)
    w.checkbox_recording.setChecked(False)
    w.entry_zmin.setValue(-2.0)
    w.entry_zmax.setValue(2.0)
    w.entry_step.setValue(1.0)
    w._add_zstack_channel_row("BF LED matrix full")
    return w


def test_toggle_acquisition_start_valid_calls_run_acquisition(qtbot, simulated_widget_deps):
    """Clicking Start with valid params calls run_acquisition() exactly once."""
    ctrl = _make_stub_controller()
    simulated_widget_deps["recordZStackController"] = ctrl

    w = _make_valid_widget(qtbot, simulated_widget_deps)

    # Simulate pressing the Start button (checked=True)
    w.toggle_acquisition(True)

    ctrl.run_acquisition.assert_called_once()


def test_toggle_acquisition_start_valid_pushes_params_to_controller(qtbot, simulated_widget_deps):
    """Clicking Start with valid params calls run_acquisition(params) with correct values."""
    from control.core.record_zstack_controller import RecordZStackAcquisitionParameters

    ctrl = _make_stub_controller()
    simulated_widget_deps["recordZStackController"] = ctrl

    w = _make_valid_widget(qtbot, simulated_widget_deps)
    w.toggle_acquisition(True)

    # run_acquisition should be called once with a RecordZStackAcquisitionParameters object
    ctrl.run_acquisition.assert_called_once()
    call_args = ctrl.run_acquisition.call_args
    params = call_args.args[0] if call_args.args else call_args.kwargs.get("params")
    assert isinstance(params, RecordZStackAcquisitionParameters)
    assert params.base_path == "/tmp/test_e3"
    assert params.zstack_enabled is True
    assert params.recording_enabled is False
    assert params.z_min_um == pytest.approx(-2.0)
    assert params.z_max_um == pytest.approx(2.0)
    assert params.z_step_um == pytest.approx(1.0)


def test_toggle_acquisition_start_invalid_no_phase_does_not_call_run(qtbot, simulated_widget_deps):
    """Clicking Start with both phases disabled does NOT call run_acquisition()."""
    ctrl = _make_stub_controller()
    simulated_widget_deps["recordZStackController"] = ctrl

    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)
    w.lineEdit_savingDir.setText("/tmp/test_e3")
    w.checkbox_zstack.setChecked(False)
    w.checkbox_recording.setChecked(False)

    with patch("control.widgets.QMessageBox.warning") as mock_warn:
        w.toggle_acquisition(True)

    ctrl.run_acquisition.assert_not_called()
    mock_warn.assert_called_once()
    # Button must be un-checked after a failed start
    assert not w.btn_startAcquisition.isChecked()


def test_toggle_acquisition_start_invalid_bad_zrange_shows_warning(qtbot, simulated_widget_deps):
    """Clicking Start with z_max <= z_min pops a warning and does not start."""
    ctrl = _make_stub_controller()
    simulated_widget_deps["recordZStackController"] = ctrl

    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)
    w.lineEdit_savingDir.setText("/tmp/test_e3")
    w.checkbox_zstack.setChecked(True)
    w.entry_zmin.setValue(3.0)
    w.entry_zmax.setValue(-3.0)  # invalid: max < min
    w.entry_step.setValue(1.0)
    w._add_zstack_channel_row("BF LED matrix full")

    warning_args = []
    with patch("control.widgets.QMessageBox.warning", side_effect=lambda *a: warning_args.append(a)):
        w.toggle_acquisition(True)

    ctrl.run_acquisition.assert_not_called()
    assert len(warning_args) == 1
    assert not w.btn_startAcquisition.isChecked()


def test_toggle_acquisition_stop_calls_request_abort(qtbot, simulated_widget_deps):
    """Un-pressing the Start button (pressed=False) calls request_abort()."""
    ctrl = _make_stub_controller()
    simulated_widget_deps["recordZStackController"] = ctrl

    w = _make_valid_widget(qtbot, simulated_widget_deps)

    # Simulate pressing stop (unchecked)
    w.toggle_acquisition(False)

    ctrl.request_abort.assert_called_once()
    ctrl.run_acquisition.assert_not_called()


# ---------------------------------------------------------------------------
# New tests added by fix-batch3
# ---------------------------------------------------------------------------


# --- IMPORTANT 9b: frame_count < 1 rejected ---


def test_validate_helper_recording_zero_frame_count():
    """fps × duration rounds to 0 frames — must be rejected."""
    # 0.1 fps × 1.0 s = 0.1 → round → 0 frames
    params = _base_params(recording_enabled=True, fps=0.1, duration_s=1.0, zstack_enabled=False)
    err = _validate_record_zstack_params(**params)
    assert err is not None
    assert "0 frames" in err or "frame" in err.lower()


def test_validate_helper_recording_one_frame_is_valid():
    """fps × duration = exactly 1 frame — must pass."""
    # 1.0 fps × 1.0 s = 1 frame
    params = _base_params(recording_enabled=True, fps=1.0, duration_s=1.0, zstack_enabled=False)
    assert _validate_record_zstack_params(**params) is None


def test_validate_helper_recording_borderline_zero_frames():
    """fps × duration just below 0.5 → rounds to 0 — must be rejected."""
    params = _base_params(recording_enabled=True, fps=0.4, duration_s=1.0, zstack_enabled=False)
    err = _validate_record_zstack_params(**params)
    assert err is not None


# --- IMPORTANT 3+4: well count handles None / glass-slide ---


def test_get_selected_well_count_glass_slide_returns_one(qtbot, simulated_widget_deps):
    """_get_selected_well_count returns 1 (not 0) when scanCoordinates.get_selected_wells() is None (glass-slide)."""
    from unittest.mock import MagicMock
    from control.widgets import RecordZStackMultiPointWidget

    sc = MagicMock()
    sc.get_selected_wells.return_value = None  # glass-slide: returns None
    simulated_widget_deps["scanCoordinates"] = sc

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    assert w._get_selected_well_count() == 1


def test_get_selected_well_count_empty_wellplate_returns_zero(qtbot, simulated_widget_deps):
    """_get_selected_well_count returns 0 when no wells are selected on a wellplate."""
    from unittest.mock import MagicMock
    from control.widgets import RecordZStackMultiPointWidget

    sc = MagicMock()
    sc.get_selected_wells.return_value = {}  # wellplate, no wells selected
    simulated_widget_deps["scanCoordinates"] = sc

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    assert w._get_selected_well_count() == 0


def test_get_selected_well_count_wellplate_selection(qtbot, simulated_widget_deps):
    """_get_selected_well_count reflects current well_selector, not a stale cached widget."""
    from unittest.mock import MagicMock
    from control.widgets import RecordZStackMultiPointWidget

    sc = MagicMock()
    sc.get_selected_wells.return_value = {"A1": (0.0, 0.0), "A2": (1.0, 0.0)}
    simulated_widget_deps["scanCoordinates"] = sc

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    assert w._get_selected_well_count() == 2

    # Simulate a plate-format change that updates the scanCoordinates well_selector
    sc.get_selected_wells.return_value = {"B1": (0.0, 1.0)}
    assert w._get_selected_well_count() == 1  # picks up new selection, no stale cache


def test_validate_rejects_no_wells_selected(qtbot, simulated_widget_deps):
    """validate() returns an error when no wells are selected (dict empty from scanCoordinates)."""
    from unittest.mock import MagicMock
    from control.widgets import RecordZStackMultiPointWidget

    sc = MagicMock()
    sc.get_selected_wells.return_value = {}  # no wells
    simulated_widget_deps["scanCoordinates"] = sc

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)
    w.lineEdit_savingDir.setText("/tmp/test")
    w.checkbox_zstack.setChecked(True)
    w.entry_zmin.setValue(-1.0)
    w.entry_zmax.setValue(1.0)
    w.entry_step.setValue(1.0)
    w._add_zstack_channel_row("BF LED matrix full")

    err = w.validate()
    assert err is not None
    assert "well" in err.lower()


# --- MEDIUM: _abort_event threading.Event path ---


def test_abort_event_set_on_request_abort():
    """request_abort() sets the abort event; run_acquisition() clears it before starting."""
    import threading
    from unittest.mock import MagicMock, patch

    ctrl_kwargs = dict(
        microscope=MagicMock(),
        live_controller=MagicMock(),
        laser_autofocus_controller=MagicMock(),
        objective_store=MagicMock(),
        scan_coordinates=MagicMock(),
        callbacks=MagicMock(),
    )

    with patch("control._def.Acquisition.USE_MULTIPROCESSING", False):
        from control.core.record_zstack_controller import RecordZStackController

        ctrl = RecordZStackController(**ctrl_kwargs)

        # Event should start clear
        assert not ctrl._abort_event.is_set()

        ctrl.request_abort()
        assert ctrl._abort_event.is_set()

        # Simulate run_acquisition clearing the event before spawning the worker
        ctrl._abort_event.clear()
        assert not ctrl._abort_event.is_set()


# ---------------------------------------------------------------------------
# Fix-batch5: signal_acquisition_started wiring
# ---------------------------------------------------------------------------


def test_signal_acquisition_started_emits_true_on_start(qtbot, simulated_widget_deps):
    """signal_acquisition_started(True) is emitted when toggle_acquisition(True) succeeds."""
    ctrl = _make_stub_controller()
    simulated_widget_deps["recordZStackController"] = ctrl

    w = _make_valid_widget(qtbot, simulated_widget_deps)

    emitted = []
    w.signal_acquisition_started.connect(emitted.append)

    w.toggle_acquisition(True)

    assert emitted == [True]


def test_signal_acquisition_started_not_emitted_on_invalid_start(qtbot, simulated_widget_deps):
    """signal_acquisition_started must NOT emit when validation rejects the start."""
    ctrl = _make_stub_controller()
    simulated_widget_deps["recordZStackController"] = ctrl

    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)
    w.lineEdit_savingDir.setText("/tmp/test")
    w.checkbox_zstack.setChecked(False)
    w.checkbox_recording.setChecked(False)

    emitted = []
    w.signal_acquisition_started.connect(emitted.append)

    with patch("control.widgets.QMessageBox.warning"):
        w.toggle_acquisition(True)

    assert emitted == []


def test_signal_acquisition_started_emits_false_on_finish(qtbot, simulated_widget_deps):
    """signal_acquisition_started(False) is emitted when acquisition_is_finished() is called."""
    ctrl = _make_stub_controller()
    simulated_widget_deps["recordZStackController"] = ctrl

    w = _make_valid_widget(qtbot, simulated_widget_deps)

    emitted = []
    w.signal_acquisition_started.connect(emitted.append)

    w.acquisition_is_finished()

    assert emitted == [False]


def test_signal_acquisition_started_emitted_before_run_acquisition(qtbot, simulated_widget_deps):
    """emit(True) must happen BEFORE run_acquisition() spawns the worker thread.

    Otherwise a fast/one-frame acquisition could finish (emit False) before this
    widget reaches the emit(True) line, leaving the UI permanently locked.
    """
    events = []

    ctrl = _make_stub_controller()
    ctrl.run_acquisition.side_effect = lambda *a, **k: events.append("run")
    simulated_widget_deps["recordZStackController"] = ctrl

    w = _make_valid_widget(qtbot, simulated_widget_deps)
    w.signal_acquisition_started.connect(lambda started: events.append(f"emit({started})"))

    w.toggle_acquisition(True)

    # The True emit must come first, then run_acquisition.
    assert events == ["emit(True)", "run"]


def test_signal_acquisition_started_emits_false_when_run_raises(qtbot, simulated_widget_deps):
    """If run_acquisition() raises, the UI is unlocked: emit(True) then emit(False)."""
    ctrl = _make_stub_controller()
    ctrl.run_acquisition.side_effect = RuntimeError("boom starting worker")
    simulated_widget_deps["recordZStackController"] = ctrl

    w = _make_valid_widget(qtbot, simulated_widget_deps)

    emitted = []
    w.signal_acquisition_started.connect(emitted.append)

    w.toggle_acquisition(True)

    assert emitted == [True, False]
    # Button must be un-checked after a failed start.
    assert not w.btn_startAcquisition.isChecked()


# ---------------------------------------------------------------------------
# FOV-grid wiring tests (entry_scan_size / entry_overlap / combobox_shape)
# ---------------------------------------------------------------------------


def test_entry_scan_size_exists(qtbot, simulated_widget_deps):
    """entry_scan_size widget is created on the widget."""
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)
    assert hasattr(w, "entry_scan_size")


def test_fov_grid_wired_overlap_calls_set_well_coordinates(qtbot, simulated_widget_deps):
    """Changing entry_overlap triggers scanCoordinates.set_well_coordinates with correct args."""
    from unittest.mock import MagicMock
    from control.widgets import RecordZStackMultiPointWidget

    sc = MagicMock()
    simulated_widget_deps["scanCoordinates"] = sc

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    sc.reset_mock()
    w.entry_overlap.setValue(20.0)

    sc.set_well_coordinates.assert_called()
    call_args = sc.set_well_coordinates.call_args
    _scan_size_mm, overlap_pct, _shape = call_args.args
    assert overlap_pct == pytest.approx(20.0)


def test_fov_grid_wired_scan_size_calls_set_well_coordinates(qtbot, simulated_widget_deps):
    """Changing entry_scan_size triggers scanCoordinates.set_well_coordinates with correct args."""
    from unittest.mock import MagicMock
    from control.widgets import RecordZStackMultiPointWidget

    sc = MagicMock()
    simulated_widget_deps["scanCoordinates"] = sc

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    sc.reset_mock()
    w.entry_scan_size.setValue(2.5)

    sc.set_well_coordinates.assert_called()
    call_args = sc.set_well_coordinates.call_args
    scan_size_mm, _overlap_pct, _shape = call_args.args
    assert scan_size_mm == pytest.approx(2.5)


def test_fov_grid_wired_shape_calls_set_well_coordinates(qtbot, simulated_widget_deps):
    """Changing combobox_shape triggers scanCoordinates.set_well_coordinates with correct shape."""
    from unittest.mock import MagicMock
    from control.widgets import RecordZStackMultiPointWidget

    sc = MagicMock()
    simulated_widget_deps["scanCoordinates"] = sc

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    sc.reset_mock()
    w.combobox_shape.setCurrentText("Circle")

    sc.set_well_coordinates.assert_called()
    call_args = sc.set_well_coordinates.call_args
    _scan_size_mm, _overlap_pct, shape = call_args.args
    assert shape == "Circle"


def test_fov_grid_wired_set_well_coordinates_receives_all_three_args(qtbot, simulated_widget_deps):
    """_update_scan_regions passes scan_size_mm, overlap_percent, shape to set_well_coordinates."""
    from unittest.mock import MagicMock
    from control.widgets import RecordZStackMultiPointWidget

    sc = MagicMock()
    simulated_widget_deps["scanCoordinates"] = sc

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    w.entry_scan_size.setValue(1.5)
    w.entry_overlap.setValue(15.0)
    w.combobox_shape.setCurrentText("Rectangle")

    sc.reset_mock()
    w._update_scan_regions()

    sc.set_well_coordinates.assert_called_once_with(pytest.approx(1.5), pytest.approx(15.0), "Rectangle")


def test_fov_grid_clears_regions_before_set_well_coordinates(qtbot, simulated_widget_deps):
    """_update_scan_regions must clear_regions() BEFORE set_well_coordinates().

    Regression guard: set_well_coordinates only adds wells not already present, so
    without clearing first, already-selected wells keep their old tile geometry and
    a new size/overlap/shape is silently ignored.
    """
    from unittest.mock import MagicMock, call
    from control.widgets import RecordZStackMultiPointWidget

    sc = MagicMock()
    sc.has_regions.return_value = True  # there is an existing region to clear
    simulated_widget_deps["scanCoordinates"] = sc

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    sc.reset_mock()
    sc.has_regions.return_value = True
    w._update_scan_regions()

    # clear_regions must be called, and must precede set_well_coordinates.
    sc.clear_regions.assert_called_once()
    sc.set_well_coordinates.assert_called_once()
    relevant = [c for c in sc.method_calls if c[0] in ("clear_regions", "set_well_coordinates")]
    assert relevant[0] == call.clear_regions()
    assert relevant[1][0] == "set_well_coordinates"


def test_fov_grid_no_crash_without_scan_coordinates(qtbot, simulated_widget_deps):
    """_update_scan_regions is a no-op when scanCoordinates is None."""
    from control.widgets import RecordZStackMultiPointWidget

    simulated_widget_deps["scanCoordinates"] = None

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    # Must not raise
    w._update_scan_regions()


def test_toggle_acquisition_calls_update_scan_regions_before_run(qtbot, simulated_widget_deps):
    """toggle_acquisition calls _update_scan_regions before run_acquisition."""
    from unittest.mock import MagicMock

    sc = MagicMock()
    # Non-empty well selection so validate() passes (no modal warning dialog).
    sc.get_selected_wells.return_value = {"A1": (0.0, 0.0)}
    simulated_widget_deps["scanCoordinates"] = sc

    ctrl = _make_stub_controller()
    simulated_widget_deps["recordZStackController"] = ctrl

    w = _make_valid_widget(qtbot, simulated_widget_deps)
    w.scanCoordinates = sc

    sc.reset_mock()
    ctrl.reset_mock()
    ctrl.acquisition_in_progress.return_value = False

    call_order = []
    sc.set_well_coordinates.side_effect = lambda *a: call_order.append("set_well_coordinates")
    ctrl.run_acquisition.side_effect = lambda *a: call_order.append("run_acquisition")

    w.toggle_acquisition(True)

    assert "set_well_coordinates" in call_order
    assert "run_acquisition" in call_order
    assert call_order.index("set_well_coordinates") < call_order.index("run_acquisition")


def test_emit_selected_channels_is_a_safe_noop(qtbot, simulated_widget_deps):
    """gui_hcs.onTabChanged duck-types emit_selected_channels() on whichever record
    tab widget becomes current; the widget must provide it (same contract as
    display_progress_bar) or every switch to the tab raises AttributeError."""
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    w.emit_selected_channels()  # must not raise


def test_refresh_channel_list_repopulates_combos(qtbot, simulated_widget_deps):
    """Channel sets are per-objective: after an objective/profile change the
    combos must repopulate, or stale names silently fall back to a bare
    channel with no illumination source (dark acquisition)."""
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)

    w.liveController.get_channels.return_value = [
        _make_channel("New Channel A"),
        _make_channel("New Channel B"),
    ]
    w.refresh_channel_list()

    rec_names = [w._recording_ch_combo.itemText(i) for i in range(w._recording_ch_combo.count())]
    add_names = [w.combobox_zstack_add_channel.itemText(i) for i in range(w.combobox_zstack_add_channel.count())]
    assert rec_names == ["New Channel A", "New Channel B"]
    assert add_names == ["New Channel A", "New Channel B"]


def test_refresh_channel_list_drops_stale_zstack_rows(qtbot, simulated_widget_deps):
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)
    w._add_zstack_channel_row("BF LED matrix full")  # valid for the old objective

    w.liveController.get_channels.return_value = [_make_channel("New Channel A")]
    w.refresh_channel_list()

    assert "BF LED matrix full" not in w._zstack_channel_names


def test_refresh_channel_list_preserves_state_on_failure(qtbot, simulated_widget_deps):
    """Round-2: a transient get_channels failure (or empty result) during an
    objective/profile switch must NOT wipe the user's configured z-stack rows
    and channel combos — keep the existing lists until a successful refresh."""
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**simulated_widget_deps)
    qtbot.addWidget(w)
    w._add_zstack_channel_row("BF LED matrix full")
    combo_count_before = w._recording_ch_combo.count()

    w.liveController.get_channels.side_effect = RuntimeError("config repo hiccup")
    w.refresh_channel_list()
    assert w._zstack_channel_names == ["BF LED matrix full"], "rows wiped on transient failure"
    assert w._recording_ch_combo.count() == combo_count_before, "combo wiped on transient failure"

    w.liveController.get_channels.side_effect = None
    w.liveController.get_channels.return_value = []
    w.refresh_channel_list()
    assert w._zstack_channel_names == ["BF LED matrix full"], "rows wiped on empty channel list"
    assert w._recording_ch_combo.count() == combo_count_before
