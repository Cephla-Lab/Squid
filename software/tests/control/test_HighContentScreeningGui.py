import unittest.mock

import pytest

import control._def

import control.gui_hcs
import control.widgets
from qtpy.QtCore import QCoreApplication, QEvent
from qtpy.QtWidgets import QMessageBox

import control.microscope
import squid.abc
import squid.stage.utils


@pytest.fixture
def confirm_exit_yes(monkeypatch):
    """Auto-accept the 'Confirm Exit' dialog GUI shutdown shows, or teardown hangs forever."""

    def confirm_exit(parent, title, text, *args, **kwargs):
        if title == "Confirm Exit":
            return QMessageBox.Yes
        raise RuntimeError(f"Unexpected QMessageBox: {title} - {text}")

    monkeypatch.setattr(QMessageBox, "question", confirm_exit)


@pytest.fixture(autouse=True)
def isolated_gallery_state(monkeypatch, tmp_path):
    """No GUI test writes the real acquisition history or gallery cache, or gets the first-launch import offer."""
    import control.widgets_gallery

    monkeypatch.setenv("SQUID_ACQUISITION_HISTORY_DIR", str(tmp_path / "history"))
    marker = tmp_path / "cache" / "gallery_import_offered.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("offered\n")
    monkeypatch.setattr(control.widgets_gallery, "IMPORT_OFFER_MARKER", str(marker))
    monkeypatch.setattr(
        control.widgets_gallery, "LAST_GALLERY_VIEW_CACHE", str(tmp_path / "cache" / "last_gallery_view.yaml")
    )


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


