#pragma once
#include <string>
#include <vector>

#include "sequencer/seq_engine.h"
#include "sequencer/seq_hal.h"

// Records every HAL call with the virtual timestamp at which the engine made it,
// and simulates axis motion with scripted per-axis move durations.
struct FakeHal : seq::SeqHal {
    struct Call {
        std::string what;
        uint32_t t_us;
        long a;
        long b;
    };
    std::vector<Call> calls;
    std::vector<seq::ExposurePlan> plans;
    uint32_t now_us = 0;                 // test advances this; engine sees tick(now)
    uint32_t move_duration_us[8] = {0};  // scripted per-axis
    uint32_t move_done_at_us[8] = {0};
    bool moving[8] = {false};
    // 10 = board-v2 maximum (2 direct + 8 expander); index space matches
    // SeqCameraConfig.ready_line
    bool ready_lines[10] = {true, true, true, true, true, true, true, true, true, true};
    bool fail_next_move = false;

    bool start_axis_move(uint8_t axis, int32_t target) override {
        calls.push_back({"move", now_us, axis, target});
        if (fail_next_move) {
            fail_next_move = false;
            return false;
        }
        moving[axis] = true;
        move_done_at_us[axis] = now_us + move_duration_us[axis];
        return true;
    }
    bool axis_in_position(uint8_t axis) override {
        if (moving[axis] && now_us >= move_done_at_us[axis]) moving[axis] = false;
        return !moving[axis];
    }
    void set_dac(uint8_t dac, uint16_t v) override { calls.push_back({"dac", now_us, dac, v}); }
    void set_led_pattern(uint8_t p) override { calls.push_back({"led", now_us, p, 0}); }
    void schedule_exposure(const seq::ExposurePlan& p) override {
        calls.push_back({"expose", now_us, p.camera_id, (long)p.t_assert_us});
        plans.push_back(p);
    }
    bool ready_line(uint8_t line) override { return ready_lines[line]; }
    void all_off() override { calls.push_back({"all_off", now_us, 0, 0}); }
};

// Advance the engine in fixed virtual-time steps (default 100 µs ~ main-loop cadence).
inline void run_until(seq::SeqEngine& e, FakeHal& hal, uint32_t t_end_us,
                      uint32_t step_us = 100) {
    while (hal.now_us < t_end_us) {
        hal.now_us += step_us;
        e.tick(hal.now_us);
    }
}
