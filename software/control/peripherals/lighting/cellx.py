import serial

from control.peripherals.serial_base import SerialDevice
from control._def import CELLX_MODULATION


class CellX:

    VALID_MODULATIONS = ["INT", "EXT Digital", "EXT Analog", "EXT Mixed"]

    """Wrapper for communicating with LDI over serial"""

    def __init__(self, SN="", initial_modulation=CELLX_MODULATION):
        self.serial_connection = SerialDevice(
            SN=SN,
            baudrate=115200,
            bytesize=serial.EIGHTBITS,
            stopbits=serial.STOPBITS_ONE,
            parity=serial.PARITY_NONE,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self.serial_connection.open_ser()
        self.power = {}

        for channel in [1, 2, 3, 4]:
            self.set_modulation(channel, initial_modulation)
            self.turn_on(channel)

    def turn_on(self, channel):
        self.serial_connection.write_and_check(
            "SOUR" + str(channel) + ":AM:STAT ON\r", "OK", read_delay=0.01, print_response=False
        )

    def turn_off(self, channel):
        self.serial_connection.write_and_check(
            "SOUR" + str(channel) + ":AM:STAT OFF\r", "OK", read_delay=0.01, print_response=False
        )

    def set_laser_power(self, channel, power):
        if not (power >= 1 and power <= 100):
            raise ValueError(f"Power={power} not in the range 1 to 100")

        if channel not in self.power.keys() or power != self.power[channel]:
            self.serial_connection.write_and_check(
                "SOUR" + str(channel) + ":POW:LEV:IMM:AMPL " + str(power / 1000) + "\r",
                "OK",
                read_delay=0.01,
                print_response=False,
            )
            self.power[channel] = power
        else:
            pass  # power is the same

    def set_modulation(self, channel, modulation):
        if modulation not in CellX.VALID_MODULATIONS:
            raise ValueError(f"Modulation '{modulation}' not in valid modulations: {CellX.VALID_MODULATIONS}")
        self.serial_connection.write_and_check(
            "SOUR" + str(channel) + ":AM:" + modulation + "\r", "OK", read_delay=0.01, print_response=False
        )

    def close(self):
        self.serial_connection.close()


class CellX_Simulation:
    """Wrapper for communicating with LDI over serial"""

    def __init__(self, SN=""):
        self.serial_connection = SerialDevice(
            SN=SN,
            baudrate=115200,
            bytesize=serial.EIGHTBITS,
            stopbits=serial.STOPBITS_ONE,
            parity=serial.PARITY_NONE,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self.serial_connection.open_ser()
        self.power = {}

    def turn_on(self, channel):
        pass

    def turn_off(self, channel):
        pass

    def set_laser_power(self, channel, power):
        if not (power >= 1 and power <= 100):
            raise ValueError(f"Power={power} not in the range 1 to 100")

        if channel not in self.power.keys() or power != self.power[channel]:
            self.power[channel] = power
        else:
            pass  # power is the same

    def set_modulation(self, channel, modulation):
        if modulation not in CellX.VALID_MODULATIONS:
            raise ValueError(f"modulation '{modulation}' not in valid choices: {CellX.VALID_MODULATIONS}")
        self.serial_connection.write_and_check(
            "SOUR" + str(channel) + "AM:" + modulation + "\r", "OK", read_delay=0.01, print_response=False
        )

    def close(self):
        pass
