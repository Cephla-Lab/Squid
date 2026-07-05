#pragma once
#include <stdint.h>

#include "sequencer/seq_hal.h"
#include "sequencer/seq_types.h"

// Sequencer engine — pure C++11 state machine, NO Arduino deps.
// Consumes (program, now_us, HAL inputs); emits HAL commands. Timing semantics are
// specified in AI-docs design §5.2 and enforced by test/test_seq_engine/.
//
// Per acquisition step k (a (layer, channel) pair in the configured order):
//   PREP(k)    launched during step k-1's readout window (or at start for k=0):
//              stack-axis move (TMC or piezo DAC), filter-wheel move, intensity DAC
//              pre-arm, LED pattern — all loop-context SPI happens HERE only.
//   WAIT(k)    stack axis settled ∧ filter in position ∧ all masked cameras ready
//              (ready line or timing model) ∧ min trigger period elapsed.
//   EXPOSE(k)  schedule µs-timestamped trigger + illumination edges via the HAL.
//   at exposure end: advance to k+1, PREP immediately (this IS the readout overlap).

namespace seq {

enum class SeqState : uint8_t { Idle, Prep, WaitHw, Exposing, Aborting, Done, Failed };

struct SeqProgress {
    uint16_t layer;
    uint16_t total_layers;
    uint8_t channel;
    uint8_t total_channels;
    uint32_t frames_fired;
    uint8_t abort_error;  // SeqError
    uint8_t abort_detail;
};

class SeqEngine {
   public:
    explicit SeqEngine(SeqHal& hal);
    ValidationResult load(const SeqLoop& loop, const SeqChannel* channels,
                          const SeqCameraConfig* cams, uint8_t n_cameras,
                          int32_t stack_axis_start);
    bool start(uint32_t now_us, uint32_t wait_timeout_us);
    void cancel();  // finish current exposure, then wind down (never truncates)
    void tick(uint32_t now_us);
    SeqState state() const { return state_; }
    const SeqProgress& progress() const { return progress_; }

   private:
    uint32_t total_steps() const;
    void step_to_layer_channel(uint32_t k, uint16_t* layer, uint8_t* ch) const;
    int32_t stack_target_for(uint16_t layer, uint8_t ch) const;
    void begin_prep(uint32_t k, uint32_t now_us);   // moves + DAC pre-arm + LED
    bool hw_ready_for(uint32_t k, uint32_t now_us);  // WAIT gate (design §5.2)
    void schedule_exposures(uint32_t k, uint32_t now_us);
    void fail(SeqError e, uint8_t detail);

    SeqHal& hal_;
    SeqLoop loop_{};
    SeqChannel channels_[kMaxChannels]{};
    SeqCameraConfig cams_[kMaxCameras]{};
    uint8_t n_cameras_ = 0;
    int32_t stack_start_ = 0;
    SeqState state_ = SeqState::Idle;
    SeqProgress progress_{};
    uint32_t step_ = 0;            // current step index k
    uint32_t wait_deadline_us_ = 0;
    uint32_t wait_timeout_us_ = 0;
    uint32_t settle_done_us_ = 0;  // stack-move settle gate
    bool settle_armed_ = false;    // stepper: set on first in-position observation
    uint32_t cur_exposure_end_us_ = 0;
    // Rolling-shutter support: PREP of the next step is deferred until cameras with
    // readout_overlap_safe == 0 finish reading out (no motion during their readout).
    uint32_t overlap_hold_until_us_ = 0;
    uint32_t last_trigger_us_[kMaxCameras]{};
    uint32_t readout_done_us_[kMaxCameras]{};
    bool cancel_requested_ = false;
};

}  // namespace seq
