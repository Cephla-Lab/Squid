from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import Optional, Set, TYPE_CHECKING

import squid.core.logging
from squid.core.abc import CameraAcquisitionMode
from squid.backend.services import CameraService
from squid.backend.services.peripheral_service import PeripheralService
from squid.backend.services.illumination_service import IlluminationService
from squid.backend.services.filter_wheel_service import FilterWheelService
from squid.backend.services.nl5_service import NL5Service
from squid.core.events import (
    EventBus,
    StartLiveCommand,
    StopLiveCommand,
    SetTriggerModeCommand,
    SetTriggerFPSCommand,
    SetFilterAutoSwitchCommand,
    UpdateIlluminationCommand,
    SetDisplayResolutionScalingCommand,
    LiveStateChanged,
    TriggerModeChanged,
    TriggerFPSChanged,
    FilterAutoSwitchChanged,
)
from squid.core.state_machine import StateMachine, InvalidStateForOperation
from squid.core.mode_gate import GlobalMode, GlobalModeGate

from _def import *
from squid.core.utils import utils_channel

if TYPE_CHECKING:
    from squid.core.utils.config_utils import ChannelMode


class LiveControllerState(Enum):
    """State machine states for LiveController."""

    STOPPED = auto()
    STARTING = auto()
    LIVE = auto()
    STOPPING = auto()


@dataclass
class LiveStateData:
    """Observable state data managed by LiveController."""

    is_live: bool = False
    current_channel: Optional[str] = None
    trigger_mode: str = "Software"
    trigger_fps: float = 10.0
    illumination_on: bool = False


