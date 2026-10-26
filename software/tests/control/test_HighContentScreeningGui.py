import logging
import threading
import unittest.mock

import pytest

import control._def

import control.gui_hcs
import control.utils
import control.widgets
from qtpy.QtGui import QCloseEvent
from qtpy.QtWidgets import QMessageBox

import control.microcontroller
import control.microscope
import squid.abc
import squid.stage.cephla
import squid.stage.utils
from control._def import STARTUP_STATE_MIN_FIRMWARE
from control.firmware_sim_serial import FirmwareSimSerial
from control.startup_homing import StartupHoming


@pytest.fixture
def confirm_exit_yes(monkeypatch):
    """Auto-accept the 'Confirm Exit' dialog GUI shutdown shows, or teardown hangs forever."""

    def confirm_exit(parent, title, text, *args, **kwargs):
        if title == "Confirm Exit":
            return QMessageBox.Yes
        raise RuntimeError(f"Unexpected QMessageBox: {title} - {text}")

    monkeypatch.setattr(QMessageBox, "question", confirm_exit)


def test_create_simulated_hcs_with_or_without_piezo(qtbot, confirm_exit_yes):
    # This just tests to make sure we can successfully create a simulated hcs gui with or without
    # the piezo objective.
    control._def.HAS_OBJECTIVE_PIEZO = True
    scope_with = control.microscope.Microscope.build_from_global_config(True)
    with_piezo = control.gui_hcs.HighContentScreeningGui(microscope=scope_with, is_simulation=True)
    qtbot.add_widget(with_piezo)

    control._def.HAS_OBJECTIVE_PIEZO = False
    scope_without = control.microscope.Microscope.build_from_global_config(True)
    without_piezo = control.gui_hcs.HighContentScreeningGui(microscope=scope_without, is_simulation=True)
    qtbot.add_widget(without_piezo)


def test_tab_change_to_simple_recording_does_not_raise(qtbot, monkeypatch, confirm_exit_yes):
    """Regression: onTabChanged used to call emit_selected_channels() on every record
    tab and toggleAcquisitionStart called display_progress_bar() on the current tab,
    but RecordingWidget (Simple Recording) has neither method, so selecting the tab
    raised AttributeError on machines with ENABLE_RECORDING on."""

    # gui_hcs star-imports _def, so patch its module-level binding before construction
    # to get the "Simple Recording" tab added.
    monkeypatch.setattr(control.gui_hcs, "ENABLE_RECORDING", True)

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    recording_index = win.recordTabWidget.indexOf(win.recordingControlWidget)
    assert recording_index >= 0, "Simple Recording tab was not added despite ENABLE_RECORDING"

    # Selecting the tab fires currentChanged -> onTabChanged; must not raise.
    win.recordTabWidget.setCurrentIndex(recording_index)
    win.onTabChanged(recording_index)

    # The same widget must also survive an acquisition start/stop notification
    # (workflow/TCP acquisitions can start while a non-multipoint tab is current).
    win.toggleAcquisitionStart(True)
    win.toggleAcquisitionStart(False)


def test_acquisition_start_emits_selected_channels(qtbot, confirm_exit_yes):
    """The napari multichannel viewer is initialized from signal_acquisition_channels.
    That signal must be emitted when the acquisition starts (alongside
    signal_acquisition_shape), for both the button path and the TCP path."""

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    widget = win.wellplateMultiPointWidget
    emitted = []
    widget.signal_acquisition_channels.connect(emitted.append)

    # _set_ui_acquisition_running is the shared start path (button + TCP/invokeMethod).
    widget._set_ui_acquisition_running(nz=1, delta_z_um=1.0)
    try:
        assert emitted, "signal_acquisition_channels was not emitted at acquisition start"
        assert emitted[-1] == widget.channel_sequence.ordered_selected_names()
    finally:
        # Unwind the acquisition-running UI state (signal_acquisition_started=True
        # disabled the other tabs via toggleAcquisitionStart).
        win.toggleAcquisitionStart(False)


def test_acquisition_finished_flushes_napari_multichannel_viewer(qtbot, monkeypatch, confirm_exit_yes):
    """The multichannel viewer coalesces refreshes and shows 2D while acquiring; the end of the
    acquisition must reach it to draw the final plane and restore the user's 2D/3D mode."""
    calls = []
    monkeypatch.setattr(control.widgets.NapariMultiChannelWidget, "finishAcquisition", lambda self: calls.append(self))

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    win.multipointController.acquisition_finished.emit()
    assert calls == [win.napariMultiChannelWidget]

    # Performance Mode disconnects the napari display feeds; enabled mid-acquisition it must
    # not cut the viewer off from the end of the run (it would stay in 2D).
    win.performanceModeToggle.setChecked(True)
    win.togglePerformanceMode()
    win.multipointController.acquisition_finished.emit()
    assert calls == [win.napariMultiChannelWidget] * 2

    # Leaving Performance Mode reconnects the feeds without connecting this a second time.
    win.performanceModeToggle.setChecked(False)
    win.togglePerformanceMode()
    win.multipointController.acquisition_finished.emit()
    assert calls == [win.napariMultiChannelWidget] * 3


