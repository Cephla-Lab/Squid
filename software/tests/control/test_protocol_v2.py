"""Tests for the protocol-v2 Python codec/client and its agreement with the C
firmware wire contract.

Covers:
- crc16 / cobs mirror the firmware vectors (B1/B2),
- frames.py struct sizes match the wire contract,
- every frames.py constant equals the value in frames.h (regex parser, the
  FirmwareSimSerial pattern), and
- the C<->Python golden vectors round-trip byte-identically.
"""

import json
import re
import struct
from pathlib import Path

import pytest

from control.protocol_v2 import Client, Timeout, Transport, cobs, crc16, frames


def repo_root() -> Path:
    # software/tests/control/ -> repo root
    return Path(__file__).resolve().parent.parent.parent.parent


def frames_header_path() -> Path:
    return repo_root() / "firmware" / "controller" / "src" / "protocol" / "frames.h"


def golden_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "protocol_v2_golden.json"


# --- crc16 mirrors the C module -------------------------------------------


def test_crc16_check_value():
    assert crc16.crc16_ccitt(b"") == 0xFFFF
    assert crc16.crc16_ccitt(b"123456789") == 0x29B1  # CCITT-FALSE check value


def test_crc16_table_spot_checks():
    assert crc16.TABLE[0] == 0x0000
    assert crc16.TABLE[1] == 0x1021
    assert crc16.TABLE[255] == 0x1EF0


# --- cobs mirrors the C module --------------------------------------------


