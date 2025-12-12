from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Optional, TYPE_CHECKING

import squid.logging
from control.microcontroller import Microcontroller
from squid.abc import CameraAcquisitionMode, AbstractCamera
from squid.services import CameraService
from squid.services.peripheral_service import PeripheralService
from squid.services.illumination_service import IlluminationService
from squid.services.filter_wheel_service import FilterWheelService
from squid.services.nl5_service import NL5Service
from squid.events import (
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

from control._def import *
from control.core.utils import utils_channel

if TYPE_CHECKING:
    from control.microscope import Microscope
    from control.utils_config import ChannelMode


@dataclass
class LiveState:
    """State managed by LiveController."""

    is_live: bool = False
    current_channel: Optional[str] = None
    trigger_mode: str = "Software"
    trigger_fps: float = 10.0
    illumination_on: bool = False


class LiveController:
    def __init__(
        self,
        microscope: "Microscope",
        # NOTE(imo): Right now, Microscope needs to import LiveController.  So we can't properly annotate it here.
        camera: AbstractCamera,
        event_bus: Optional[EventBus] = None,
        camera_service: Optional[CameraService] = None,
        illumination_service: Optional[IlluminationService] = None,
        peripheral_service: Optional[PeripheralService] = None,
        filter_wheel_service: Optional[FilterWheelService] = None,
        nl5_service: Optional[NL5Service] = None,
        control_illumination: bool = True,
        use_internal_timer_for_hardware_trigger: bool = True,
        for_displacement_measurement: bool = False,
    ) -> None:
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._lock = threading.RLock()  # Thread safety lock
        self.microscope: "Microscope" = microscope
        self.camera: AbstractCamera = camera
        self._bus: Optional[EventBus] = event_bus
        self._bus_subscribed: bool = False
        self._camera_service = camera_service
        self._illumination_service = illumination_service
        self._peripheral_service = peripheral_service
        self._filter_wheel_service = filter_wheel_service
        self._nl5_service = nl5_service
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

        # Initialize state for event-driven communication
        self._state = LiveState(
            trigger_mode=self._trigger_mode_to_str(self.trigger_mode),
            trigger_fps=self.fps_trigger,
        )

        # Subscribe to commands if event bus provided
        self._subscribe_to_bus(self._bus)

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

    def attach_event_bus(self, bus: EventBus) -> None:
        """
        Attach an EventBus after construction (for ApplicationContext wiring).

        Idempotent: if already attached to the same bus, this is a no-op.
        """
        if self._bus is bus and self._bus_subscribed:
            return
        self._bus = bus
        self._bus_subscribed = False
        self._subscribe_to_bus(bus)

    # illumination control
    def _extract_wavelength(self) -> Optional[int]:
        """Safely extract wavelength from the current configuration name."""
        if self.currentConfiguration is None:
            return None
        try:
            wavelength = utils_channel.extract_wavelength_from_config_name(
                self.currentConfiguration.name
            )
            return int(wavelength) if wavelength is not None else None
        except Exception:
            self._log.exception(
                "Failed to extract wavelength from configuration name '%s'",
                getattr(self.currentConfiguration, "name", None),
            )
            return None

    def turn_on_illumination(self) -> None:
        if self.currentConfiguration is None:
            return
        wavelength = self._extract_wavelength()
        if wavelength is None:
            self._log.debug("Cannot turn on illumination: wavelength not found.")
            return
        is_led_matrix = "LED matrix" in self.currentConfiguration.name
        if (
            self._illumination_service
            and not is_led_matrix
            and getattr(self._illumination_service, "has_channel", lambda c: True)(
                wavelength
            )
        ):
            try:
                self._illumination_service.turn_on_channel(wavelength)
                self.illumination_on = True
                return
            except Exception:
                self._log.exception("Failed to turn on illumination via service")
        # Fallback to legacy hardware paths if service unavailable
        if "LED matrix" not in self.currentConfiguration.name:
            if hasattr(self.microscope, "illumination_controller"):
                self.microscope.illumination_controller.turn_on_illumination(
                    wavelength
                )
            else:
                self._log.warning("No illumination controller available to turn on channel")
        elif self.microscope.addons.sci_microscopy_led_array and "LED matrix" in self.currentConfiguration.name:
            self.microscope.addons.sci_microscopy_led_array.turn_on_illumination()
        else:
            self._log.warning("LED matrix illumination controller unavailable; skipping turn on")
        self.illumination_on = True

    def turn_off_illumination(self) -> None:
        if self.currentConfiguration is None:
            return
        wavelength = self._extract_wavelength()
        if wavelength is None:
            self._log.warning("Cannot turn off illumination: wavelength not found.")
            return
        is_led_matrix = "LED matrix" in self.currentConfiguration.name
        if (
            self._illumination_service
            and not is_led_matrix
            and getattr(self._illumination_service, "has_channel", lambda c: True)(
                wavelength
            )
        ):
            try:
                self._illumination_service.turn_off_channel(wavelength)
                self.illumination_on = False
                return
            except Exception:
                self._log.exception("Failed to turn off illumination via service")
        if "LED matrix" not in self.currentConfiguration.name:
            if hasattr(self.microscope, "illumination_controller"):
                self.microscope.illumination_controller.turn_off_illumination(
                    wavelength
                )
        elif self.microscope.addons.sci_microscopy_led_array and "LED matrix" in self.currentConfiguration.name:
            self.microscope.addons.sci_microscopy_led_array.turn_off_illumination()
        else:
            self._log.warning("LED matrix illumination controller unavailable; skipping turn off")
        self.illumination_on = False

    def update_illumination(self) -> None:
        if self.currentConfiguration is None:
            return
        illumination_source = self.currentConfiguration.illumination_source
        intensity = self.currentConfiguration.illumination_intensity
        is_led_matrix = "LED matrix" in self.currentConfiguration.name
        if (
            self._illumination_service
            and not is_led_matrix
            and getattr(self._illumination_service, "has_channel", lambda c: True)(
                illumination_source
            )
        ):
            try:
                self._illumination_service.set_channel_power(
                    illumination_source, intensity
                )
                return
            except Exception:
                self._log.exception("Failed to update illumination via service")
        if illumination_source < 10:  # LED matrix
            if self.microscope.addons.sci_microscopy_led_array:
                # set color
                led_array = self.microscope.addons.sci_microscopy_led_array
                if "BF LED matrix full_R" in self.currentConfiguration.name:
                    led_colors = (1, 0, 0)
                elif "BF LED matrix full_G" in self.currentConfiguration.name:
                    led_colors = (0, 1, 0)
                elif "BF LED matrix full_B" in self.currentConfiguration.name:
                    led_colors = (0, 0, 1)
                else:
                    led_colors = SCIMICROSCOPY_LED_ARRAY_DEFAULT_COLOR

                # set mode
                if "BF LED matrix left half" in self.currentConfiguration.name:
                    led_mode = "dpc.l"
                elif "BF LED matrix right half" in self.currentConfiguration.name:
                    led_mode = "dpc.r"
                elif "BF LED matrix top half" in self.currentConfiguration.name:
                    led_mode = "dpc.t"
                elif "BF LED matrix bottom half" in self.currentConfiguration.name:
                    led_mode = "dpc.b"
                elif "BF LED matrix full" in self.currentConfiguration.name:
                    led_mode = "bf"
                elif "DF LED matrix" in self.currentConfiguration.name:
                    led_mode = "df"
                else:
                    self._log.warning(
                        "Unknown configuration name, using default mode 'bf'."
                    )
                    led_mode = "bf"

                led_array.set_color(led_colors)
                led_array.set_brightness(intensity)
                led_array.set_illumination(led_mode)
            else:
                micro: Microcontroller = (
                    self.microscope.low_level_drivers.microcontroller
                )
                if "BF LED matrix full_R" in self.currentConfiguration.name:
                    micro.set_illumination_led_matrix(
                        illumination_source, r=(intensity / 100), g=0, b=0
                    )
                elif "BF LED matrix full_G" in self.currentConfiguration.name:
                    micro.set_illumination_led_matrix(
                        illumination_source, r=0, g=(intensity / 100), b=0
                    )
                elif "BF LED matrix full_B" in self.currentConfiguration.name:
                    micro.set_illumination_led_matrix(
                        illumination_source, r=0, g=0, b=(intensity / 100)
                    )
                else:
                    micro.set_illumination_led_matrix(
                        illumination_source,
                        r=(intensity / 100) * LED_MATRIX_R_FACTOR,
                        g=(intensity / 100) * LED_MATRIX_G_FACTOR,
                        b=(intensity / 100) * LED_MATRIX_B_FACTOR,
                    )
        else:
            # update illumination
            wavelength = int(
                utils_channel.extract_wavelength_from_config_name(
                    self.currentConfiguration.name
                )
            )
            self.microscope.illumination_controller.set_intensity(wavelength, intensity)
            if (
                self._nl5_service
                and NL5_USE_DOUT
                and "Fluorescence" in self.currentConfiguration.name
            ):
                try:
                    self._nl5_service.set_active_channel(
                        NL5_WAVENLENGTH_MAP[wavelength]
                    )
                    if NL5_USE_AOUT:
                        self._nl5_service.set_laser_power(
                            NL5_WAVENLENGTH_MAP[wavelength], int(intensity)
                        )
                    if self.microscope.addons.cellx and ENABLE_CELLX:
                        self.microscope.addons.cellx.set_laser_power(
                            NL5_WAVENLENGTH_MAP[wavelength], int(intensity)
                        )
                except Exception:
                    self._log.exception("Failed to set NL5 laser power via service")

        # set emission filter position
        if ENABLE_SPINNING_DISK_CONFOCAL:
            if self.microscope.addons.xlight and not USE_DRAGONFLY:
                try:
                    self.microscope.addons.xlight.set_emission_filter(
                        XLIGHT_EMISSION_FILTER_MAPPING[illumination_source],
                        extraction=False,
                        validate=XLIGHT_VALIDATE_WHEEL_POS,
                    )
                except Exception as e:
                    print("not setting emission filter position due to " + str(e))
            elif USE_DRAGONFLY and self.microscope.addons.dragonfly:
                try:
                    self.microscope.addons.dragonfly.set_emission_filter(
                        self.microscope.addons.dragonfly.get_camera_port(),
                        self.currentConfiguration.emission_filter_position,
                    )
                except Exception as e:
                    print("not setting emission filter position due to " + str(e))

        if (
            self._filter_wheel_service
            and self._filter_wheel_service.is_available()
            and self.enable_channel_auto_filter_switching
        ):
            try:
                delay = 0
                if self.trigger_mode == TriggerMode.HARDWARE:
                    delay = -self.camera.get_strobe_time()
                self._filter_wheel_service.set_delay_offset_ms(delay)
                self._filter_wheel_service.set_filter_wheel_position(
                    {1: self.currentConfiguration.emission_filter_position}
                )
            except Exception:
                self._log.exception("Failed to set emission filter position via service")

    def start_live(self) -> None:
        with self._lock:
            self.is_live = True
            if self._camera_service:
                self._camera_service.start_streaming()
                if self.trigger_mode == TriggerMode.SOFTWARE or (
                    self.trigger_mode == TriggerMode.HARDWARE
                    and self.use_internal_timer_for_hardware_trigger
                ):
                    self._camera_service.enable_callbacks(True)
                    self._start_triggerred_acquisition()
            else:
                self.camera.start_streaming()
                if self.trigger_mode == TriggerMode.SOFTWARE or (
                    self.trigger_mode == TriggerMode.HARDWARE
                    and self.use_internal_timer_for_hardware_trigger
                ):
                    self.camera.enable_callbacks(True)
                    self._start_triggerred_acquisition()
            if self.for_displacement_measurement:
                if self._peripheral_service:
                    try:
                        self._peripheral_service.turn_on_af_laser()
                    except Exception:
                        self._log.exception("Failed to turn on AF laser via peripheral service")
                else:
                    self._log.warning("Peripheral service missing; cannot toggle AF laser safely")

    def stop_live(self) -> None:
        with self._lock:
            if self.is_live:
                self.is_live = False
                # Stop timer-based triggering for SOFTWARE and HARDWARE modes
                if (self.trigger_mode == TriggerMode.SOFTWARE) or (
                    self.trigger_mode == TriggerMode.HARDWARE
                    and self.use_internal_timer_for_hardware_trigger
                ):
                    self._stop_triggerred_acquisition()
                # Stop streaming for ALL trigger modes, not just CONTINUOUS.
                # start_live() always starts streaming, so stop_live() should always stop it.
                if self._camera_service:
                    self._camera_service.stop_streaming()
                else:
                    self.camera.stop_streaming()
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
            ready = (
                self._camera_service.get_ready_for_trigger()
                if self._camera_service
                else self.camera.get_ready_for_trigger()
            )
            if not ready:
                self._trigger_skip_count += 1
                if self._trigger_skip_count % 100 == 1:
                    total_frame_time = (
                        self._camera_service.get_total_frame_time()
                        if self._camera_service
                        else getattr(self.camera, "get_total_frame_time", lambda: 0)()
                    )
                    self._log.debug(
                        f"Not ready for trigger, skipping (_trigger_skip_count={self._trigger_skip_count}, total frame time = {total_frame_time} [ms])."
                    )
                return False

            self._trigger_skip_count = 0
            if self.trigger_mode == TriggerMode.SOFTWARE and self.control_illumination:
                if not self.illumination_on:
                    self.turn_on_illumination()

            self.trigger_ID = self.trigger_ID + 1

            if self._camera_service:
                self._camera_service.send_trigger()
            else:
                self.camera.send_trigger(self.camera.get_exposure_time())

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
                self.camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
                if self.is_live:
                    self._start_triggerred_acquisition()
            if mode == TriggerMode.HARDWARE:
                if self.trigger_mode == TriggerMode.SOFTWARE and self.is_live:
                    self._stop_triggerred_acquisition()
                self.camera.set_acquisition_mode(CameraAcquisitionMode.HARDWARE_TRIGGER)
                if self.currentConfiguration is not None:
                    self.camera.set_exposure_time(self.currentConfiguration.exposure_time)

                if self.is_live and self.use_internal_timer_for_hardware_trigger:
                    self._start_triggerred_acquisition()
            if mode == TriggerMode.CONTINUOUS:
                if (self.trigger_mode == TriggerMode.SOFTWARE) or (
                    self.trigger_mode == TriggerMode.HARDWARE
                    and self.use_internal_timer_for_hardware_trigger
                ):
                    self._stop_triggerred_acquisition()
                self.camera.set_acquisition_mode(CameraAcquisitionMode.CONTINUOUS)
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
            if self._camera_service:
                self._camera_service.set_exposure_time(exposure)
                try:
                    self._camera_service.set_analog_gain(gain)
                except Exception:
                    self._log.debug("Analog gain not supported by camera service")
            else:
                self.camera.set_exposure_time(exposure)
                try:
                    self.camera.set_analog_gain(gain)
                except NotImplementedError:
                    pass

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
    def state(self) -> LiveState:
        """Get current state."""
        return self._state

    def _on_start_live_command(self, cmd: StartLiveCommand) -> None:
        """Handle StartLiveCommand from EventBus."""
        self._log.info(f"_on_start_live_command: is_live={self.is_live}, config={cmd.configuration}")
        with self._lock:
            if self.is_live:
                self._log.info("Already live, returning early")
                return  # Already running

            # Resolve configuration name to ChannelMode if available so
            # illumination and trigger settings have context.
            resolved_configuration = None
            if cmd.configuration:
                manager = getattr(
                    self.microscope, "channel_configuration_manager", None
                )
                objective_store = getattr(self.microscope, "objective_store", None)
                current_objective = getattr(objective_store, "current_objective", None)
                if manager is not None and current_objective is not None:
                    try:
                        resolved_configuration = manager.get_channel_configuration_by_name(  # type: ignore[attr-defined]
                            current_objective, cmd.configuration
                        )
                    except Exception:
                        # If resolution fails, fall back to existing configuration
                        self._log.exception(
                            "Failed to resolve configuration %s", cmd.configuration
                        )
            if resolved_configuration is not None:
                self.currentConfiguration = resolved_configuration

            self.is_live = True

            # Start streaming via services when available
            if self._camera_service:
                self._camera_service.start_streaming()
                if self.trigger_mode == TriggerMode.SOFTWARE or (
                    self.trigger_mode == TriggerMode.HARDWARE
                    and self.use_internal_timer_for_hardware_trigger
                ):
                    self._camera_service.enable_callbacks(True)
                    self._start_triggerred_acquisition()
            else:
                self.camera.start_streaming()
                if self.trigger_mode == TriggerMode.SOFTWARE or (
                    self.trigger_mode == TriggerMode.HARDWARE
                    and self.use_internal_timer_for_hardware_trigger
                ):
                    self.camera.enable_callbacks(True)
                    self._start_triggerred_acquisition()

            if self.for_displacement_measurement:
                if self._peripheral_service:
                    try:
                        self._peripheral_service.turn_on_af_laser()
                    except Exception:
                        self._log.exception("Failed to turn on AF laser via peripheral service")
                else:
                    self.microscope.low_level_drivers.microcontroller.set_pin_level(
                        MCU_PINS.AF_LASER, 1
                    )

            # Update state inside lock
            self._state = replace(
                self._state,
                is_live=True,
                current_channel=(
                    self.currentConfiguration.name
                    if self.currentConfiguration is not None
                    else cmd.configuration
                ),
                illumination_on=self.illumination_on,
            )

        # Publish outside lock
        if self._bus:
            self._log.info("Publishing LiveStateChanged(is_live=True)")
            self._bus.publish(LiveStateChanged(
                is_live=True,
                configuration=cmd.configuration,
            ))
        else:
            self._log.warning("No event bus, not publishing LiveStateChanged")

    def _on_stop_live_command(self, cmd: StopLiveCommand) -> None:
        """Handle StopLiveCommand from EventBus."""
        self._log.info(f"_on_stop_live_command: is_live={self.is_live}")
        with self._lock:
            if not self.is_live:
                self._log.info("Not live, returning early from stop")
                return  # Not running

            self.is_live = False
            if self.trigger_mode == TriggerMode.SOFTWARE or (
                self.trigger_mode == TriggerMode.HARDWARE
                and self.use_internal_timer_for_hardware_trigger
            ):
                self._stop_triggerred_acquisition()
            if self._camera_service:
                self._camera_service.stop_streaming()
            else:
                self.camera.stop_streaming()
            if self.control_illumination:
                self.turn_off_illumination()
            if self.for_displacement_measurement:
                if self._peripheral_service:
                    try:
                        self._peripheral_service.turn_off_af_laser()
                    except Exception:
                        self._log.exception("Failed to turn off AF laser via peripheral service")
                else:
                    self.microscope.low_level_drivers.microcontroller.set_pin_level(
                        MCU_PINS.AF_LASER, 0
                    )

            # Update state inside lock
            self._state = replace(
                self._state,
                is_live=False,
                illumination_on=False,
            )

        # Publish outside lock
        if self._bus:
            self._log.info("Publishing LiveStateChanged(is_live=False)")
            self._bus.publish(LiveStateChanged(
                is_live=False,
                configuration=None,
            ))
        else:
            self._log.warning("No event bus, not publishing LiveStateChanged(is_live=False)")

    def _on_set_trigger_mode_command(self, cmd: SetTriggerModeCommand) -> None:
        """Handle SetTriggerModeCommand from EventBus."""
        mode = self._str_to_trigger_mode(cmd.mode)

        with self._lock:
            if mode == TriggerMode.SOFTWARE:
                if self.is_live and (
                    self.trigger_mode == TriggerMode.HARDWARE
                    and self.use_internal_timer_for_hardware_trigger
                ):
                    self._stop_triggerred_acquisition()
                if self._camera_service:
                    self._camera_service.set_acquisition_mode(
                        CameraAcquisitionMode.SOFTWARE_TRIGGER
                    )
                    self._camera_service.enable_callbacks(True)
                else:
                    self.camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
                    self.camera.enable_callbacks(True)
                if self.is_live:
                    self._start_triggerred_acquisition()
            elif mode == TriggerMode.HARDWARE:
                if self.trigger_mode == TriggerMode.SOFTWARE and self.is_live:
                    self._stop_triggerred_acquisition()
                if self._camera_service:
                    self._camera_service.set_acquisition_mode(
                        CameraAcquisitionMode.HARDWARE_TRIGGER
                    )
                else:
                    self.camera.set_acquisition_mode(CameraAcquisitionMode.HARDWARE_TRIGGER)
                if self.currentConfiguration is not None:
                    exposure = self.currentConfiguration.exposure_time
                    if self._camera_service:
                        self._camera_service.set_exposure_time(exposure)
                    else:
                        self.camera.set_exposure_time(exposure)
                if self.is_live and self.use_internal_timer_for_hardware_trigger:
                    self._start_triggerred_acquisition()
            elif mode == TriggerMode.CONTINUOUS:
                if (self.trigger_mode == TriggerMode.SOFTWARE) or (
                    self.trigger_mode == TriggerMode.HARDWARE
                    and self.use_internal_timer_for_hardware_trigger
                ):
                    self._stop_triggerred_acquisition()
                if self._camera_service:
                    self._camera_service.set_acquisition_mode(
                        CameraAcquisitionMode.CONTINUOUS
                    )
                    self._camera_service.enable_callbacks(True)
                    if self.is_live:
                        self._camera_service.start_streaming()
                else:
                    self.camera.set_acquisition_mode(CameraAcquisitionMode.CONTINUOUS)
                    self.camera.enable_callbacks(True)
                    if self.is_live:
                        self.camera.start_streaming()
            else:
                self._log.error(f"Unknown trigger mode: {mode}")

            # Update state inside lock
            self.trigger_mode = mode
            self._state = replace(self._state, trigger_mode=cmd.mode)

        # Publish outside lock
        if self._bus:
            self._bus.publish(TriggerModeChanged(mode=cmd.mode))

    def _on_set_trigger_fps_command(self, cmd: SetTriggerFPSCommand) -> None:
        """Handle SetTriggerFPSCommand from EventBus."""
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

            # Update state inside lock
            self._state = replace(self._state, trigger_fps=cmd.fps)

        # Publish outside lock
        if self._bus:
            self._bus.publish(TriggerFPSChanged(fps=cmd.fps))

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
