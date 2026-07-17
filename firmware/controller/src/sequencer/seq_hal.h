#pragma once
#include <stdint.h>

#include "sequencer/seq_types.h"

// Hardware interface consumed by the sequencer engine — pure C++11, NO Arduino deps.
// Tests inject FakeHal (test/test_seq_engine/fake_hal.h); Phase D binds real hardware
// (seq_bind.cpp: TMC moves, DAC80508, TTL pins via the µs event timer).

namespace seq {

// One camera's exposure, fully timestamped. The HAL owns µs-precise edge execution;
// the engine owns the semantics (when to schedule, what the times mean).
struct ExposurePlan {
    uint8_t camera_id;
    uint8_t trigger_mode;    // TriggerMode
    uint8_t illum_ttl_mask;  // TTL ports driven for this exposure
    uint32_t t_assert_us;    // trigger asserted (active edge)
    uint32_t t_illum_on_us;  // = t_assert + strobe_delay
    uint32_t t_illum_off_us;  // = t_illum_on + exposure
    uint32_t t_deassert_us;  // Level: == t_illum_off ; Edge: t_assert + kEdgePulseUs
};

class SeqHal {
   public:
    virtual ~SeqHal() {}
    // Motion (stepper axes and filter wheels). Returns false if the move is rejected.
    virtual bool start_axis_move(uint8_t axis_id, int32_t target_usteps) = 0;
    virtual bool axis_in_position(uint8_t axis_id) = 0;
    // Analog / illumination setup (loop-context SPI — engine only calls these in PREP).
    virtual void set_dac(uint8_t dac_id, uint16_t value) = 0;
    virtual void set_led_pattern(uint8_t pattern_id) = 0;
    // Exposure execution (µs-precise trigger + illumination edges).
    virtual void schedule_exposure(const ExposurePlan& plan) = 0;
    // Camera trigger-ready input (polarity-raw; engine normalizes).
    virtual bool ready_line(uint8_t line) = 0;
    // Abort path: all illumination off, all triggers deasserted.
    virtual void all_off() = 0;
};

}  // namespace seq
