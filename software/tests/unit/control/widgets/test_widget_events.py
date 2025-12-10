"""Widget event wiring tests for Phase 5 refactor."""

import os
from dataclasses import dataclass
from typing import Any, List, Tuple

import pytest
from qtpy.QtWidgets import QApplication

from squid.events import (
    MoveStageRelativeCommand,
    SetExposureTimeCommand,
    SetAnalogGainCommand,
    StartLiveCommand,
    StopLiveCommand,
    SetTriggerModeCommand,
    SetTriggerFPSCommand,
    SetAutofocusParamsCommand,
    StartAutofocusCommand,
    StopAutofocusCommand,
    SetDACCommand,
    StagePositionChanged,
    SaveWellplateCalibrationCommand,
)


class FakeBus:
    """Simple event bus test double."""

    def __init__(self) -> None:
        self.published: List[Any] = []
        self.subscriptions: List[Tuple[type, Any]] = []

    def publish(self, event: Any) -> None:
        self.published.append(event)

    def subscribe(self, event_type: type, handler: Any) -> None:
        self.subscriptions.append((event_type, handler))

    def unsubscribe(self, event_type: type, handler: Any) -> None:
        # optional for these tests
        pass


@pytest.fixture(scope="module")
def qapp():
    """Ensure a QApplication exists."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


_skip_widgets = os.environ.get("QT_QPA_PLATFORM") == "offscreen"


@pytest.mark.skipif(_skip_widgets, reason="Skipping widget tests in offscreen mode")
class TestNavigationWidgetEvents:
    """NavigationWidget publishes relative move events."""

    class FakeStageService:
        def get_x_mm_per_ustep(self) -> float:
            return 0.001

        def get_y_mm_per_ustep(self) -> float:
            return 0.001

        def get_z_mm_per_ustep(self) -> float:
            return 0.001

    def test_move_x_forward_publishes_relative_event(self, qapp):
        from control.widgets.stage.navigation import NavigationWidget

        bus = FakeBus()
        stage_service = self.FakeStageService()
        widget = NavigationWidget(stage_service=stage_service, event_bus=bus)
        bus.published.clear()

        widget.entry_dX.setValue(1.0)
        widget.move_x_forward()

        assert isinstance(bus.published[-1], MoveStageRelativeCommand)
        assert bus.published[-1].x_mm == 1.0

    def test_navigation_subscribes_to_position(self, qapp):
        from control.widgets.stage.navigation import NavigationWidget

        bus = FakeBus()
        stage_service = self.FakeStageService()
        NavigationWidget(stage_service=stage_service, event_bus=bus)
        assert any(sub[0] is StagePositionChanged for sub in bus.subscriptions)


@pytest.mark.skipif(_skip_widgets, reason="Skipping widget tests in offscreen mode")
class TestLiveControlWidgetEvents:
    """LiveControlWidget publishes start/stop live commands."""

    @dataclass
    class FakeChannelMode:
        name: str
        exposure_time: float
        analog_gain: float
        illumination_intensity: float

    class FakeChannelConfigurationManager:
        def __init__(self, mode):
            self._mode = mode

        def get_channel_configurations_for_objective(self, obj):
            return [self._mode]

        def get_channel_configuration_by_name(self, obj, name):
            return self._mode

    class FakeStreamHandler:
        def set_display_fps(self, fps: float) -> None:
            self.fps = fps

    class FakeObjectiveStore:
        def __init__(self):
            self.current_objective = "obj"

    def test_toggle_live_publishes_commands(self, qapp):
        from control.widgets.camera.live_control import LiveControlWidget

        bus = FakeBus()
        mode = self.FakeChannelMode("mode1", 10.0, 1.0, 50.0)
        channel_manager = self.FakeChannelConfigurationManager(mode)
        stream_handler = self.FakeStreamHandler()
        objective_store = self.FakeObjectiveStore()

        widget = LiveControlWidget(
            event_bus=bus,
            streamHandler=stream_handler,
            objectiveStore=objective_store,
            channelConfigurationManager=channel_manager,
        )
        bus.published.clear()

        widget.toggle_live(True)
        assert any(isinstance(evt, StartLiveCommand) for evt in bus.published)

        bus.published.clear()
        widget.toggle_live(False)
        assert any(isinstance(evt, StopLiveCommand) for evt in bus.published)

    def test_live_control_subscribes(self, qapp):
        from control.widgets.camera.live_control import LiveControlWidget

        bus = FakeBus()
        mode = self.FakeChannelMode("mode1", 10.0, 1.0, 50.0)
        channel_manager = self.FakeChannelConfigurationManager(mode)
        stream_handler = self.FakeStreamHandler()
        objective_store = self.FakeObjectiveStore()

        LiveControlWidget(
            event_bus=bus,
            streamHandler=stream_handler,
            objectiveStore=objective_store,
            channelConfigurationManager=channel_manager,
        )
        subscribed_types = {sub[0] for sub in bus.subscriptions}
        assert TriggerModeChanged in subscribed_types
        assert TriggerFPSChanged in subscribed_types


@pytest.mark.skipif(_skip_widgets, reason="Skipping widget tests in offscreen mode")
class TestCameraSettingsWidgetEvents:
    """CameraSettingsWidget publishes settings commands."""

    @dataclass
    class FakeGainRange:
        min_gain: float
        max_gain: float
        gain_step: float

    class FakeCameraService:
        def get_exposure_limits(self):
            return (0.1, 1000.0)

        def get_gain_range(self):
            return TestCameraSettingsWidgetEvents.FakeGainRange(0.0, 10.0, 0.5)

        def get_available_pixel_formats(self):
            class PF:
                def __init__(self, name):
                    self.name = name

            return [PF("MONO8"), PF("MONO16")]

        def get_pixel_format(self):
            return None

        def get_region_of_interest(self):
            return (0, 0, 64, 64)

        def get_resolution(self):
            return (128, 128)

    def test_exposure_change_publishes_command(self, qapp):
        from control.widgets.camera.settings import CameraSettingsWidget

        bus = FakeBus()
        widget = CameraSettingsWidget(
            event_bus=bus,
            exposure_limits=self.FakeCameraService.get_exposure_limits(None),
            gain_range=self.FakeCameraService.get_gain_range(None),
            pixel_format_names=[pf.name for pf in self.FakeCameraService.get_available_pixel_formats(None)],
            current_pixel_format=None,
            roi_info=self.FakeCameraService.get_region_of_interest(None),
            resolution=self.FakeCameraService.get_resolution(None),
            binning_options=self.FakeCameraService.get_binning_options(None),
            current_binning=self.FakeCameraService.get_binning(None),
            include_gain_exposure_time=True,
            include_camera_temperature_setting=False,
            include_camera_auto_wb_setting=False,
        )
        bus.published.clear()

        widget.entry_exposureTime.setValue(25.0)
        assert isinstance(bus.published[-1], SetExposureTimeCommand)
        assert bus.published[-1].exposure_time_ms == 25.0

    def test_gain_change_publishes_command(self, qapp):
        from control.widgets.camera.settings import CameraSettingsWidget

        bus = FakeBus()
        widget = CameraSettingsWidget(
            event_bus=bus,
            exposure_limits=self.FakeCameraService.get_exposure_limits(None),
            gain_range=self.FakeCameraService.get_gain_range(None),
            pixel_format_names=[pf.name for pf in self.FakeCameraService.get_available_pixel_formats(None)],
            current_pixel_format=None,
            roi_info=self.FakeCameraService.get_region_of_interest(None),
            resolution=self.FakeCameraService.get_resolution(None),
            binning_options=self.FakeCameraService.get_binning_options(None),
            current_binning=self.FakeCameraService.get_binning(None),
            include_gain_exposure_time=True,
            include_camera_temperature_setting=False,
            include_camera_auto_wb_setting=False,
        )
        bus.published.clear()

        widget.entry_analogGain.setValue(2.5)
        assert isinstance(bus.published[-1], SetAnalogGainCommand)
        assert bus.published[-1].gain == 2.5


@pytest.mark.skipif(_skip_widgets, reason="Skipping widget tests in offscreen mode")
class TestAutoFocusWidgetEvents:
    """AutoFocusWidget publishes autofocus commands."""

    def test_autofocus_start_stop(self, qapp):
        from control.widgets.stage.autofocus import AutoFocusWidget

        bus = FakeBus()
        widget = AutoFocusWidget(event_bus=bus)
        bus.published.clear()

        widget._on_autofocus_toggled(True)
        assert any(isinstance(evt, SetAutofocusParamsCommand) for evt in bus.published)
        assert any(isinstance(evt, StartAutofocusCommand) for evt in bus.published)

        bus.published.clear()
        widget._on_autofocus_toggled(False)
        assert any(isinstance(evt, StopAutofocusCommand) for evt in bus.published)

    def test_autofocus_subscribes(self, qapp):
        from control.widgets.stage.autofocus import AutoFocusWidget

        bus = FakeBus()
        AutoFocusWidget(event_bus=bus)
        subscribed_types = {sub[0] for sub in bus.subscriptions}
        assert AutofocusProgress in subscribed_types
        assert AutofocusCompleted in subscribed_types


@pytest.mark.skipif(_skip_widgets, reason="Skipping widget tests in offscreen mode")
class TestTriggerControlWidgetEvents:
    """TriggerControlWidget publishes trigger commands."""

    def test_trigger_mode_and_fps_publish_events(self, qapp):
        from control.widgets.hardware.trigger import TriggerControlWidget

        bus = FakeBus()
        widget = TriggerControlWidget(event_bus=bus)
        bus.published.clear()

        widget.dropdown_triggerManu.setCurrentText("Hardware")
        widget.update_trigger_mode()
        assert isinstance(bus.published[-1], SetTriggerModeCommand)

        bus.published.clear()
        widget.update_trigger_fps(20.0)
        assert any(isinstance(evt, SetTriggerFPSCommand) for evt in bus.published)

    def test_toggle_live_publishes_start_stop(self, qapp):
        from control.widgets.hardware.trigger import TriggerControlWidget

        bus = FakeBus()
        widget = TriggerControlWidget(event_bus=bus)
        bus.published.clear()

        widget.toggle_live(True)
        assert not bus.published  # no legacy start/stop trigger commands

        bus.published.clear()
        widget.toggle_live(False)
        assert not bus.published

    def test_trigger_subscribes_to_state(self, qapp):
        from control.widgets.hardware.trigger import TriggerControlWidget

        bus = FakeBus()
        TriggerControlWidget(event_bus=bus)
        subscribed_types = {sub[0] for sub in bus.subscriptions}
        assert TriggerModeChanged in subscribed_types
        assert TriggerFPSChanged in subscribed_types


@pytest.mark.skipif(_skip_widgets, reason="Skipping widget tests in offscreen mode")
class TestDACWidgetEvents:
    """DACControWidget publishes DAC commands."""

    def test_dac_slider_publishes_normalized_value(self, qapp):
        from control.widgets.hardware.dac import DACControWidget

        bus = FakeBus()
        widget = DACControWidget(event_bus=bus)
        bus.published.clear()

        widget.set_DAC0(50)
        assert isinstance(bus.published[-1], SetDACCommand)
        assert bus.published[-1].channel == 0
        assert bus.published[-1].value == 0.5


@pytest.mark.skipif(_skip_widgets, reason="Skipping widget tests in offscreen mode")
class TestWellplateFormatWidgetEvents:
    """WellplateFormatWidget handles save calibration events."""

    class DummyNav:
        pass

    class DummyStream:
        pass

    def test_save_calibration_event_updates_settings(self, qapp):
        from control.widgets.wellplate.format import WellplateFormatWidget, WELLPLATE_FORMAT_SETTINGS

        bus = FakeBus()
        widget = WellplateFormatWidget(
            event_bus=bus,
            navigationViewer=self.DummyNav(),
            streamHandler=self.DummyStream(),
        )
        calibration = {"a1_x_mm": 1.0, "a1_y_mm": 2.0, "well_size_mm": 3.0, "well_spacing_mm": 9.0, "number_of_skip": 0, "rows": 2, "cols": 2, "a1_x_pixel": 0, "a1_y_pixel": 0}
        widget._on_save_calibration(
            SaveWellplateCalibrationCommand(calibration=calibration, name="test_format")
        )
        assert "test_format" in WELLPLATE_FORMAT_SETTINGS


@pytest.mark.skipif(_skip_widgets, reason="Skipping widget tests in offscreen mode")
class TestNoDirectHardwareAttributes:
    """Widgets should not expose direct hardware/controller attributes."""

    def test_widgets_do_not_expose_controllers(self, qapp):
        from control.widgets.stage.navigation import NavigationWidget
        from control.widgets.camera.live_control import LiveControlWidget
        from control.widgets.camera.settings import CameraSettingsWidget
        from control.widgets.stage.autofocus import AutoFocusWidget
        from control.widgets.hardware.trigger import TriggerControlWidget
        from control.widgets.wellplate.calibration import WellplateCalibration

        bus = FakeBus()

        class DummyStageService:
            def get_x_mm_per_ustep(self): return 0.001
            def get_y_mm_per_ustep(self): return 0.001
            def get_z_mm_per_ustep(self): return 0.001

        class DummyLiveDeps:
            pass

        nav = NavigationWidget(stage_service=DummyStageService(), event_bus=bus)
        live = LiveControlWidget(
            event_bus=bus,
            streamHandler=DummyLiveDeps(),
            objectiveStore=DummyLiveDeps(),
            channelConfigurationManager=DummyLiveDeps(),
        )
        cam = CameraSettingsWidget(
            event_bus=bus,
            exposure_limits=(0.1, 1000.0),
            gain_range=None,
            pixel_format_names=["MONO8"],
            current_pixel_format=None,
            roi_info=(0, 0, 64, 64),
            resolution=(128, 128),
            binning_options=[(1, 1)],
            current_binning=(1, 1),
            include_gain_exposure_time=True,
            include_camera_temperature_setting=False,
            include_camera_auto_wb_setting=False,
        )
        af = AutoFocusWidget(event_bus=bus)
        trig = TriggerControlWidget(event_bus=bus)
        well = WellplateCalibration(
            event_bus=bus,
            wellplateFormatWidget=DummyLiveDeps(),
            navigationViewer=DummyLiveDeps(),
            streamHandler=DummyLiveDeps(),
        )

        assert not hasattr(nav, "stage")
        assert not hasattr(live, "liveController")
        assert not hasattr(cam, "_service")
        assert not hasattr(af, "autofocusController")
        assert not hasattr(trig, "microcontroller")
        assert not hasattr(well, "stage")
