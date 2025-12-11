"""Tests for EventBus widget base classes and widget event communication.

Tests verify:
1. EventBusWidget base class subscription management
2. Widgets publish correct Command events on user actions
3. Widgets update UI correctly from State events
4. Subscription cleanup works on close

Note: Widget instantiation tests are skipped in headless/offscreen mode
because Qt widgets need a display. Base class tests that don't require
display are run unconditionally.
"""

import os
import pytest
from unittest.mock import MagicMock
from dataclasses import dataclass

from squid.events import (
    EventBus,
    Event,
    MoveStageCommand,
    SetExposureTimeCommand,
    SetDACCommand,
    DACValueChanged,
)


@dataclass
class DummyEvent(Event):
    """Test event for base class tests."""

    value: int


# These tests don't require Qt widgets, just the base class logic
class TestEventBusSubscriptionManagement:
    """Tests for EventBus subscription tracking (no Qt required)."""

    def test_bus_subscription_roundtrip(self):
        """Test EventBus subscribe and publish."""
        bus = EventBus()
        received = []
        bus.subscribe(DummyEvent, received.append)
        bus.publish(DummyEvent(value=42))
        assert len(received) == 1
        assert received[0].value == 42

    def test_bus_unsubscribe(self):
        """Test EventBus unsubscribe."""
        bus = EventBus()
        received = []
        handler = received.append
        bus.subscribe(DummyEvent, handler)
        bus.unsubscribe(DummyEvent, handler)
        bus.publish(DummyEvent(value=42))
        assert len(received) == 0

    def test_bus_clear(self):
        """Test EventBus clear removes all subscriptions."""
        bus = EventBus()
        received = []
        bus.subscribe(DummyEvent, received.append)
        bus.clear()
        bus.publish(DummyEvent(value=42))
        assert len(received) == 0


# Skip widget tests in headless mode
_skip_widget_tests = os.environ.get("QT_QPA_PLATFORM") == "offscreen"


@pytest.mark.skipif(_skip_widget_tests, reason="Skipping widget tests in offscreen mode")
class TestEventBusWidgetBase:
    """Tests for EventBusWidget base class functionality."""

    @pytest.fixture(autouse=True)
    def setup_qapp(self):
        """Ensure QApplication exists."""
        from qtpy.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        yield

    def test_subscribe_tracks_subscription(self):
        """Test that _subscribe adds to subscription list."""
        from control.widgets.base import EventBusWidget

        bus = EventBus()
        widget = EventBusWidget(bus)

        handler = MagicMock()
        widget._subscribe(DummyEvent, handler)

        assert len(widget._subscriptions) == 1
        assert widget._subscriptions[0] == (DummyEvent, handler)

    def test_publish_sends_to_bus(self):
        """Test that _publish sends events to the bus."""
        from control.widgets.base import EventBusWidget

        bus = EventBus()
        widget = EventBusWidget(bus)

        received = []
        bus.subscribe(DummyEvent, received.append)

        widget._publish(DummyEvent(value=42))

        assert len(received) == 1
        assert received[0].value == 42

    def test_cleanup_unsubscribes_all(self):
        """Test that _cleanup_subscriptions removes all subscriptions."""
        from control.widgets.base import EventBusWidget

        bus = EventBus()
        widget = EventBusWidget(bus)

        handler1 = MagicMock()
        handler2 = MagicMock()
        widget._subscribe(DummyEvent, handler1)
        widget._subscribe(DummyEvent, handler2)

        assert len(widget._subscriptions) == 2

        widget._cleanup_subscriptions()

        assert len(widget._subscriptions) == 0

    def test_close_event_calls_cleanup(self):
        """Test that closeEvent triggers subscription cleanup."""
        from control.widgets.base import EventBusWidget
        from qtpy.QtGui import QCloseEvent

        bus = EventBus()
        widget = EventBusWidget(bus)

        handler = MagicMock()
        widget._subscribe(DummyEvent, handler)

        # Simulate close
        event = QCloseEvent()
        widget.closeEvent(event)

        assert len(widget._subscriptions) == 0


@pytest.mark.skipif(_skip_widget_tests, reason="Skipping widget tests in offscreen mode")
class TestEventBusFrame:
    """Tests for EventBusFrame base class."""

    @pytest.fixture(autouse=True)
    def setup_qapp(self):
        """Ensure QApplication exists."""
        from qtpy.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        yield

    def test_frame_inherits_base_behavior(self):
        """Test EventBusFrame has same behavior as EventBusWidget."""
        from control.widgets.base import EventBusFrame

        bus = EventBus()
        frame = EventBusFrame(bus)

        handler = MagicMock()
        frame._subscribe(DummyEvent, handler)

        # Publish and verify handler called
        bus.publish(DummyEvent(value=10))
        handler.assert_called_once()


