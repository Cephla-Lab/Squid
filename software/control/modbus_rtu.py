from __future__ import annotations

import logging
import struct
import threading
import time
from typing import Optional

import serial

logger = logging.getLogger(__name__)

# CRC-16 Modbus lookup table (polynomial: 0xA001, initial: 0xFFFF)
CRC16_TABLE = [
    0x0000,
    0xC0C1,
    0xC181,
    0x0140,
    0xC301,
    0x03C0,
    0x0280,
    0xC241,
    0xC601,
    0x06C0,
    0x0780,
    0xC741,
    0x0500,
    0xC5C1,
    0xC481,
    0x0440,
    0xCC01,
    0x0CC0,
    0x0D80,
    0xCD41,
    0x0F00,
    0xCFC1,
    0xCE81,
    0x0E40,
    0x0A00,
    0xCAC1,
    0xCB81,
    0x0B40,
    0xC901,
    0x09C0,
    0x0880,
    0xC841,
    0xD801,
    0x18C0,
    0x1980,
    0xD941,
    0x1B00,
    0xDBC1,
    0xDA81,
    0x1A40,
    0x1E00,
    0xDEC1,
    0xDF81,
    0x1F40,
    0xDD01,
    0x1DC0,
    0x1C80,
    0xDC41,
    0x1400,
    0xD4C1,
    0xD581,
    0x1540,
    0xD701,
    0x17C0,
    0x1680,
    0xD641,
    0xD201,
    0x12C0,
    0x1380,
    0xD341,
    0x1100,
    0xD1C1,
    0xD081,
    0x1040,
    0xF001,
    0x30C0,
    0x3180,
    0xF141,
    0x3300,
    0xF3C1,
    0xF281,
    0x3240,
    0x3600,
    0xF6C1,
    0xF781,
    0x3740,
    0xF501,
    0x35C0,
    0x3480,
    0xF441,
    0x3C00,
    0xFCC1,
    0xFD81,
    0x3D40,
    0xFF01,
    0x3FC0,
    0x3E80,
    0xFE41,
    0xFA01,
    0x3AC0,
    0x3B80,
    0xFB41,
    0x3900,
    0xF9C1,
    0xF881,
    0x3840,
    0x2800,
    0xE8C1,
    0xE981,
    0x2940,
    0xEB01,
    0x2BC0,
    0x2A80,
    0xEA41,
    0xEE01,
    0x2EC0,
    0x2F80,
    0xEF41,
    0x2D00,
    0xEDC1,
    0xEC81,
    0x2C40,
    0xE401,
    0x24C0,
    0x2580,
    0xE541,
    0x2700,
    0xE7C1,
    0xE681,
    0x2640,
    0x2200,
    0xE2C1,
    0xE381,
    0x2340,
    0xE101,
    0x21C0,
    0x2080,
    0xE041,
    0xA001,
    0x60C0,
    0x6180,
    0xA141,
    0x6300,
    0xA3C1,
    0xA281,
    0x6240,
    0x6600,
    0xA6C1,
    0xA781,
    0x6740,
    0xA501,
    0x65C0,
    0x6480,
    0xA441,
    0x6C00,
    0xACC1,
    0xAD81,
    0x6D40,
    0xAF01,
    0x6FC0,
    0x6E80,
    0xAE41,
    0xAA01,
    0x6AC0,
    0x6B80,
    0xAB41,
    0x6900,
    0xA9C1,
    0xA881,
    0x6840,
    0x7800,
    0xB8C1,
    0xB981,
    0x7940,
    0xBB01,
    0x7BC0,
    0x7A80,
    0xBA41,
    0xBE01,
    0x7EC0,
    0x7F80,
    0xBF41,
    0x7D00,
    0xBDC1,
    0xBC81,
    0x7C40,
    0xB401,
    0x74C0,
    0x7580,
    0xB541,
    0x7700,
    0xB7C1,
    0xB681,
    0x7640,
    0x7200,
    0xB2C1,
    0xB381,
    0x7340,
    0xB101,
    0x71C0,
    0x7080,
    0xB041,
    0x5000,
    0x90C1,
    0x9181,
    0x5140,
    0x9301,
    0x53C0,
    0x5280,
    0x9241,
    0x9601,
    0x56C0,
    0x5780,
    0x9741,
    0x5500,
    0x95C1,
    0x9481,
    0x5440,
    0x9C01,
    0x5CC0,
    0x5D80,
    0x9D41,
    0x5F00,
    0x9FC1,
    0x9E81,
    0x5E40,
    0x5A00,
    0x9AC1,
    0x9B81,
    0x5B40,
    0x9901,
    0x59C0,
    0x5880,
    0x9841,
    0x8801,
    0x48C0,
    0x4980,
    0x8941,
    0x4B00,
    0x8BC1,
    0x8A81,
    0x4A40,
    0x4E00,
    0x8EC1,
    0x8F81,
    0x4F40,
    0x8D01,
    0x4DC0,
    0x4C80,
    0x8C41,
    0x4400,
    0x84C1,
    0x8581,
    0x4540,
    0x8701,
    0x47C0,
    0x4680,
    0x8641,
    0x8201,
    0x42C0,
    0x4380,
    0x8341,
    0x4100,
    0x81C1,
    0x8081,
    0x4040,
]

FRAME_INTERVAL = 0.003


def calculate_crc(data: bytes | bytearray) -> int:
    crc = 0xFFFF
    for byte in data:
        crc = (crc >> 8) ^ CRC16_TABLE[(crc ^ byte) & 0xFF]
    return crc