class LiveController(StateMachine[LiveControllerState]):
    def __init__(
        self,
        camera_service: CameraService,
        event_bus: EventBus,
        illumination_service: Optional[IlluminationService] = None,
        peripheral_service: Optional[PeripheralService] = None,
        filter_wheel_service: Optional[FilterWheelService] = None,
        nl5_service: Optional[NL5Service] = None,
        mode_gate: Optional[GlobalModeGate] = None,
        control_illumination: bool = True,
        use_internal_timer_for_hardware_trigger: bool = True,
        for_displacement_measurement: bool = False,
        *,
        camera: str = "main",
    ) -> None:
        # Initialize state machine with transitions
        transitions = {
            LiveControllerState.STOPPED: {LiveControllerState.STARTING},
            LiveControllerState.STARTING: {LiveControllerState.LIVE, LiveControllerState.STOPPED},
            LiveControllerState.LIVE: {LiveControllerState.STOPPING},
            LiveControllerState.STOPPING: {LiveControllerState.STOPPED},
        }
        super().__init__(
            initial_state=LiveControllerState.STOPPED,
            transitions=transitions,
            event_bus=event_bus,
            name=f"LiveController[{camera}]",
        )

        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        # Note: StateMachine base class provides self._lock
        self._bus: EventBus = event_bus
        self._bus_subscribed: bool = False
        self._camera_service: CameraService = camera_service
        self._camera: str = camera
        self._illumination_service = illumination_service
        self._peripheral_service = peripheral_service
        self._filter_wheel_service = filter_wheel_service
        self._nl5_service = nl5_service
        self._mode_gate = mode_gate
        self.currentConfiguration: Optional["ChannelMode"] = None
        self.trigger_mode: Optional[TriggerMode] = (
            TriggerMode.SOFTWARE
        )  # @@@ change to None
        self.is_live: bool = False
        self.control_illumination: bool = control_illumination
        self.illumination_on: bool = False
        self.use_internal_timer_for_hardware_trigger: bool = (
            use_internal_timer_for_hardware_trigger  # use Timer vs timer in the MCU
        )
        self.for_displacement_measurement: bool = for_displacement_measurement

        self.fps_trigger: float = 1
        self.timer_trigger_interval: float = (1.0 / self.fps_trigger) * 1000
        self._trigger_skip_count: int = 0
        self.timer_trigger: Optional[threading.Timer] = None

        self.trigger_ID: int = -1

        self.fps_real: float = 0
        self.counter: int = 0
        self.timestamp_last: float = 0

        self.display_resolution_scaling: float = 1

        self.enable_channel_auto_filter_switching: bool = True

        # Initialize observable state for event-driven communication
        self._observable_state = LiveStateData(
            trigger_mode=self._trigger_mode_to_str(self.trigger_mode),
            trigger_fps=self.fps_trigger,
        )

        if self.control_illumination and self._illumination_service is None:
            raise ValueError(
                "LiveController(control_illumination=True) requires IlluminationService"
            )

        # Register valid commands per state
        self.register_valid_commands(
            LiveControllerState.STOPPED,
            {StartLiveCommand, SetTriggerModeCommand, SetTriggerFPSCommand}
        )
        self.register_valid_commands(
            LiveControllerState.LIVE,
            {StopLiveCommand, SetTriggerModeCommand, SetTriggerFPSCommand, UpdateIlluminationCommand}
        )

        # Subscribe to commands if event bus provided
        self._subscribe_to_bus(self._bus)

    def _publish_state_changed(self, old_state: LiveControllerState, new_state: LiveControllerState) -> None:
        """Publish state change event (StateMachine abstract method)."""
        if self._bus:
            is_live = new_state == LiveControllerState.LIVE
            self._bus.publish(LiveStateChanged(
                camera=self._camera,
                is_live=is_live,
                configuration=self.currentConfiguration.name if self.currentConfiguration else None,
            ))

    def _subscribe_to_bus(self, bus: Optional[EventBus]) -> None:
        """Subscribe to command events once for the provided bus."""
        if bus is None or self._bus_subscribed:
            return
        bus.subscribe(StartLiveCommand, self._on_start_live_command)
        bus.subscribe(StopLiveCommand, self._on_stop_live_command)
        bus.subscribe(SetTriggerModeCommand, self._on_set_trigger_mode_command)
        bus.subscribe(SetTriggerFPSCommand, self._on_set_trigger_fps_command)
        bus.subscribe(SetFilterAutoSwitchCommand, self._on_set_filter_auto_switch)
        bus.subscribe(UpdateIlluminationCommand, self._on_update_illumination)
        bus.subscribe(
            SetDisplayResolutionScalingCommand,
            self._on_set_display_resolution_scaling,
        )
        self._bus_subscribed = True

    # illumination control
    def _get_illumination_channel(self) -> Optional[int]:
        """Return the configured illumination channel for the current mode."""
        if self.currentConfiguration is None:
            return None
        channel = getattr(self.currentConfiguration, "illumination_source", None)
        if channel is None:
            self._log.debug(
                "Cannot control illumination: configuration missing illumination_source (%s)",
                getattr(self.currentConfiguration, "name", None),
            )
            return None
        try:
            return int(channel)
        except Exception:
            self._log.debug(
                "Cannot control illumination: invalid illumination_source=%r (%s)",
                channel,
                getattr(self.currentConfiguration, "name", None),
            )
            return None

    def _get_illumination_intensity(self) -> Optional[float]:
        if self.currentConfiguration is None:
            return None
        intensity = getattr(self.currentConfiguration, "illumination_intensity", None)
        if intensity is None:
            intensity = getattr(self.currentConfiguration, "intensity", None)
        if intensity is None:
            return None
        try:
            return float(intensity)
        except Exception:
            return None

    def turn_on_illumination(self) -> None:
        if self.currentConfiguration is None:
            return
        if self._illumination_service is None:
            return
        channel = self._get_illumination_channel()
        if channel is None:
            return
        try:
            intensity = self._get_illumination_intensity()
            if intensity is not None:
                self._illumination_service.set_channel_power(channel, intensity)
            self._illumination_service.turn_on_channel(channel)
        except Exception:
            self._log.exception("Failed to turn on illumination via service")
            return
        self.illumination_on = True

    def turn_off_illumination(self) -> None:
        if self.currentConfiguration is None:
            return
        if self._illumination_service is None:
            return
        channel = self._get_illumination_channel()
        if channel is None:
            return
        try:
            self._illumination_service.turn_off_channel(channel)
        except Exception:
            self._log.exception("Failed to turn off illumination via service")
            return
        self.illumination_on = False

    def update_illumination(self) -> None:
        if self.currentConfiguration is None:
            return
        if self._illumination_service is None:
            return
        illumination_source = self._get_illumination_channel()
        intensity = self._get_illumination_intensity()
        if illumination_source is None or intensity is None:
            return
        try:
            self._illumination_service.set_channel_power(illumination_source, intensity)
        except Exception:
            self._log.exception("Failed to update illumination via service")
            return

        if (
            self._nl5_service
            and NL5_USE_DOUT
            and "Fluorescence" in self.currentConfiguration.name
        ):
            try:
                wavelength = int(
                    utils_channel.extract_wavelength_from_config_name(
                        self.currentConfiguration.name
                    )
                )
                self._nl5_service.set_active_channel(NL5_WAVENLENGTH_MAP[wavelength])
                if NL5_USE_AOUT:
                    self._nl5_service.set_laser_power(
                        NL5_WAVENLENGTH_MAP[wavelength], int(intensity)
                    )
            except Exception:
                self._log.exception("Failed to set NL5 laser power via service")

        if (
            self._filter_wheel_service
            and self._filter_wheel_service.is_available()
            and self.enable_channel_auto_filter_switching
        ):
            try:
                delay = 0
                if self.trigger_mode == TriggerMode.HARDWARE:
                    delay = -self._camera_service.get_strobe_time()
                self._filter_wheel_service.set_delay_offset_ms(delay)
                self._filter_wheel_service.set_filter_wheel_position(
                    {1: self.currentConfiguration.emission_filter_position}
                )
            except Exception:
                self._log.exception("Failed to set emission filter position via service")

    def start_live(self, configuration: Optional[str] = None) -> None:
        """Start live imaging using the state machine/resource guard path."""
        self._start_live(configuration)

    def _start_live(self, configuration: Optional[str]) -> None:
        self._log.info(f"_start_live: is_live={self.is_live}, config={configuration}")

        previous_mode = self._mode_gate.get_mode() if self._mode_gate else None
        if self._mode_gate and self._mode_gate.blocked_for_ui_hardware_commands():
            self._log.info(
                "Ignoring start live: mode=%s", self._mode_gate.get_mode().name
            )
            return

        if not self._is_in_state(LiveControllerState.STOPPED):
            self._log.info(f"Cannot start live: state is {self.state.name}")
            return

        # Transition to STARTING
        try:
            self._transition_to(LiveControllerState.STARTING)
        except InvalidStateForOperation:
            self._log.warning("Invalid state for start_live")
            return

        try:
            with self._lock:
                self.is_live = True

                self._camera_service.start_streaming()
                if self.trigger_mode == TriggerMode.SOFTWARE or (
                    self.trigger_mode == TriggerMode.HARDWARE
                    and self.use_internal_timer_for_hardware_trigger
                ):
                    self._camera_service.enable_callbacks(True)
                    self._start_triggerred_acquisition()

                if self.for_displacement_measurement:
                    if self._peripheral_service:
                        try:
                            self._peripheral_service.turn_on_af_laser()
                        except Exception:
                            self._log.exception("Failed to turn on AF laser via peripheral service")
                    else:
                        self._log.warning("Peripheral service missing; cannot toggle AF laser safely")

                # Update observable state inside lock
                self._observable_state = replace(
                    self._observable_state,
                    is_live=True,
                    current_channel=(
                        self.currentConfiguration.name
                        if self.currentConfiguration is not None
                        else configuration
                    ),
                    illumination_on=self.illumination_on,
                )
        except Exception:
            self._log.exception("Failed to start live; cleaning up")
            if self._mode_gate and previous_mode is not None:
                self._mode_gate.restore_mode(previous_mode, reason="live start failed")
            self._transition_to(LiveControllerState.STOPPED)
            return

        # Transition to LIVE (publishes state changed event via _publish_state_changed)
        self._transition_to(LiveControllerState.LIVE)
        if self._mode_gate:
            self._mode_gate.set_mode(GlobalMode.LIVE, reason="live start")

    def stop_live(self) -> None:
        """Stop live imaging using the state machine/resource guard path."""
        self._stop_live()

    def _stop_live(self) -> None:
        self._log.info(f"_stop_live: is_live={self.is_live}")

        if not self._is_in_state(LiveControllerState.LIVE):
            self._log.info(f"Cannot stop live: state is {self.state.name}")
            return

        # Transition to STOPPING
        self._transition_to(LiveControllerState.STOPPING)

        try:
            with self._lock:
                self.is_live = False
                if self.trigger_mode == TriggerMode.SOFTWARE or (
                    self.trigger_mode == TriggerMode.HARDWARE
                    and self.use_internal_timer_for_hardware_trigger
                ):
                    self._stop_triggerred_acquisition()
                self._camera_service.stop_streaming()
                if self.control_illumination:
                    self.turn_off_illumination()
                if self.for_displacement_measurement:
                    if self._peripheral_service:
                        try:
                            self._peripheral_service.turn_off_af_laser()
                        except Exception:
                            self._log.exception("Failed to turn off AF laser via peripheral service")
                    else:
                        self._log.warning("Peripheral service missing; cannot toggle AF laser safely")

                # Update observable state inside lock
                self._observable_state = replace(
                    self._observable_state,
                    is_live=False,
                    illumination_on=False,
                )
        except Exception:
            self._log.exception("Failed while stopping live; forcing STOPPED")
            if self._mode_gate:
                self._mode_gate.set_mode(GlobalMode.IDLE, reason="live stop failed")
            self._force_state(LiveControllerState.STOPPED, reason="stop_live failure")
            return

        # Transition to STOPPED (publishes state changed event via _publish_state_changed)
        self._transition_to(LiveControllerState.STOPPED)
        if self._mode_gate:
            self._mode_gate.set_mode(GlobalMode.IDLE, reason="live stop")

    def _trigger_acquisition_timer_fn(self) -> None:
        if self.trigger_acquisition():
            if self.is_live:
                self._start_new_timer()
        else:
            if self.is_live:
                # It failed, try again real soon
                # Use a short period so we get back here fast and check again.
                re_check_period_ms = 10
                self._start_new_timer(maybe_custom_interval_ms=re_check_period_ms)

    # software trigger related
    def trigger_acquisition(self) -> bool:
        with self._lock:
            ready = self._camera_service.get_ready_for_trigger()
            if not ready:
                self._trigger_skip_count += 1
                if self._trigger_skip_count % 100 == 1:
                    total_frame_time = self._camera_service.get_total_frame_time()
                    self._log.debug(
                        f"Not ready for trigger, skipping (_trigger_skip_count={self._trigger_skip_count}, total frame time = {total_frame_time} [ms])."
                    )
                return False

            self._trigger_skip_count = 0
            if self.trigger_mode == TriggerMode.SOFTWARE and self.control_illumination:
                if not self.illumination_on:
                    self.turn_on_illumination()

            self.trigger_ID = self.trigger_ID + 1

            self._camera_service.send_trigger()

            return True

    def _stop_existing_timer(self) -> None:
        with self._lock:
            if self.timer_trigger and self.timer_trigger.is_alive():
                self.timer_trigger.cancel()
            self.timer_trigger = None

    def _start_new_timer(
        self, maybe_custom_interval_ms: Optional[float] = None
    ) -> None:
        with self._lock:
            # Stop existing timer (inline to avoid nested lock acquisition)
            if self.timer_trigger and self.timer_trigger.is_alive():
                self.timer_trigger.cancel()
            self.timer_trigger = None

            if maybe_custom_interval_ms:
                interval_s = maybe_custom_interval_ms / 1000.0
            else:
                interval_s = self.timer_trigger_interval / 1000.0
            self.timer_trigger = threading.Timer(
                interval_s, self._trigger_acquisition_timer_fn
            )
            self.timer_trigger.daemon = True
            self.timer_trigger.start()

    def _start_triggerred_acquisition(self) -> None:
        self._start_new_timer()

    def _set_trigger_fps(self, fps_trigger: float) -> None:
        if fps_trigger <= 0:
            raise ValueError(f"fps_trigger must be > 0, but {fps_trigger=}")
        self._log.debug(f"Setting {fps_trigger=}")
        with self._lock:
            self.fps_trigger = fps_trigger
            self.timer_trigger_interval = (1 / self.fps_trigger) * 1000
            if self.is_live:
                # Inline timer restart to avoid nested lock acquisition
                if self.timer_trigger and self.timer_trigger.is_alive():
                    self.timer_trigger.cancel()
                self.timer_trigger = None
                interval_s = self.timer_trigger_interval / 1000.0
                self.timer_trigger = threading.Timer(
                    interval_s, self._trigger_acquisition_timer_fn
                )
                self.timer_trigger.daemon = True
                self.timer_trigger.start()

    def _stop_triggerred_acquisition(self) -> None:
        self._stop_existing_timer()

    # trigger mode and settings
    def set_trigger_mode(self, mode: TriggerMode) -> None:
        with self._lock:
            if mode == TriggerMode.SOFTWARE:
                if self.is_live and (
                    self.trigger_mode == TriggerMode.HARDWARE
                    and self.use_internal_timer_for_hardware_trigger
                ):
                    self._stop_triggerred_acquisition()
                self._camera_service.set_acquisition_mode(
                    CameraAcquisitionMode.SOFTWARE_TRIGGER
                )
                if self.is_live:
                    self._start_triggerred_acquisition()
            if mode == TriggerMode.HARDWARE:
                if self.trigger_mode == TriggerMode.SOFTWARE and self.is_live:
                    self._stop_triggerred_acquisition()
                self._camera_service.set_acquisition_mode(
                    CameraAcquisitionMode.HARDWARE_TRIGGER
                )
                if self.currentConfiguration is not None:
                    self._camera_service.set_exposure_time(
                        self.currentConfiguration.exposure_time
                    )

                if self.is_live and self.use_internal_timer_for_hardware_trigger:
                    self._start_triggerred_acquisition()
            if mode == TriggerMode.CONTINUOUS:
                if (self.trigger_mode == TriggerMode.SOFTWARE) or (
                    self.trigger_mode == TriggerMode.HARDWARE
                    and self.use_internal_timer_for_hardware_trigger
                ):
                    self._stop_triggerred_acquisition()
                self._camera_service.set_acquisition_mode(CameraAcquisitionMode.CONTINUOUS)
            self.trigger_mode = mode

    def set_trigger_fps(self, fps: float) -> None:
        if (self.trigger_mode == TriggerMode.SOFTWARE) or (
            self.trigger_mode == TriggerMode.HARDWARE
            and self.use_internal_timer_for_hardware_trigger
        ):
            self._set_trigger_fps(fps)

    # set microscope mode
    # @@@ to do: change softwareTriggerGenerator to TriggerGeneratror
    def set_microscope_mode(self, configuration: "ChannelMode") -> None:
        with self._lock:
            self.currentConfiguration = configuration
            self._log.info("setting microscope mode to " + self.currentConfiguration.name)

            # temporarily stop live while changing mode
            if self.is_live is True:
                self._stop_existing_timer()
                if self.control_illumination:
                    self.turn_off_illumination()

            # set camera exposure time and analog gain
            exposure = self.currentConfiguration.exposure_time
            gain = self.currentConfiguration.analog_gain
            self._camera_service.set_exposure_time(exposure)
            try:
                self._camera_service.set_analog_gain(gain)
            except Exception:
                self._log.debug("Analog gain not supported by camera service")

            # set illumination
            if self.control_illumination:
                self.update_illumination()

            # restart live
            if self.is_live is True:
                if self.control_illumination:
                    self.turn_on_illumination()
                self._start_new_timer()
            self._log.info("Done setting microscope mode.")

    def get_trigger_mode(self) -> Optional[TriggerMode]:
        return self.trigger_mode

    # slot
    def on_new_frame(self) -> None:
        if self.fps_trigger <= 5:
            if self.control_illumination and self.illumination_on:
                self.turn_off_illumination()

    def set_display_resolution_scaling(self, display_resolution_scaling: float) -> None:
        self.display_resolution_scaling = display_resolution_scaling / 100

    # =========================================================================
    # Event-driven command handlers
    # =========================================================================

    @staticmethod
    def _trigger_mode_to_str(mode: Optional[TriggerMode]) -> str:
        """Convert TriggerMode enum to string."""
        if mode == TriggerMode.SOFTWARE:
            return "Software"
        elif mode == TriggerMode.HARDWARE:
            return "Hardware"
        elif mode == TriggerMode.CONTINUOUS:
            return "Continuous"
        return "Software"

    @staticmethod
    def _str_to_trigger_mode(mode_str: str) -> TriggerMode:
        """Convert string to TriggerMode enum."""
        mode_str_lower = mode_str.lower()
        if mode_str_lower == "software":
            return TriggerMode.SOFTWARE
        elif mode_str_lower == "hardware":
            return TriggerMode.HARDWARE
        elif mode_str_lower == "continuous":
            return TriggerMode.CONTINUOUS
        return TriggerMode.SOFTWARE

    @property
    def observable_state(self) -> LiveStateData:
        """Get current observable state data."""
        return self._observable_state

    def _on_start_live_command(self, cmd: StartLiveCommand) -> None:
        """Handle StartLiveCommand from EventBus."""
        if getattr(cmd, "camera", "main") != self._camera:
            return
        self._log.info(f"_on_start_live_command: is_live={self.is_live}, config={cmd.configuration}")
        self._start_live(cmd.configuration)

    def _on_stop_live_command(self, cmd: StopLiveCommand) -> None:
        """Handle StopLiveCommand from EventBus."""
        if getattr(cmd, "camera", "main") != self._camera:
            return
        self._log.info(f"_on_stop_live_command: is_live={self.is_live}")
        self._stop_live()

    def _on_set_trigger_mode_command(self, cmd: SetTriggerModeCommand) -> None:
        """Handle SetTriggerModeCommand from EventBus."""
        if getattr(cmd, "camera", "main") != self._camera:
            return
        mode = self._str_to_trigger_mode(cmd.mode)

        with self._lock:
            if mode == TriggerMode.SOFTWARE:
                if self.is_live and (
                    self.trigger_mode == TriggerMode.HARDWARE
                    and self.use_internal_timer_for_hardware_trigger
                ):
                    self._stop_triggerred_acquisition()
                self._camera_service.set_acquisition_mode(
                    CameraAcquisitionMode.SOFTWARE_TRIGGER
                )
                self._camera_service.enable_callbacks(True)
                if self.is_live:
                    self._start_triggerred_acquisition()
            elif mode == TriggerMode.HARDWARE:
                if self.trigger_mode == TriggerMode.SOFTWARE and self.is_live:
                    self._stop_triggerred_acquisition()
                self._camera_service.set_acquisition_mode(
                    CameraAcquisitionMode.HARDWARE_TRIGGER
                )
                if self.currentConfiguration is not None:
                    exposure = self.currentConfiguration.exposure_time
                    self._camera_service.set_exposure_time(exposure)
                if self.is_live and self.use_internal_timer_for_hardware_trigger:
                    self._start_triggerred_acquisition()
            elif mode == TriggerMode.CONTINUOUS:
                if (self.trigger_mode == TriggerMode.SOFTWARE) or (
                    self.trigger_mode == TriggerMode.HARDWARE
                    and self.use_internal_timer_for_hardware_trigger
                ):
                    self._stop_triggerred_acquisition()
                self._camera_service.set_acquisition_mode(
                    CameraAcquisitionMode.CONTINUOUS
                )
                self._camera_service.enable_callbacks(True)
                if self.is_live:
                    self._camera_service.start_streaming()
            else:
                self._log.error(f"Unknown trigger mode: {mode}")

            # Update state inside lock
            self.trigger_mode = mode
            self._observable_state = replace(self._observable_state, trigger_mode=cmd.mode)

        # Publish outside lock
        if self._bus:
            self._bus.publish(TriggerModeChanged(camera=self._camera, mode=cmd.mode))

    def _on_set_trigger_fps_command(self, cmd: SetTriggerFPSCommand) -> None:
        """Handle SetTriggerFPSCommand from EventBus."""
        if getattr(cmd, "camera", "main") != self._camera:
            return
        with self._lock:
            # Inline set_trigger_fps logic to avoid nested lock
            if (self.trigger_mode == TriggerMode.SOFTWARE) or (
                self.trigger_mode == TriggerMode.HARDWARE
                and self.use_internal_timer_for_hardware_trigger
            ):
                if cmd.fps <= 0:
                    raise ValueError(f"fps must be > 0, but {cmd.fps=}")
                self._log.debug(f"Setting fps_trigger={cmd.fps}")
                self.fps_trigger = cmd.fps
                self.timer_trigger_interval = (1 / self.fps_trigger) * 1000
                if self.is_live:
                    # Inline timer restart
                    if self.timer_trigger and self.timer_trigger.is_alive():
                        self.timer_trigger.cancel()
                    self.timer_trigger = None
                    interval_s = self.timer_trigger_interval / 1000.0
                    self.timer_trigger = threading.Timer(
                        interval_s, self._trigger_acquisition_timer_fn
                    )
                    self.timer_trigger.daemon = True
                    self.timer_trigger.start()

            # Update observable state inside lock
            self._observable_state = replace(self._observable_state, trigger_fps=cmd.fps)

        # Publish outside lock
        if self._bus:
            self._bus.publish(TriggerFPSChanged(camera=self._camera, fps=cmd.fps))

    def _on_set_filter_auto_switch(self, cmd: SetFilterAutoSwitchCommand) -> None:
        """Handle SetFilterAutoSwitchCommand from EventBus."""
        with self._lock:
            self.enable_channel_auto_filter_switching = cmd.enabled

        # Publish state change outside lock
        if self._bus:
            self._bus.publish(FilterAutoSwitchChanged(enabled=cmd.enabled))

    def _on_update_illumination(self, cmd: UpdateIlluminationCommand) -> None:
        """Handle UpdateIlluminationCommand from EventBus."""
        self.update_illumination()

    def _on_set_display_resolution_scaling(self, cmd: SetDisplayResolutionScalingCommand) -> None:
        """Handle SetDisplayResolutionScalingCommand from EventBus."""
        self.set_display_resolution_scaling(cmd.scaling)
