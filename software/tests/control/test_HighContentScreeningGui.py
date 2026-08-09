import pytest

import control._def

import control.gui_hcs
from qtpy.QtWidgets import QMessageBox

import control.microscope
from control.core.config.repository import ConfigRepository
from control.core.live_controller import LiveController
from control.models.camera_registry import CameraDefinition, CameraRegistryConfig


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


def test_single_camera_gui_has_one_plain_camera_tab(qtbot, confirm_exit_yes):
    """Single-camera systems must not drift: one tab literally named "Camera", no
    per-camera extras, and the trigger dropdown offering Software + Hardware."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    labels = [win.cameraTabWidget.tabText(i) for i in range(win.cameraTabWidget.count())]
    assert labels.count("Camera") == 1
    assert win.cameraSettingWidgets_extra == {}

    combo = win.liveControlWidget.dropdown_triggerManu
    options = [combo.itemText(i) for i in range(combo.count())]
    expected = [control._def.TriggerMode.SOFTWARE, control._def.TriggerMode.HARDWARE]
    if control.gui_hcs.ENABLE_RECORDING:
        expected.append(control._def.TriggerMode.CONTINUOUS)
    assert options == expected


def test_multi_camera_gui_names_tabs_from_registry(qtbot, monkeypatch, confirm_exit_yes):
    """Two cameras: the primary tab takes its cameras.yaml name and each extra camera
    gets its own settings tab bound to its own concrete camera (never the facade)."""
    registry = CameraRegistryConfig(
        cameras=[
            CameraDefinition(name="Main Camera", id=1, serial_number="SIM-1", type="Toupcam"),
            CameraDefinition(name="Side Camera", id=2, serial_number="SIM-2", type="Toupcam", hardware_trigger=False),
        ]
    )
    monkeypatch.setattr(ConfigRepository, "get_camera_registry", lambda self: registry)

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    labels = [win.cameraTabWidget.tabText(i) for i in range(win.cameraTabWidget.count())]
    assert "Main Camera" in labels
    assert "Side Camera" in labels
    assert "Camera" not in labels
    assert list(win.cameraSettingWidgets_extra) == [2]
    assert win.cameraSettingWidgets_extra[2].camera is scope.cameras[2]
    assert win.cameraSettingWidget.camera is scope.cameras[control._def.PRIMARY_CAMERA_ID]

    # The trigger dropdown follows the active camera's capability. Camera 2 declares
    # hardware_trigger: false, so Hardware must disappear once it becomes active.
    combo = win.liveControlWidget.dropdown_triggerManu
    assert control._def.TriggerMode.HARDWARE in [combo.itemText(i) for i in range(combo.count())]
    scope.set_active_camera(2)
    # The listener hops to the GUI thread via QTimer.singleShot; call the same handler
    # directly rather than spinning the event loop inside the test.
    win._on_active_camera_changed(2)
    assert [combo.itemText(i) for i in range(combo.count())] == [control._def.TriggerMode.SOFTWARE]


def test_startup_channel_on_secondary_camera_syncs_trigger_options(qtbot, monkeypatch, confirm_exit_yes):
    """LiveControlWidget builds its trigger dropdown, then set_microscope_mode on the
    startup channel may switch the active camera - before the GUI's camera-change
    listener is wired. The widget must sync itself, or the dropdown offers Hardware
    for a camera whose trigger line is not wired."""
    registry = CameraRegistryConfig(
        cameras=[
            CameraDefinition(name="Main Camera", id=1, serial_number="SIM-1", type="Toupcam"),
            CameraDefinition(name="Side Camera", id=2, serial_number="SIM-2", type="Toupcam", hardware_trigger=False),
        ]
    )
    monkeypatch.setattr(ConfigRepository, "get_camera_registry", lambda self: registry)

    original_get_channels = LiveController.get_channels

    def get_channels_with_secondary_first(self, objective):
        # Copy so the repository's shared channel objects are never mutated.
        channels = [channel.model_copy(deep=True) for channel in original_get_channels(self, objective)]
        if channels:
            channels[0].camera = 2
        return channels

    monkeypatch.setattr(LiveController, "get_channels", get_channels_with_secondary_first)

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    assert scope.active_camera_id == 2
    combo = win.liveControlWidget.dropdown_triggerManu
    assert [combo.itemText(i) for i in range(combo.count())] == [control._def.TriggerMode.SOFTWARE]


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