def test_image_display_signals_connected_once(qtbot, monkeypatch, confirm_exit_yes):
    """Regression: make_connections and makeNapariConnections both used to wire the
    non-Napari image-display signals, causing slots to fire twice per click/scroll."""

    # Patch slots at the class level *before* construction so signal-slot bindings
    # made inside __init__ resolve to these counters.
    z_calls = []
    click_calls = []
    monkeypatch.setattr(
        control.gui_hcs.HighContentScreeningGui, "move_z_from_scroll", lambda self, delta_um: z_calls.append(delta_um)
    )
    monkeypatch.setattr(
        control.gui_hcs.HighContentScreeningGui,
        "move_from_click_image",
        lambda self, *args, **kwargs: click_calls.append(args),
    )

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    win.imageDisplayWindow.signal_z_um_delta.emit(1.0)
    win.imageDisplayWindow.image_click_coordinates.emit(0.0, 0.0, 0, 0)

    assert len(z_calls) == 1, f"signal_z_um_delta wired {len(z_calls)} times, expected 1"
    assert len(click_calls) == 1, f"image_click_coordinates wired {len(click_calls)} times, expected 1"


def test_cleanup_closes_stage_before_microcontroller(qtbot, monkeypatch, confirm_exit_yes):
    """The stage may own its own transport (e.g. the PI C-414 serial handle), so cleanup
    must call stage.close() — before the microcontroller, mirroring Microscope.close()."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)

    calls = []
    # Shadow the inherited no-op close() so the test can observe the call.
    gui.stage.close = lambda: calls.append("stage")
    original_micro_close = gui.microcontroller.close

    def recording_micro_close():
        calls.append("microcontroller")
        original_micro_close()

    monkeypatch.setattr(gui.microcontroller, "close", recording_micro_close)

    # Keep teardown's closeEvent from re-running cleanup against closed devices. Instance
    # attr, not monkeypatch: it must outlive monkeypatch teardown, which runs before qtbot
    # closes widgets.
    gui.closeEvent = lambda event: event.accept()

    gui._cleanup_common(for_restart=True)

    assert calls == ["stage", "microcontroller"]


def test_cleanup_continues_when_the_light_source_fails_to_close(qtbot, monkeypatch, confirm_exit_yes):
    """A light source that errors on shut_down (e.g. its link dropped) is only logged, even on a full
    shutdown, so the stage and microcontroller are still closed."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)

    class DroppedLightSource:
        def shut_down(self):
            raise ConnectionError("light source link dropped")

    scope.illumination_controller.light_source = DroppedLightSource()
    calls = []
    gui.stage.close = lambda: calls.append("stage")
    original_micro_close = gui.microcontroller.close

    def recording_micro_close():
        calls.append("microcontroller")
        original_micro_close()

    monkeypatch.setattr(gui.microcontroller, "close", recording_micro_close)
    gui.closeEvent = lambda event: event.accept()  # keep teardown from re-running cleanup

    gui._cleanup_common(for_restart=False)

    assert calls == ["stage", "microcontroller"]