@pytest.mark.parametrize("length", [0, 1, 2, 253, 254, 255, 506])
def test_cobs_roundtrip(length):
    data = bytearray(((i % 255) + 1) for i in range(length))
    # Place zeros at head / tail / middle to exercise COBS boundaries.
    if length >= 1:
        data[0] = 0
    if length >= 2:
        data[-1] = 0
    if length >= 3:
        data[length // 2] = 0
    data = bytes(data)
    enc = cobs.cobs_encode(data)
    assert 0 not in enc  # no delimiter byte inside the encoded stream
    assert cobs.cobs_decode(enc) == data


def test_cobs_decode_rejects_embedded_zero():
    with pytest.raises(ValueError):
        cobs.cobs_decode(b"\x03\x41\x00")


def test_cobs_decode_rejects_truncated():
    with pytest.raises(ValueError):
        cobs.cobs_decode(b"\x05\x41\x42")


# --- frames.py struct sizes match the wire contract -----------------------


def test_struct_sizes():
    assert struct.calcsize(frames.FRAME_HEADER) == 4
    assert struct.calcsize(frames.SLOT) == 4
    assert struct.calcsize(frames.RING_ENTRY) == 4
    assert struct.calcsize(frames.AXIS_STATE) == 8
    assert struct.calcsize(frames.SEQ_PROGRESS) == 12
    assert struct.calcsize(frames.STANDARD_RESPONSE) == 158
    assert struct.calcsize(frames.HELLO_PAYLOAD) == 16
    assert struct.calcsize(frames.INFO_PAYLOAD) == 22
    assert struct.calcsize(frames.DIAG_PAYLOAD) == 40
    assert struct.calcsize(frames.FAULT_ENTRY) == 8


# --- frames.py constants equal frames.h (the single source of truth) ------


def parse_frames_header(path: Path):
    text = path.read_text()
    consts = {}
    # Enum members / simple constants: NAME = 0xHH or NAME = decimal (uppercase).
    for m in re.finditer(r"\b([A-Z][A-Z0-9_]+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*[,;]", text):
        consts[m.group(1)] = int(m.group(2), 0)
    # kCamelCase sizing constants: static const ... kMaxFrame = 512;
    for m in re.finditer(r"\b(k[A-Za-z0-9_]+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*;", text):
        consts[m.group(1)] = int(m.group(2), 0)
    # Resource bits: RES_X = uint32_t(1) << N;
    for m in re.finditer(r"\b(RES_[A-Z0-9_]+)\s*=\s*uint32_t\(1\)\s*<<\s*(\d+)", text):
        consts[m.group(1)] = 1 << int(m.group(2))
    # Layout sizes from static_assert(sizeof(TYPE) == N, ...).
    sizes = {}
    for m in re.finditer(r"sizeof\((\w+)\)\s*==\s*(\d+)", text):
        sizes[m.group(1)] = int(m.group(2))
    return consts, sizes


# Constant names shared verbatim between frames.h and frames.py.
_SAME_NAME_CONSTS = [
    "REQUEST",
    "RESPONSE",
    "EVENT",
    "FLAG_RETRY",
    "STATUS_OK",
    "STATUS_ACCEPTED",
    "STATUS_REJECTED",
    "STATUS_FAILED",
    "HELLO",
    "GET_INFO",
    "GET_STATE",
    "DIAG",
    "ACK_ERROR",
    "SET_WATCHDOG",
    "HEARTBEAT",
    "REBOOT_TO_BOOTLOADER",
    "INITIALIZE",
    "RESET",
    "ERR_NONE",
    "ERR_UNKNOWN_COMMAND",
    "ERR_INVALID_PARAMETER",
    "ERR_BAD_LENGTH",
    "ERR_RESOURCE_BUSY",
    "ERR_NO_SLOTS",
    "ERR_SYSTEM_IN_ERROR",
    "ERR_PACKET_CRC",
    "ERR_PACKET_LENGTH",
    "RES_ILLUM_TTL",
    "RES_LED_MATRIX",
    "RES_CAM_TRIGGERS",
    "RES_GPIO",
    "RES_SEQUENCER",
    "RES_SYS_CONFIG",
]


def test_frames_py_constants_match_header():
    header = frames_header_path()
    if not header.exists():
        pytest.skip(f"frames.h not found: {header}")
    consts, sizes = parse_frames_header(header)

    for name in _SAME_NAME_CONSTS:
        assert name in consts, f"{name} not found in frames.h"
        assert consts[name] == getattr(frames, name), f"{name} mismatch"

    # Renamed sizing constants (C kCamelCase -> Python UPPER_SNAKE).
    assert consts["kMaxFrame"] == frames.K_MAX_FRAME
    assert consts["kMaxPayload"] == frames.K_MAX_PAYLOAD
    assert consts["kProtocolVersion"] == frames.K_PROTOCOL_VERSION

    # Layout sizes agree with the header static_asserts.
    assert sizes["StandardResponse"] == struct.calcsize(frames.STANDARD_RESPONSE)
    assert sizes["HelloPayload"] == struct.calcsize(frames.HELLO_PAYLOAD)
    assert sizes["InfoPayload"] == struct.calcsize(frames.INFO_PAYLOAD)
    assert sizes["DiagPayload"] == struct.calcsize(frames.DIAG_PAYLOAD)
    assert sizes["FaultEntryWire"] == struct.calcsize(frames.FAULT_ENTRY)


def test_resource_bit_helpers():
    assert frames.res_axis(0) == 1
    assert frames.res_axis(7) == 0x80
    assert frames.res_dac(0) == 0x100
    assert frames.RES_ILLUM_TTL == (1 << 16)


# --- C<->Python golden vectors round-trip byte-identically ----------------


def load_golden():
    path = golden_path()
    if not path.exists():
        pytest.skip(f"golden vectors not found: {path}")
    return json.loads(path.read_text())


def test_golden_vectors_encode_and_decode():
    cases = load_golden()
    assert len(cases) > 0
    for c in cases:
        payload = bytes.fromhex(c["payload"])
        wire = bytes.fromhex(c["wire"])
        decoded = bytes.fromhex(c["decoded"])

        built = frames.encode_frame(c["type"], c["cmd_id"], c["cmd_type"], c["flags"], payload)
        assert built == wire, f"{c['name']}: encode mismatch"

        ftype, cmd_id, cmd_type, flags, pl = frames.decode_frame(wire)
        assert (ftype, cmd_id, cmd_type, flags) == (c["type"], c["cmd_id"], c["cmd_type"], c["flags"])
        assert pl == payload, f"{c['name']}: decoded payload mismatch"

        # The COBS body (frame + CRC) matches the recorded decoded bytes.
        assert cobs.cobs_decode(wire[:-1]) == decoded, f"{c['name']}: decoded bytes mismatch"


# --- Client request/response ----------------------------------------------


class LoopTransport(Transport):
    def __init__(self):
        self.written = []
        self.responses = []

    def write(self, data):
        self.written.append(data)

    def read_frame(self, timeout):
        if not self.responses:
            raise Timeout("no response queued")
        return self.responses.pop(0)


def test_client_request_matches_response():
    t = LoopTransport()
    t.responses.append(frames.encode_frame(frames.RESPONSE, 1, frames.GET_STATE, 0, b"\x00\x00\x00\x00"))
    c = Client(t)
    resp = c.request(frames.GET_STATE, b"", cmd_id=1)

    sent = frames.decode_frame(t.written[0])
    assert sent[:4] == (frames.REQUEST, 1, frames.GET_STATE, 0)
    assert resp.cmd_id == 1
    assert resp.cmd_type == frames.GET_STATE
    assert resp.status == frames.STATUS_OK


def test_client_retry_sets_flag():
    t = LoopTransport()
    t.responses.append(frames.encode_frame(frames.RESPONSE, 2, frames.DIAG, 0, b"\x00\x00\x00\x00"))
    c = Client(t)
    c.request(frames.DIAG, b"\x00", retry=True, cmd_id=2)

    sent = frames.decode_frame(t.written[0])
    assert sent[3] & frames.FLAG_RETRY


def test_client_skips_unmatched_then_matches():
    t = LoopTransport()
    # A stale response (wrong cmd_id) precedes the real one.
    t.responses.append(frames.encode_frame(frames.RESPONSE, 99, frames.GET_STATE, 0, b"\x00\x00\x00\x00"))
    t.responses.append(frames.encode_frame(frames.RESPONSE, 3, frames.GET_STATE, 0, b"\x00\x00\x00\x00"))
    c = Client(t)
    resp = c.request(frames.GET_STATE, cmd_id=3)
    assert resp.cmd_id == 3


def test_client_timeout():
    t = LoopTransport()  # nothing queued
    c = Client(t)
    with pytest.raises(Timeout):
        c.request(frames.GET_STATE, cmd_id=4)