def _append_crc(data: bytes | bytearray) -> bytes:
    crc = calculate_crc(data)
    return bytes(data) + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def _verify_crc(data: bytes | bytearray) -> bool:
    if len(data) < 3:
        return False
    payload = data[:-2]
    received_crc = data[-2] | (data[-1] << 8)
    return calculate_crc(payload) == received_crc


def build_read_registers_frame(slave_id: int, address: int, count: int) -> bytes:
    frame = struct.pack(">BBHH", slave_id, 0x03, address, count)
    return _append_crc(frame)


def build_write_register_frame(slave_id: int, address: int, value: int) -> bytes:
    frame = struct.pack(">BBHH", slave_id, 0x06, address, value)
    return _append_crc(frame)


def build_write_multiple_registers_frame(slave_id: int, address: int, values: list[int]) -> bytes:
    count = len(values)
    byte_count = count * 2
    frame = struct.pack(">BBHHB", slave_id, 0x10, address, count, byte_count)
    for v in values:
        frame += struct.pack(">H", v)
    return _append_crc(frame)


class ModbusError(Exception):
    def __init__(self, message: str, slave_id: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.slave_id = slave_id

    def __str__(self) -> str:
        return self.message


class ModbusRTUClient:
    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = 115200,
        timeout: float = 0.5,
        retries: int = 3,
    ):
        self._port = port
        self._baudrate = baudrate
        self._timeout = timeout
        self._retries = retries
        self._serial: Optional[serial.Serial] = None
        self._lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def connect(self, port: Optional[str] = None, baudrate: Optional[int] = None):
        if port is not None:
            self._port = port
        if baudrate is not None:
            self._baudrate = baudrate
        if self._port is None:
            raise ModbusError("No serial port specified")
        if self._serial is not None:
            self._serial.close()
            self._serial = None
        try:
            self._serial = serial.Serial(self._port, baudrate=self._baudrate, timeout=self._timeout)
        except (serial.SerialException, OSError) as e:
            raise ModbusError(str(e)) from e
        logger.info(f"Modbus RTU connected: {self._port}")

    def disconnect(self):
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None
            logger.info("Modbus RTU disconnected")

    def _require_connected(self):
        if not self.is_connected:
            raise ModbusError("Client is not connected")

    def read_register(self, slave_id: int, address: int) -> int:
        self._require_connected()
        frame = build_read_registers_frame(slave_id, address, 1)
        # Response: slave(1) + fc(1) + byte_count(1) + data(2) + crc(2) = 7
        response = self._send_receive(frame, expected_response_len=7)
        return (response[3] << 8) | response[4]

    def read_register_32bit(self, slave_id: int, address: int, signed: bool = False) -> int:
        self._require_connected()
        frame = build_read_registers_frame(slave_id, address, 2)
        # Response: slave(1) + fc(1) + byte_count(1) + data(4) + crc(2) = 9
        response = self._send_receive(frame, expected_response_len=9)
        high = (response[3] << 8) | response[4]
        low = (response[5] << 8) | response[6]
        value = (high << 16) | low
        if signed and value >= 0x80000000:
            value -= 0x100000000
        return value

    def write_register(self, slave_id: int, address: int, value: int):
        self._require_connected()
        frame = build_write_register_frame(slave_id, address, value)
        # Response: slave(1) + fc(1) + address(2) + value(2) + crc(2) = 8
        self._send_receive(frame, expected_response_len=8)

    def write_register_32bit(self, slave_id: int, address: int, value: int, signed: bool = False):
        self._require_connected()
        if signed and value < 0:
            value += 0x100000000
        high = (value >> 16) & 0xFFFF
        low = value & 0xFFFF
        frame = build_write_multiple_registers_frame(slave_id, address, [high, low])
        # Response: slave(1) + fc(1) + address(2) + quantity(2) + crc(2) = 8
        self._send_receive(frame, expected_response_len=8)

    def _send_receive(self, frame: bytes, expected_response_len: int) -> bytes:
        with self._lock:
            last_error: Optional[Exception] = None
            for attempt in range(self._retries + 1):
                try:
                    self._serial.reset_input_buffer()
                    self._serial.write(frame)
                    time.sleep(FRAME_INTERVAL)
                    response = self._serial.read(expected_response_len)
                except (serial.SerialException, OSError) as e:
                    last_error = ModbusError(str(e), slave_id=frame[0])
                    logger.warning(f"Modbus request failed (attempt {attempt + 1}/" f"{self._retries + 1}): {e}")
                    if attempt < self._retries:
                        time.sleep(FRAME_INTERVAL * 2)
                    continue

                # Exception responses are 5 bytes — check before incomplete check
                if len(response) >= 5 and (response[1] & 0x80) and _verify_crc(response[:5]):
                    exception_code = response[2]
                    raise ModbusError(
                        f"Modbus exception response: FC=0x{response[1]:02X}, " f"code=0x{exception_code:02X}",
                        slave_id=response[0],
                    )

                if len(response) < expected_response_len:
                    last_error = ModbusError(
                        f"Incomplete response: expected {expected_response_len} " f"bytes, got {len(response)}",
                        slave_id=frame[0],
                    )
                    logger.warning(
                        f"Modbus request failed (attempt {attempt + 1}/" f"{self._retries + 1}): {last_error}"
                    )
                    if attempt < self._retries:
                        time.sleep(FRAME_INTERVAL * 2)
                    continue

                if not _verify_crc(response):
                    last_error = ModbusError("CRC verification failed", slave_id=frame[0])
                    logger.warning(
                        f"Modbus request failed (attempt {attempt + 1}/" f"{self._retries + 1}): {last_error}"
                    )
                    if attempt < self._retries:
                        time.sleep(FRAME_INTERVAL * 2)
                    continue

                return response

            raise last_error

    def __enter__(self) -> "ModbusRTUClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()
