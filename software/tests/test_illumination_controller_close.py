"""Closing the microscope turns an external light source off, once."""

from control.lighting import IlluminationController, IntensityControlMode, LightSourceType, ShutterControlMode
from control.microcontroller import Microcontroller, SimSerial


class FakeLightSource:
    channel_mappings = {405: 0}

    def __init__(self):
        self.shut_downs = 0

    def initialize(self):
        return True

    def set_intensity_control_mode(self, mode):
        pass

    def set_shutter_control_mode(self, mode):
        pass

    def get_intensity(self, line):
        return 0

    def get_shutter_state(self, line):
        return False

    def shut_down(self):
        self.shut_downs += 1


def test_close_shuts_the_light_source_down_once():
    mcu = Microcontroller(SimSerial(), reset_and_initialize=False)
    source = FakeLightSource()
    controller = IlluminationController(
        mcu, IntensityControlMode.Software, ShutterControlMode.Software, LightSourceType.VersaLase, source
    )

    controller.close()
    controller.close()  # the GUI and Microscope.close() both close it

    assert source.shut_downs == 1
    mcu.close()


def test_close_without_light_source_is_a_no_op():
    mcu = Microcontroller(SimSerial(), reset_and_initialize=False)
    IlluminationController(mcu).close()
    mcu.close()
