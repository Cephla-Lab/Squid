import serial

from squid.mcs.drivers.peripherals.serial_base import SerialDevice
from squid.mcs.drivers.lighting.led import IntensityControlMode, ShutterControlMode
from _def import LDI_INTENSITY_MODE, LDI_SHUTTER_MODE
from squid.core.abc import LightSource

import squid.core.logging


class LDI(LightSource):
    """Wrapper for communicating with LDI over serial"""

    def __init__(self, SN="00000001"):
        """
        Provide serial number
        """
        self.log = squid.core.logging.get_logger(self.__class__.__name__)
        self.serial_connection = SerialDevice(
            SN=SN,
            baudrate=9600,
            bytesize=serial.EIGHTBITS,
            stopbits=serial.STOPBITS_ONE,
            parity=serial.PARITY_NONE,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self.serial_connection.open_ser()
        if LDI_INTENSITY_MODE == "PC":
            self.intensity_mode = IntensityControlMode.Software
        elif LDI_INTENSITY_MODE == "EXT":
            self.intensity_mode = IntensityControlMode.SquidControllerDAC
        if LDI_SHUTTER_MODE == "PC":
            self.shutter_mode = ShutterControlMode.Software
        elif LDI_SHUTTER_MODE == "EXT":
            self.shutter_mode = ShutterControlMode.TTL

        self.channel_mappings = {
            405: 405,
            470: 470,
            488: 470,
            545: 555,
            550: 555,
            555: 555,
            561: 555,
            638: 640,
            640: 640,
            730: 730,
            735: 730,
            750: 730,
        }
        self.active_channel = None

    def initialize(self):
        self.serial_connection.write_and_check("run!\r", "ok")

    def set_shutter_control_mode(self, mode):
        if mode == ShutterControlMode.TTL:
            self.serial_connection.write_and_check("SH_MODE=EXT\r", "ok")
        elif mode == ShutterControlMode.Software:
            self.serial_connection.write_and_check("SH_MODE=PC\r", "ok")
        self.shutter_mode = mode

    def get_shutter_control_mode(self):
        pass

    def set_intensity_control_mode(self, mode):
        if mode == IntensityControlMode.SquidControllerDAC:
            self.serial_connection.write_and_check("INT_MODE=EXT\r", "ok")
        elif mode == IntensityControlMode.Software:
            self.serial_connection.write_and_check("INT_MODE=PC\r", "ok")
        self.intensity_mode = mode

    def get_intensity_control_mode(self):
        pass

    def set_intensity(self, channel, intensity):
        channel = str(channel)
        intensity = "{:.2f}".format(intensity)
        self.log.debug("set:" + channel + "=" + intensity + "\r")
        self.serial_connection.write_and_check(
            "set:" + channel + "=" + intensity + "\r", "ok"
        )

    def get_intensity(self, channel):
        try:
            response = self.serial_connection.write_and_read("set?\r")
            pairs = response.replace("SET:", "").split(",")
            intensities = {}
            for pair in pairs:
                channel, value = pair.split("=")
                intensities[int(channel)] = int(value)
            return intensities[channel]
        except Exception:
            return None

    def set_shutter_state(self, channel, on):
        channel = str(channel)
        state = str(on)
        if self.active_channel is not None and channel != self.active_channel:
            self.set_active_channel_shutter(False)
        self.serial_connection.write_and_check(
            "shutter:" + channel + "=" + state + "\r", "ok"
        )
        if on:
            self.active_channel = channel

    def get_shutter_state(self, channel):
        try:
            response = self.serial_connection.write_and_read(
                "shutter?" + channel + "\r"
            )
            state = response.split("=")[1]
            return 1 if state == "OPEN" else 0
        except Exception:
            return None

    def set_active_channel_shutter(self, state):
        channel = str(self.active_channel)
        state = str(state)
        self.log.debug("shutter:" + channel + "=" + state + "\r")
        self.serial_connection.write_and_check(
            "shutter:" + channel + "=" + state + "\r", "ok"
        )

    def shut_down(self):
        for ch in list(set(self.channel_mappings.values())):
            self.set_intensity(ch, 0)
            self.set_shutter_state(ch, False)
        self.serial_connection.close()


class LDI_Simulation(LightSource):
    """Wrapper for communicating with LDI over serial"""

    def __init__(self, SN="00000001"):
        """
        Provide serial number
        """
        self.log = squid.core.logging.get_logger(self.__class__.__name__)
        self.intensity_mode = IntensityControlMode.Software
        self.shutter_mode = ShutterControlMode.Software

        self.channel_mappings = {
            405: 405,
            470: 470,
            488: 470,
            545: 555,
            550: 555,
            555: 555,
            561: 555,
            638: 640,
            640: 640,
            730: 730,
            735: 730,
            750: 730,
        }
        self.active_channel = None

    def initialize(self):
        pass

    def set_shutter_control_mode(self, mode):
        self.shutter_mode = mode

    def get_shutter_control_mode(self):
        pass

    def set_intensity_control_mode(self, mode):
        self.intensity_mode = mode

    def get_intensity_control_mode(self):
        pass

    def set_intensity(self, channel, intensity):
        channel = str(channel)
        intensity = "{:.2f}".format(intensity)
        self.log.debug("set:" + channel + "=" + intensity + "\r")

    def get_intensity(self, channel):
        return 100

    def set_shutter_state(self, channel, on):
        channel = str(channel)
        str(on)
        if self.active_channel is not None and channel != self.active_channel:
            self.set_active_channel_shutter(False)
        if on:
            self.active_channel = channel

    def get_shutter_state(self, channel):
        return 1

    def set_active_channel_shutter(self, state):
        channel = str(self.active_channel)
        state = str(state)
        self.log.debug("shutter:" + channel + "=" + state + "\r")

    def shut_down(self):
        for ch in list(set(self.channel_mappings.values())):
            self.set_intensity(ch, 0)
            self.set_shutter_state(ch, False)