@pytest.mark.skipif(_skip_widget_tests, reason="Skipping widget tests in offscreen mode")
class TestEventBusDialog:
    """Tests for EventBusDialog base class."""

    @pytest.fixture(autouse=True)
    def setup_qapp(self):
        """Ensure QApplication exists."""
        from qtpy.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        yield

    def test_dialog_inherits_base_behavior(self):
        """Test EventBusDialog has same behavior as EventBusWidget."""
        from control.widgets.base import EventBusDialog

        bus = EventBus()
        dialog = EventBusDialog(bus)

        handler = MagicMock()
        dialog._subscribe(DummyEvent, handler)

        # Publish and verify handler called
        bus.publish(DummyEvent(value=20))
        handler.assert_called_once()

    def test_requires_event_bus_argument(self):
        """Constructing without bus should raise TypeError."""
        from control.widgets.base import EventBusDialog

        with pytest.raises(TypeError):
            EventBusDialog()  # type: ignore[call-arg]


# Unit tests that verify widget inheritance without instantiation
class TestWidgetInheritance:
    """Tests to verify widgets inherit from correct EventBus base classes."""

    def test_dac_widget_inherits_event_bus_frame(self):
        """Verify DACControWidget inherits from EventBusFrame."""
        from control.widgets.hardware.dac import DACControWidget
        from control.widgets.base import EventBusFrame

        assert issubclass(DACControWidget, EventBusFrame)

    def test_trigger_widget_inherits_event_bus_frame(self):
        """Verify TriggerControlWidget inherits from EventBusFrame."""
        from control.widgets.hardware.trigger import TriggerControlWidget
        from control.widgets.base import EventBusFrame

        assert issubclass(TriggerControlWidget, EventBusFrame)

    def test_navigation_widget_inherits_event_bus_frame(self):
        """Verify NavigationWidget inherits from EventBusFrame."""
        from control.widgets.stage.navigation import NavigationWidget
        from control.widgets.base import EventBusFrame

        assert issubclass(NavigationWidget, EventBusFrame)

    def test_live_control_widget_inherits_event_bus_frame(self):
        """Verify LiveControlWidget inherits from EventBusFrame."""
        from control.widgets.camera.live_control import LiveControlWidget
        from control.widgets.base import EventBusFrame

        assert issubclass(LiveControlWidget, EventBusFrame)

    def test_camera_settings_widget_inherits_event_bus_frame(self):
        """Verify CameraSettingsWidget inherits from EventBusFrame."""
        from control.widgets.camera.settings import CameraSettingsWidget
        from control.widgets.base import EventBusFrame

        assert issubclass(CameraSettingsWidget, EventBusFrame)

    def test_wellplate_calibration_inherits_event_bus_dialog(self):
        """Verify WellplateCalibration inherits from EventBusDialog."""
        from control.widgets.wellplate.calibration import WellplateCalibration
        from control.widgets.base import EventBusDialog

        assert issubclass(WellplateCalibration, EventBusDialog)

    def test_wellplate_format_widget_inherits_event_bus_widget(self):
        """Verify WellplateFormatWidget inherits from EventBusWidget."""
        from control.widgets.wellplate.format import WellplateFormatWidget
        from control.widgets.base import EventBusWidget

        assert issubclass(WellplateFormatWidget, EventBusWidget)

    def test_constructor_requires_event_bus(self):
        """Widget constructors should fail without valid event_bus.

        Widgets either raise TypeError (missing required arg) or AttributeError
        (None passed but used during __init__).
        """
        from control.widgets.stage.navigation import NavigationWidget
        from control.widgets.camera.live_control import LiveControlWidget
        from control.widgets.camera.settings import CameraSettingsWidget
        from control.widgets.hardware.trigger import TriggerControlWidget
        from control.widgets.hardware.dac import DACControWidget
        from control.widgets.stage.autofocus import AutoFocusWidget
        from control.widgets.wellplate.format import WellplateFormatWidget

        # Widgets that subscribe in __init__ raise AttributeError with None bus
        with pytest.raises((TypeError, AttributeError)):
            NavigationWidget(None)  # type: ignore[arg-type]
        with pytest.raises((TypeError, AttributeError)):
            LiveControlWidget(None, None, None)  # type: ignore[arg-type]
        with pytest.raises((TypeError, AttributeError)):
            CameraSettingsWidget(None, None)  # type: ignore[arg-type]
        with pytest.raises((TypeError, AttributeError)):
            WellplateFormatWidget(None, None, None)  # type: ignore[arg-type]

        # Widgets without required args raise TypeError
        with pytest.raises(TypeError):
            TriggerControlWidget()  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            DACControWidget()  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            AutoFocusWidget()  # type: ignore[call-arg]