def test_cleanup_stops_the_versalase_tab_before_shutting_the_lasers_down(qtbot, monkeypatch, confirm_exit_yes):
    """No status read may use the lasers while they are shut down and their devices closed."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)
    order = []

    class RecordingLightSource:
        def shut_down(self):
            order.append("lasers shut down")

    class RecordingTab:
        def stop(self):
            order.append("tab stopped")

    scope.illumination_controller.light_source = RecordingLightSource()
    gui.versaLaseWidget = RecordingTab()
    gui.closeEvent = lambda event: event.accept()  # keep teardown from re-running cleanup

    gui._cleanup_common(for_restart=True)

    assert order == ["tab stopped", "lasers shut down"]


def test_create_simulated_hcs_with_dragonfly(qtbot, monkeypatch, confirm_exit_yes):
    """Regression: with a Dragonfly as the spinning disk unit, GUI construction wired the
    X-Light-only iris signals onto DragonflyConfocalWidget and raised AttributeError."""
    import control.widgets
    from tests.control.spinning_disk_test_utils import enable_spinning_disk

    enable_spinning_disk(monkeypatch, control.gui_hcs, dragonfly=True)

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    assert isinstance(win.spinningDiskConfocalWidget, control.widgets.DragonflyConfocalWidget)
    assert win.cameraTabWidget.indexOf(win.spinningDiskConfocalWidget) >= 0

    # The widget's toggle drives the live controller's confocal mode, as the X-Light one does.
    assert scope.live_controller.is_confocal_mode() is False
    win.spinningDiskConfocalWidget.btn_toggle_confocal.click()
    assert scope.addons.dragonfly.get_modality() == "CONFOCAL"
    assert scope.live_controller.is_confocal_mode() is True


def test_gui_cleanup_closes_the_dragonfly(qtbot, monkeypatch, confirm_exit_yes):
    """closeEvent/restart go through _cleanup_common, which closes devices itself and never
    calls Microscope.close(); the Dragonfly serial port must be released on that path too."""
    import control.serial_peripherals
    from tests.control.spinning_disk_test_utils import enable_spinning_disk

    enable_spinning_disk(monkeypatch, control.gui_hcs, dragonfly=True)

    scope = control.microscope.Microscope.build_from_global_config(True)
    closed = []
    monkeypatch.setattr(scope.addons.dragonfly, "close", lambda: closed.append("dragonfly"))
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)
    gui.closeEvent = lambda event: event.accept()  # keep teardown from re-running cleanup

    gui._cleanup_common(for_restart=True)

    assert closed == ["dragonfly"]


def _set_z_flags(monkeypatch, *, homing_z, pi_focus):
    """Microscope.home_xyz and squid.stage.utils read the flags through control._def; gui_hcs star-imports
    them, so its module-level bindings are patched too."""
    for module in (control._def, control.gui_hcs):
        monkeypatch.setattr(module, "HOMING_ENABLED_Z", homing_z)
        monkeypatch.setattr(module, "USE_PI_FOCUS_STAGE", pi_focus)
        monkeypatch.setattr(module, "HOMING_ENABLED_X", True)
        monkeypatch.setattr(module, "HOMING_ENABLED_Y", True)
    if pi_focus:
        monkeypatch.setattr(control._def, "SIMULATE_PI_FOCUS_STAGE", True)


def _build_gui_with_cached_position(qtbot, monkeypatch, cached):
    """Build the GUI as if `cached` were in cache/last_coords.txt, with the stage's absolute moves mocked
    so a test can assert what startup commanded."""
    monkeypatch.setattr(squid.stage.utils, "get_cached_position", lambda *args, **kwargs: cached)
    scope = control.microscope.Microscope.build_from_global_config(True)
    moves = {axis: unittest.mock.MagicMock(name=f"move_{axis}_to") for axis in ("x", "y", "z")}
    for axis, mock in moves.items():
        monkeypatch.setattr(scope.stage, f"move_{axis}_to", mock)
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)
    return gui, moves


def test_startup_restores_cached_xy_but_not_z_when_z_is_not_referenced(qtbot, monkeypatch, confirm_exit_yes):
    """An XY-only Cephla stage (e.g. the Aries/Dragonfly config) runs with homing_enabled_z = False: its
    cached X/Y must still be restored, and its meaningless cached Z must not be commanded."""
    _set_z_flags(monkeypatch, homing_z=False, pi_focus=False)
    cached = squid.abc.Pos(x_mm=23.0, y_mm=31.0, z_mm=1.5, theta_rad=None)

    _, moves = _build_gui_with_cached_position(qtbot, monkeypatch, cached)

    moves["x"].assert_called_once_with(cached.x_mm)
    moves["y"].assert_called_once_with(cached.y_mm)
    moves["z"].assert_not_called()


def test_pi_focus_stage_is_a_referenced_z_even_without_cephla_z_homing(qtbot, monkeypatch, confirm_exit_yes):
    """A Cephla XY stage with a PI V-308 as its Z has no Cephla Z to home, so it runs with homing_enabled_z
    off; Z is still restored at startup and validated at shutdown."""
    _set_z_flags(monkeypatch, homing_z=False, pi_focus=True)
    cached = squid.abc.Pos(x_mm=23.0, y_mm=31.0, z_mm=0.3, theta_rad=None)
    gui, moves = _build_gui_with_cached_position(qtbot, monkeypatch, cached)
    moves["z"].assert_called_once_with(cached.z_mm)

    cache_position = unittest.mock.MagicMock()
    monkeypatch.setattr(squid.stage.utils, "cache_position", cache_position)
    gui.closeEvent = lambda event: event.accept()  # keep teardown from re-running cleanup
    gui._cleanup_common(for_restart=True)

    cache_position.assert_called_once()
    assert cache_position.call_args.kwargs["validate_z"] is True


def test_there_is_one_utils_menu_with_every_utility(qtbot, monkeypatch, confirm_exit_yes):
    # main_hcs.py used to add a second "Utils" menu next to the one the GUI builds (two "Utils" in the menu bar)
    monkeypatch.setattr(control.gui_hcs, "USE_OBJECTIVE_TURRET", True)
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    menus = [action for action in win.menuBar().actions() if action.text() == "Utils"]
    assert len(menus) == 1
    items = [action.text() for action in menus[0].menu().actions()]
    assert items[:3] == ["Stage Utils", "Reset Objective Turret", "Workflow Runner..."]


def test_objective_prompt_defaults_to_cancel(qtbot):
    import control.widgets
    from control.startup_homing import OBJECTIVE_LOWERED_PROMPT

    box, home_btn = control.widgets._objective_lowered_box(None)
    assert box.text() == OBJECTIVE_LOWERED_PROMPT
    assert home_btn.text() == "Objective lowered – Home"
    assert box.defaultButton() is not home_btn and box.defaultButton().text() == "Cancel – don't home"


# --- Home stage, Home X/Y, the homing lock and the not-homed warning ---


@pytest.fixture
def piezo_only(monkeypatch):
    monkeypatch.setattr(control._def, "IS_PIEZO_ONLY", True)


def _gui(qtbot, **build_kwargs):
    scope = control.microscope.Microscope.build_from_global_config(True, **build_kwargs)
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)
    return gui


def _auto_confirm(monkeypatch):
    """Accept every homing confirmation (the generic OK/Cancel box and the piezo-only prompt). A refusal would open a
    modal information box and hang the test: it raises instead."""
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: QMessageBox.Ok)
    monkeypatch.setattr(control.widgets, "ask_objective_lowered", lambda parent: True)

    def refused(parent, title, text, *args, **kwargs):
        raise AssertionError(f"Unexpected refusal: {title} - {text}")

    monkeypatch.setattr(QMessageBox, "information", refused)


def _capture_warnings(monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *args, **kwargs: warnings.append(text))
    return warnings


def _capture_refusals(monkeypatch):
    refusals = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *args, **kwargs: refusals.append(text))
    return refusals


def _start_a_move_that_stays_under_way(gui):
    """The controller keeps reporting the last command in progress (a move still under way)."""
    sim = gui.microscope.low_level_drivers.microcontroller._serial
    sim.hold_in_progress = True
    gui.stage.move_x(0.5, blocking=False)
    return sim


def _run(fn, **kw):
    """As threaded_operation_helper calls its callback: (True, None) or (False, the error)."""
    try:
        fn(**kw)
        return True, None
    except Exception as e:
        return False, str(e)


def test_home_stage_blocker_refuses_autofocus_but_not_the_host_busy_flag(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    monkeypatch.setattr(gui.autofocusController, "autofocus_in_progress", True)
    assert "autofocus" in gui._stage_homing_blocker()
    monkeypatch.setattr(gui.autofocusController, "autofocus_in_progress", False)
    # The host's flag is set by every command (live's included): it is not evidence that the stage moves.
    monkeypatch.setattr(gui.stage, "get_state", lambda: squid.abc.StageStage(busy=True))
    assert gui._stage_homing_blocker() is None


def test_home_stage_proceeds_with_only_the_host_busy_flag_set(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    _auto_confirm(monkeypatch)
    micro = gui.microscope.low_level_drivers.microcontroller
    qtbot.waitUntil(lambda: gui.stage.reports_stopped(), timeout=2000)  # a fresh COMPLETED report
    micro.mcu_cmd_execution_in_progress = True  # e.g. live's last command, its ack not read yet
    finished = []
    gui.signal_home_stage_finished.connect(lambda ok, error: finished.append((ok, error)))
    gui._on_home_stage_requested()  # a refusal would raise (_auto_confirm)
    qtbot.waitUntil(lambda: bool(finished), timeout=10000)
    assert finished == [(True, "")]
    qtbot.waitUntil(lambda: not gui.microscope.stage_homing_in_progress, timeout=5000)


def test_home_stage_refused_while_the_stage_is_still_moving(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    _auto_confirm(monkeypatch)
    refusals = _capture_refusals(monkeypatch)
    dispatched = []
    monkeypatch.setattr(control.utils, "threaded_operation_helper", lambda *args, **kwargs: dispatched.append(args))
    gui.liveController.start_live()
    sim = _start_a_move_that_stays_under_way(gui)
    gui._on_home_stage_requested()
    assert refusals == ["Homing refused: the stage is still moving."]
    assert dispatched == []  # nothing homed
    # The claim stays held until the stage stops: live paused, controls locked
    assert gui.microscope.stage_homing_in_progress and not gui.liveController.is_live
    assert not gui.navigationWidget.isEnabled()
    sim.hold_in_progress = False
    sim.send_status_packet()
    qtbot.waitUntil(
        lambda: not gui.microscope.stage_homing_in_progress
        and gui.liveController.is_live
        and gui.navigationWidget.isEnabled(),
        timeout=5000,
    )


def test_home_stage_claims_before_dispatch_and_restores_live(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    _auto_confirm(monkeypatch)
    dispatched = []
    monkeypatch.setattr(
        control.utils,
        "threaded_operation_helper",
        lambda fn, cb, **kw: dispatched.append(gui.microscope.stage_homing_in_progress)
        or threading.Thread(target=lambda: cb(*_run(fn, **kw))).start(),
    )
    gui.liveController.start_live()
    gui._on_home_stage_requested()
    assert dispatched == [True] and not gui.liveController.is_live
    qtbot.waitUntil(lambda: not gui.microscope.stage_homing_in_progress, timeout=10000)
    qtbot.waitUntil(lambda: gui.liveController.is_live and gui.navigationWidget.isEnabled(), timeout=5000)


def test_home_stage_timeout_keeps_gui_locked_until_stopped(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    _auto_confirm(monkeypatch)
    warnings = _capture_warnings(monkeypatch)
    sim = gui.microscope.low_level_drivers.microcontroller._serial
    monkeypatch.setattr(squid.stage.cephla.CephlaStage, "_calc_move_timeout", staticmethod(lambda d, s: 0.3))
    sim.hold_in_progress = True
    gui._on_home_stage_requested()
    qtbot.waitUntil(lambda: bool(warnings), timeout=5000)
    assert "still reports motion" in warnings[0]
    assert not gui.navigationWidget.isEnabled() and gui.microscope.stage_homing_in_progress
    sim.hold_in_progress = False
    sim.send_status_packet()
    # Until the claim clears too: the window refuses to close while the stage homes
    qtbot.waitUntil(
        lambda: gui.navigationWidget.isEnabled() and not gui.microscope.stage_homing_in_progress, timeout=5000
    )


def test_home_x_pauses_live(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    _auto_confirm(monkeypatch)
    seen = []
    gui.microscope.add_stage_homing_listener(lambda active: seen.append((active, gui.liveController.is_live)))
    gui.liveController.start_live()
    gui.stageUtils.home_x()
    assert seen[0] == (True, False) and gui.liveController.is_live


def test_home_x_refused_while_the_stage_is_still_moving(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    _auto_confirm(monkeypatch)
    refusals = _capture_refusals(monkeypatch)
    homes = []
    monkeypatch.setattr(gui.stage, "home", lambda **kw: homes.append(kw))
    gui.liveController.start_live()
    sim = _start_a_move_that_stays_under_way(gui)
    gui.stageUtils.home_x()
    assert refusals == ["Homing refused: the stage is still moving."]
    assert homes == []  # nothing homed
    assert gui.microscope.stage_homing_in_progress and not gui.liveController.is_live
    sim.hold_in_progress = False
    sim.send_status_packet()
    qtbot.waitUntil(lambda: not gui.microscope.stage_homing_in_progress and gui.liveController.is_live, timeout=5000)


def test_home_x_on_piezo_only_asks_and_passes_the_confirmation(qtbot, confirm_exit_yes, monkeypatch, piezo_only):
    gui = _gui(qtbot, confirm_objective_lowered=lambda: True)
    _auto_confirm(monkeypatch)
    homes = []
    monkeypatch.setattr(gui.stage, "home", lambda **kw: homes.append(kw))
    gui.stageUtils.home_x()
    assert homes == [dict(x=True, y=False, z=False, theta=False, objective_lowered=True)]

    # Cancel: nothing moves
    monkeypatch.setattr(control.widgets, "ask_objective_lowered", lambda parent: False)
    gui.stageUtils.home_y()
    assert len(homes) == 1


def test_tcp_home_locks_the_gui(qtbot, confirm_exit_yes):
    gui = _gui(qtbot)
    claim = gui.microscope.claim_stage_homing("TCP home")
    qtbot.waitUntil(lambda: not gui.navigationWidget.isEnabled(), timeout=2000)
    assert gui._stage_moves_blocked_by_objective_swap("Click to move")
    assert gui.multipointController.start_blocker() == "the stage is homing"
    claim.release_when_stopped()
    qtbot.waitUntil(lambda: gui.navigationWidget.isEnabled(), timeout=2000)


def test_homing_claimed_off_the_gui_thread_locks_the_gui_before_the_claim_returns(qtbot, confirm_exit_yes):
    """A TCP home claims from the server's thread: the GUI (and the control panel lock command) must be locked
    before that thread sends its first homing command, and unlocked only after the claim's release."""
    gui = _gui(qtbot)
    seen = {}

    def tcp_home():
        claim = gui.microscope.claim_stage_homing("TCP home")
        seen["locked_when_claimed"] = not gui.navigationWidget.isEnabled()
        claim.release_when_stopped()
        seen["done"] = True

    threading.Thread(target=tcp_home).start()
    qtbot.waitUntil(lambda: "done" in seen, timeout=5000)
    assert seen["locked_when_claimed"]
    qtbot.waitUntil(lambda: gui.navigationWidget.isEnabled(), timeout=2000)


