/**
 * Protocol v2 wire contract — the SINGLE SOURCE OF TRUTH for frame layout.
 *
 * All multi-byte fields are little-endian. Structs are byte-packed so their
 * on-wire size is alignment-independent and identical on x86_64 (native tests)
 * and ARM Cortex-M7 (Teensy 4.1). The mirrored Python codec
 * (software/control/protocol_v2/frames.py) and the golden vectors parse/mirror
 * THIS header; never redefine these constants by hand elsewhere.
 *
 * Frame (decoded, before COBS):
 *   [type u8][cmd_id u8][cmd_type u8][flags u8][payload...][crc16 LE]
 * Wire: COBS_encode(frame) + 0x00.  Max decoded frame 512 B -> max payload 506 B.
 *
 * Pure declarations only — no functions with side effects. No Arduino deps.
 */

#ifndef PROTOCOL_FRAMES_H
#define PROTOCOL_FRAMES_H

#include <stddef.h>
#include <stdint.h>

#if defined(__GNUC__) || defined(__clang__)
#define PROTO_PACKED __attribute__((packed))
#else
#define PROTO_PACKED
#endif

namespace protocol {

// --- Sizing constants -----------------------------------------------------

static const size_t kMaxFrame = 512;          // max decoded frame (before COBS)
static const size_t kMaxPayload = 506;         // kMaxFrame - sizeof(FrameHeader) - 2 (crc16)
static const uint8_t kProtocolVersion = 2;

// --- Enumerations (unscoped so members are usable as protocol::HELLO) -----

enum FrameType : uint8_t {
    REQUEST = 0x01,
    RESPONSE = 0x02,
    EVENT = 0x03,  // reserved, unused in v2.0
};

enum FrameFlags : uint8_t {
    FLAG_RETRY = 0x01,  // bit0: re-send of the same cmd_id (dedup via slots+ring)
};

enum ResponseStatus : uint8_t {
    STATUS_OK = 0,
    STATUS_ACCEPTED = 1,
    STATUS_REJECTED = 2,
    STATUS_FAILED = 3,
};

// CommandType blocks (values not in the system block are Phase C/D):
//   0x01-0x0F motion | 0x10-0x1F axis config | 0x20-0x2F output/GPIO
//   0x30-0x3F illumination | 0x40-0x4F camera | 0x50-0x5F sequencer
// Phase B implements the system block only.
enum CommandType : uint8_t {
    HELLO = 0xF0,
    GET_INFO = 0xF1,
    GET_STATE = 0xF2,
    DIAG = 0xF3,
    // Reserved now (declared for code completeness; handlers land later):
    ACK_ERROR = 0xF4,
    SET_WATCHDOG = 0xF5,
    HEARTBEAT = 0xF6,
    REBOOT_TO_BOOTLOADER = 0xFD,
    INITIALIZE = 0xFE,
    RESET = 0xFF,
};

enum ErrorCode : uint8_t {
    ERR_NONE = 0x00,
    // 0x10-0x2F rejection
    ERR_UNKNOWN_COMMAND = 0x10,
    ERR_INVALID_PARAMETER = 0x11,
    ERR_BAD_LENGTH = 0x12,
    ERR_RESOURCE_BUSY = 0x15,
    ERR_NO_SLOTS = 0x16,
    ERR_SYSTEM_IN_ERROR = 0x17,
    // 0x40-0x5F hardware faults (Phase C/D)
    // 0x60-0x6F comm
    ERR_PACKET_CRC = 0x60,
    ERR_PACKET_LENGTH = 0x61,
};

// --- Resource bits (u32 claim mask, design section 4.5) -------------------

constexpr uint32_t res_axis(uint8_t n) { return uint32_t(1) << n; }         // n in 0..7
constexpr uint32_t res_dac(uint8_t n) { return uint32_t(1) << (8 + n); }    // n in 0..7
static const uint32_t RES_ILLUM_TTL = uint32_t(1) << 16;
static const uint32_t RES_LED_MATRIX = uint32_t(1) << 17;
static const uint32_t RES_CAM_TRIGGERS = uint32_t(1) << 18;
static const uint32_t RES_GPIO = uint32_t(1) << 19;
static const uint32_t RES_SEQUENCER = uint32_t(1) << 20;
static const uint32_t RES_SYS_CONFIG = uint32_t(1) << 21;

// --- Packed wire structs --------------------------------------------------

struct PROTO_PACKED FrameHeader {
    uint8_t type;      // FrameType
    uint8_t cmd_id;
    uint8_t cmd_type;  // CommandType
    uint8_t flags;     // FrameFlags
};

struct PROTO_PACKED Slot {
    uint8_t cmd_id;
    uint8_t cmd_type;
    uint8_t state;
    uint8_t progress;
};

struct PROTO_PACKED RingEntry {
    uint8_t cmd_id;
    uint8_t cmd_type;
    uint8_t final_status;
    uint8_t error_code;
};

struct PROTO_PACKED AxisStateWire {
    int32_t pos;
    uint8_t state;
    uint8_t error;
    uint8_t homed;
    uint8_t rsv;
};

struct PROTO_PACKED SeqProgressWire {
    uint16_t layer;
    uint16_t total;
    uint8_t ch;
    uint8_t total_ch;
    uint32_t frames;
    uint8_t err;
    uint8_t det;
};

// Fixed prefix of EVERY response payload (158 bytes).
struct PROTO_PACKED StandardResponse {
    uint8_t status;         // ResponseStatus
    uint8_t error_code;     // ErrorCode
    uint8_t error_detail0;
    uint8_t error_detail1;
    Slot slots[5];
    uint8_t ring_head_seq;
    RingEntry ring[8];
    uint8_t mode;
    AxisStateWire axes[8];
    uint16_t dac_values[8];
    uint8_t illum_ttl_mask;
    uint8_t led_pattern;
    uint8_t cam_trigger_states;
    uint8_t cam_ready_mask;
    SeqProgressWire seq;
    uint8_t input_states;   // bit0 interlock_ok, bit1 power_good, bit2 joystick_btn
    uint8_t fw_version_major;
    uint8_t fw_version_minor;
    uint8_t protocol_version;
};

// Appended after StandardResponse in a HELLO response (16 bytes).
struct PROTO_PACKED HelloPayload {
    uint8_t protocol_version;
    uint8_t fw_major;
    uint8_t fw_minor;
    uint8_t reset_cause;
    uint32_t session_nonce;
    uint32_t boot_count;
    uint32_t uptime_ms;
};

// GET_INFO descriptor, appended after StandardResponse (22 bytes).
struct PROTO_PACKED InfoPayload {
    uint8_t board_id;
    uint8_t board_rev;
    uint8_t mcu_id;
    uint8_t n_axes;
    uint8_t axis_driver[8];  // 0=none, 1=TMC2660, 2=TMC2240
    uint8_t n_dacs;
    uint8_t n_illum_ttl;
    uint8_t has_led_matrix;
    uint8_t n_cam_triggers;
    uint8_t n_ready_inputs;
    uint8_t max_program_channels;
    uint32_t feature_bits;
};

// DIAG page 0 counters, appended after StandardResponse (40 bytes).
struct PROTO_PACKED DiagPayload {
    uint32_t loop_max_us;
    uint32_t isr_max_us;
    uint32_t crc_err;
    uint32_t resync;
    uint32_t rx_overflow;
    uint32_t tx_drop;
    uint32_t stack_free_min;
    uint32_t uptime_ms;
    uint32_t boot_count;
    uint8_t fault_count;
    uint8_t page;
    uint8_t rsv[2];
};

// DIAG page N>=1 fault-ring entry (8 bytes); up to 16 per page.
struct PROTO_PACKED FaultEntryWire {
    uint32_t uptime_ms;
    uint8_t code;
    uint8_t detail;
    uint16_t rsv;
};

// --- Layout guarantees (enforced on EVERY target that includes this header:
// native tests, teensy41, teensy41_boardv2, CI). Packed structs keep these
// identical on x86_64 and ARM; any drift breaks the build here, at the source
// of truth, rather than only in the native test.
static_assert(sizeof(FrameHeader) == 4, "FrameHeader must be 4 bytes");
static_assert(sizeof(Slot) == 4, "Slot must be 4 bytes");
static_assert(sizeof(RingEntry) == 4, "RingEntry must be 4 bytes");
static_assert(sizeof(AxisStateWire) == 8, "AxisStateWire must be 8 bytes");
static_assert(sizeof(SeqProgressWire) == 12, "SeqProgressWire must be 12 bytes");
static_assert(sizeof(StandardResponse) == 158, "StandardResponse must be 158 bytes");
static_assert(sizeof(HelloPayload) == 16, "HelloPayload must be 16 bytes");
static_assert(sizeof(InfoPayload) == 22, "InfoPayload must be 22 bytes");
static_assert(sizeof(DiagPayload) == 40, "DiagPayload must be 40 bytes");
static_assert(sizeof(FaultEntryWire) == 8, "FaultEntryWire must be 8 bytes");
static_assert(kMaxFrame == sizeof(FrameHeader) + kMaxPayload + 2, "frame budget: header + payload + crc16");

}  // namespace protocol

#endif  // PROTOCOL_FRAMES_H
