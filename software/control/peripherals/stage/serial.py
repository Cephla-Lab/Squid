"""
Serial communication abstractions for Cephla microcontroller.
"""
import abc
import struct
import sys
import threading
import time
from abc import abstractmethod

import serial
import serial.tools.list_ports
from crc import CrcCalculator, Crc8
from serial.serialutil import SerialException

import squid.logging
from control._def import (
    AXIS,
    BIT_POS_JOYSTICK_BUTTON,
    BIT_POS_SWITCH,
    CMD_EXECUTION_STATUS,
    CMD_SET,
)

_log = squid.logging.get_logger("microcontroller.serial")


def payload_to_int(payload, number_of_bytes) -> int:
    """Convert a byte payload to a signed integer."""
    signed = 0
    for i in range(number_of_bytes):
        signed = signed + int(payload[i]) * (256 ** (number_of_bytes - 1 - i))
    if signed >= 256**number_of_bytes / 2:
        signed = signed - 256**number_of_bytes
    return int(signed)


class AbstractCephlaMicroSerial(abc.ABC):

    def __init__(self):
        self._log = squid.logging.get_logger(self.__class__.__name__)

    @abstractmethod
    def close(self) -> None:
        """
        A noop if already closed.  Can throw an IOError for close related errors or invalid states.
        """
        pass

    @abstractmethod
    def reset_input_buffer(self) -> bool:
        """
        Reset the input buffer of the serial port.
        """
        pass

    @abstractmethod
    def write(self, data: bytearray, reconnect_tries: int = 0) -> int:
        """
        This must raise an IOError or OSError on any io issues, or ValueError if data is not sendable.

        If reconnect_tries > 0, this will attempt to reconnect if the device isn't connected (up to the number of tries
        specified, and with exponential backoff.  This means that if you specific reconnect_tries=5 and it needs to
        try 5 times, it may hang for a while!)
        """
        pass

    @abstractmethod
    def read(self, count: int = 1, reconnect_tries: int = 0) -> bytes:
        """
        Read up to count bytes, and return them as bytes.  Can throw IOError or OSError if the device is in an invalid
        state, or ValueError if count is not valid.

        If reconnect_tries > 0, this will attempt to reconnect if the device isn't connected (up to the number of tries
        specified, and with exponential backoff.  This means that if you specific reconnect_tries=5 and it needs to
        try 5 times, it may hang for a while!)
        """
        pass

    @abstractmethod
    def bytes_available(self) -> int:
        """
        Returns the number of bytes in the read buffer ready for immediate reading.

        If the device is no longer connected or is in an invalid state, this might be None or might throw IOError.
        """
        pass

    @abstractmethod
    def is_open(self) -> bool:
        """
        Returns true if the device is open and ready for read/writing, false otherwise.
        """
        pass

    @abstractmethod
    def reconnect(self, attempts: int) -> bool:
        """
        Attempts to reconnect, if needed, and returns true if successful.  If already connected, this is a noop (and will return True)
        """
        pass