def test_declined_startup_moves_nothing_and_warns(qtbot, confirm_exit_yes, monkeypatch, piezo_only):
    # A piezo-only system has no stepper Z to home: X and Y are the axes startup homes.
    _set_z_flags(monkeypatch, homing_z=False, pi_focus=False)
    # A cached position the startup restore would move to, were the declined startup not special-cased
    cached = squid.abc.Pos(x_mm=23.0, y_mm=31.0, z_mm=1.5, theta_rad=None)
    monkeypatch.setattr(squid.stage.utils, "get_cached_position", lambda *args, **kwargs: cached)
    scope = control.microscope.Microscope.build_from_global_config(True, confirm_objective_lowered=lambda: False)
    before = scope.stage.get_pos()
    assert (before.x_mm, before.y_mm) != (cached.x_mm, cached.y_mm)
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)
    assert scope.stage.get_pos() == before
    # The window is never shown in these tests: isVisibleTo(gui) is "visible once the window is", status bar included.
    assert gui._not_homed_label.isVisibleTo(gui)
    gui.stageUtils.signal_axes_homed.emit({"x"})
    assert gui._not_homed_label.isVisibleTo(gui)
    gui.stageUtils.signal_axes_homed.emit({"y"})
    assert not gui._not_homed_label.isVisibleTo(gui)


