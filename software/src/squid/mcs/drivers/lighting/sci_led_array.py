import serial
import time

from squid.mcs.drivers.peripherals.serial_base import SerialDevice


class SciMicroscopyLEDArray:
    """Wrapper for communicating with SciMicroscopy over serial"""

    def __init__(self, SN, array_distance=50, turn_on_delay=0.03):
        """
        Provide serial number
        """
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
        self.check_about()
        self.set_distance(array_distance)
        self.set_brightness(1)

        self.illumination = None
        self.NA = 0.5
        self.turn_on_delay = turn_on_delay

    def write(self, command):
        self.serial_connection.write_and_check(
            command + "\r", "", read_delay=0.01, print_response=True
        )

    def check_about(self):
        self.serial_connection.write_and_check(
            "about" + "\r", "=", read_delay=0.01, print_response=True
        )

    def set_distance(self, array_distance):
        # array distance in mm
        array_distance = str(int(array_distance))
        self.serial_connection.write_and_check(
            "sad." + array_distance + "\r",
            "Current array distance from sample is " + array_distance + "mm",
            read_delay=0.01,
            print_response=False,
        )

    def set_NA(self, NA):
        self.NA = NA
        NA = str(int(NA * 100))
        self.serial_connection.write_and_check(
            "na." + NA + "\r",
            "Current NA is 0." + NA,
            read_delay=0.01,
            print_response=False,
        )

    def set_color(self, color):
        # (r,g,b), 0-1
        r = int(255 * color[0])
        g = int(255 * color[1])
        b = int(255 * color[2])
        self.serial_connection.write_and_check(
            f"sc.{r}.{g}.{b}\r",
            f"Current color balance values are {r}.{g}.{b}",
            read_delay=0.01,
            print_response=False,
        )

    def set_brightness(self, brightness):
        # 0 to 100
        brightness = str(int(255 * (brightness / 100.0)))
        self.serial_connection.write_and_check(
            f"sb.{brightness}\r",
            f"Current brightness value is {brightness}.",
            read_delay=0.01,
            print_response=False,
        )

    def turn_on_bf(self):
        self.serial_connection.write_and_check(
            "bf\r", "-==-", read_delay=0.01, print_response=False
        )

    def turn_on_dpc(self, quadrant):
        self.serial_connection.write_and_check(
            f"dpc.{quadrant[0]}\r", "-==-", read_delay=0.01, print_response=False
        )

    def turn_on_df(self):
        self.serial_connection.write_and_check(
            "df\r", "-==-", read_delay=0.01, print_response=False
        )

    def set_illumination(self, illumination):
        self.illumination = illumination

    def clear(self):
        self.serial_connection.write_and_check(
            "x\r", "-==-", read_delay=0.01, print_response=False
        )

    def turn_on_illumination(self):
        if self.illumination is not None:
            self.serial_connection.write_and_check(
                f"{self.illumination}\r", "-==-", read_delay=0.01, print_response=False
            )
            time.sleep(self.turn_on_delay)

    def turn_off_illumination(self):
        self.clear()


class SciMicroscopyLEDArray_Simulation:
    """Wrapper for communicating with SciMicroscopy over serial"""

    def __init__(self, SN, array_distance=50, turn_on_delay=0.03):
        """
        Provide serial number
        """
        self.illumination = None
        self.NA = 0.5
        self.turn_on_delay = turn_on_delay

    def write(self, command):
        pass

    def check_about(self):
        pass

    def set_distance(self, array_distance):
        # array distance in mm
        pass

    def set_NA(self, NA):
        self.NA = NA

    def set_color(self, color):
        # (r,g,b), 0-1
        pass

    def set_brightness(self, brightness):
        # 0 to 100
        pass

    def turn_on_bf(self):
        pass

    def turn_on_dpc(self, quadrant):
        pass

    def turn_on_df(self):
        pass

    def set_illumination(self, illumination):
        self.illumination = illumination

    def clear(self):
        pass

    def turn_on_illumination(self):
        pass

    def turn_off_illumination(self):
        pass
