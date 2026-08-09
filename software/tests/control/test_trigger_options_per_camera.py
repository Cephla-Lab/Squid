"""Per-camera trigger options (Task 10).

The trigger dropdown must only offer Hardware when the *active* camera has its
trigger line wired, must resync to the mode the LiveController actually holds
after a camera switch, and must never turn its own repopulation into an MCU
trigger-mode command.

The stubs below borrow the REAL widget methods so the production code is what
runs (same pattern as test_channel_display_labels.py).
"""

from types import SimpleNamespace

import pytest
from qtpy.QtWidgets import QComboBox, QDoubleSpinBox

import control.widgets
from control._def import TriggerMode
from control.gui_hcs import HighContentScreeningGui
from control.models.camera_registry import CameraDefinition, CameraRegistryConfig
from control.widgets import LiveControlWidget, NapariLiveWidget

TWO_CAM = CameraRegistryConfig(
    cameras=[
        CameraDefinition(name="Main Camera", id=1, serial_number="SN1", type="Toupcam"),
        CameraDefinition(name="Side Camera", id=2, serial_number="SN2", type="Toupcam", hardware_trigger=False),
    ]
)


def _fake_camera(supports_hw, exposure_limits):
    return SimpleNamespace(
        supports_hardware_trigger=lambda: supports_hw,
        get_exposure_limits=lambda: exposure_limits,
    )


class _LiveTriggerStub:
    """LiveControlWidget-shaped stub bound to the real trigger methods."""

    refresh_trigger_options = LiveControlWidget.refresh_trigger_options
    on_active_camera_changed = LiveControlWidget.on_active_camera_changed
    update_trigger_mode = LiveControlWidget.update_trigger_mode

    def __init__(self, supports_hw=True, trigger_mode=TriggerMode.SOFTWARE, exposure_limits=(0.1, 1000.0)):
        self.is_switching_mode = False
        self.applied_modes = []
        self.dropdown_triggerManu = QComboBox()
        self.entry_exposureTime = QDoubleSpinBox()
        self.entry_exposureTime.setRange(0.1, 5000.0)
        self.entry_exposureTime.setValue(2000.0)
        self.camera = _fake_camera(supports_hw, exposure_limits)
        self.liveController = SimpleNamespace(
            trigger_mode=trigger_mode,
            set_trigger_mode=self.applied_modes.append,
        )
        # Same wiring the real add_components makes.
        self.dropdown_triggerManu.currentIndexChanged.connect(self.update_trigger_mode)

    def items(self):
        return [self.dropdown_triggerManu.itemText(i) for i in range(self.dropdown_triggerManu.count())]


class _NapariTriggerStub:
    """NapariLiveWidget-shaped stub bound to the real trigger methods."""

    refresh_trigger_options = NapariLiveWidget.refresh_trigger_options
    on_active_camera_changed = NapariLiveWidget.on_active_camera_changed
    on_trigger_mode_changed = NapariLiveWidget.on_trigger_mode_changed

    def __init__(self, supports_hw=True, trigger_mode=TriggerMode.SOFTWARE, exposure_limits=(0.1, 1000.0)):
        self.is_switching_mode = False
        self.observed = []
        self.dropdown_triggerMode = QComboBox()
        self.entry_exposureTime = QDoubleSpinBox()
        self.entry_exposureTime.setRange(0.1, 5000.0)
        self.entry_exposureTime.setValue(2000.0)
        self.liveController = SimpleNamespace(
            trigger_mode=trigger_mode,
            camera=_fake_camera(supports_hw, exposure_limits),
        )
        self.dropdown_triggerMode.currentIndexChanged.connect(self._record)

    def _record(self, index):
        self.on_trigger_mode_changed(index)
        if not self.is_switching_mode:
            self.observed.append(self.dropdown_triggerMode.itemData(index))

    def items(self):
        return [self.dropdown_triggerMode.itemData(i) for i in range(self.dropdown_triggerMode.count())]