def test_declined_startup_with_z_homing_warns_until_z_is_homed_too(qtbot, confirm_exit_yes, monkeypatch, piezo_only):
    _set_z_flags(monkeypatch, homing_z=True, pi_focus=False)
    scope = control.microscope.Microscope.build_from_global_config(True, confirm_objective_lowered=lambda: False)
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)
    gui.stageUtils.signal_axes_homed.emit({"x", "y"})
    assert gui._not_homed_label.isVisibleTo(gui)
    gui.stageUtils.signal_axes_homed.emit({"z"})
    assert not gui._not_homed_label.isVisibleTo(gui)


@pytest.mark.parametrize("at_the_cached_xy", [True, False])
def test_already_homed_leaves_xy_and_restores_z_only_at_the_cached_xy(
    qtbot, confirm_exit_yes, monkeypatch, caplog, at_the_cached_xy
):
    """A warm relaunch: the controller vouched that the stage is still homed, so X/Y stay where the stage stands.
    Z comes back as after a homed startup only while X/Y are where the position was cached (within 0.1 mm on each
    axis). After a crash, or a panel jog while Squid was closed, the cached Z is another place's focus height: Z stays
    where it is. Either way the multipoint widgets take the Z the stage ends at."""
    _set_z_flags(monkeypatch, homing_z=True, pi_focus=False)
    monkeypatch.setattr(control._def, "ENABLE_MEMORY_PROFILING", True)  # the RAM monitor appears with the window
    # strict=False: the GUI also sends commands the sim's protocol whitelist lacks (SET_WATCHDOG_TIMEOUT, HEARTBEAT)
    sim = FirmwareSimSerial(strict=False, firmware_version=STARTUP_STATE_MIN_FIRMWARE)
    monkeypatch.setattr(control.microcontroller, "get_microcontroller_serial_device", lambda **kw: sim)
    earlier = control.microscope.Microscope.build_from_global_config(True)  # homes and stores the signature
    earlier.stage.move_x_to(10.0)
    earlier.stage.move_y_to(10.0)
    earlier.stage.move_z_to(0.8)  # where that session left Z (it died before a clean close retracted it)
    earlier.close()
    sim.reconnect(1)  # the controller stayed powered

    safety_z_mm = int(control._def.Z_HOME_SAFETY_POINT) / 1000.0
    # Above the safety point, so a restore moves Z to the cached Z itself; Y 0.2 mm off is beyond the 0.1 mm tolerance
    cached_y_mm = 10.0 if at_the_cached_xy else 10.2
    cached = squid.abc.Pos(x_mm=10.0, y_mm=cached_y_mm, z_mm=safety_z_mm + 0.5, theta_rad=None)
    monkeypatch.setattr(squid.stage.utils, "get_cached_position", lambda *args, **kwargs: cached)
    scope = control.microscope.Microscope.build_from_global_config(True)
    assert scope.startup_homing is StartupHoming.ALREADY_HOMED
    z_before = scope.stage.get_pos().z_mm
    assert z_before == pytest.approx(0.8, abs=1e-3)  # a warm start kept it
    moves = _record_xy_moves(monkeypatch, scope.stage)
    _spy_moves(monkeypatch, moves, scope.stage, "move_z_to", "z")
    init_z_calls = []
    for widget_class in (control.widgets.FlexibleMultiPointWidget, control.widgets.WellplateMultiPointWidget):
        real_init_z = widget_class.init_z

        def init_z(self, *args, _real=real_init_z, **kwargs):
            init_z_calls.append(type(self).__name__)
            return _real(self, *args, **kwargs)

        monkeypatch.setattr(widget_class, "init_z", init_z)
    with caplog.at_level(logging.INFO):
        gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)

    pos = scope.stage.get_pos()
    assert (pos.x_mm, pos.y_mm) == pytest.approx((10.0, 10.0), abs=1e-3)  # to a microstep
    if at_the_cached_xy:
        assert moves == [("z", cached.z_mm)] and pos.z_mm == pytest.approx(cached.z_mm, abs=1e-3)
        assert "Cached position was saved at another XY" not in caplog.text
    else:
        assert moves == [] and pos.z_mm == z_before
        assert "Cached position was saved at another XY: Z left where it is" in caplog.text
    # init_z runs either way: the multipoint Z range starts at the Z the stage ends at
    assert sorted(init_z_calls) == ["FlexibleMultiPointWidget", "WellplateMultiPointWidget"]
    assert gui.flexibleMultiPointWidget.entry_minZ.value() == pytest.approx(pos.z_mm * 1000, abs=1)
    assert gui.wellplateMultiPointWidget.entry_minZ.value() == pytest.approx(pos.z_mm * 1000, abs=1)

    # The message shows once the window does, and the status-bar widgets that appear with it do not cover it
    gui.show()
    qtbot.waitExposed(gui)
    assert gui.statusBar().currentMessage() == "Stage still homed: startup homing skipped"
    assert not gui.ramMonitorWidget.isVisible()


