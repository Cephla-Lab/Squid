#include "sequencer/seq_engine.h"

namespace seq {

SeqEngine::SeqEngine(SeqHal& hal) : hal_(hal) {}

ValidationResult SeqEngine::load(const SeqLoop& loop, const SeqChannel* channels,
                                 const SeqCameraConfig* cams, uint8_t n_cameras,
                                 int32_t stack_axis_start) {
    ValidationResult r = validate(loop, channels, cams, n_cameras, 8, 8);
    if (r.error != SeqError::None) return r;
    loop_ = loop;
    n_cameras_ = (n_cameras < kMaxCameras) ? n_cameras : kMaxCameras;
    stack_start_ = stack_axis_start;
    for (uint8_t i = 0; i < loop.n_channels; i++) channels_[i] = channels[i];
    for (uint8_t i = 0; i < n_cameras_; i++) cams_[i] = cams[i];
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

bool SeqEngine::start(uint32_t now_us, uint32_t wait_timeout_us) {
    if (state_ != SeqState::Idle) return false;
    wait_timeout_us_ = wait_timeout_us;
    progress_ = SeqProgress{};
    progress_.total_layers = loop_.n_layers;
    progress_.total_channels = loop_.n_channels;
    for (uint8_t i = 0; i < kMaxCameras; i++) {
        last_trigger_us_[i] = 0;
        readout_done_us_[i] = 0;
    }
    step_ = 0;
    cancel_requested_ = false;
    begin_prep(0, now_us);
    if (state_ == SeqState::Failed) return true;  // started, then immediately failed
    wait_deadline_us_ = now_us + wait_timeout_us_;
    state_ = SeqState::WaitHw;
    return true;
}

void SeqEngine::cancel() { cancel_requested_ = true; }

void SeqEngine::begin_prep(uint32_t k, uint32_t now_us) {
    uint16_t layer;
    uint8_t chi;
    step_to_layer_channel(k, &layer, &chi);
    const SeqChannel& ch = channels_[chi];
    // Stack axis
    int32_t target = stack_target_for(layer, chi);
    if (loop_.stack_axis_type == (uint8_t)StackAxisType::Piezo) {
        hal_.set_dac(loop_.stack_axis_id, (uint16_t)target);
        settle_armed_ = true;
        settle_done_us_ = now_us + loop_.z_settle_us;
    } else {
        if (!hal_.start_axis_move(loop_.stack_axis_id, target)) {
            fail(SeqError::MoveFailed, loop_.stack_axis_id);
            return;
        }
        settle_armed_ = false;  // armed on first in-position observation
        settle_done_us_ = 0;
    }
    // Filter wheel
    if (ch.filter_wheel != kNone) {
        if (!hal_.start_axis_move(ch.filter_wheel, ch.filter_pos)) {
            fail(SeqError::MoveFailed, ch.filter_wheel);
            return;
        }
    }
    // Intensity pre-arm + LED pattern (loop-context SPI: only ever in PREP)
    if (ch.intensity_dac != kNone) hal_.set_dac(ch.intensity_dac, ch.intensity);
    if (ch.led_pattern != kNone) hal_.set_led_pattern(ch.led_pattern);
}

bool SeqEngine::hw_ready_for(uint32_t k, uint32_t now_us) {
    uint16_t layer;
    uint8_t chi;
    step_to_layer_channel(k, &layer, &chi);
    const SeqChannel& ch = channels_[chi];
    // Stack axis settled?
    if (loop_.stack_axis_type == (uint8_t)StackAxisType::Stepper) {
        if (!hal_.axis_in_position(loop_.stack_axis_id)) return false;
        if (!settle_armed_) {
            settle_armed_ = true;
            settle_done_us_ = now_us + loop_.z_settle_us;
        }
    }
    if (now_us < settle_done_us_) return false;
    // Filter wheel in position?
    if (ch.filter_wheel != kNone && !hal_.axis_in_position(ch.filter_wheel)) return false;
    // Every camera in the mask ready?
    for (uint8_t cam = 0; cam < n_cameras_; cam++) {
        if (!((ch.camera_mask >> cam) & 1)) continue;
        const SeqCameraConfig& cc = cams_[cam];
        if (cc.ready_line != kNone) {
            if (hal_.ready_line(cc.ready_line) != (bool)cc.ready_active_high) return false;
        } else {
            if (now_us < readout_done_us_[cam]) return false;
        }
        if (cc.min_trigger_period_us && last_trigger_us_[cam] != 0 &&
            now_us - last_trigger_us_[cam] < cc.min_trigger_period_us)
            return false;
    }
    return true;
}

void SeqEngine::schedule_exposures(uint32_t k, uint32_t now_us) {
    uint16_t layer;
    uint8_t chi;
    step_to_layer_channel(k, &layer, &chi);
    const SeqChannel& ch = channels_[chi];
    cur_exposure_end_us_ = 0;
    overlap_hold_until_us_ = 0;
    for (uint8_t cam = 0; cam < n_cameras_; cam++) {
        if (!((ch.camera_mask >> cam) & 1)) continue;
        const SeqCameraConfig& cc = cams_[cam];
        ExposurePlan p{};
        p.camera_id = cam;
        p.trigger_mode = cc.trigger_mode;
        p.illum_ttl_mask = ch.illum_ttl_mask;
        p.t_assert_us = now_us;
        p.t_illum_on_us = now_us + cc.strobe_delay_us;
        p.t_illum_off_us = p.t_illum_on_us + ch.exposure_us;
        p.t_deassert_us = (cc.trigger_mode == (uint8_t)TriggerMode::Level)
                              ? p.t_illum_off_us
                              : now_us + kEdgePulseUs;
        hal_.schedule_exposure(p);
        last_trigger_us_[cam] = now_us;
        uint32_t end = (p.t_illum_off_us > p.t_deassert_us) ? p.t_illum_off_us
                                                            : p.t_deassert_us;
        readout_done_us_[cam] = end + cc.readout_time_us;
        if (end > cur_exposure_end_us_) cur_exposure_end_us_ = end;
        if (!cc.readout_overlap_safe && readout_done_us_[cam] > overlap_hold_until_us_)
            overlap_hold_until_us_ = readout_done_us_[cam];
    }
    progress_.frames_fired++;
    progress_.layer = layer;
    progress_.channel = chi;
    state_ = SeqState::Exposing;
}

void SeqEngine::fail(SeqError e, uint8_t detail) {
    hal_.all_off();
    progress_.abort_error = (uint8_t)e;
    progress_.abort_detail = detail;
    state_ = SeqState::Failed;
}

void SeqEngine::tick(uint32_t now_us) {
    switch (state_) {
        case SeqState::WaitHw:
            if (hw_ready_for(step_, now_us)) {
                schedule_exposures(step_, now_us);
                break;
            }
            if (now_us >= wait_deadline_us_) fail(SeqError::WaitTimeout, 0);
            break;
        case SeqState::Exposing: {
            if (now_us < cur_exposure_end_us_ || now_us < overlap_hold_until_us_) break;
            // Exposure over -> readout window begins: advance and PREP the next step
            // NOW — this is the overlap that hides filter/z moves behind readout.
            step_++;
            if (cancel_requested_ || step_ >= total_steps()) {
                if (loop_.return_to_start) {
                    if (loop_.stack_axis_type == (uint8_t)StackAxisType::Piezo)
                        hal_.set_dac(loop_.stack_axis_id, (uint16_t)stack_start_);
                    else
                        hal_.start_axis_move(loop_.stack_axis_id, stack_start_);
                }
                if (cancel_requested_ && step_ < total_steps())
                    progress_.abort_error = (uint8_t)SeqError::Canceled;
                state_ = SeqState::Done;
                break;
            }
            begin_prep(step_, now_us);
            if (state_ == SeqState::Failed) break;  // begin_prep may fail()
            wait_deadline_us_ = now_us + wait_timeout_us_;
            state_ = SeqState::WaitHw;
            break;
        }
        default:
            break;
    }
}

}  // namespace seq
