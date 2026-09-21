#pragma once
#include <stdint.h>

// Sequencer program types — pure C++11, NO Arduino dependencies.
// These structs are the wire format for SEQ_UPLOAD_PROGRAM (protocol v2, Phase B/C)
// and the input to the sequencer engine (seq_engine.h). Packed little-endian.
// Design: AI-docs/Squid/to-do/2026-07-04-firmware-v2-design.md §5.

namespace seq {

constexpr uint8_t kMaxChannels = 16;
constexpr uint8_t kMaxCameras = 8;  // board v2 has 8 trigger channels (v1 has 4)
constexpr uint8_t kNone = 0xFF;
constexpr uint32_t kEdgePulseUs = 50;  // matches v1 TRIGGER_PULSE_LENGTH_us
// Stuck ready-line check: how long after a trigger a line may still read "ready" before a line that
// never went busy is called stuck. Far above any camera's output latency, far below any readout.
constexpr uint32_t kReadyStuckMinUs = 1000;
// Upper bound for every duration the engine handles (~17.9 min). Keeps any two engine
// timestamps within 2^31 us of each other, which wrap-safe comparisons on the 32-bit
// micros() timebase require.
constexpr uint32_t kMaxDurationUs = 0x3FFFFFFF;

enum class StackAxisType : uint8_t { Stepper = 0, Piezo = 1 };
enum class Order : uint8_t { ChannelsInner = 0, ZInner = 1 };
enum class TriggerMode : uint8_t { Edge = 0, Level = 1 };

struct __attribute__((packed)) SeqLoop {
    uint8_t stack_axis_type;  // StackAxisType
    uint8_t stack_axis_id;    // stepper axis id, or DAC id when Piezo
    int32_t dz;               // usteps (stepper) or DAC LSB (piezo) per layer, signed
    uint16_t n_layers;        // >= 1
    uint8_t order;            // Order
    uint32_t z_settle_us;     // wait after stack move reports done
    uint8_t return_to_start;  // bool: move stack axis back after the sequence
    uint8_t n_channels;       // 1..kMaxChannels
};

struct __attribute__((packed)) SeqChannel {
    uint8_t filter_wheel;    // kNone, or wheel axis id
    int32_t filter_target;   // absolute microsteps (host owns slot->ustep mapping)
    uint8_t illum_ttl_mask;  // TTL ports ON during exposure (0 = LED-matrix only)
    uint8_t led_pattern;     // kNone, or LED-matrix pattern id
    uint8_t intensity_dac;   // kNone, or DAC id (pre-armed during previous readout)
    uint16_t intensity;      // DAC value
    uint32_t exposure_us;    // > 0
    uint8_t camera_mask;     // != 0; bit i = camera i
    int32_t z_offset;        // per-channel stack-axis offset
    uint8_t flags;           // reserved, 0
};

static_assert(sizeof(SeqLoop) == 15, "SeqLoop is wire format (seq_wire.h)");
static_assert(sizeof(SeqChannel) == 20, "SeqChannel is wire format (seq_wire.h)");

// Runtime per-camera config (set via SET_CAMERA_PARAMS, not uploaded with programs).
struct SeqCameraConfig {
    uint8_t trigger_mode;            // TriggerMode
    uint32_t strobe_delay_us;        // trigger assert -> illumination on
    uint32_t readout_time_us;        // model-based readiness after exposure end
    uint32_t min_trigger_period_us;  // 0 = no constraint
    uint8_t ready_line;              // kNone = model-only, else ready input index
    uint8_t ready_active_high;
    uint8_t readout_overlap_safe;  // 0 = no motion during this camera's readout
};

enum class SeqError : uint8_t {
    None = 0,
    BadLayerCount,
    BadChannelCount,
    BadStackAxis,
    BadChannel,
    BadCamera,
    BadExposure,
    WaitTimeout,
    MoveFailed,
    ReadyLineStuck,  // a ready line that reads ready but never went busy. Was ReadyTimeout (never raised
                     // under that name - it is not a timeout, WaitTimeout is); the wire value is unchanged.
    Canceled,         // 10
    // Values are wire format (status byte, seq_wire.h) — append only, never renumber.
    StackOutOfRange,  // 11  a stack target leaves the axis range (piezo: 0..65535)
    InterlockOpen,    // 12
    HostAbort,        // 13  TURN_OFF_ALL_PORTS / serial watchdog
    BadDuration,      // 14  a duration exceeds kMaxDurationUs
    Busy,             // 15  load/run while a sequence is running
    BadProgram,       // 16  staging length / version / CRC
    NotCommitted,     // 17  SEQ_RUN without a committed program
    EdgeQueueFull,    // 18  an exposure could not be scheduled (binding's edge queue)
};

struct ValidationResult {
    SeqError error;
    uint8_t detail;  // channel index (or axis id) the error refers to
};

ValidationResult validate(const SeqLoop& loop, const SeqChannel* channels,
                          const SeqCameraConfig* cams, uint8_t n_cameras, uint8_t n_axes,
                          uint8_t n_dacs);

}  // namespace seq