def _z_lift_system_closed_at_loading(monkeypatch, tmp_path):
    """A Z lift system (V-308 focus, the lift on the Cephla Z) whose last session was closed at the loading position.
    Returns its 1.9 controller, patched in as every Microscope's."""
    _set_z_flags(monkeypatch, homing_z=False, pi_focus=True)
    for name, value in (
        ("USE_Z_LIFT_STAGE", True),
        ("Z_LIFT_MIN_MM", 0.0),
        ("Z_LIFT_MAX_MM", 8.0),
        ("Z_LIFT_FOCUS_WHEEL_DRIVES_PI", False),
        ("Z_LIFT_HEIGHTS_PATH", str(tmp_path / "heights.json")),
        ("Z_LIFT_LOADING_STATE_PATH", str(tmp_path / "loading_state.json")),
        ("USE_OBJECTIVE_TURRET", False),
        ("USE_XERYON", False),
    ):
        monkeypatch.setattr(control._def, name, value)
    (tmp_path / "loading_state.json").write_text(
        '{"scanning_z_mm": null, "lift_mm": 2.0, "lift_format": "96 well plate"}'
    )
    # strict=False: the GUI also sends commands the sim's protocol whitelist lacks (SET_WATCHDOG_TIMEOUT, HEARTBEAT)
    sim = FirmwareSimSerial(strict=False, firmware_version=STARTUP_STATE_MIN_FIRMWARE)
    monkeypatch.setattr(control.microcontroller, "get_microcontroller_serial_device", lambda **kw: sim)
    return sim


def _spy_moves(monkeypatch, moves, owner, method, label):
    """Append (label, target) to `moves` for each call of owner.<method>(target, ...) from now on, still carried out."""
    real = getattr(owner, method)

    def spy(mm, *args, **kwargs):
        moves.append((label, mm))
        return real(mm, *args, **kwargs)

    monkeypatch.setattr(owner, method, spy)


def _record_xy_moves(monkeypatch, stage):
    """The absolute X/Y moves sent through `stage` from now on, each still carried out."""
    moves = []
    for axis in ("x", "y"):
        _spy_moves(monkeypatch, moves, stage, f"move_{axis}_to", axis)
    return moves


def _record_moves(monkeypatch, scope):
    """The absolute X, Y and Z (objective) moves sent through scope.stage and the Z lift's moves, from now on, in
    order, each still carried out."""
    moves = _record_xy_moves(monkeypatch, scope.stage)
    _spy_moves(monkeypatch, moves, scope.stage, "move_z_to", "z")
    _spy_moves(monkeypatch, moves, scope.z_lift, "move_to", "lift")
    return moves


def _gui_on(qtbot, scope):
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)
    gui.show()
    qtbot.waitExposed(gui)
    qtbot.waitUntil(lambda: gui._loading_position_locked, timeout=5000)  # the restored loading state locks it
    return gui