def test_destroying_the_gui_neither_crashes_nor_leaves_timers_firing_at_deleted_widgets(qtbot, confirm_exit_yes):
    """Regression: FlexibleMultiPointWidget added one QSpacerItem to several layouts, so the
    layouts freed it once each (a segfault when Qt ran the window's DeferredDelete), and
    ImageDisplayWindow's unparented update timer kept firing at the deleted QLabels."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)

    win.close()
    win.deleteLater()
    # Run the pending delete now, rather than whenever a later event loop happens to service it.
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    # The update timer fires every 100 ms; a RuntimeError ("wrapped C/C++ object ... has been
    # deleted") in its slot is reported by pytest-qt as a failure of this test.
    qtbot.wait(500)


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
    monkeypatch.setattr(control.gui_hcs, "HOMING_ENABLED_X", True)
    monkeypatch.setattr(control.gui_hcs, "HOMING_ENABLED_Y", True)
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


@pytest.mark.parametrize("start_on_piezo", [False, True])
def test_wellplate_yaml_with_a_piezo_range_reloads_set_range_and_reaches_the_controller(
    qtbot, monkeypatch, confirm_exit_yes, start_on_piezo
):
    from control.acquisition_yaml_loader import AcquisitionYAMLData

    monkeypatch.setattr(control._def, "HAS_OBJECTIVE_PIEZO", True)
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    w = win.wellplateMultiPointWidget
    w.checkbox_usePiezo.setChecked(start_on_piezo)

    w._apply_yaml_settings(
        AcquisitionYAMLData(
            widget_type="wellplate", nz=4, delta_z_um=2.0, use_piezo=True, piezo_z_range_um=(20.0, 26.0)
        )
    )

    assert w.combobox_z_mode.currentText() == "Set Range"
    assert (w.entry_minZ.value(), w.entry_maxZ.value(), w.entry_NZ.value()) == (20.0, 26.0, 4)
    assert w.entry_maxZ.maximum() == scope.addons.piezo_stage.range_um
    assert not w.checkbox_withReflectionAutofocus.isEnabled()

    w.configure_controller_from_ui()  # what Start runs
    assert w.multipointController.use_piezo
    assert tuple(w.multipointController.piezo_z_range_um) == (20.0, 26.0)


def test_wellplate_yaml_moving_the_stack_to_the_stepper_gives_the_entries_the_stepper_limits(
    qtbot, monkeypatch, confirm_exit_yes
):
    from control.acquisition_yaml_loader import AcquisitionYAMLData

    monkeypatch.setattr(control._def, "HAS_OBJECTIVE_PIEZO", True)
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    w = win.wellplateMultiPointWidget
    w.checkbox_usePiezo.setChecked(True)

    w._apply_yaml_settings(AcquisitionYAMLData(widget_type="wellplate", nz=4, delta_z_um=2.0, use_piezo=False))

    assert w.entry_minZ.maximum() == control._def.SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000


def _gallery_row(gallery, entry_id):
    return next(row for row in gallery.rows() if row.entry.id == entry_id)


def test_gallery_is_wired_to_the_history_viewer_and_launcher(qtbot, confirm_exit_yes, monkeypatch, tmp_path):
    """The Gallery tab shows the history the controller records into; opening an acquisition goes to the NDViewer
    tab or an ndviewer_light window, loading its settings to the multipoint tab it was run from."""
    import os
    import socket

    from qtpy.QtWidgets import QWidget

    from control.ndviewer_launcher import LauncherResolution
    from squid.acquisition_history import HistoryStore
    from tests.control.gallery_fixtures import write_acquisition

    monkeypatch.setenv("SQUID_ACQUISITION_HISTORY_DIR", str(tmp_path / "history"))
    resolutions = [LauncherResolution(["ndviewer-light"], "path", "ndviewer-light on the PATH")]
    monkeypatch.setattr(control.gui_hcs, "resolve_ndviewer_light", lambda preference="": resolutions[-1])
    launched = []
    monkeypatch.setattr(
        control.gui_hcs, "launch_ndviewer_light", lambda resolution, folder: launched.append((resolution, folder))
    )

    done = write_acquisition(tmp_path, "done_2026-11-07_10-00-00.000000")
    (tmp_path / "live").mkdir()
    seed = HistoryStore(tmp_path / "history")
    seed.append({"type": "started", "id": "done", "kind": "run", "name": "done", "folder": str(done), "at": 1000.0})
    seed.append({"type": "finished", "id": "done", "outcome": "complete", "fraction_done": 1.0, "at": 1100.0})
    # Running in another Squid: left alone. Running here in a process that is gone: closed at startup.
    live = {"type": "started", "id": "live", "kind": "run", "name": "live", "folder": str(tmp_path / "live")}
    seed.append({**live, "at": 2000.0, "machine": "another-host", "pid": 1})
    orphan = {"type": "started", "id": "orphan", "kind": "run", "name": "orphan", "folder": str(tmp_path / "orphan")}
    seed.append({**orphan, "at": 1500.0, "machine": socket.gethostname(), "pid": None})

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    assert (
        win.multipointController.history_recorder is win.historyRecorder
        and win.historyStore.path.parent == tmp_path / "history"
    )
    assert win.historyStore.entry("orphan").outcome == "failed"  # reconciled at startup
    assert win.historyStore.entry("live").outcome == "running"
    gallery = win.acquisitionGalleryWidget
    assert gallery._store is win.historyStore
    assert win.imageDisplayTabs.tabText(win.imageDisplayTabs.indexOf(gallery)) == "Gallery"
    assert win._acquisition_saving_root() == win.multipointController.base_path

    gallery.reload()
    qtbot.waitUntil(lambda: {r.entry.id for r in gallery.rows()} == {"done", "live", "orphan"}, timeout=10000)
    assert gallery.select_entry("done")
    assert gallery.btn_open_in_window.isEnabled()

    # Open in Squid: the NDViewer tab shows the folder and comes to the front.
    class FakeViewerTab(QWidget):
        def __init__(self):
            super().__init__()
            self.opened = []

        def set_dataset_path(self, path):
            self.opened.append(path)

    viewer = FakeViewerTab()
    win.imageDisplayTabs.addTab(viewer, "NDViewer")
    win.ndviewerTab = viewer
    gallery.signal_open_in_squid.emit(str(tmp_path))
    assert viewer.opened == [str(tmp_path)]
    assert win.imageDisplayTabs.currentWidget() is viewer

    # Open in window: ndviewer_light is launched with the resolved command; a failed launch is reported.
    gallery.signal_open_in_window.emit(str(done))
    assert launched == [(resolutions[0], str(done))]
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append((args[1], args[2])))

    def failing_launch(resolution, folder):
        raise OSError("no such program")

    monkeypatch.setattr(control.gui_hcs, "launch_ndviewer_light", failing_launch)
    gallery.signal_open_in_window.emit(str(done))
    assert warnings == [("Cannot open ndviewer_light", "no such program")]
    warnings.clear()

    # Launcher unavailable: re-resolved when the tab is shown, the button says why.
    resolutions.append(LauncherResolution(None, "none", "ndviewer_light is not installed"))
    gallery.signal_shown.emit()
    assert not gallery.btn_open_in_window.isEnabled()
    assert gallery.btn_open_in_window.toolTip() == "ndviewer_light is not installed"

    # During an acquisition the NDViewer tab is busy with the running one.
    mpc = win.multipointController
    mpc.signal_acquisition_start.emit()
    assert not gallery.btn_open_in_squid.isEnabled()
    assert gallery.btn_open_in_squid.toolTip() == "Wait for the running acquisition to finish"
    qtbot.waitUntil(lambda: not gallery._loading, timeout=10000)  # the reload the start asked for

    # Progress: region 1 of 2, FOV 2 of 4, one timepoint -> 2 of 8 FOVs.
    mpc.signal_acquisition_progress.emit(1, 2, 0)
    mpc.signal_region_progress.emit(2, 4)
    assert _gallery_row(gallery, "live").status_label == "Running 25%"

    mpc.acquisition_finished.emit()
    assert gallery.btn_open_in_squid.isEnabled()
    assert not gallery.btn_open_in_squid.isHidden()

    # Performance mode turns the Gallery tab off like the other display tabs.
    win.performanceModeToggle.setChecked(True)
    win.togglePerformanceMode()
    assert not win.imageDisplayTabs.isTabEnabled(win.imageDisplayTabs.indexOf(gallery))
    win.performanceModeToggle.setChecked(False)
    win.togglePerformanceMode()
    assert win.imageDisplayTabs.isTabEnabled(win.imageDisplayTabs.indexOf(gallery))

    # Load settings: to the multipoint tab the acquisition was run from, not the current one.
    loaded = {}
    for name, widget in (("wellplate", win.wellplateMultiPointWidget), ("flexible", win.flexibleMultiPointWidget)):

        def record_load(path, name=name):
            loaded[name] = path
            return True

        monkeypatch.setattr(widget, "_load_acquisition_yaml", record_load)
    flexible = write_acquisition(tmp_path, "flex_2026-11-07_10-00-00.000000", widget_type="flexible")
    win.recordTabWidget.setCurrentWidget(win.wellplateMultiPointWidget)

    gallery.signal_load_settings.emit(str(flexible))

    assert loaded == {"flexible": os.path.join(str(flexible), "acquisition.yaml")}
    assert win.recordTabWidget.currentWidget() is win.flexibleMultiPointWidget
    assert warnings == []

    monkeypatch.setattr(win.multipointController, "acquisition_in_progress", lambda: True)
    assert win._load_acquisition_settings_from_folder(str(flexible)) is False
    assert [title for title, _text in warnings] == ["Acquisition Running"]
    assert len(loaded) == 1


def _preview_timing_gui(qtbot, monkeypatch, tmp_path):
    """A simulated GUI whose recorder records make_preview calls, with an overview save expected and a 500 ms wait;
    the controller's own history_entry_id reads None (a short run has already cleared it)."""
    monkeypatch.setenv("SQUID_ACQUISITION_HISTORY_DIR", str(tmp_path / "history"))
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    calls = []
    monkeypatch.setattr(
        win.historyRecorder,
        "make_preview",
        lambda entry_id, folder, replace=False: calls.append((entry_id, folder, replace)),
    )
    win.GALLERY_PREVIEW_WAIT_MS = 500
    monkeypatch.setattr(control._def, "SAVE_DOWNSAMPLED_OVERVIEW", True)
    mosaic = win.unifiedMosaicWidget
    assert mosaic is not None
    monkeypatch.setattr(mosaic, "layers_initialized", True)
    monkeypatch.setattr(mosaic, "save_for_timepoint", lambda time_point: None)  # the saves are emitted by hand
    monkeypatch.setattr(type(win.multipointController), "history_entry_id", property(lambda self: None))
    return win, calls


