#include "sequencer/seq_wire.h"

#include <string.h>

namespace seq {
namespace wire {

ValidationResult parse_staging(const uint8_t* buf, uint16_t len, ParsedProgram* out) {
    if (len < kChannelsOffset || len > kStagingBytes) return {SeqError::BadProgram, 0};
    // memcpy, not casts: the staging buffer has no alignment guarantee.
    WireHeader h;
    memcpy(&h, buf, sizeof h);
    if (h.version != kWireVersion) return {SeqError::BadProgram, 1};
    if (h.n_cameras < 1 || h.n_cameras > kMaxCameras) return {SeqError::BadCamera, 0};
    memcpy(&out->loop, buf + kLoopOffset, sizeof(SeqLoop));
    const uint8_t n_ch = out->loop.n_channels;
    if (n_ch < 1 || n_ch > kMaxChannels) return {SeqError::BadChannelCount, 0};
    if (len != program_bytes(n_ch, h.n_cameras)) return {SeqError::BadProgram, 2};
    // frames_fired travels as a u16 in the status packet
    if ((uint32_t)out->loop.n_layers * n_ch > 65535u) return {SeqError::BadLayerCount, 0};
    memcpy(out->channels, buf + kChannelsOffset, n_ch * sizeof(SeqChannel));
    const uint8_t* cam = buf + kChannelsOffset + n_ch * sizeof(SeqChannel);
    for (uint8_t i = 0; i < h.n_cameras; i++, cam += sizeof(WireCamera)) {
        WireCamera w;
        memcpy(&w, cam, sizeof w);
        SeqCameraConfig& c = out->cams[i];
        c.trigger_mode = w.trigger_mode;
        c.strobe_delay_us = w.strobe_delay_us;
        c.readout_time_us = w.readout_time_us;
        c.min_trigger_period_us = w.min_trigger_period_us;
        c.ready_line = w.ready_line;
        c.ready_active_high = w.ready_active_high;
        c.readout_overlap_safe = w.readout_overlap_safe;
    }
    out->n_cameras = h.n_cameras;
    out->wait_timeout_us = h.wait_timeout_us;
    return {SeqError::None, 0};
}

}  // namespace wire
}  // namespace seq
