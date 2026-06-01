"""Pure-Python tests for Modbus RTU CRC, frame builders, and response parsing."""

from __future__ import annotations

import pytest

from control.modbus_rtu import (
    _verify_crc,
    build_read_input_registers_frame,
    build_read_registers_frame,
    build_write_multiple_registers_frame,
    build_write_register_frame,
    calculate_crc,
)


def test_crc_known_vectors():
    # Slave 0x01, FC 0x03, addr 0x0000, count 0x0001 -> CRC 0x840A (LE)
    assert calculate_crc(b"\x01\x03\x00\x00\x00\x01") == 0x0A84
    # Slave 0x11, FC 0x06, addr 0x0001, value 0x0003 -> CRC 0x9A9B (LE)
    assert calculate_crc(b"\x11\x06\x00\x01\x00\x03") == 0x9B9A


def test_verify_crc_round_trip():
    body = b"\x01\x03\x02\x00\x06"
    crc = calculate_crc(body)
    frame = body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])
    assert _verify_crc(frame) is True

    # Single-bit flip in the payload -> CRC fails.
    corrupted = bytearray(frame)
    corrupted[2] ^= 0x01
    assert _verify_crc(bytes(corrupted)) is False


def test_verify_crc_rejects_short_frames():
    assert _verify_crc(b"") is False
    assert _verify_crc(b"\x01\x03\x00") is False  # too short to carry CRC


def test_build_read_holding_registers_frame():
    frame = build_read_registers_frame(slave_id=1, address=0x0021, count=2)
    assert len(frame) == 8
    assert frame[0] == 0x01  # slave
    assert frame[1] == 0x03  # FC=read holding
    assert (frame[2] << 8) | frame[3] == 0x0021
    assert (frame[4] << 8) | frame[5] == 0x0002
    assert _verify_crc(frame)


def test_build_read_input_registers_frame_uses_fc4():
    frame = build_read_input_registers_frame(slave_id=1, address=0x001F, count=1)
    assert frame[1] == 0x04  # FC=read input
    assert _verify_crc(frame)


def test_build_write_single_register_frame():
    frame = build_write_register_frame(slave_id=2, address=0x0051, value=0x000F)
    assert len(frame) == 8
    assert frame[0] == 0x02
    assert frame[1] == 0x06  # FC=write single
    assert (frame[2] << 8) | frame[3] == 0x0051
    assert (frame[4] << 8) | frame[5] == 0x000F
    assert _verify_crc(frame)


def test_build_write_multiple_registers_frame():
    frame = build_write_multiple_registers_frame(slave_id=1, address=0x0053, values=[0x0001, 0x9CB8])
    # slave(1) + fc(1) + addr(2) + qty(2) + byte_count(1) + 2*data(2) + crc(2) = 13
    assert len(frame) == 13
    assert frame[1] == 0x10  # FC=write multiple
    assert (frame[2] << 8) | frame[3] == 0x0053
    assert (frame[4] << 8) | frame[5] == 2  # quantity
    assert frame[6] == 4  # byte count
    assert _verify_crc(frame)


def test_build_write_multiple_with_empty_values_still_produces_valid_frame():
    # Builder doesn't validate count >= 1; the slave is expected to reject
    # quantity=0 with a Modbus exception (not a local CRC failure).
    frame = build_write_multiple_registers_frame(slave_id=1, address=0x0000, values=[])
    assert _verify_crc(frame)
    assert (frame[4] << 8) | frame[5] == 0
    assert frame[6] == 0


@pytest.mark.parametrize(
    "value",
    [0x0000, 0x0001, 0x7FFF, 0x8000, 0xFFFF],
)
def test_write_register_round_trip(value):
    """Build a write frame, then re-extract the value from the frame bytes."""
    frame = build_write_register_frame(slave_id=1, address=0x0010, value=value)
    extracted = (frame[4] << 8) | frame[5]
    assert extracted == value
    assert _verify_crc(frame)
