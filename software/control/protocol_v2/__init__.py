"""Protocol v2 host-side codec and client.

Mirrors firmware/controller/src/protocol/ (crc16, cobs, frames) byte-for-byte;
cross-language agreement is enforced by the golden vectors and the frames.h
parser test in software/tests/control/test_protocol_v2.py.
"""

from . import cobs, crc16, frames
from .client import Client, Response, Timeout, Transport
from .cobs import cobs_decode, cobs_encode
from .crc16 import crc16_ccitt
from .frames import decode_frame, encode_frame

__all__ = [
    "cobs",
    "crc16",
    "frames",
    "Client",
    "Response",
    "Timeout",
    "Transport",
    "cobs_decode",
    "cobs_encode",
    "crc16_ccitt",
    "decode_frame",
    "encode_frame",
]