class SimSerial(AbstractCephlaMicroSerial):
    @staticmethod
    def response_bytes_for(command_id, execution_status, x, y, z, theta, joystick_button, switch) -> bytes:
        """
        - command ID (1 byte)
        - execution status (1 byte)
        - X pos (4 bytes)
        - Y pos (4 bytes)
        - Z pos (4 bytes)
        - Theta (4 bytes)
        - buttons and switches (1 byte)
        - reserved (4 bytes)
        - CRC (1 byte)
        """
        crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)

        button_state = joystick_button << BIT_POS_JOYSTICK_BUTTON | switch << BIT_POS_SWITCH
        reserved_state = 0  # This is just filler for the 4 reserved bytes.
        response = bytearray(
            struct.pack(">BBiiiiBi", command_id, execution_status, x, y, z, theta, button_state, reserved_state)
        )
        response.append(crc_calculator.calculate_checksum(response))
        return bytes(response)

    def __init__(self):
        super().__init__()
        # All the public methods must hold this to modify internal state.  Any _ prefixed members are
        # assumed to be called from a context that already holds the lock
        self._update_lock = threading.Lock()
        self._in_waiting = 0
        self.response_buffer = []

        self.x = 0
        self.y = 0
        self.z = 0
        self.theta = 0
        self.joystick_button = False
        self.switch = False

        self._closed = False

    @staticmethod
    def unpack_position(pos_bytes):
        return payload_to_int(pos_bytes, len(pos_bytes))

    def _respond_to(self, write_bytes):
        # NOTE: As we need more and more microcontroller simulator functionality, add
        # CMD_SET handlers here.  Prefer this over adding checks for simulated mode in
        # the Microcontroller!
        command_byte = write_bytes[1]
        # If this is a position related command, these are our position bytes.
        position_bytes = write_bytes[2:6]
        if command_byte == CMD_SET.MOVE_X:
            self.x += self.unpack_position(position_bytes)
        elif command_byte == CMD_SET.MOVE_Y:
            self.y += self.unpack_position(position_bytes)
        elif command_byte == CMD_SET.MOVE_Z:
            self.z += self.unpack_position(position_bytes)
        elif command_byte == CMD_SET.MOVE_THETA:
            self.theta += self.unpack_position(position_bytes)
        elif command_byte == CMD_SET.MOVETO_X:
            self.x = self.unpack_position(position_bytes)
        elif command_byte == CMD_SET.MOVETO_Y:
            self.y = self.unpack_position(position_bytes)
        elif command_byte == CMD_SET.MOVETO_Z:
            self.z = self.unpack_position(position_bytes)
        elif command_byte == CMD_SET.HOME_OR_ZERO:
            axis = write_bytes[2]
            # NOTE: write_bytes[3] might indicate that we only want to "ZERO", but
            # in the simulated case zeroing is the same as homing.  So don't check
            # that here.  If we want to simulate the homing motion in the future
            # we'd need to do that here.
            if axis == AXIS.X:
                self.x = 0
            elif axis == AXIS.Y:
                self.y = 0
            elif axis == AXIS.Z:
                self.z = 0
            elif axis == AXIS.THETA:
                self.theta = 0
            elif axis == AXIS.XY:
                self.x = 0
                self.y = 0

        self.response_buffer.extend(
            SimSerial.response_bytes_for(
                write_bytes[0],
                CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS,
                self.x,
                self.y,
                self.z,
                self.theta,
                self.joystick_button,
                self.switch,
            )
        )

        self._update_internal_state()

    def _update_internal_state(self, clear_buffer: bool = False):
        if clear_buffer:
            self.response_buffer.clear()

        self._in_waiting = len(self.response_buffer)

    def close(self):
        with self._update_lock:
            self._closed = True
            self._update_internal_state(clear_buffer=True)

    def reset_input_buffer(self) -> bool:
        with self._update_lock:
            self._update_internal_state(clear_buffer=True)
            return True

    def write(self, data: bytearray, reconnect_tries: int = 0) -> int:
        # Reconnect takes the lock and checks closed too, so let it handle locking for reconnect
        if self._closed:
            if not self.reconnect(reconnect_tries):
                raise IOError("Closed")
        with self._update_lock:
            self._respond_to(data)
            return len(data)

    def read(self, count=1, reconnect_tries: int = 0) -> bytes:
        # Reconnect takes the lock and checks closed too, so let it handle locking for reconnect
        if self._closed:
            if not self.reconnect(reconnect_tries):
                raise IOError("Closed")

        with self._update_lock:
            response = bytearray()
            for i in range(count):
                if not len(self.response_buffer):
                    break
                response.append(self.response_buffer.pop(0))

            self._update_internal_state()
            return response

    def bytes_available(self) -> int:
        with self._update_lock:
            self._update_internal_state()
            return self._in_waiting

    def is_open(self) -> bool:
        with self._update_lock:
            return not self._closed

    def reconnect(self, attempts: int) -> bool:
        with self._update_lock:
            self._update_internal_state()
            if not attempts:
                # open takes the lock, so we can't use it.
                return not self._closed

            if self._closed:
                self._log.warning("Reconnect required, succeeded.")
                self._update_internal_state(clear_buffer=True)
                self._closed = False

        return True