def test_preview_waits_for_the_final_overview_save(qtbot, confirm_exit_yes, monkeypatch, tmp_path):
    """The preview is made from the last timepoint's mosaic overview once it is written, or after a timeout (then
    remade when the overview lands); without an overview to wait for it is made when the acquisition ends."""
    import os

    win, calls = _preview_timing_gui(qtbot, monkeypatch, tmp_path)
    mpc = win.multipointController
    mosaic = win.unifiedMosaicWidget

    def folder(entry_id):
        return str(tmp_path / f"run_{entry_id}")

    def start(entry_id):
        mpc.signal_acquisition_start.emit()
        mpc.signal_acquisition_save_target.emit(folder(entry_id))
        mpc.signal_history_run_started.emit(entry_id, folder(entry_id))

    def saved(entry_id, time_point):
        mosaic.signal_view_saved.emit(os.path.join(folder(entry_id), str(time_point), "mosaic_view"))

    # (a) The final save comes after the end; an earlier timepoint's late save is not the one awaited.
    start("e1")
    mpc.timepoint_finished.emit(0)
    mpc.timepoint_finished.emit(1)
    mpc.acquisition_finished.emit()
    assert calls == []
    saved("e1", 0)
    assert calls == []
    saved("e1", 1)
    assert calls == [("e1", folder("e1"), False)]
    qtbot.wait(700)
    assert calls == [("e1", folder("e1"), False)]

    # (b) The overview never reports: the preview is made anyway once the wait is over.
    calls.clear()
    start("e2")
    mpc.timepoint_finished.emit(0)
    mpc.acquisition_finished.emit()
    assert calls == []
    qtbot.waitUntil(lambda: calls == [("e2", folder("e2"), False)], timeout=1000)
    qtbot.wait(800)  # past the second wait too
    assert calls == [("e2", folder("e2"), False)]
    saved("e2", 0)  # too late to be waited for
    assert calls == [("e2", folder("e2"), False)]

    # (c) The overview lands after the timeout: the provisional preview is replaced.
    calls.clear()
    start("e3")
    mpc.timepoint_finished.emit(0)
    mpc.acquisition_finished.emit()
    qtbot.waitUntil(lambda: calls == [("e3", folder("e3"), False)], timeout=1000)
    saved("e3", 0)
    assert calls == [("e3", folder("e3"), False), ("e3", folder("e3"), True)]
    saved("e3", 0)
    assert len(calls) == 2

    # (d) The overview is written before the acquisition ends: the preview is made at the end, without waiting.
    calls.clear()
    start("e4")
    mpc.timepoint_finished.emit(0)
    saved("e4", 0)
    assert calls == []
    mpc.acquisition_finished.emit()
    assert calls == [("e4", folder("e4"), False)]
    qtbot.wait(700)
    assert calls == [("e4", folder("e4"), False)]

    # (e) No overview expected: the preview is made when the acquisition ends.
    calls.clear()
    monkeypatch.setattr(mosaic, "layers_initialized", False)
    start("e5")
    mpc.timepoint_finished.emit(0)
    mpc.acquisition_finished.emit()
    assert calls == [("e5", folder("e5"), False)]
    qtbot.wait(700)
    assert calls == [("e5", folder("e5"), False)]


