#include "sequencer/seq_types.h"

namespace seq {

static ValidationResult err(SeqError e, uint8_t detail = 0) { return {e, detail}; }

ValidationResult validate(const SeqLoop& loop, const SeqChannel* channels,
                          const SeqCameraConfig* cams, uint8_t n_cameras, uint8_t n_axes,
                          uint8_t n_dacs) {
    if (loop.n_layers < 1) return err(SeqError::BadLayerCount);
    if (loop.n_channels < 1 || loop.n_channels > kMaxChannels)
        return err(SeqError::BadChannelCount);
    if (loop.stack_axis_type == (uint8_t)StackAxisType::Stepper) {
        if (loop.stack_axis_id >= n_axes) return err(SeqError::BadStackAxis);
    } else if (loop.stack_axis_type == (uint8_t)StackAxisType::Piezo) {
        if (loop.stack_axis_id >= n_dacs) return err(SeqError::BadStackAxis);
    } else {
        return err(SeqError::BadStackAxis);
    }
    // Every duration is bounded so the engine's wrap-safe time comparisons stay valid.
    if (loop.z_settle_us > kMaxDurationUs) return err(SeqError::BadDuration);
    for (uint8_t i = 0; i < n_cameras && i < kMaxCameras; i++) {
        if (cams[i].strobe_delay_us > kMaxDurationUs || cams[i].readout_time_us > kMaxDurationUs ||
            cams[i].min_trigger_period_us > kMaxDurationUs)
            return err(SeqError::BadDuration, i);
    }
    for (uint8_t i = 0; i < loop.n_channels; i++) {
        const SeqChannel& c = channels[i];
        if (c.exposure_us == 0 || c.exposure_us > kMaxDurationUs)
            return err(SeqError::BadExposure, i);
        if (c.camera_mask == 0) return err(SeqError::BadCamera, i);
        for (uint8_t cam = 0; cam < 8; cam++) {
            if ((c.camera_mask >> cam) & 1) {
                if (cam >= n_cameras || cam >= kMaxCameras) return err(SeqError::BadCamera, i);
            }
        }
        if (c.filter_wheel != kNone && c.filter_wheel >= n_axes)
            return err(SeqError::BadChannel, i);
        if (c.intensity_dac != kNone && c.intensity_dac >= n_dacs)
            return err(SeqError::BadChannel, i);
        // Every deadline the engine derives from one exposure (illumination on and off, readout
        // done) lies at most lead + strobe + exposure + readout past the trigger, so that SUM has
        // to respect the bound too, not only each term: the parts are bounded one by one above.
        // And a TTL channel lights ONE shared mask for all its cameras with one OFF edge per
        // camera (seq_engine.cpp schedule_exposures): with different strobe delays the earliest
        // OFF would darken the other cameras' exposures, so the delays have to agree.
        bool have_strobe = false;
        uint32_t shared_strobe = 0;
        for (uint8_t cam = 0; cam < n_cameras && cam < kMaxCameras; cam++) {
            if (!((c.camera_mask >> cam) & 1)) continue;
            const uint64_t deadline = (uint64_t)c.illum_lead_us + cams[cam].strobe_delay_us +
                                      c.exposure_us + cams[cam].readout_time_us;
            if (deadline > kMaxDurationUs) return err(SeqError::BadDuration, i);
            if (c.illum_ttl_mask) {
                if (have_strobe && cams[cam].strobe_delay_us != shared_strobe)
                    return err(SeqError::BadCamera, i);
                shared_strobe = cams[cam].strobe_delay_us;
                have_strobe = true;
            }
        }
    }
    return err(SeqError::None);
}

}  // namespace seq