class TestLiveControlTriggerOptions:
    def test_hardware_offered_when_camera_supports_it(self, qtbot, monkeypatch):
        monkeypatch.setattr(control.widgets, "ENABLE_RECORDING", False)
        stub = _LiveTriggerStub(supports_hw=True)
        stub.refresh_trigger_options()
        assert stub.items() == [TriggerMode.SOFTWARE, TriggerMode.HARDWARE]

    def test_hardware_hidden_when_camera_cannot_be_triggered(self, qtbot, monkeypatch):
        monkeypatch.setattr(control.widgets, "ENABLE_RECORDING", False)
        stub = _LiveTriggerStub(supports_hw=False)
        stub.refresh_trigger_options()
        assert stub.items() == [TriggerMode.SOFTWARE]

    def test_continuous_added_when_recording_enabled(self, qtbot, monkeypatch):
        monkeypatch.setattr(control.widgets, "ENABLE_RECORDING", True)
        stub = _LiveTriggerStub(supports_hw=True)
        stub.refresh_trigger_options()
        assert stub.items() == [TriggerMode.SOFTWARE, TriggerMode.HARDWARE, TriggerMode.CONTINUOUS]

    def test_selection_resyncs_to_live_controller_mode(self, qtbot, monkeypatch):
        """A dropdown call that lost a race with a camera switch left the UI showing a
        mode the controller does not hold; the refresh makes the UI honest again."""
        monkeypatch.setattr(control.widgets, "ENABLE_RECORDING", False)
        stub = _LiveTriggerStub(supports_hw=True, trigger_mode=TriggerMode.HARDWARE)
        stub.refresh_trigger_options()
        assert stub.dropdown_triggerManu.currentText() == TriggerMode.HARDWARE

    def test_refresh_sends_no_trigger_mode_command(self, qtbot, monkeypatch):
        """Repopulating must not look like a user choice: no MCU trigger-mode command."""
        monkeypatch.setattr(control.widgets, "ENABLE_RECORDING", False)
        stub = _LiveTriggerStub(supports_hw=True, trigger_mode=TriggerMode.HARDWARE)
        stub.refresh_trigger_options()
        # Switch to a camera with no trigger line: the Hardware entry disappears, which
        # would otherwise fire currentIndexChanged -> set_trigger_mode.
        stub.camera = _fake_camera(supports_hw=False, exposure_limits=(0.1, 1000.0))
        stub.liveController.trigger_mode = TriggerMode.SOFTWARE
        stub.refresh_trigger_options()
        assert stub.items() == [TriggerMode.SOFTWARE]
        assert stub.applied_modes == []
        assert stub.is_switching_mode is False

    def test_update_trigger_mode_guarded_by_is_switching_mode(self, qtbot):
        stub = _LiveTriggerStub()
        stub.refresh_trigger_options()
        stub.is_switching_mode = True
        stub.update_trigger_mode()
        assert stub.applied_modes == []

    def test_user_selection_still_applies_the_mode(self, qtbot, monkeypatch):
        monkeypatch.setattr(control.widgets, "ENABLE_RECORDING", False)
        stub = _LiveTriggerStub(supports_hw=True)
        stub.refresh_trigger_options()
        stub.dropdown_triggerManu.setCurrentIndex(1)  # Hardware
        assert stub.applied_modes == [TriggerMode.HARDWARE]

    def test_camera_change_reclamps_exposure_without_persisting(self, qtbot, monkeypatch):
        monkeypatch.setattr(control.widgets, "ENABLE_RECORDING", False)
        persisted = []
        stub = _LiveTriggerStub(supports_hw=False, exposure_limits=(1.0, 500.0))
        stub.entry_exposureTime.valueChanged.connect(
            lambda v: persisted.append(v) if not stub.is_switching_mode else None
        )
        stub.on_active_camera_changed(2)
        assert stub.entry_exposureTime.minimum() == 1.0
        assert stub.entry_exposureTime.maximum() == 500.0
        assert stub.entry_exposureTime.value() == 500.0  # Qt clamped the held 2000 ms
        assert persisted == []  # ...but the clamp was not treated as a user edit
        assert stub.items() == [TriggerMode.SOFTWARE]
        assert stub.is_switching_mode is False


class TestNapariLiveTriggerOptions:
    def test_hardware_hidden_when_camera_cannot_be_triggered(self, qtbot):
        stub = _NapariTriggerStub(supports_hw=False)
        stub.refresh_trigger_options()
        assert stub.items() == [TriggerMode.SOFTWARE, TriggerMode.CONTINUOUS]

    def test_hardware_offered_when_supported_and_selection_resyncs(self, qtbot):
        stub = _NapariTriggerStub(supports_hw=True, trigger_mode=TriggerMode.CONTINUOUS)
        stub.refresh_trigger_options()
        assert stub.items() == [TriggerMode.SOFTWARE, TriggerMode.HARDWARE, TriggerMode.CONTINUOUS]
        assert stub.dropdown_triggerMode.currentData() == TriggerMode.CONTINUOUS

    def test_camera_change_refreshes_options_and_exposure_range(self, qtbot):
        stub = _NapariTriggerStub(supports_hw=True)
        stub.refresh_trigger_options()
        stub.liveController.camera = _fake_camera(supports_hw=False, exposure_limits=(2.0, 300.0))
        stub.on_active_camera_changed(2)
        assert stub.items() == [TriggerMode.SOFTWARE, TriggerMode.CONTINUOUS]
        assert (stub.entry_exposureTime.minimum(), stub.entry_exposureTime.maximum()) == (2.0, 300.0)
        assert stub.observed == []  # repopulation is not a user choice
        assert stub.is_switching_mode is False


class TestCameraTabName:
    @pytest.mark.parametrize(
        "camera_id,expected",
        [(1, "Main Camera"), (2, "Side Camera")],
    )
    def test_named_cameras_use_registry_name(self, camera_id, expected):
        assert HighContentScreeningGui._camera_tab_name(camera_id, TWO_CAM) == expected

    def test_camera_absent_from_registry_falls_back_to_id(self):
        assert HighContentScreeningGui._camera_tab_name(3, TWO_CAM) == "Camera 3"

    def test_missing_registry_falls_back_to_id(self):
        assert HighContentScreeningGui._camera_tab_name(3, None) == "Camera 3"
