#pragma once
#include <stdint.h>

#include "sequencer/seq_engine.h"
#include "sequencer/seq_types.h"

// Wire contract for the v1-protocol sequencer shim — pure C++11, NO Arduino deps.
// Design: AI-docs/Squid/to-do/2026-09-20-firmware-v2-hardware-sequencer-v1-shim-design.md §4.4.
// The Python mirror (software/control/sequencer_program.py) is cross-checked against this
// header by tests; change both or neither.
//
// The host stages a program with SEQ_WRITE (4 bytes per command, absolute word offsets, so
// a blind v1 resend is idempotent), seals it with SEQ_COMMIT (length + CRC-16), then sends
// SEQ_RUN once per FOV. Progress rides the 10 ms status packet — the host never polls
// during a run, because v1 tracks exactly one pending command.

namespace seq {
namespace wire {

constexpr uint8_t kWireVersion = 1;

// v1 opcodes. 44-50 belong to PR #645 — do not use.
constexpr uint8_t kOpSeqWrite = 60;   // [2] word index, [3..6] 4 data bytes
constexpr uint8_t kOpSeqCommit = 61;  // [2..3] byte length BE, [4..5] CRC-16/CCITT-FALSE BE
constexpr uint8_t kOpSeqRun = 62;     // [2..5] i32 stack_start BE (DAC LSB for a piezo stack)
constexpr uint8_t kOpSeqCancel = 63;

// Sequencer status in the 24-byte v1 response. Bytes 19-21 belong to PR #645. Bytes 14-17
// were never written by firmware (the host parsed them as an unused theta position).
constexpr uint8_t kStatusByteState = 14;     // SeqState (3 b) << 5 | SeqError (5 b)
constexpr uint8_t kStatusByteDetail = 15;    // abort detail (channel index / axis id)
constexpr uint8_t kStatusByteFramesHi = 16;  // frames_fired, big-endian like the positions
constexpr uint8_t kStatusByteFramesLo = 17;

// First firmware version that implements this contract. Older firmware routes unknown
// opcodes to callback_default() and reports success, so the host must gate on this.
constexpr uint8_t kFwMajor = 1;
constexpr uint8_t kFwMinor = 7;

constexpr uint8_t kWordBytes = 4;

// ValidationResult.detail for SeqError::BadProgram
constexpr uint8_t kBadProgramLength = 0;    // length outside the staging buffer / fixed part
constexpr uint8_t kBadProgramVersion = 1;   // WireHeader.version
constexpr uint8_t kBadProgramMismatch = 2;  // length != program_bytes(n_channels, n_cameras)
constexpr uint8_t kBadProgramCrc = 3;       // CRC-16 of the staged bytes

struct __attribute__((packed)) WireHeader {
    uint8_t version;  // kWireVersion
    uint8_t n_cameras;
    uint16_t reserved;
    uint32_t wait_timeout_us;
};

struct __attribute__((packed)) WireCamera {
    uint8_t trigger_mode;  // TriggerMode
    uint8_t ready_line;    // kNone = timing model, else ready input index
    uint8_t ready_active_high;
    uint8_t readout_overlap_safe;
    uint32_t strobe_delay_us;
    uint32_t readout_time_us;
    uint32_t min_trigger_period_us;
};

static_assert(sizeof(WireHeader) == 8, "wire format");
static_assert(sizeof(WireCamera) == 16, "wire format");

// Staging layout, little-endian: header @0, SeqLoop @8 (15 B + 1 pad), SeqChannel[n] @24,
// WireCamera[n_cameras] directly after the channels.
constexpr uint16_t kLoopOffset = 8;
constexpr uint16_t kChannelsOffset = 24;
constexpr uint16_t kStagingBytes =
    kChannelsOffset + kMaxChannels * sizeof(SeqChannel) + kMaxCameras * sizeof(WireCamera);

inline uint16_t program_bytes(uint8_t n_channels, uint8_t n_cameras) {
    return (uint16_t)(kChannelsOffset + n_channels * sizeof(SeqChannel) +
                      n_cameras * sizeof(WireCamera));
}

inline uint8_t pack_status(SeqState s, SeqError e) {
    return (uint8_t)(((uint8_t)s << 5) | ((uint8_t)e & 0x1F));
}

struct ParsedProgram {
    SeqLoop loop;
    SeqChannel channels[kMaxChannels];
    SeqCameraConfig cams[kMaxCameras];
    uint8_t n_cameras;
    uint32_t wait_timeout_us;
};

// Structural checks only (version, counts, exact length, frame-counter range); semantic
// validation stays in seq::validate(), reached through SeqEngine::load().
ValidationResult parse_staging(const uint8_t* buf, uint16_t len, ParsedProgram* out);

}  // namespace wire
}  // namespace seq