class MicrocontrollerSerial(AbstractCephlaMicroSerial):
    INITIAL_RECONNECT_INTERVAL = 0.5

    @staticmethod
    def exponential_backoff_time(attempt_index: int, initial_interval: float) -> float:
        """
        This is the time to sleep before you attempt the attempt_index attempt, where attempt_index is 0 indexed.
        EG:
          time.sleep(exponential_backoff_time(0, 0.5))
          attempt_0()
          time.sleep(exponential_backoff_time(1, 0.5))
          attempt_1()

        will have a 0 sleep before attempt_0(), and 0.5 before attempt_1()
        """
        if attempt_index <= 0:
            return 0.0
        else:
            return initial_interval * 2**attempt_index

    def __init__(self, port: str, baudrate: int):
        super().__init__()
        self._port = port
        self._baudrate = baudrate
        self._serial = serial.Serial(port, baudrate)

    def __del__(self):
        self.close()

    def close(self) -> None:
        return self._serial.close()

    def reset_input_buffer(self) -> bool:
        try:
            self._serial.reset_input_buffer()
            return True
        except Exception as e:
            self._log.exception(f"Failed to clear input buffer: {e}")
            return False

    def write(self, data: bytearray, reconnect_tries: int = 0) -> int:
        # the is_open attribute is unreliable - if a device just recently dropped out, it may not be up to date.
        # So we just try to write, and if we get an OS error we try to write again but without retrying
        try:
            return self._serial.write(data)
        except (IOError, OSError, SerialException) as e:
            if reconnect_tries > 0:
                if not self.reconnect(reconnect_tries):
                    raise
                return self.write(data, reconnect_tries=0)
            else:
                raise

    def read(self, count: int = 1, reconnect_tries: int = 0) -> bytes:
        # the is_open attribute is unreliable - if a device just recently dropped out, it may not be up to date.
        # So we just try to read, and if we get an OS error we try to read again but without retrying
        try:
            return self._serial.read(count)
        except (IOError, OSError, SerialException) as e:
            if reconnect_tries > 0:
                if not self.reconnect(reconnect_tries):
                    raise
                self.read(count, reconnect_tries=0)
            else:
                raise

    def bytes_available(self) -> int:
        if not self.is_open():
            return 0

        return self._serial.in_waiting

    def is_open(self) -> bool:
        try:
            if not self._serial.is_open:
                return False
            # pyserial is_open is sortof useless - it doesn't force a check to see if the device is still valid.
            # but the in_waiting does an ioctl to check for the bytes in the read buffer.  This is a system call, so
            # not the best from a performance perspective, but we are operating with 2 mega baud and a system call
            # is insignificant on that timescale!
            bytes_avail = self._serial.in_waiting

            return True
        except OSError:
            return False

    def reconnect(self, attempts: int) -> bool:
        self._log.debug(f"Attempting reconnect to {self._serial.port}.  With max of {attempts} attempts.")
        for i in range(attempts):
            this_interval = MicrocontrollerSerial.exponential_backoff_time(
                i, MicrocontrollerSerial.INITIAL_RECONNECT_INTERVAL
            )
            if not self.is_open():
                time.sleep(this_interval)
                try:
                    try:
                        self._serial.close()
                    except OSError:
                        pass
                    self._serial = serial.Serial(port=self._port, baudrate=self._baudrate)
                except (IOError, OSError, SerialException) as se:
                    if i + 1 == attempts:
                        self._log.error(
                            f"Reconnect to {self._serial.port} failed after {attempts} attempts. Last reconnect interval was {this_interval} [s]",
                            exc_info=se,
                        )
                        # This is the last time around the loop, so it'll exit and return self.is_open() as false after this.
                    else:
                        self._log.warning(
                            f"Couldn't reconnect serial={self._serial.port} @ baud={self._serial.baudrate}.  Attempt {i + 1}/{attempts}."
                        )
            else:
                break

        # We print warnings/errors in the loop above, so here we can just return the result of our best efforts!
        return self.is_open()


def get_microcontroller_serial_device(
    version=None, sn=None, baudrate=2000000, simulated=False
) -> AbstractCephlaMicroSerial:
    if simulated:
        return SimSerial()
    else:
        _log.info(f"Getting serial device for microcontroller {version=}")
        if version == "Arduino Due":
            controller_ports = [
                p.device for p in serial.tools.list_ports.comports() if "Arduino Due" == p.description
            ]  # autodetect - based on Deepak's code
        else:
            if sn is not None:
                controller_ports = [p.device for p in serial.tools.list_ports.comports() if sn == p.serial_number]
            else:
                if sys.platform == "win32":
                    controller_ports = [
                        p.device for p in serial.tools.list_ports.comports() if p.manufacturer == "Microsoft"
                    ]
                else:
                    controller_ports = [
                        p.device for p in serial.tools.list_ports.comports() if p.manufacturer == "Teensyduino"
                    ]

        if not controller_ports:
            raise IOError("no controller found for serial device")
        if len(controller_ports) > 1:
            _log.warning("multiple controller found - using the first")

        return MicrocontrollerSerial(controller_ports[0], baudrate)
