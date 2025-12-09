"""
Event bus for decoupled component communication.

Provides a simple publish/subscribe mechanism that allows components
to communicate without direct references to each other.

Usage:
    from squid.events import Event, EventBus, event_bus

    # Define event types as dataclasses
    @dataclass
    class ImageCaptured(Event):
        frame: CameraFrame
        info: CaptureInfo

    # Subscribe to events
    event_bus.subscribe(ImageCaptured, lambda e: display(e.frame))

    # Publish events
    event_bus.publish(ImageCaptured(frame=frame, info=info))
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Type, TypeVar, Optional
from threading import Lock
import squid.logging

_log = squid.logging.get_logger("squid.events")


@dataclass
class Event:
    """Base class for all events."""

    pass


E = TypeVar("E", bound=Event)


class EventBus:
    """
    Simple event bus for decoupled communication.

    Thread-safe publish/subscribe mechanism. Handler exceptions
    are logged but do not crash the bus.

    Example:
        bus = EventBus()

        # Subscribe
        bus.subscribe(ImageCaptured, self.on_image)

        # Publish
        bus.publish(ImageCaptured(frame=frame, info=info))

        # Unsubscribe
        bus.unsubscribe(ImageCaptured, self.on_image)

        # Enable debug mode to print all events
        bus.set_debug(True)
    """

    def __init__(self):
        self._subscribers: Dict[Type[Event], List[Callable]] = {}
        self._lock = Lock()
        self._debug = False

    def set_debug(self, enabled: bool) -> None:
        """Enable or disable debug mode to print all events."""
        self._debug = enabled
        if enabled:
            _log.info("EventBus debug mode enabled - all events will be logged")

    def subscribe(self, event_type: Type[E], handler: Callable[[E], None]) -> None:
        """
        Subscribe to an event type.

        Args:
            event_type: The event class to subscribe to
            handler: Function called with event when published
        """
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: Type[E], handler: Callable[[E], None]) -> None:
        """
        Unsubscribe from an event type.

        Args:
            event_type: The event class to unsubscribe from
            handler: The handler to remove
        """
        with self._lock:
            if event_type in self._subscribers:
                try:
                    self._subscribers[event_type].remove(handler)
                except ValueError:
                    pass  # Handler not in list

    def publish(self, event: Event) -> None:
        """
        Publish an event to all subscribers.

        Args:
            event: The event to publish
        """
        if self._debug:
            _log.debug(f"[EventBus] {type(event).__name__}: {event}")

        with self._lock:
            handlers = list(self._subscribers.get(type(event), []))

        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                _log.exception(f"Handler {handler} failed for event {event}: {e}")

    def clear(self) -> None:
        """Remove all subscriptions."""
        with self._lock:
            self._subscribers.clear()


# Global event bus instance
# Can be replaced with dependency injection if needed
event_bus = EventBus()


# Common event types


@dataclass
class AcquisitionStarted(Event):
    """Emitted when acquisition begins."""

    experiment_id: str
    timestamp: float


@dataclass
class AcquisitionFinished(Event):
    """Emitted when acquisition completes."""

    success: bool
    error: Optional[Exception] = None


@dataclass
class ImageCaptured(Event):
    """Emitted when an image is captured."""

    frame_id: int
    # Note: Don't include heavy objects like frame data
    # Use frame_id to look up from a cache instead


@dataclass
class StageMovedTo(Event):
    """Emitted when stage moves to a position."""

    x_mm: float
    y_mm: float
    z_mm: float


@dataclass
class FocusChanged(Event):
    """Emitted when focus changes."""

    z_mm: float
    source: str  # "autofocus", "manual", "focus_map"


# ============================================================
# Command Events (GUI -> Service)
# ============================================================


@dataclass
class SetExposureTimeCommand(Event):
    """Command to set camera exposure time."""

    exposure_time_ms: float


@dataclass
class SetAnalogGainCommand(Event):
    """Command to set camera analog gain."""

    gain: float


@dataclass
class SetDACCommand(Event):
    """Command to set DAC output value."""

    channel: int
    value: float  # 0-100 percentage


@dataclass
class StartCameraTriggerCommand(Event):
    """Command to start camera hardware trigger."""

    pass


@dataclass
class StopCameraTriggerCommand(Event):
    """Command to stop camera hardware trigger."""

    pass


@dataclass
class SetCameraTriggerFrequencyCommand(Event):
    """Command to set camera trigger frequency (Hz)."""

    fps: float


@dataclass
class TurnOnAFLaserCommand(Event):
    """Command to turn on AF laser."""

    wait_for_completion: bool = True


@dataclass
class TurnOffAFLaserCommand(Event):
    """Command to turn off AF laser."""

    wait_for_completion: bool = True


@dataclass
class MoveStageCommand(Event):
    """Command to move stage by relative distance."""

    axis: str  # 'x', 'y', or 'z'
    distance_mm: float


@dataclass
class MoveStageToCommand(Event):
    """Command to move stage to absolute position."""

    x_mm: Optional[float] = None
    y_mm: Optional[float] = None
    z_mm: Optional[float] = None


@dataclass
class HomeStageCommand(Event):
    """Command to home stage axes."""

    x: bool = False
    y: bool = False
    z: bool = False
    theta: bool = False


@dataclass
class ZeroStageCommand(Event):
    """Command to zero stage axes."""

    x: bool = False
    y: bool = False
    z: bool = False
    theta: bool = False


@dataclass
class MoveStageToLoadingPositionCommand(Event):
    """Command to move stage to loading position."""

    blocking: bool = True
    callback: Optional[Callable[[bool, Optional[str]], None]] = None
    is_wellplate: bool = True


@dataclass
class MoveStageToScanningPositionCommand(Event):
    """Command to move stage to scanning position."""

    blocking: bool = True
    callback: Optional[Callable[[bool, Optional[str]], None]] = None
    is_wellplate: bool = True


@dataclass
class SetIlluminationCommand(Event):
    """Command to set illumination."""

    channel: int
    intensity: float
    on: bool


@dataclass
class StartLiveCommand(Event):
    """Command to start live view."""

    configuration: Optional[str] = None


@dataclass
class StopLiveCommand(Event):
    """Command to stop live view."""

    pass


# ============================================================
# State Events (Service -> GUI)
# ============================================================


@dataclass
class ExposureTimeChanged(Event):
    """Notification that exposure time changed."""

    exposure_time_ms: float


@dataclass
class AnalogGainChanged(Event):
    """Notification that analog gain changed."""

    gain: float


@dataclass
class StagePositionChanged(Event):
    """Notification that stage position changed."""

    x_mm: float
    y_mm: float
    z_mm: float
    theta_rad: Optional[float] = None


@dataclass
class LiveStateChanged(Event):
    """Notification that live view state changed."""

    is_live: bool
    configuration: Optional[str] = None


@dataclass
class DACValueChanged(Event):
    """Notification that DAC value changed."""

    channel: int
    value: float


@dataclass
class ROIChanged(Event):
    """Notification that ROI changed."""

    x_offset: int
    y_offset: int
    width: int
    height: int


@dataclass
class BinningChanged(Event):
    """Notification that binning changed."""

    binning_x: int
    binning_y: int


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squid.config import CameraPixelFormat


@dataclass
class PixelFormatChanged(Event):
    """Notification that pixel format changed."""

    pixel_format: "CameraPixelFormat"  # Forward reference to avoid circular import


# ============================================================
# Trigger Control Commands
# ============================================================


@dataclass
class SetTriggerModeCommand(Event):
    """Command to set camera trigger mode."""

    mode: str  # "Software", "Hardware", "Continuous"


@dataclass
class SetTriggerFPSCommand(Event):
    """Command to set trigger frequency."""

    fps: float


# ============================================================
# Trigger State Events
# ============================================================


@dataclass
class TriggerModeChanged(Event):
    """Notification that trigger mode changed."""

    mode: str


@dataclass
class TriggerFPSChanged(Event):
    """Notification that trigger FPS changed."""

    fps: float


# ============================================================
# Microscope Mode Commands
# ============================================================


@dataclass
class SetMicroscopeModeCommand(Event):
    """Command to set microscope mode/channel configuration."""

    configuration_name: str
    objective: str


# ============================================================
# Microscope Mode State Events
# ============================================================


@dataclass
class MicroscopeModeChanged(Event):
    """Notification that microscope mode changed."""

    configuration_name: str