def test_a_back_to_back_start_keeps_the_previous_runs_preview(qtbot, confirm_exit_yes, monkeypatch, tmp_path):
    """Run N still waiting for its last overview save when run N+1 starts gets its preview: on its save (even one
    landing during run N+1), or on its timeout and then replaced by the late save."""
    import os

    win, calls = _preview_timing_gui(qtbot, monkeypatch, tmp_path)
    mpc = win.multipointController
    mosaic = win.unifiedMosaicWidget

    def run(entry_id):
        folder = str(tmp_path / f"run_{entry_id}")
        mpc.signal_acquisition_start.emit()
        mpc.signal_history_run_started.emit(entry_id, folder)
        mpc.timepoint_finished.emit(0)
        return folder

    def saved(folder):
        mosaic.signal_view_saved.emit(os.path.join(folder, "0", "mosaic_view"))

    # Its save lands while the next run is going.
    f6 = run("e6")
    mpc.acquisition_finished.emit()
    f7 = run("e7")
    assert calls == []
    saved(f6)
    assert calls == [("e6", f6, False)]

    # The next run's own wait is unaffected; its save never comes, so its timeout makes it.
    mpc.acquisition_finished.emit()
    f8 = run("e8")
    qtbot.waitUntil(lambda: ("e7", f7, False) in calls, timeout=1000)
    # ... and the save landing during the run after it still replaces the provisional picture.
    saved(f7)
    assert calls == [("e6", f6, False), ("e7", f7, False), ("e7", f7, True)]
    mpc.acquisition_finished.emit()
    saved(f8)
    assert calls[-1] == ("e8", f8, False) and len(calls) == 4


def test_the_preview_uses_the_history_id_captured_at_start(qtbot, confirm_exit_yes, monkeypatch, tmp_path):
    """A TCP-started run can be over (and the controller's id cleared) before a queued GUI slot runs: the id travels
    with signal_history_run_started instead."""
    win, calls = _preview_timing_gui(qtbot, monkeypatch, tmp_path)
    monkeypatch.setattr(win.unifiedMosaicWidget, "layers_initialized", False)
    mpc = win.multipointController
    folder = str(tmp_path / "run_e9")
    assert mpc.history_entry_id is None

    mpc.signal_history_run_started.emit("e9", folder)
    mpc.signal_acquisition_start.emit()
    mpc.timepoint_finished.emit(0)
    mpc.acquisition_finished.emit()

    assert calls == [("e9", folder, False)]
