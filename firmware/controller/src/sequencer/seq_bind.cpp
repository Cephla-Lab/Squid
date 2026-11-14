#include "sequencer/seq_bind.h"

#include <Arduino.h>

#include "constants.h"
#include "functions.h"
#include "globals.h"
#include "timing/event_timer.h"
#include "tmc/drivers/stepper_driver.h"  // tmc_driver_ready
#include "trigger_pins.h"

namespace {

const uint8_t kNumTtlPorts = 5;  // D1-D5

class TeensySeqHal : public seq::SeqHal {
   public:
    // The sequencer commands the TMC4361A directly and keeps its own targets. It must NOT go
    // through the *_commanded_movement_in_progress flags: check_position() clears
    // mcu_cmd_execution_in_progress when such a move completes, which would report the
    // pending SEQ_RUN as finished in the middle of a stack.
    bool start_axis_move(uint8_t axis_id, int32_t target_usteps) override {
        const uint8_t a = protocol_axis_to_internal(axis_id);
        if (a == 0xFF || !tmc_driver_ready(&tmc4361[a])) return false;
        if (tmc4361A_moveTo(&tmc4361[a], target_usteps) != 0) return false;
        targets_[a] = target_usteps;
        commanded_[a] = true;
        return true;
    }

    bool axis_in_position(uint8_t axis_id) override {
        const uint8_t a = protocol_axis_to_internal(axis_id);
        if (a == 0xFF) return false;
        return tmc4361A_currentPosition(&tmc4361[a]) == targets_[a] &&
               !tmc4361A_isRunning(&tmc4361[a], stage_PID_enabled[a]);
    }

    // Loop-context SPI; the engine only calls this during PREP.
    void set_dac(uint8_t dac_id, uint16_t value) override { set_DAC8050x_output(dac_id, value); }

    void set_led_pattern(uint8_t pattern_id) override {
        if (pattern_id == seq::kNone) {
            clear_matrix(matrix);
            FastLED.show();
            return;
        }
        turn_on_LED_matrix_pattern(matrix, pattern_id, led_matrix_r, led_matrix_g, led_matrix_b);
    }

    void schedule_exposure(const seq::ExposurePlan& plan) override {
        if (!event_timer_schedule(plan)) schedule_failed_ = true;
    }

    bool ready_line(uint8_t) override { return digitalReadFast(TRIGGER_READY_PIN) == HIGH; }

    void all_off() override {
        event_timer_cancel_all();
        turn_off_all_ports();
    }

    void stop_motion() override {
        for (uint8_t a = 0; a < TOTAL_AXES; a++) {
            if (!commanded_[a]) continue;
            tmc4361A_stop(&tmc4361[a]);
            commanded_[a] = false;
        }
    }

    void begin_run() {
        schedule_failed_ = false;
        for (uint8_t a = 0; a < TOTAL_AXES; a++) commanded_[a] = false;
    }
    bool schedule_failed() const { return schedule_failed_; }

   private:
    int32_t targets_[TOTAL_AXES] = {0};
    bool commanded_[TOTAL_AXES] = {false};
    bool schedule_failed_ = false;
};

TeensySeqHal hal;
seq::SeqEngine engine(hal);
uint32_t wait_timeout_us = 0;

}  // namespace

seq::ValidationResult seq_load(const seq::wire::ParsedProgram& p) {
    // The flashed controller profile is the authority on what hardware exists.
    if (p.n_cameras > NUM_CAMERA_TRIGGERS) return {seq::SeqError::BadCamera, p.n_cameras};
    for (uint8_t i = 0; i < p.n_cameras; i++) {
        const uint8_t line = p.cams[i].ready_line;
        if (line != seq::kNone && (line != 0 || TRIGGER_READY_PIN < 0))
            return {seq::SeqError::BadCamera, i};
    }
    // The stepper binding above keeps its own targets and never writes focusPosition, which
    // do_focus_control() re-issues as the Z target every pass once the run ends: a stepper stack
    // would be undone (and a stopped abort move re-issued) the moment the sequence finished.
    // Refused until the stepper stack axis is bound properly; the host builds piezo stacks only.
    if (p.loop.stack_axis_type == (uint8_t)seq::StackAxisType::Stepper)
        return {seq::SeqError::BadStackAxis, p.loop.stack_axis_id};
    for (uint8_t i = 0; i < p.loop.n_channels; i++) {
        if (p.channels[i].illum_ttl_mask >> kNumTtlPorts) return {seq::SeqError::BadChannel, i};
    }
    const seq::ValidationResult r = engine.load(p.loop, p.channels, p.cams, p.n_cameras);
    if (r.error != seq::SeqError::None) return r;
    wait_timeout_us = p.wait_timeout_us;
    return r;
}

bool seq_start(int32_t stack_start, uint8_t channel) {
    if (engine.running()) return false;
    // The run owns the camera triggers and the illumination ports: a port the host switched on
    // before the run, a strobe still pending in the v1 ISR or a trigger pulse still held would
    // otherwise light or release into the first exposures. fail() releases them the same way.
    cancel_legacy_camera_outputs();
    hal.begin_run();
    return engine.start(micros(), wait_timeout_us, stack_start, channel);
}

void seq_cancel() { engine.cancel(); }

void seq_abort(seq::SeqError e) { engine.abort(e); }

void seq_tick() {
    if (!engine.running()) return;
    // Abort THROUGH the engine: loop() also forces the TTL ports LOW when the interlock opens,
    // and without this the run would complete "successfully" with dark frames. The interlock only
    // concerns the TTL (laser) ports, and only the channel(s) THIS run lights (SEQ_RUN's selector).
    if (engine.run_uses_ttl() && (!INTERLOCK_OK() || event_timer_interlock_tripped())) {
        engine.abort(seq::SeqError::InterlockOpen);
        return;
    }
    // Edges that were never queued, or queued but never armed, would leave a trigger or a TTL
    // port asserted: abort, which releases them (fail -> all_off -> event_timer_cancel_all).
    if (hal.schedule_failed() || event_timer_arm_failed()) {
        engine.abort(seq::SeqError::EdgeQueueFull);
        return;
    }
    engine.tick(micros());
}

bool seq_running() { return engine.running(); }
seq::SeqState seq_state() { return engine.state(); }
const seq::SeqProgress& seq_progress() { return engine.progress(); }
