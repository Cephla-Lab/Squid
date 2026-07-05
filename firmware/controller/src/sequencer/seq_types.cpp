#include "sequencer/seq_types.h"

namespace seq {

static ValidationResult err(SeqError e, uint8_t detail = 0) { return {e, detail}; }

ValidationResult validate(const SeqLoop& loop, const SeqChannel* channels,
                          const SeqCameraConfig* cams, uint8_t n_cameras, uint8_t n_axes,
                          uint8_t n_dacs) {
    (void)cams;
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
    for (uint8_t i = 0; i < loop.n_channels; i++) {
        const SeqChannel& c = channels[i];
        if (c.exposure_us == 0) return err(SeqError::BadExposure, i);
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
    }
    return err(SeqError::None);
}

}  // namespace seq
