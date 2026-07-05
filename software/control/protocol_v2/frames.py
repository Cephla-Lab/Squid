"""Protocol v2 wire contract — Python mirror of
firmware/controller/src/protocol/frames.h.

frames.h is the single source of truth; test_protocol_v2.py parses it and
asserts every constant here matches. All multi-byte fields are little-endian
and packed (struct '<' format = standard sizes, no alignment padding).
"""

from . import cobs, crc16

# --- Sizing constants -----------------------------------------------------
K_MAX_FRAME = 512
K_MAX_PAYLOAD = 506
K_PROTOCOL_VERSION = 2

# --- FrameType ------------------------------------------------------------
REQUEST = 0x01
RESPONSE = 0x02
EVENT = 0x03

# --- FrameFlags -----------------------------------------------------------
FLAG_RETRY = 0x01

# --- ResponseStatus -------------------------------------------------------
STATUS_OK = 0
STATUS_ACCEPTED = 1
STATUS_REJECTED = 2
STATUS_FAILED = 3

# --- CommandType (system block) -------------------------------------------
HELLO = 0xF0
GET_INFO = 0xF1
GET_STATE = 0xF2
DIAG = 0xF3
ACK_ERROR = 0xF4
SET_WATCHDOG = 0xF5
HEARTBEAT = 0xF6
REBOOT_TO_BOOTLOADER = 0xFD
INITIALIZE = 0xFE
RESET = 0xFF

# --- ErrorCode ------------------------------------------------------------
ERR_NONE = 0x00
ERR_UNKNOWN_COMMAND = 0x10
ERR_INVALID_PARAMETER = 0x11
ERR_BAD_LENGTH = 0x12
ERR_RESOURCE_BUSY = 0x15
ERR_NO_SLOTS = 0x16
ERR_SYSTEM_IN_ERROR = 0x17
ERR_PACKET_CRC = 0x60
ERR_PACKET_LENGTH = 0x61

# --- Resource bits --------------------------------------------------------
RES_ILLUM_TTL = 1 << 16
RES_LED_MATRIX = 1 << 17
RES_CAM_TRIGGERS = 1 << 18
RES_GPIO = 1 << 19
RES_SEQUENCER = 1 << 20
RES_SYS_CONFIG = 1 << 21


def res_axis(n: int) -> int:
    return 1 << n


def res_dac(n: int) -> int:
    return 1 << (8 + n)


# --- Packed struct formats (little-endian, no padding) --------------------
FRAME_HEADER = "<BBBB"  # type, cmd_id, cmd_type, flags
SLOT = "<BBBB"  # cmd_id, cmd_type, state, progress
RING_ENTRY = "<BBBB"  # cmd_id, cmd_type, final_status, error_code
AXIS_STATE = "<iBBBB"  # pos, state, error, homed, rsv
SEQ_PROGRESS = "<HHBBIBB"  # layer, total, ch, total_ch, frames, err, det
HELLO_PAYLOAD = "<BBBBIII"  # proto, fw_major, fw_minor, reset_cause, nonce, boot_count, uptime
INFO_PAYLOAD = "<BBBB8sBBBBBBI"  # ...axis_driver[8]..., feature_bits
DIAG_PAYLOAD = "<IIIIIIIIIBBH"  # 9 counters, fault_count, page, rsv[2]
FAULT_ENTRY = "<IBBH"  # uptime_ms, code, detail, rsv

STANDARD_RESPONSE = (
    "<"
    + "BBBB"  # status, error_code, error_detail0, error_detail1
    + "BBBB" * 5  # slots[5]
    + "B"  # ring_head_seq
    + "BBBB" * 8  # ring[8]
    + "B"  # mode
    + "iBBBB" * 8  # axes[8]
    + "H" * 8  # dac_values[8]
    + "BB"  # illum_ttl_mask, led_pattern
    + "BB"  # cam_trigger_states, cam_ready_mask
    + "HHBBIBB"  # seq
    + "B"  # input_states
    + "BBB"  # fw_version_major, fw_version_minor, protocol_version
)


# --- Framing helpers ------------------------------------------------------
def encode_frame(ftype: int, cmd_id: int, cmd_type: int, flags: int, payload: bytes = b"") -> bytes:
    """Build one wire frame: COBS(header + payload + CRC-16 LE) + 0x00."""
    frame = bytes([ftype, cmd_id, cmd_type, flags]) + bytes(payload)
    crc = crc16.crc16_ccitt(frame)
    frame_crc = frame + bytes([crc & 0xFF, (crc >> 8) & 0xFF])
    return cobs.cobs_encode(frame_crc) + b"\x00"


def decode_frame(wire: bytes):
    """Parse one wire frame; return (type, cmd_id, cmd_type, flags, payload).

    Raises ValueError on malformed COBS, a runt frame, or a CRC mismatch.
    """
    if wire.endswith(b"\x00"):
        wire = wire[:-1]
    frame_crc = cobs.cobs_decode(wire)
    if len(frame_crc) < 6:  # header(4) + crc(2)
        raise ValueError("frame too short")
    frame = frame_crc[:-2]
    rx_crc = frame_crc[-2] | (frame_crc[-1] << 8)
    if rx_crc != crc16.crc16_ccitt(frame):
        raise ValueError("CRC mismatch")
    return frame[0], frame[1], frame[2], frame[3], bytes(frame[4:])
