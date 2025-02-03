from enum import Enum


class LightSourceType(Enum):
    SquidLED = 0
    SquidLaser = 1
    LDI = 2
    CELESTA = 3
    VersaLase = 4
    SCI = 5


class IntensityControlMode(Enum):
    SquidControllerDAC = 0
    Software = 1


class ShutterControlMode(Enum):
    TTL = 0
    Software = 1


class IlluminationController:
    def __init__(
        self, microcontroller, intensity_control_mode, shutter_control_mode, light_source_type=None, light_source=None
    ):
        self.microcontroller = microcontroller
        self.intensity_control_mode = intensity_control_mode
        self.shutter_control_mode = shutter_control_mode
        self.light_source_type = light_source_type
        self.light_source = light_source
        self.channel_mappings_TTL = {
            405: 11,
            470: 12,
            488: 12,
            545: 14,
            550: 14,
            555: 14,
            561: 14,
            638: 13,
            640: 13,
            730: 15,
            735: 15,
            750: 15,
        }

        self.channel_mappings_software = {}
        self.is_on = {}
        self.intensity_settings = {}
        self.current_channel = None

        if self.light_source_type is not None:
            self.configure_light_source()

    def configure_light_source(self):
        self.light_source.initialize()
        self.set_intensity_control_mode(self.intensity_control_mode)
        self.set_shutter_control_mode(self.shutter_control_mode)
        self.channel_mappings_software = self.light_source.channel_mappings
        for ch in self.channel_mappings_software:
            self.intensity_settings[ch] = self.get_intensity(ch)
            self.is_on[ch] = self.light_source.get_shutter_state(self.channel_mappings_software[ch])

    def set_intensity_control_mode(self, mode):
        self.light_source.set_intensity_control_mode(mode)
        self.intensity_control_mode = mode

    def get_intensity_control_mode(self):
        mode = self.light_source.get_intensity_control_mode()
        if mode is not None:
            self.intensity_control_mode = mode
            return mode

    def set_shutter_control_mode(self, mode):
        self.light_source.set_shutter_control_mode(mode)
        self.shutter_control_mode = mode

    def get_shutter_control_mode(self):
        mode = self.light_source.get_shutter_control_mode()
        if mode is not None:
            self.shutter_control_mode = mode
            return mode

    def get_intensity(self, channel):
        if self.intensity_control_mode == IntensityControlMode.Software:
            intensity = self.light_source.get_intensity(self.channel_mappings_software[channel])
            self.intensity_settings[channel] = intensity
            return intensity  # 0 - 100

    def turn_on_illumination(self, channel=None):
        if channel is None:
            channel = self.current_channel

        if self.shutter_control_mode == ShutterControlMode.Software:
            self.light_source.set_shutter_state(self.channel_mappings_software[channel], on=True)
        elif self.shutter_control_mode == ShutterControlMode.TTL:
            # self.microcontroller.set_illumination(self.channel_mappings_TTL[channel], self.intensity_settings[channel])
            self.microcontroller.turn_on_illumination()

        self.is_on[channel] = True

    def turn_off_illumination(self, channel=None):
        if channel is None:
            channel = self.current_channel

        if self.shutter_control_mode == ShutterControlMode.Software:
            self.light_source.set_shutter_state(self.channel_mappings_software[channel], on=False)
        elif self.shutter_control_mode == ShutterControlMode.TTL:
            self.microcontroller.turn_off_illumination()

        self.is_on[channel] = False

    def set_current_channel(self, channel):
        self.current_channel = channel

    def set_intensity(self, channel, intensity):
        if self.intensity_control_mode == IntensityControlMode.Software:
            if intensity != self.intensity_settings[channel]:
                self.light_source.set_intensity(self.channel_mappings_software[channel], intensity)
                self.intensity_settings[channel] = intensity
        self.microcontroller.set_illumination(self.channel_mappings_TTL[channel], intensity)

    def get_shutter_state(self):
        return self.is_on

    def close(self):
        self.light_source.shut_down()
