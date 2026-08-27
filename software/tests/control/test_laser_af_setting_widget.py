"""Tests for the one-click 'Run Laser AF Test' button in LaserAutofocusSettingWidget."""

import pytest

import control._def
import control.microscope
import tests.control.test_stubs as ts
from control.core.config import ConfigRepository
from control.widgets import LaserAutofocusSettingWidget


class _StreamHandlerStub:
    def set_display_fps(self, fps):
        self.display_fps = fps


@pytest.fixture
def widget_env(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(control._def, "SUPPORT_LASER_AUTOFOCUS", True)
    monkeypatch.setattr(control._def, "DEFAULT_SAVING_PATH", str(tmp_path))
    scope = control.microscope.Microscope.build_from_global_config(True)
    # Keep per-objective laser AF configs (written by set_reference) out of the repo tree.
    scope.config_repo = ConfigRepository(base_path=tmp_path)
    # Start in-soft-limit like a focused stage (the simulated stage boots below the Z minimum).
    z_config = scope.stage.get_config().Z_AXIS
    scope.stage.move_z_to((z_config.MIN_POSITION + z_config.MAX_POSITION) / 2.0)
    controller = ts.get_test_laser_autofocus_controller(scope)

    widget = LaserAutofocusSettingWidget(
        streamHandler=_StreamHandlerStub(),
        liveController=controller.liveController,
        laserAutofocusController=controller,
        stretch=False,
    )
    qtbot.addWidget(widget)
    yield widget, controller
    scope.close()


def test_run_button_gated_on_initialization_and_reference(widget_env):
    widget, controller = widget_env

    assert not widget.btn_run_laser_af_test.isEnabled()

    assert controller.initialize_auto()
    widget.update_values()
    assert not widget.btn_run_laser_af_test.isEnabled()  # initialized but no reference yet

    assert controller.set_reference()  # signal_reference_changed refreshes the gating
    assert widget.btn_run_laser_af_test.isEnabled()


def test_run_button_executes_test_and_reports_results(qtbot, widget_env):
    widget, controller = widget_env
    assert controller.initialize_auto()
    assert controller.set_reference()

    widget.test_sweep_range_spinbox.setValue(10.0)
    widget.test_sweep_steps_spinbox.setValue(3)
    widget.test_repeat_cycles_spinbox.setValue(2)
    widget.test_repeat_offset_spinbox.setValue(5.0)
    widget.test_stability_samples_spinbox.setValue(0)  # skip the stability phase
    widget.test_save_images_checkbox.setChecked(False)

    with qtbot.waitSignal(widget.laser_af_test_runner.signal_finished, timeout=60000):
        widget.btn_run_laser_af_test.click()
        assert not widget.btn_run_laser_af_test.isEnabled()  # disabled while running

    qtbot.waitUntil(lambda: widget.btn_run_laser_af_test.isEnabled(), timeout=5000)
    result_text = widget.laser_af_test_result_label.text()
    assert "Saved to:" in result_text
    assert "laser_af_tests" in result_text
