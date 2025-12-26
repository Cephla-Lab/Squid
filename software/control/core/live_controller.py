from __future__ import annotations

import time
import threading
from typing import Optional

import squid.logging
from control.microcontroller import Microcontroller
from squid.abc import CameraAcquisitionMode, AbstractCamera

from control._def import *
from control import utils_channel


class LiveController:
    def __init__(
        self,
        microscope: "Microscope",
        # NOTE(imo): Right now, Microscope needs to import LiveController.  So we can't properly annotate it here.
        camera: AbstractCamera,
        control_illumination: bool = True,
        use_internal_timer_for_hardware_trigger: bool = True,
        for_displacement_measurement: bool = False,
    ):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.microscope = microscope
        self.camera: AbstractCamera = camera
        self.currentConfiguration = None
        self.trigger_mode: Optional[TriggerMode] = TriggerMode.SOFTWARE  # @@@ change to None
        self.is_live = False
        self.control_illumination = control_illumination
        self.illumination_on = False
        self.use_internal_timer_for_hardware_trigger = (
            use_internal_timer_for_hardware_trigger  # use Timer vs timer in the MCU
        )
        self.for_displacement_measurement = for_displacement_measurement

        self.fps_trigger = 1
        self.timer_trigger_interval = (1.0 / self.fps_trigger) * 1000
        self._trigger_skip_count = 0
        self.timer_trigger: Optional[threading.Timer] = None

        self.trigger_ID = -1

        self.fps_real = 0
        self.counter = 0
        self.timestamp_last = 0

        self.display_resolution_scaling = 1

        self.enable_channel_auto_filter_switching: bool = True

    # illumination control
    def turn_on_illumination(self):
        if not "LED matrix" in self.currentConfiguration.name:
            self.microscope.illumination_controller.turn_on_illumination(
                int(utils_channel.extract_wavelength_from_config_name(self.currentConfiguration.name))
            )
        elif self.microscope.addons.sci_microscopy_led_array and "LED matrix" in self.currentConfiguration.name:
            self.microscope.addons.sci_microscopy_led_array.turn_on_illumination()
        # LED matrix
        else:
            self.microscope.low_level_drivers.microcontroller.turn_on_illumination()  # to wrap microcontroller in Squid_led_array
        self.illumination_on = True

    def turn_off_illumination(self):
        if not "LED matrix" in self.currentConfiguration.name:
            self.microscope.illumination_controller.turn_off_illumination(
                int(utils_channel.extract_wavelength_from_config_name(self.currentConfiguration.name))
            )
        elif self.microscope.addons.sci_microscopy_led_array and "LED matrix" in self.currentConfiguration.name:
            self.microscope.addons.sci_microscopy_led_array.turn_off_illumination()
        # LED matrix
        else:
            self.microscope.low_level_drivers.microcontroller.turn_off_illumination()  # to wrap microcontroller in Squid_led_array
        self.illumination_on = False

    def update_illumination(self):
        illumination_source = self.currentConfiguration.illumination_source
        intensity = self.currentConfiguration.illumination_intensity
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
                    self._log.warning("Unknown configuration name, using default mode 'bf'.")
                    led_mode = "bf"

                led_array.set_color(led_colors)
                led_array.set_brightness(intensity)
                led_array.set_illumination(led_mode)
            else:
                micro: Microcontroller = self.microscope.low_level_drivers.microcontroller
                if "BF LED matrix full_R" in self.currentConfiguration.name:
                    micro.set_illumination_led_matrix(illumination_source, r=(intensity / 100), g=0, b=0)
                elif "BF LED matrix full_G" in self.currentConfiguration.name:
                    micro.set_illumination_led_matrix(illumination_source, r=0, g=(intensity / 100), b=0)
                elif "BF LED matrix full_B" in self.currentConfiguration.name:
                    micro.set_illumination_led_matrix(illumination_source, r=0, g=0, b=(intensity / 100))
                else:
                    micro.set_illumination_led_matrix(
                        illumination_source,
                        r=(intensity / 100) * LED_MATRIX_R_FACTOR,
                        g=(intensity / 100) * LED_MATRIX_G_FACTOR,
                        b=(intensity / 100) * LED_MATRIX_B_FACTOR,
                    )
        else:
            # update illumination
            wavelength = int(utils_channel.extract_wavelength_from_config_name(self.currentConfiguration.name))
            self.microscope.illumination_controller.set_intensity(wavelength, intensity)
            if self.microscope.addons.nl5 and NL5_USE_DOUT and "Fluorescence" in self.currentConfiguration.name:
                self.microscope.addons.nl5.set_active_channel(NL5_WAVENLENGTH_MAP[wavelength])
                if NL5_USE_AOUT:
                    self.microscope.addons.nl5.set_laser_power(NL5_WAVENLENGTH_MAP[wavelength], int(intensity))
                if self.microscope.addons.cellx and ENABLE_CELLX:
                    self.microscope.addons.cellx.set_laser_power(NL5_WAVENLENGTH_MAP[wavelength], int(intensity))

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

        if self.microscope.addons.emission_filter_wheel and self.enable_channel_auto_filter_switching:
            try:
                if self.trigger_mode == TriggerMode.SOFTWARE:
                    self.microscope.addons.emission_filter_wheel.set_delay_offset_ms(0)
                elif self.trigger_mode == TriggerMode.HARDWARE:
                    self.microscope.addons.emission_filter_wheel.set_delay_offset_ms(-self.camera.get_strobe_time())
                self.microscope.addons.emission_filter_wheel.set_filter_wheel_position(
                    {1: self.currentConfiguration.emission_filter_position}
                )
            except Exception as e:
                print("not setting emission filter position due to " + str(e))

    def start_live(self):
        self.is_live = True
        self.camera.start_streaming()
        if self.trigger_mode == TriggerMode.SOFTWARE or (
            self.trigger_mode == TriggerMode.HARDWARE and self.use_internal_timer_for_hardware_trigger
        ):
            self.camera.enable_callbacks(True)  # in case it's disabled e.g. by the laser AF controller
            self._start_triggerred_acquisition()
        # if controlling the laser displacement measurement camera
        if self.for_displacement_measurement:
            self.microscope.low_level_drivers.microcontroller.set_pin_level(MCU_PINS.AF_LASER, 1)

    def stop_live(self):
        if self.is_live:
            self.is_live = False
            if self.trigger_mode == TriggerMode.SOFTWARE:
                self._stop_triggerred_acquisition()
            if self.trigger_mode == TriggerMode.CONTINUOUS:
                self.camera.stop_streaming()
            if (self.trigger_mode == TriggerMode.SOFTWARE) or (
                self.trigger_mode == TriggerMode.HARDWARE and self.use_internal_timer_for_hardware_trigger
            ):
                self._stop_triggerred_acquisition()
            if self.control_illumination:
                self.turn_off_illumination()
            # if controlling the laser displacement measurement camera
            if self.for_displacement_measurement:
                self.microscope.low_level_drivers.microcontroller.set_pin_level(MCU_PINS.AF_LASER, 0)

    def _trigger_acquisition_timer_fn(self):
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
    def trigger_acquisition(self):
        if not self.camera.get_ready_for_trigger():
            # TODO(imo): Before, send_trigger would pass silently for this case.  Now
            # we do the same here.  Should this warn?  I didn't add a warning because it seems like
            # we over-trigger as standard practice (eg: we trigger at our exposure time frequency, but
            # the cameras can't give us images that fast so we essentially always have at least 1 skipped trigger)
            self._trigger_skip_count += 1
            if self._trigger_skip_count % 100 == 1:
                self._log.debug(
                    f"Not ready for trigger, skipping (_trigger_skip_count={self._trigger_skip_count}, total frame time = {self.camera.get_total_frame_time()} [ms])."
                )
            return False

        self._trigger_skip_count = 0
        if self.trigger_mode == TriggerMode.SOFTWARE and self.control_illumination:
            if not self.illumination_on:
                self.turn_on_illumination()

        self.trigger_ID = self.trigger_ID + 1

        self.camera.send_trigger(self.camera.get_exposure_time())

        if self.trigger_mode == TriggerMode.SOFTWARE:
            if self.control_illumination and self.illumination_on == False:
                self.turn_on_illumination()

        return True

    def _stop_existing_timer(self):
        if self.timer_trigger and self.timer_trigger.is_alive():
            self.timer_trigger.cancel()
        self.timer_trigger = None

    def _start_new_timer(self, maybe_custom_interval_ms=None):
        self._stop_existing_timer()
        if maybe_custom_interval_ms:
            interval_s = maybe_custom_interval_ms / 1000.0
        else:
            interval_s = self.timer_trigger_interval / 1000.0
        self.timer_trigger = threading.Timer(interval_s, self._trigger_acquisition_timer_fn)
        self.timer_trigger.daemon = True
        self.timer_trigger.start()

    def _start_triggerred_acquisition(self):
        self._start_new_timer()

    def _set_trigger_fps(self, fps_trigger):
        if fps_trigger <= 0:
            raise ValueError(f"fps_trigger must be > 0, but {fps_trigger=}")
        self._log.debug(f"Setting {fps_trigger=}")
        self.fps_trigger = fps_trigger
        self.timer_trigger_interval = (1 / self.fps_trigger) * 1000
        if self.is_live:
            self._start_new_timer()

    def _stop_triggerred_acquisition(self):
        self._stop_existing_timer()

    # trigger mode and settings
    def set_trigger_mode(self, mode):
        if mode == TriggerMode.SOFTWARE:
            if self.is_live and (
                self.trigger_mode == TriggerMode.HARDWARE and self.use_internal_timer_for_hardware_trigger
            ):
                self._stop_triggerred_acquisition()
            self.camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
            if self.is_live:
                self._start_triggerred_acquisition()
            self.microscope.low_level_drivers.microcontroller.set_trigger_mode(0)
        if mode == TriggerMode.HARDWARE:
            if self.trigger_mode == TriggerMode.SOFTWARE and self.is_live:
                self._stop_triggerred_acquisition()
            self.camera.set_acquisition_mode(CameraAcquisitionMode.HARDWARE_TRIGGER)
            self.camera.set_exposure_time(self.currentConfiguration.exposure_time)

            if self.is_live and self.use_internal_timer_for_hardware_trigger:
                self._start_triggerred_acquisition()

            if TOUPCAMER_LEVEL_TRIGGER_ENABLE:
                self.microscope.low_level_drivers.microcontroller.set_trigger_mode(1)
            else:
                self.microscope.low_level_drivers.microcontroller.set_trigger_mode(0)

        if mode == TriggerMode.CONTINUOUS:
            if (self.trigger_mode == TriggerMode.SOFTWARE) or (
                self.trigger_mode == TriggerMode.HARDWARE and self.use_internal_timer_for_hardware_trigger
            ):
                self._stop_triggerred_acquisition()
            self.camera.set_acquisition_mode(CameraAcquisitionMode.CONTINUOUS)
            self.microscope.low_level_drivers.microcontroller.set_trigger_mode(0)
        self.trigger_mode = mode

    def set_trigger_fps(self, fps):
        if (self.trigger_mode == TriggerMode.SOFTWARE) or (
            self.trigger_mode == TriggerMode.HARDWARE and self.use_internal_timer_for_hardware_trigger
        ):
            self._set_trigger_fps(fps)

    # set microscope mode
    # @@@ to do: change softwareTriggerGenerator to TriggerGeneratror
    def set_microscope_mode(self, configuration):

        self.currentConfiguration = configuration
        self._log.info("setting microscope mode to " + self.currentConfiguration.name)

        # temporarily stop live while changing mode
        if self.is_live is True:
            self._stop_existing_timer()
            if self.control_illumination:
                self.turn_off_illumination()

        # set camera exposure time and analog gain
        self.camera.set_exposure_time(self.currentConfiguration.exposure_time)
        try:
            self.camera.set_analog_gain(self.currentConfiguration.analog_gain)
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

    def get_trigger_mode(self):
        return self.trigger_mode

    # slot
    def on_new_frame(self):
        if self.fps_trigger <= 5:
            if self.control_illumination and self.illumination_on == True:
                self.turn_off_illumination()

    def set_display_resolution_scaling(self, display_resolution_scaling):
        self.display_resolution_scaling = display_resolution_scaling / 100
