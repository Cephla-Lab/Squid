"""
Event bus for decoupled component communication.

Provides a simple publish/subscribe mechanism that allows components
to communicate without direct references to each other.

Usage:
    from squid.core.events import Event, EventBus, event_bus

    # Define event types as dataclasses
    @dataclass
    class StageMoved(Event):
        x_mm: float
        y_mm: float

    # Subscribe to events
    event_bus.subscribe(StageMoved, lambda e: print(e.x_mm, e.y_mm))

    # Publish events
    event_bus.publish(StageMoved(x_mm=1.0, y_mm=2.0))
"""

import queue
import threading
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, TypeVar

import numpy as np

import squid.core.logging

_log = squid.core.logging.get_logger("squid.events")


@dataclass
class Event:
    """Base class for all events."""

    pass


E = TypeVar("E", bound=Event)


class EventBus:
    """
    Queued event bus for decoupled communication.

    Thread-safe publish/subscribe mechanism with queued dispatch.
    Events are enqueued and dispatched by a dedicated thread, ensuring
    deterministic ordering and preventing callback deadlocks.

    Handler exceptions are logged but do not crash the bus.

    Example:
        bus = EventBus()
        bus.start()  # Start the dispatch thread

        # Subscribe
        bus.subscribe(StageMoved, self.on_stage_moved)

        # Publish (O(1), non-blocking)
        bus.publish(StageMoved(x_mm=1.0, y_mm=2.0))

        # Unsubscribe
        bus.unsubscribe(StageMoved, self.on_stage_moved)

        # Shutdown
        bus.stop()
    """

    def __init__(self):
        self._subscribers: Dict[Type[Event], List[Callable]] = {}
        self._lock = Lock()
        self._debug = False
        self._queue: queue.Queue[Event] = queue.Queue()
        self._dispatch_thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        """Start the dispatch thread.

        Events published before start() will be queued and dispatched
        once the thread begins.
        """
        if self._running:
            return
        self._running = True
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop,
            name="EventBus-Dispatch",
            daemon=True,
        )
        self._dispatch_thread.start()
        _log.debug("EventBus dispatch thread started")

    def stop(self, timeout_s: float = 2.0) -> None:
        """Stop the dispatch thread gracefully.

        Args:
            timeout_s: Maximum time to wait for thread to finish.
        """
        if not self._running:
            return
        self._running = False
        if self._dispatch_thread and self._dispatch_thread.is_alive():
            self._dispatch_thread.join(timeout=timeout_s)
            if self._dispatch_thread.is_alive():
                _log.warning("EventBus dispatch thread did not stop cleanly")
        self._dispatch_thread = None
        _log.debug("EventBus dispatch thread stopped")

    @property
    def is_running(self) -> bool:
        """Return whether the dispatch thread is running."""
        return self._running

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

        This is O(1) and non-blocking - the event is queued for
        dispatch by the background thread.

        Args:
            event: The event to publish
        """
        if not self._running:
            # Ensure the dispatch thread is alive before enqueueing
            self.start()
        if self._debug:
            _log.debug(f"[EventBus] enqueue {type(event).__name__}: {event}")
        self._queue.put(event)

    def _dispatch_loop(self) -> None:
        """Main dispatch loop - drains queue and dispatches events."""
        while self._running:
            try:
                event = self._queue.get(timeout=0.1)
                self._dispatch(event)
            except queue.Empty:
                continue
            except Exception as e:
                _log.exception(f"Unexpected error in dispatch loop: {e}")

    def _dispatch(self, event: Event) -> None:
        """Dispatch a single event to all subscribers."""
        if self._debug:
            _log.debug(f"[EventBus] dispatch {type(event).__name__}: {event}")

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

    def drain(self, timeout_s: float = 1.0) -> int:
        """Drain the queue by processing all pending events.

        Useful for testing to ensure all events have been processed.

        Args:
            timeout_s: Maximum time to wait for queue to drain.

        Returns:
            Number of events processed.
        """
        count = 0
        deadline = time.time() + timeout_s
        idle_start: Optional[float] = None

        while time.time() < deadline:
            remaining = max(0.001, min(0.05, deadline - time.time()))
            try:
                event = self._queue.get(timeout=remaining)
                idle_start = None
                self._dispatch(event)
                count += 1
            except queue.Empty:
                if idle_start is None:
                    idle_start = time.time()
                elif time.time() - idle_start >= 0.05:
                    # Queue stayed empty for a brief period; assume drained
                    break
                continue

        return count


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
    experiment_id: str
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
class StageMovementStopped(Event):
    """Emitted when stage stops moving (debounced).

    This is distinct from StagePositionChanged which emits continuously.
    StageMovementStopped only emits once per move, after the stage settles.
    Used by NavigationViewer to update FOV position after moves complete.
    """

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
    value: float  # 0.0-1.0 normalized (legacy: 0-100 percentage)


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
class MoveStageRelativeCommand(Event):
    """Command to move stage relative to current position."""

    x_mm: Optional[float] = None
    y_mm: Optional[float] = None
    z_mm: Optional[float] = None


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
    is_wellplate: bool = True


@dataclass
class MoveStageToScanningPositionCommand(Event):
    """Command to move stage to scanning position."""

    blocking: bool = True
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

    camera: str = "main"
    configuration: Optional[str] = None


@dataclass
class StopLiveCommand(Event):
    """Command to stop live view."""

    camera: str = "main"


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
class IlluminationStateChanged(Event):
    """Notification that illumination changed."""

    channel: int
    intensity: float
    on: bool


@dataclass
class LiveStateChanged(Event):
    """Notification that live view state changed."""

    is_live: bool
    camera: str = "main"
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
    pixel_size_binned_um: Optional[float] = None  # Pixel size after binning


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squid.core.config import CameraPixelFormat


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
    camera: str = "main"


@dataclass
class SetTriggerFPSCommand(Event):
    """Command to set trigger frequency."""

    fps: float
    camera: str = "main"


# ============================================================
# Trigger State Events
# ============================================================


@dataclass
class TriggerModeChanged(Event):
    """Notification that trigger mode changed."""

    mode: str
    camera: str = "main"


@dataclass
class TriggerFPSChanged(Event):
    """Notification that trigger FPS changed."""

    fps: float
    camera: str = "main"


# ============================================================
# Display/Live Commands
# ============================================================


@dataclass
class UpdateIlluminationCommand(Event):
    """Command to update illumination for current configuration."""

    pass  # Uses current configuration from controller


@dataclass
class SetDisplayResolutionScalingCommand(Event):
    """Command to set display resolution scaling."""

    scaling: float  # 10-100 percentage


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
    exposure_time_ms: Optional[float] = None
    analog_gain: Optional[float] = None
    illumination_intensity: Optional[float] = None


@dataclass
class UpdateChannelConfigurationCommand(Event):
    """Command to update a channel configuration setting.

    Used by widgets to persist changes to channel configurations.
    Only non-None fields will be updated.
    """

    objective_name: str
    config_name: str
    exposure_time_ms: Optional[float] = None
    analog_gain: Optional[float] = None
    illumination_intensity: Optional[float] = None


@dataclass
class ChannelConfigurationsChanged(Event):
    """Notification that available channel configurations have changed.

    Published when objective changes or configurations are reloaded.
    """

    objective_name: str
    configuration_names: List[str]


# ============================================================================
# Peripheral Commands
# ============================================================================


@dataclass
class SetFilterPositionCommand(Event):
    """Set filter wheel position."""

    position: int
    wheel_index: int = 0


@dataclass
class SetObjectiveCommand(Event):
    """Change objective lens."""

    position: int


@dataclass
class SetSpinningDiskPositionCommand(Event):
    """Move disk in/out of beam path."""

    in_beam: bool


@dataclass
class SetSpinningDiskSpinningCommand(Event):
    """Start/stop disk spinning."""

    spinning: bool


@dataclass
class SetDiskDichroicCommand(Event):
    """Set spinning disk dichroic position."""

    position: int


@dataclass
class SetDiskEmissionFilterCommand(Event):
    """Set spinning disk emission filter position."""

    position: int


@dataclass
class SetPiezoPositionCommand(Event):
    """Set piezo Z position (absolute)."""

    position_um: float


@dataclass
class MovePiezoRelativeCommand(Event):
    """Move piezo Z relative to current position."""

    delta_um: float


# ============================================================================
# Peripheral State Events
# ============================================================================


@dataclass
class FilterPositionChanged(Event):
    """Filter wheel position changed."""

    position: int
    wheel_index: int = 0


@dataclass
class HomeFilterWheelCommand(Event):
    """Command to home filter wheel."""

    wheel_index: int = 0


@dataclass
class SetFilterAutoSwitchCommand(Event):
    """Command to enable/disable automatic filter switching on channel change."""

    enabled: bool


@dataclass
class FilterAutoSwitchChanged(Event):
    """State event: auto-switch mode changed."""

    enabled: bool


@dataclass
class ObjectiveChanged(Event):
    """Objective lens changed."""

    position: int
    objective_name: Optional[str] = None
    magnification: Optional[float] = None
    pixel_size_um: Optional[float] = None


@dataclass
class PixelSizeChanged(Event):
    """Pixel size changed (due to objective or binning change)."""

    pixel_size_um: float


@dataclass
class SpinningDiskStateChanged(Event):
    """Spinning disk state changed."""

    is_disk_in: bool
    is_spinning: bool
    motor_speed: int
    dichroic: int
    emission_filter: int


@dataclass
class PiezoPositionChanged(Event):
    """Piezo Z position changed."""

    position_um: float


# ============================================================================
# Profile and Configuration Events
# ============================================================================


@dataclass
class ProfileChanged(Event):
    """Emitted when user changes the active microscope profile.

    The profile defines available microscope modes and their configurations.
    Widgets should refresh their mode lists when this event is received.
    """

    profile_name: str


@dataclass
class WellplateFormatChanged(Event):
    """Emitted when wellplate format changes.

    Contains all wellplate configuration settings needed by widgets
    to update their displays and coordinate systems.
    """

    format_name: str
    rows: int
    cols: int
    well_spacing_mm: float
    well_size_mm: float
    a1_x_mm: float
    a1_y_mm: float
    a1_x_pixel: int
    a1_y_pixel: int
    number_of_skip: int


@dataclass
class ConfocalModeChanged(Event):
    """Emitted when confocal/widefield mode is toggled."""

    is_confocal: bool


@dataclass
class SetConfocalModeCommand(Event):
    """Command to set confocal vs widefield configuration mode for an objective."""

    objective_name: str
    is_confocal: bool


# ============================================================================
# Stage Position Events
# ============================================================================


@dataclass
class ThreadedStageMoveBegan(Event):
    """Emitted when a threaded stage move operation begins.

    Used by widgets to know when the stage is moving asynchronously.
    """

    pass


@dataclass
class LoadingPositionReached(Event):
    """Emitted when stage reaches loading position.

    Used to disable acquisition start buttons while stage is at loading position.
    """

    pass


@dataclass
class ScanningPositionReached(Event):
    """Emitted when stage reaches scanning position.

    Used to enable acquisition start buttons when stage is ready for scanning.
    """

    pass


@dataclass
class StageMoveToLoadingPositionFinished(Event):
    """Emitted when a move-to-loading operation completes."""

    success: bool
    error_message: Optional[str] = None


@dataclass
class StageMoveToScanningPositionFinished(Event):
    """Emitted when a move-to-scanning operation completes."""

    success: bool
    error_message: Optional[str] = None


# ============================================================================
# Display Commands
# ============================================================================


@dataclass
class AutoLevelCommand(Event):
    """Command to set auto-level display mode."""

    enabled: bool


# ============================================================================
# Well Selection Events
# ============================================================================


@dataclass
class WellSelectedCommand(Event):
    """Command to move to selected well position."""

    x_mm: float
    y_mm: float
    well_id: Optional[str] = None


# ============================================================================
# Fluidics Events
# ============================================================================


@dataclass
class FluidicsInitialized(Event):
    """Emitted when fluidics system initialization is completed."""

    pass


# ============================================================================
# Laser Autofocus Events
# ============================================================================


@dataclass
class LaserAFCrossCorrelationResult(Event):
    """Cross-correlation result from laser autofocus."""

    result: Any  # correlation data


# ============================================================================
# Acquisition Commands
# ============================================================================


@dataclass
class SetAcquisitionParametersCommand(Event):
    """Command to set acquisition parameters."""

    delta_z_um: Optional[float] = None
    n_z: Optional[int] = None
    n_x: Optional[int] = None
    n_y: Optional[int] = None
    delta_x_mm: Optional[float] = None
    delta_y_mm: Optional[float] = None
    delta_t_s: Optional[float] = None
    n_t: Optional[int] = None
    use_piezo: Optional[bool] = None
    use_autofocus: Optional[bool] = None
    use_reflection_af: Optional[bool] = None
    gen_focus_map: Optional[bool] = None
    use_manual_focus_map: Optional[bool] = None
    z_range: Optional[Tuple[float, float]] = None
    focus_map: Optional[Any] = None
    use_fluidics: Optional[bool] = None


@dataclass
class SetAcquisitionPathCommand(Event):
    """Command to set acquisition save path."""

    base_path: str


@dataclass
class SetAcquisitionChannelsCommand(Event):
    """Command to set channels for acquisition."""

    channel_names: List[str]


@dataclass
class StartNewExperimentCommand(Event):
    """Command to start a new experiment (create folder, save metadata)."""

    experiment_id: str


@dataclass
class StartAcquisitionCommand(Event):
    """Start multi-point acquisition."""

    experiment_id: Optional[str] = None
    acquire_current_fov: bool = False


@dataclass
class StopAcquisitionCommand(Event):
    """Stop/abort acquisition."""

    pass


@dataclass
class PauseAcquisitionCommand(Event):
    """Pause acquisition."""

    pass


@dataclass
class ResumeAcquisitionCommand(Event):
    """Resume paused acquisition."""

    pass


# ============================================================================
# Acquisition State Events
# ============================================================================


@dataclass
class AcquisitionProgress(Event):
    """Progress update during acquisition."""

    current_fov: int
    total_fovs: int
    current_round: int
    total_rounds: int
    current_channel: str
    progress_percent: float
    experiment_id: str
    eta_seconds: Optional[float] = None


@dataclass
class AcquisitionStateChanged(Event):
    """Notification that acquisition state changed."""

    in_progress: bool
    experiment_id: str
    is_aborting: bool = False


@dataclass
class AcquisitionRegionProgress(Event):
    """Progress update for regions during acquisition."""

    current_region: int
    total_regions: int
    experiment_id: str


@dataclass
class AcquisitionPaused(Event):
    """Acquisition was paused."""

    pass


@dataclass
class AcquisitionResumed(Event):
    """Acquisition was resumed."""

    pass


@dataclass
class CurrentFOVRegistered(Event):
    """Emitted when a new FOV is acquired during multipoint acquisition.

    Used by NavigationViewer to draw the scan grid and track progress.
    """

    x_mm: float
    y_mm: float
    fov_width_mm: float = 0.0  # FOV width at time of acquisition
    fov_height_mm: float = 0.0  # FOV height at time of acquisition


@dataclass
class AcquisitionCoordinates(Event):
    """Emitted during acquisition with current position and region info.

    Used for tracking acquisition progress with spatial context.
    """

    x_mm: float
    y_mm: float
    z_mm: float
    region_id: int


# ============================================================================
# Worker Events (emitted by worker threads, consumed by controllers)
# ============================================================================


@dataclass
class AcquisitionWorkerFinished(Event):
    """Emitted by MultiPointWorker when acquisition completes.

    The controller subscribes to this event and uses experiment_id to filter
    out stale events from previous acquisitions.
    """

    experiment_id: str
    success: bool
    error: Optional[str] = None
    final_fov_count: int = 0


@dataclass
class AcquisitionWorkerProgress(Event):
    """Emitted by MultiPointWorker for progress updates.

    Provides detailed progress information including region, FOV, and timepoint.
    The controller subscribes to this for internal tracking.
    """

    experiment_id: str
    current_region: int
    total_regions: int
    current_fov: int
    total_fovs: int
    current_timepoint: int
    total_timepoints: int


# ============================================================================
# Fluidics Commands
# ============================================================================


@dataclass
class SetFluidicsRoundsCommand(Event):
    """Command to set fluidics rounds for acquisition."""

    rounds: list  # List of round numbers


# ============================================================================
# Autofocus Commands
# ============================================================================


@dataclass
class StartAutofocusCommand(Event):
    """Start autofocus."""

    pass


@dataclass
class StopAutofocusCommand(Event):
    """Stop autofocus."""

    pass


@dataclass
class SetAutofocusParamsCommand(Event):
    """Configure autofocus parameters."""

    n_planes: Optional[int] = None
    delta_z_um: Optional[float] = None
    focus_metric: Optional[str] = None


# ============================================================================
# Autofocus State Events
# ============================================================================


@dataclass
class AutofocusProgress(Event):
    """Autofocus progress update."""

    current_step: int
    total_steps: int
    current_z: float
    best_z: Optional[float]
    best_score: Optional[float]


@dataclass
class AutofocusCompleted(Event):
    """Autofocus completed."""

    success: bool
    z_position: Optional[float]
    score: Optional[float]
    error: Optional[str] = None


@dataclass
class AutofocusStateChanged(Event):
    """Autofocus controller state changed."""

    old_state: str
    new_state: str
    is_running: bool  # Convenience field for UI


@dataclass
class AutofocusWorkerFinished(Event):
    """Emitted by the autofocus worker thread when it exits.

    Controller cleanup must be performed by the controller on the EventBus thread.
    """

    success: bool
    aborted: bool
    error: Optional[str] = None


# Note: FocusChanged already exists above (line ~170)


# ============================================================================
# Laser Autofocus Commands
# ============================================================================


@dataclass
class SetLaserAFPropertiesCommand(Event):
    """Command to set laser AF properties."""

    properties: dict  # Property updates


@dataclass
class InitializeLaserAFCommand(Event):
    """Command to initialize laser AF."""

    pass


@dataclass
class SetLaserAFCharacterizationModeCommand(Event):
    """Command to set characterization mode."""

    enabled: bool


@dataclass
class UpdateLaserAFThresholdCommand(Event):
    """Command to update threshold properties."""

    updates: dict


@dataclass
class MoveToLaserAFTargetCommand(Event):
    """Command to move to AF target."""

    displacement_um: Optional[float] = None


@dataclass
class SetLaserAFReferenceCommand(Event):
    """Command to set AF reference point."""

    pass


@dataclass
class MeasureLaserAFDisplacementCommand(Event):
    """Command to measure displacement."""

    pass


@dataclass
class CaptureLaserAFFrameCommand(Event):
    """Command to capture a single frame with AF laser illumination."""

    pass


# ============================================================================
# Laser Autofocus State Events
# ============================================================================


@dataclass
class LaserAFPropertiesChanged(Event):
    """State: AF properties changed."""

    properties: dict


@dataclass
class LaserAFInitialized(Event):
    """State: AF initialization status changed."""

    is_initialized: bool
    success: bool


@dataclass
class LaserAFReferenceSet(Event):
    """State: AF reference was set."""

    success: bool


@dataclass
class LaserAFDisplacementMeasured(Event):
    """State: Displacement measurement completed."""

    displacement_um: Optional[float]
    success: bool


@dataclass
class LaserAFFrameCaptured(Event):
    """State: Frame captured with AF laser illumination."""

    success: bool


@dataclass
class LaserAFCrossCorrelationMeasured(Event):
    """State: Cross-correlation value from spot alignment verification."""

    correlation: float


@dataclass
class LaserAFSpotCentroidMeasured(Event):
    """State: Laser spot centroid measured."""

    success: bool
    x_px: Optional[float] = None
    y_px: Optional[float] = None
    error: Optional[str] = None
    image: Optional[Any] = None  # np.ndarray, included for display purposes


@dataclass
class LaserAFMoveCompleted(Event):
    """State: Move to target displacement completed."""

    success: bool
    target_um: float
    final_displacement_um: Optional[float] = None
    error: Optional[str] = None


# ============================================================================
# Camera Settings Commands (for widget decoupling)
# ============================================================================


@dataclass
class SetROICommand(Event):
    """Command to set camera region of interest."""

    x_offset: int
    y_offset: int
    width: int
    height: int


@dataclass
class SetBinningCommand(Event):
    """Command to set camera binning."""

    binning_x: int
    binning_y: int


@dataclass
class SetPixelFormatCommand(Event):
    """Command to set camera pixel format."""

    pixel_format: str  # Name of the pixel format (e.g., "MONO8", "MONO16")


@dataclass
class SetCameraTemperatureCommand(Event):
    """Command to set camera temperature."""

    temperature_celsius: float


@dataclass
class SetBlackLevelCommand(Event):
    """Command to set camera black level."""

    level: int


@dataclass
class SetAutoWhiteBalanceCommand(Event):
    """Command to set auto white balance mode."""

    enabled: bool


# ============================================================================
# Camera Settings State Events (some already exist above, adding missing ones)
# ============================================================================


@dataclass
class CameraTemperatureChanged(Event):
    """Notification that camera temperature changed."""

    set_temperature_celsius: float
    measured_temperature_celsius: Optional[float] = None


@dataclass
class BlackLevelChanged(Event):
    """Notification that black level changed."""

    level: int


@dataclass
class AutoWhiteBalanceChanged(Event):
    """Notification that auto white balance mode changed."""

    enabled: bool


# ============================================================================
# Wellplate Commands
# ============================================================================


@dataclass
class SaveWellplateCalibrationCommand(Event):
    """Command to save wellplate calibration."""

    calibration: Any  # WellplateCalibration object
    name: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


@dataclass
class WellplateCalibrationSaved(Event):
    """Notification that wellplate calibration was saved."""

    success: bool
    error: Optional[str] = None


# ============================================================================
# Tracking Commands
# ============================================================================


@dataclass
class SetTrackingParametersCommand(Event):
    """Command to set tracking parameters."""

    time_interval_s: Optional[float] = None
    enable_stage_tracking: Optional[bool] = None
    save_images: Optional[bool] = None
    tracker_type: Optional[str] = None
    pixel_size_um: Optional[float] = None
    objective: Optional[str] = None
    image_resizing_factor: Optional[float] = None


@dataclass
class SetTrackingPathCommand(Event):
    """Command to set tracking save path."""

    base_path: str


@dataclass
class SetTrackingChannelsCommand(Event):
    """Command to set channels for tracking."""

    channel_names: List[str]


@dataclass
class StartTrackingExperimentCommand(Event):
    """Command to start a new tracking experiment."""

    experiment_id: str


@dataclass
class StartTrackingCommand(Event):
    """Command to start tracking."""

    roi_bbox: Tuple[int, int, int, int]


@dataclass
class StopTrackingCommand(Event):
    """Command to stop tracking."""

    pass


# ============================================================================
# Tracking State Events
# ============================================================================


@dataclass
class TrackingStateChanged(Event):
    """Notification that tracking state changed."""

    is_tracking: bool


@dataclass
class TrackingWorkerFinished(Event):
    """Worker finished notification for tracking workflow."""

    success: bool
    aborted: bool = False
    error: Optional[str] = None


# ============================================================================
# Plate Reader Commands
# ============================================================================


@dataclass
class SetPlateReaderParametersCommand(Event):
    """Command to set plate reader parameters."""

    use_autofocus: Optional[bool] = None


@dataclass
class SetPlateReaderPathCommand(Event):
    """Command to set plate reader save path."""

    base_path: str


@dataclass
class SetPlateReaderChannelsCommand(Event):
    """Command to set channels for plate reading."""

    channel_names: List[str]


@dataclass
class SetPlateReaderColumnsCommand(Event):
    """Command to set columns for plate reading."""

    columns: List[int]


@dataclass
class StartPlateReaderExperimentCommand(Event):
    """Command to start a new plate reader experiment."""

    experiment_id: str


@dataclass
class StartPlateReaderCommand(Event):
    """Command to start plate reader acquisition."""

    pass


@dataclass
class StopPlateReaderCommand(Event):
    """Command to stop plate reader acquisition."""

    pass


# ============================================================================
# Plate Reader State Events
# ============================================================================


@dataclass
class PlateReaderStateChanged(Event):
    """Notification that plate reader state changed."""

    is_running: bool


@dataclass
class PlateReaderAcquisitionFinished(Event):
    """Notification that plate reader acquisition finished."""

    pass


# ============================================================================
# Plate Reader Navigation Commands
# ============================================================================


@dataclass
class PlateReaderHomeCommand(Event):
    """Command to home plate reader."""

    pass


@dataclass
class PlateReaderMoveToCommand(Event):
    """Command to move plate reader to position."""

    column: str
    row: str


@dataclass
class PlateReaderHomingComplete(Event):
    """Notification that plate reader homing is complete."""

    pass


@dataclass
class PlateReaderLocationChanged(Event):
    """Notification that plate reader location changed."""

    location_str: str


# ============================================================================
# Displacement Measurement Commands
# ============================================================================


@dataclass
class SetDisplacementMeasurementSettingsCommand(Event):
    """Command to update displacement measurement settings."""

    x_offset: float
    y_offset: float
    x_scaling: float
    y_scaling: float
    n_average: int
    n: int


@dataclass
class SetWaveformDisplayNCommand(Event):
    """Command to update waveform display N parameter."""

    n: int


@dataclass
class DisplacementReadingsChanged(Event):
    """Notification that displacement readings have changed."""

    readings: List[float]


# ============================================================================
# Global Mode Events
# ============================================================================


@dataclass
class GlobalModeChanged(Event):
    """Notification that global operating mode changed.

    The global mode is owned by the backend control plane and indicates what
    the system is currently doing (IDLE, LIVE, ACQUIRING, etc.).
    """

    old_mode: str  # GlobalMode enum name
    new_mode: str  # GlobalMode enum name
    reason: str


# ============================================================================
# UI State Events (coarse-grained events for widget consumption)
# ============================================================================
# These events aggregate fine-grained state into widget-friendly packages.
# Widgets subscribe to these instead of multiple fine-grained events.
# Published by UIStateAggregator which subscribes to underlying events.


@dataclass
class AcquisitionUIStateChanged(Event):
    """Coarse-grained acquisition state for UI widgets.

    Aggregates AcquisitionStateChanged, AcquisitionProgress, and
    AcquisitionRegionProgress into a single event that widgets can
    subscribe to for complete state updates.
    """

    experiment_id: str
    is_running: bool
    is_aborting: bool = False
    current_region: int = 0
    total_regions: int = 0
    current_fov: int = 0
    total_fovs: int = 0
    progress_percent: float = 0.0


@dataclass
class LiveUIStateChanged(Event):
    """Coarse-grained live view state for UI widgets.

    Aggregates LiveStateChanged, TriggerModeChanged, and ExposureTimeChanged
    into a single event that widgets can subscribe to.
    """

    is_live: bool
    current_configuration: Optional[str] = None
    exposure_time_ms: Optional[float] = None
    trigger_mode: Optional[str] = None


@dataclass
class NavigationViewerStateChanged(Event):
    """State of the navigation viewer for UI synchronization.

    Published when the navigation viewer's visible state changes,
    allowing widgets to update their display without direct references.
    """

    x_mm: float
    y_mm: float
    fov_width_mm: float
    fov_height_mm: float
    wellplate_format: Optional[str] = None


@dataclass
class ScanCoordinatesUpdated(Event):
    """Notification that scan coordinates have been updated.

    Published when regions/FOVs are added, removed, or modified.
    Widgets subscribe to this instead of holding direct references
    to ScanCoordinates objects.
    """

    total_regions: int
    total_fovs: int
    region_ids: Tuple[str, ...]  # Immutable tuple of region identifiers


@dataclass
class FocusPointOverlaySet(Event):
    """Command/state event: replace the focus point overlay points.

    Used to avoid direct widget-to-widget calls between FocusMapWidget and NavigationViewer.
    """

    points: Tuple[Tuple[float, float], ...]  # (x_mm, y_mm) pairs


@dataclass
class FocusPointOverlayVisibilityChanged(Event):
    """Command/state event: show/hide focus point overlay."""

    enabled: bool


# ============================================================================
# Phase 8: UI Decoupling Commands and Events
# ============================================================================


@dataclass
class ImageCoordinateClickedCommand(Event):
    """Command from UI when user clicks on an image to move stage.

    The ImageClickController subscribes to this and converts pixel coordinates
    to stage movement commands based on objective/binning settings.
    """

    x_pixel: float  # Pixels from image center
    y_pixel: float  # Pixels from image center
    image_width: int
    image_height: int
    from_napari: bool = False


@dataclass
class ClearScanCoordinatesCommand(Event):
    """Command to clear all scan coordinate regions."""


@dataclass
class SortScanCoordinatesCommand(Event):
    """Command to sort scan coordinate regions/FOVs using ScanCoordinates rules."""


@dataclass
class SetLiveScanCoordinatesCommand(Event):
    """Command to define a live scan grid around a center position."""

    x_mm: float
    y_mm: float
    scan_size_mm: float
    overlap_percent: float
    shape: str


@dataclass
class AddTemplateRegionCommand(Event):
    """Command to add a scan coordinate region from template offsets.

    Template regions are defined by (x,y) offsets relative to a center position.
    """

    region_id: str
    center_x_mm: float
    center_y_mm: float
    center_z_mm: float
    x_offsets_mm: Tuple[float, ...]
    y_offsets_mm: Tuple[float, ...]


@dataclass
class SelectedWellsChanged(Event):
    """UI state event: selected well cells changed."""

    format_name: str
    selected_cells: Tuple[Tuple[int, int], ...]


@dataclass
class ActiveAcquisitionTabChanged(Event):
    """UI state event: active acquisition tab changed."""

    active_tab: str  # e.g. "wellplate", "flexible", "fluidics", "other"


@dataclass
class ManualShapeDrawingEnabledChanged(Event):
    """UI state event: enable/disable manual ROI drawing in mosaic view."""

    enabled: bool


@dataclass
class ManualShapesChanged(Event):
    """UI state event: manual ROI shapes (in mm) changed in mosaic view."""

    shapes_mm: Optional[Tuple[Tuple[Tuple[float, float], ...], ...]]


@dataclass
class MosaicLayersInitialized(Event):
    """UI state event: mosaic viewer layers initialized."""

    pass


@dataclass
class SetWellSelectionScanCoordinatesCommand(Event):
    """Command to compute scan coordinates from the current well selection."""

    scan_size_mm: float
    overlap_percent: float
    shape: str


@dataclass
class SetManualScanCoordinatesCommand(Event):
    """Command to compute scan coordinates from manual ROI shapes."""

    manual_shapes_mm: Optional[Tuple[Tuple[Tuple[float, float], ...], ...]]
    overlap_percent: float


@dataclass
class LoadScanCoordinatesCommand(Event):
    """Command to replace scan coordinates with explicit region/FOV coordinates."""

    region_fov_coordinates: Dict[str, Tuple[Tuple[float, ...], ...]]
    region_centers: Optional[Dict[str, Tuple[float, ...]]] = None


@dataclass
class RequestScanCoordinatesSnapshotCommand(Event):
    """Command to request a one-time snapshot of current scan coordinates."""

    request_id: str


@dataclass
class ScanCoordinatesSnapshot(Event):
    """Snapshot of current scan coordinates."""

    request_id: str
    region_fov_coordinates: Dict[str, Tuple[Tuple[float, ...], ...]]
    region_centers: Dict[str, Tuple[float, ...]]


@dataclass
class AddFlexibleRegionCommand(Event):
    """Command to add a flexible grid region defined by NX/NY and overlap."""

    region_id: str
    center_x_mm: float
    center_y_mm: float
    center_z_mm: float
    n_x: int
    n_y: int
    overlap_percent: float


@dataclass
class AddFlexibleRegionWithStepSizeCommand(Event):
    """Command to add a flexible grid region defined by NX/NY and step size."""

    region_id: str
    center_x_mm: float
    center_y_mm: float
    center_z_mm: float
    n_x: int
    n_y: int
    delta_x_mm: float
    delta_y_mm: float


@dataclass
class RemoveScanCoordinateRegionCommand(Event):
    """Command to remove a scan coordinate region by id."""

    region_id: str


@dataclass
class RenameScanCoordinateRegionCommand(Event):
    """Command to rename a scan coordinate region."""

    old_region_id: str
    new_region_id: str


@dataclass
class UpdateScanCoordinateRegionZCommand(Event):
    """Command to update z-level for an entire scan coordinate region."""

    region_id: str
    z_mm: float


@dataclass
class ClickToMoveEnabledChanged(Event):
    """State event: click-to-move feature enabled/disabled."""

    enabled: bool
