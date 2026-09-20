#include "sequencer/seq_engine.h"

namespace seq {

namespace {
// Wrap-safe "now has reached t" on the 32-bit micros() timebase (wraps every 71.6 min).
// Valid while the two are within 2^31 us, which validate() guarantees by bounding every
// duration to kMaxDurationUs. Never compare engine timestamps with < or >.
inline bool reached(uint32_t now_us, uint32_t t_us) { return (int32_t)(now_us - t_us) >= 0; }
}  // namespace

SeqEngine::SeqEngine(SeqHal& hal) : hal_(hal) {}

bool SeqEngine::running() const {
    return state_ == SeqState::WaitHw || state_ == SeqState::Exposing ||
           state_ == SeqState::Returning;
}

ValidationResult SeqEngine::load(const SeqLoop& loop, const SeqChannel* channels,
                                 const SeqCameraConfig* cams, uint8_t n_cameras) {
    if (running()) return {SeqError::Busy, 0};
    ValidationResult r = validate(loop, channels, cams, n_cameras, 8, 8);
    if (r.error != SeqError::None) return r;
    loop_ = loop;
    n_cameras_ = (n_cameras < kMaxCameras) ? n_cameras : kMaxCameras;
    for (uint8_t i = 0; i < loop.n_channels; i++) channels_[i] = channels[i];
    for (uint8_t i = 0; i < n_cameras_; i++) cams_[i] = cams[i];
    loaded_ = true;
    state_ = SeqState::Idle;
    return r;
}

uint32_t SeqEngine::total_steps() const {
    return (uint32_t)loop_.n_layers * loop_.n_channels;
}

void SeqEngine::step_to_layer_channel(uint32_t k, uint16_t* layer, uint8_t* ch) const {
    if (loop_.order == (uint8_t)Order::ChannelsInner) {
        *layer = (uint16_t)(k / loop_.n_channels);
        *ch = (uint8_t)(k % loop_.n_channels);
    } else {
        *ch = (uint8_t)(k / loop_.n_layers);
        *layer = (uint16_t)(k % loop_.n_layers);
    }
}

int32_t SeqEngine::stack_target_for(uint16_t layer, uint8_t ch) const {
    return stack_start_ + (int32_t)layer * loop_.dz + channels_[ch].z_offset;
}

bool SeqEngine::stack_range_ok(int32_t start, uint8_t* bad_channel) const {
    const bool piezo = loop_.stack_axis_type == (uint8_t)StackAxisType::Piezo;
    const int64_t lo = piezo ? 0 : (int64_t)INT32_MIN;  // piezo target is a u16 DAC code
    const int64_t hi = piezo ? 65535 : (int64_t)INT32_MAX;
    *bad_channel = 0xFF;
    if (start < lo || start > hi) return false;
    // Targets are linear in the layer index, so checking both ends covers every layer.
    const int64_t span = (int64_t)(loop_.n_layers - 1) * loop_.dz;
    for (uint8_t c = 0; c < loop_.n_channels; c++) {
        const int64_t first = (int64_t)start + channels_[c].z_offset;
        const int64_t last = first + span;
        if (first < lo || first > hi || last < lo || last > hi) {
            *bad_channel = c;
            return false;
        }
    }
    return true;
}

bool SeqEngine::start(uint32_t now_us, uint32_t wait_timeout_us, int32_t stack_axis_start) {
    if (!loaded_ || running()) return false;  // Idle, Done and Failed may all (re)start
    wait_timeout_us_ = wait_timeout_us;
    stack_start_ = stack_axis_start;
    progress_ = SeqProgress{};
    progress_.total_layers = loop_.n_layers;
    progress_.total_channels = loop_.n_channels;
    for (uint8_t i = 0; i < kMaxCameras; i++) {
        trigger_valid_[i] = false;
        readout_valid_[i] = false;
    }
    overlap_hold_valid_ = false;
    step_ = 0;
    cancel_requested_ = false;
    led_on_ = false;
    // Enter the running state BEFORE the first PREP: a restart from Failed must not read
    // the previous run's Failed as "this run failed", so fail() below is the only way there.
    state_ = SeqState::WaitHw;
    // Refuse the whole run before the first move: nothing may be commanded for a stack
    // that would leave the axis range part-way through.
    uint8_t bad_channel = 0;
    if (wait_timeout_us > kMaxDurationUs) {
        fail(SeqError::BadDuration, 0);
        return true;
    }
    if (!stack_range_ok(stack_axis_start, &bad_channel)) {
        fail(SeqError::StackOutOfRange, bad_channel);
        return true;
    }
    begin_prep(0, now_us);
    if (state_ == SeqState::Failed) return true;  // started, then immediately failed
    wait_deadline_us_ = now_us + wait_timeout_us_;
    return true;
}

void SeqEngine::cancel() { cancel_requested_ = true; }

void SeqEngine::abort(SeqError e) {
    if (running()) fail(e, 0);
}

bool SeqEngine::command_stack(int32_t target, uint32_t now_us) {
    if (loop_.stack_axis_type == (uint8_t)StackAxisType::Piezo) {
        hal_.set_dac(loop_.stack_axis_id, (uint16_t)target);  // range proven by stack_range_ok()
        settle_armed_ = true;
        settle_done_us_ = now_us + loop_.z_settle_us;
        return true;
    }
    if (!hal_.start_axis_move(loop_.stack_axis_id, target)) return false;
    settle_armed_ = false;  // armed on the first in-position observation
    return true;
}

bool SeqEngine::stack_settled(uint32_t now_us) {
    if (loop_.stack_axis_type == (uint8_t)StackAxisType::Stepper) {
        if (!hal_.axis_in_position(loop_.stack_axis_id)) return false;
        if (!settle_armed_) {
            settle_armed_ = true;
            settle_done_us_ = now_us + loop_.z_settle_us;
        }
    }
    return reached(now_us, settle_done_us_);
}

// End of the run (all steps done, or cancel): command the return move if asked and hand
// over to Returning. Done is only reported once the stack axis is back, because the host
// starts the next XY move on Done.
void SeqEngine::finish(uint32_t now_us) {
    if (led_on_) {
        hal_.set_led_pattern(kNone);
        led_on_ = false;
    }
    if (cancel_requested_ && step_ < total_steps())
        progress_.abort_error = (uint8_t)SeqError::Canceled;
    if (!loop_.return_to_start) {
        state_ = SeqState::Done;
        return;
    }
    if (!command_stack(stack_start_, now_us)) {
        fail(SeqError::MoveFailed, loop_.stack_axis_id);
        return;
    }
    wait_deadline_us_ = now_us + wait_timeout_us_;
    state_ = SeqState::Returning;
}

void SeqEngine::begin_prep(uint32_t k, uint32_t now_us) {
    uint16_t layer;
    uint8_t chi;
    step_to_layer_channel(k, &layer, &chi);
    const SeqChannel& ch = channels_[chi];
    // Stack axis
    if (!command_stack(stack_target_for(layer, chi), now_us)) {
        fail(SeqError::MoveFailed, loop_.stack_axis_id);
        return;
    }
    // Filter wheel
    if (ch.filter_wheel != kNone) {
        if (!hal_.start_axis_move(ch.filter_wheel, ch.filter_target)) {
            fail(SeqError::MoveFailed, ch.filter_wheel);
            return;
        }
    }
    // Intensity pre-arm + LED pattern (loop-context SPI: only ever in PREP)
    if (ch.intensity_dac != kNone) hal_.set_dac(ch.intensity_dac, ch.intensity);
    // The matrix is not strobed by the exposure edges (FastLED is too slow for an ISR), so a
    // lit pattern would bleed into a following TTL-only channel unless PREP turns it off.
    if (ch.led_pattern != kNone) {
        hal_.set_led_pattern(ch.led_pattern);
        led_on_ = true;
    } else if (led_on_) {
        hal_.set_led_pattern(kNone);
        led_on_ = false;
    }
}

bool SeqEngine::hw_ready_for(uint32_t k, uint32_t now_us) {
    uint16_t layer;
    uint8_t chi;
    step_to_layer_channel(k, &layer, &chi);
    const SeqChannel& ch = channels_[chi];
    if (!stack_settled(now_us)) return false;
    // Filter wheel in position?
    if (ch.filter_wheel != kNone && !hal_.axis_in_position(ch.filter_wheel)) return false;
    // Every camera in the mask ready?
    for (uint8_t cam = 0; cam < n_cameras_; cam++) {
        if (!((ch.camera_mask >> cam) & 1)) continue;
        const SeqCameraConfig& cc = cams_[cam];
        if (cc.ready_line != kNone) {
            if (hal_.ready_line(cc.ready_line) != (bool)cc.ready_active_high) return false;
        } else if (readout_valid_[cam] && !reached(now_us, readout_done_us_[cam])) {
            return false;
        }
        if (cc.min_trigger_period_us && trigger_valid_[cam] &&
            (uint32_t)(now_us - last_trigger_us_[cam]) < cc.min_trigger_period_us)
            return false;
    }
    return true;
}

void SeqEngine::schedule_exposures(uint32_t k, uint32_t now_us) {
    uint16_t layer;
    uint8_t chi;
    step_to_layer_channel(k, &layer, &chi);
    const SeqChannel& ch = channels_[chi];
    // Every max() is taken in OFFSET space (relative to now_us) and only then added to
    // now_us: absolute timestamps cannot be ordered with > across the micros() wrap.
    uint32_t max_end_off = 0, max_hold_off = 0;
    bool hold = false;
    for (uint8_t cam = 0; cam < n_cameras_; cam++) {
        if (!((ch.camera_mask >> cam) & 1)) continue;
        const SeqCameraConfig& cc = cams_[cam];
        const uint32_t illum_off = cc.strobe_delay_us + ch.exposure_us;
        const uint32_t deassert_off =
            (cc.trigger_mode == (uint8_t)TriggerMode::Level) ? illum_off : kEdgePulseUs;
        const uint32_t end_off = (illum_off > deassert_off) ? illum_off : deassert_off;
        ExposurePlan p{};
        p.camera_id = cam;
        p.trigger_mode = cc.trigger_mode;
        p.illum_ttl_mask = ch.illum_ttl_mask;
        p.t_assert_us = now_us;
        p.t_illum_on_us = now_us + cc.strobe_delay_us;
        p.t_illum_off_us = now_us + illum_off;
        p.t_deassert_us = now_us + deassert_off;
        hal_.schedule_exposure(p);
        last_trigger_us_[cam] = now_us;
        trigger_valid_[cam] = true;
        readout_done_us_[cam] = now_us + end_off + cc.readout_time_us;
        readout_valid_[cam] = true;
        if (end_off > max_end_off) max_end_off = end_off;
        if (!cc.readout_overlap_safe) {
            hold = true;
            if (end_off + cc.readout_time_us > max_hold_off)
                max_hold_off = end_off + cc.readout_time_us;
        }
    }
    cur_exposure_end_us_ = now_us + max_end_off;
    overlap_hold_valid_ = hold;
    overlap_hold_until_us_ = now_us + max_hold_off;
    progress_.frames_fired++;
    progress_.layer = layer;
    progress_.channel = chi;
    state_ = SeqState::Exposing;
}

void SeqEngine::fail(SeqError e, uint8_t detail) {
    hal_.all_off();
    hal_.stop_motion();
    progress_.abort_error = (uint8_t)e;
    progress_.abort_detail = detail;
    state_ = SeqState::Failed;
}

void SeqEngine::tick(uint32_t now_us) {
    switch (state_) {
        case SeqState::WaitHw:
            // A cancel while waiting winds down at once: no further frame, and no waiting
            // out a camera-ready line that may never assert.
            if (cancel_requested_) {
                finish(now_us);
                break;
            }
            if (hw_ready_for(step_, now_us)) {
                schedule_exposures(step_, now_us);
                break;
            }
            if (reached(now_us, wait_deadline_us_)) fail(SeqError::WaitTimeout, 0);
            break;
        case SeqState::Exposing: {
            if (!reached(now_us, cur_exposure_end_us_)) break;
            if (overlap_hold_valid_ && !reached(now_us, overlap_hold_until_us_)) break;
            // Exposure over -> readout window begins: advance and PREP the next step
            // NOW — this is the overlap that hides filter/z moves behind readout.
            step_++;
            if (cancel_requested_ || step_ >= total_steps()) {
                finish(now_us);
                break;
            }
            begin_prep(step_, now_us);
            if (state_ == SeqState::Failed) break;  // begin_prep may fail()
            wait_deadline_us_ = now_us + wait_timeout_us_;
            state_ = SeqState::WaitHw;
            break;
        }
        case SeqState::Returning:
            if (stack_settled(now_us)) {
                state_ = SeqState::Done;
                break;
            }
            if (reached(now_us, wait_deadline_us_))
                fail(SeqError::WaitTimeout, loop_.stack_axis_id);
            break;
        default:
            break;
    }
}

}  // namespace seq
