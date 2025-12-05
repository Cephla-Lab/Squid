import serial
from serial.tools import list_ports
import time

import squid.logging

log = squid.logging.get_logger(__name__)


class SerialDevice:
    """
    General wrapper for serial devices, with
    automating device finding based on VID/PID
    or serial number.
    """

    def __init__(self, port=None, VID=None, PID=None, SN=None, baudrate=9600, read_timeout=0.1, **kwargs):
        # Initialize the serial connection
        self.port = port
        self.VID = VID
        self.PID = PID
        self.SN = SN

        self.baudrate = baudrate
        self.read_timeout = read_timeout
        self.serial_kwargs = kwargs

        self.serial = None

        if VID is not None and PID is not None:
            for d in list_ports.comports():
                if d.vid == VID and d.pid == PID:
                    self.port = d.device
                    break
        if SN is not None:
            for d in list_ports.comports():
                if d.serial_number == SN:
                    self.port = d.device
                    break

        if self.port is not None:
            self.serial = serial.Serial(self.port, baudrate=baudrate, timeout=read_timeout, **kwargs)

    def open_ser(self, SN=None, VID=None, PID=None, baudrate=None, read_timeout=None, **kwargs):
        if self.serial is not None and not self.serial.is_open:
            self.serial.open()

        if SN is None:
            SN = self.SN

        if VID is None:
            VID = self.VID

        if PID is None:
            PID = self.PID

        if baudrate is None:
            baudrate = self.baudrate

        if read_timeout is None:
            read_timeout = self.read_timeout

        for k in self.serial_kwargs.keys():
            if k not in kwargs:
                kwargs[k] = self.serial_kwargs[k]

        if self.serial is None:
            if VID is not None and PID is not None:
                for d in list_ports.comports():
                    if d.vid == VID and d.pid == PID:
                        self.port = d.device
                        break
            if SN is not None:
                for d in list_ports.comports():
                    if d.serial_number == SN:
                        self.port = d.device
                        break
            if self.port is not None:
                self.serial = serial.Serial(self.port, **kwargs)

    def write_and_check(
        self,
        command,
        expected_response,
        read_delay=0.1,
        max_attempts=5,
        attempt_delay=1,
        check_prefix=True,
        print_response=False,
    ):
        # Write a command and check the response
        for attempt in range(max_attempts):
            self.serial.write(command.encode())
            time.sleep(read_delay)  # Wait for the command to be sent/executed

            response = self.serial.readline().decode().strip()
            if print_response:
                log.info(response)

            # flush the input buffer
            while self.serial.in_waiting:
                if print_response:
                    log.info(self.serial.readline().decode().strip())
                else:
                    self.serial.readline().decode().strip()

            # check response
            if response == expected_response:
                return response
            else:
                log.warning(response)

            # check prefix if the full response does not match
            if check_prefix:
                if response.startswith(expected_response):
                    return response
            else:
                time.sleep(attempt_delay)  # Wait before retrying

        raise SerialDeviceError("Max attempts reached without receiving expected response.")

    def write_and_read(self, command, read_delay=0.1, max_attempts=3, attempt_delay=1):
        for attempt in range(max_attempts):
            self.serial.write(command.encode())
            time.sleep(read_delay)  # Wait for the command to be sent
            response = self.serial.readline().decode().strip()
            if response:
                return response
            else:
                time.sleep(attempt_delay)  # Wait before retrying

        raise SerialDeviceError("Max attempts reached without receiving response.")

    def write(self, command):
        self.serial.write(command.encode())

    def close(self):
        # Close the serial connection
        self.serial.close()


class SerialDeviceError(RuntimeError):
    pass