@pytest.mark.parametrize("left_at_loading_xy", [True, False])
def test_already_homed_at_the_loading_position_moves_xy_only_if_not_there(
    qtbot, confirm_exit_yes, monkeypatch, tmp_path, left_at_loading_xy
):
    """Ruling B-R6: a warm start on a system closed at the loading position sends no XY move when the stage is at the
    loading XY; a session that died on the way (elsewhere) still goes there. Either way the loading lock applies and
    the warm start says so."""
    sim = _z_lift_system_closed_at_loading(monkeypatch, tmp_path)
    earlier = control.microscope.Microscope.build_from_global_config(True)  # homes and stores the signature
    if left_at_loading_xy:
        squid.stage.utils.move_xy_to_loading_position(earlier.stage, is_wellplate=True)
    else:
        earlier.stage.move_x_to(10.0)
        earlier.stage.move_y_to(10.0)
    earlier.close()
    sim.reconnect(1)  # the controller stayed powered

    scope = control.microscope.Microscope.build_from_global_config(True)
    assert scope.startup_homing is StartupHoming.ALREADY_HOMED
    moves = _record_moves(monkeypatch, scope)
    gui = _gui_on(qtbot, scope)

    loading = (control._def.SLIDE_POSITION.LOADING_X_MM, control._def.SLIDE_POSITION.LOADING_Y_MM)
    pos = scope.stage.get_pos()
    assert (pos.x_mm, pos.y_mm) == pytest.approx(loading, abs=1e-3)
    assert (moves == []) is left_at_loading_xy  # at the loading XY nothing moves: not XY, the objective or the lift
    assert gui.stageUtils.slide_position == "loading" and not gui.navigationWidget.isEnabled()
    assert gui.statusBar().currentMessage() == "Stage still homed: startup homing skipped"


def test_already_homed_away_from_the_loading_position_clears_the_sample_before_xy(
    qtbot, confirm_exit_yes, monkeypatch, tmp_path
):
    """A session that died on its way back to scanning (the lift raised, the objective lowered, the loading state not
    yet cleared): a warm relaunch retracts the objective and lowers the lift before XY travels back to the loading
    position, as the move there does. The Z and lift height to return to stay the restored state's."""
    sim = _z_lift_system_closed_at_loading(monkeypatch, tmp_path)
    state_path = tmp_path / "loading_state.json"
    state_path.write_text('{"scanning_z_mm": 1.2, "lift_mm": 2.0, "lift_format": "96 well plate"}')
    state_before = state_path.read_text()
    earlier = control.microscope.Microscope.build_from_global_config(True)  # homes and stores the signature
    earlier.stage.move_x_to(10.0)
    earlier.stage.move_y_to(10.0)
    earlier.z_lift.move_to(3.0)  # the return raised the lift
    earlier.close()
    sim.reconnect(1)  # the controller stayed powered

    scope = control.microscope.Microscope.build_from_global_config(True)
    assert scope.startup_homing is StartupHoming.ALREADY_HOMED
    assert scope.z_lift.get_position_mm() == pytest.approx(3.0, abs=1e-3)  # still raised: a warm start keeps it
    scope.stage.move_z_to(1.5)  # and the return had lowered the objective to its working height
    moves = _record_moves(monkeypatch, scope)
    gui = _gui_on(qtbot, scope)

    first_xy = next(i for i, (label, _) in enumerate(moves) if label in ("x", "y"))
    assert moves[:first_xy] == [("z", control._def.OBJECTIVE_RETRACTED_POS_MM), ("lift", scope.z_lift.lowered_mm)]
    loading_x, loading_y = control._def.SLIDE_POSITION.LOADING_X_MM, control._def.SLIDE_POSITION.LOADING_Y_MM
    assert moves[first_xy:] == [("y", 15), ("x", 35), ("y", loading_y), ("x", loading_x)]
    assert scope.z_lift.is_lowered()
    # Not recorded again, and the heights to return to are the restored ones (not Z 1.5, lift 3.0)
    assert state_path.read_text() == state_before
    assert scope.stage._scanning_position_z_mm == 1.2
    assert scope.stage._scanning_position_lift_mm == (2.0, "96 well plate")
    assert gui.stageUtils.slide_position == "loading" and not gui.navigationWidget.isEnabled()
    assert gui.statusBar().currentMessage() == "Stage still homed: startup homing skipped"


def test_homed_start_closed_at_the_loading_position_moves_back_there(qtbot, confirm_exit_yes, monkeypatch, tmp_path):
    """Unchanged by B-R6: after a homing startup the stage goes back to the loading position by the loading path."""
    _z_lift_system_closed_at_loading(monkeypatch, tmp_path)
    scope = control.microscope.Microscope.build_from_global_config(True)  # a first launch: homes
    assert scope.startup_homing is StartupHoming.HOMED
    moves = _record_xy_moves(monkeypatch, scope.stage)
    gui = _gui_on(qtbot, scope)

    loading_x, loading_y = control._def.SLIDE_POSITION.LOADING_X_MM, control._def.SLIDE_POSITION.LOADING_Y_MM
    assert moves == [("y", 15), ("x", 35), ("y", loading_y), ("x", loading_x)]
    assert gui.stageUtils.slide_position == "loading" and not gui.navigationWidget.isEnabled()
    assert gui.statusBar().currentMessage() == ""


# --- One exclusion for every claim, failures shown, close refused while homing ---


@pytest.mark.parametrize("busy", ["objective_swap", "loading_position", "acquisition"])
def test_tcp_home_is_refused_while_the_gui_drives_the_stage(qtbot, confirm_exit_yes, busy):
    """A TCP home, on the server's thread, is refused with the GUI's reason while an objective swap, the loading
    position or an acquisition holds the stage: nothing moves and nothing is claimed, so the homing lock never
    nests inside those locks."""
    from control.microscope_control_server import MicroscopeControlServer
    from tests.control.test_startup_homing import _spy_commands

    gui = _gui(qtbot)
    server = MicroscopeControlServer(gui.microscope, multipoint_controller=gui.multipointController, gui=gui)
    acquiring = unittest.mock.patch.object(gui.multipointController, "acquisition_in_progress", return_value=True)
    reason, start, end = {
        "objective_swap": (
            "an objective change is in progress",
            lambda: gui._on_objective_swap_active(True),
            lambda: gui._on_objective_swap_active(False),
        ),
        "loading_position": (
            "the stage is at (or moving to or from) the loading position",
            gui._lock_stage_at_loading_position,
            gui._unlock_stage_after_loading_position,
        ),
        "acquisition": ("an acquisition is running", acquiring.start, acquiring.stop),
    }[busy]
    start()
    try:
        events = []
        gui.microscope.add_stage_homing_listener(events.append)
        sent = _spy_commands(gui.microscope.low_level_drivers.microcontroller)
        responses = []
        threading.Thread(target=lambda: responses.append(server._execute("home", {"objective_lowered": True}))).start()
        qtbot.waitUntil(lambda: bool(responses), timeout=5000)
        assert responses == [{"success": False, "error": f"Home XYZ refused: {reason}"}]
        assert sent == [] and events == [] and not gui.microscope.stage_homing_in_progress
    finally:
        qtbot.waitUntil(lambda: not gui.microscope.stage_homing_in_progress, timeout=5000)
        end()


def test_old_firmware_panel_lock_warning_is_logged_once(qtbot, confirm_exit_yes, caplog):
    gui = _gui(qtbot)
    assert not gui.microcontroller.supports_panel_lock()  # the simulated controller's firmware is older than 1.8
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            gui.microscope.claim_stage_homing("TCP home").release_when_stopped()
            qtbot.waitUntil(lambda: not gui.microscope.stage_homing_in_progress, timeout=2000)
        gui._lock_stage_at_loading_position()  # the loading lock still asks for the panel lock
        gui._unlock_stage_after_loading_position()
    assert len([r for r in caplog.records if "cannot lock the control panel" in r.getMessage()]) == 1


def test_home_x_timeout_says_homing_did_not_finish(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    _auto_confirm(monkeypatch)
    warnings = _capture_warnings(monkeypatch)
    sim = gui.microscope.low_level_drivers.microcontroller._serial
    monkeypatch.setattr(squid.stage.cephla.CephlaStage, "_calc_move_timeout", staticmethod(lambda d, s: 0.3))
    sim.hold_in_progress = True
    gui.stageUtils.home_x()  # the failure is shown, not raised out of the click
    assert len(warnings) == 1 and warnings[0].startswith("Homing did not finish (")
    assert warnings[0].endswith("The stage still reports motion: motion controls stay locked until it stops.")
    assert gui.microscope.stage_homing_in_progress and not gui.navigationWidget.isEnabled()
    sim.hold_in_progress = False
    sim.send_status_packet()
    qtbot.waitUntil(
        lambda: not gui.microscope.stage_homing_in_progress and gui.navigationWidget.isEnabled(), timeout=5000
    )


def test_home_y_failure_after_release_says_homing_failed(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    _auto_confirm(monkeypatch)
    warnings = _capture_warnings(monkeypatch)
    homed = []
    gui.stageUtils.signal_axes_homed.connect(homed.append)

    def link_lost(**kwargs):
        raise ConnectionError("link lost")  # before anything was sent: the stage still reports stopped

    monkeypatch.setattr(gui.stage, "home", link_lost)
    gui.stageUtils.home_y()
    assert warnings == ["Homing failed: link lost"]
    assert homed == [] and not gui.microscope.stage_homing_in_progress


def test_close_while_homing_is_refused_and_moves_nothing(qtbot, confirm_exit_yes, monkeypatch):
    """The cleanup would cache a mid-sweep position and send the Z retract from the GUI thread while the homing
    thread still drives the controller."""
    from tests.control.test_startup_homing import _spy_commands

    gui = _gui(qtbot)
    refusals = _capture_refusals(monkeypatch)
    cleanups = []
    spawned = []
    monkeypatch.setattr(control.gui_hcs.subprocess, "Popen", lambda *args, **kwargs: spawned.append(args))
    gui._cleanup_common = lambda **kw: cleanups.append(kw)
    try:
        claim = gui.microscope.claim_stage_homing("Home stage")
        sent = _spy_commands(gui.microscope.low_level_drivers.microcontroller)
        event = QCloseEvent()
        gui.closeEvent(event)
        gui.restart_application()
        assert not event.isAccepted() and cleanups == [] and spawned == [] and sent == []
        assert refusals == [
            "The stage is homing. Close Squid once it has finished.",
            "The stage is homing. Restart Squid once it has finished.",
        ]
        claim.release_when_stopped()
        qtbot.waitUntil(lambda: not gui.microscope.stage_homing_in_progress, timeout=2000)
    finally:
        del gui._cleanup_common  # the teardown's own close runs the real cleanup
