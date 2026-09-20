#include "sequencer/seq_selftest.h"

#ifdef SEQ_SELFTEST

#ifndef SEQ_SELFTEST_STACK_DAC
#error "SEQ_SELFTEST needs -D SEQ_SELFTEST_STACK_DAC=<0..7>: the DAC channel stepped as the stack axis (7 = the real objective piezo, which WILL move)"
#endif
#ifndef SEQ_SELFTEST_TTL_MASK
#define SEQ_SELFTEST_TTL_MASK 0
#endif

#include <Arduino.h>

#include "sequencer/seq_bind.h"

namespace {

const uint32_t kPeriodMs = 2000;
const int32_t kStackStart = 32768;  // mid-range
bool loaded = false;
uint32_t last_start_ms = 0;

// 3 layers x 2 channels: a 20 ms and a 50 ms exposure per layer, so the scope shows the pulse
// width following the channel, and a stack step landing in the readout window between layers.
void load_program() {
    seq::wire::ParsedProgram p{};
    p.n_cameras = 1;
    p.wait_timeout_us = 2000000;
    p.loop.stack_axis_type = (uint8_t)seq::StackAxisType::Piezo;
    p.loop.stack_axis_id = SEQ_SELFTEST_STACK_DAC;
    p.loop.dz = 218;  // ~1 um on a 300 um piezo
    p.loop.n_layers = 3;
    p.loop.order = (uint8_t)seq::Order::ChannelsInner;
    p.loop.z_settle_us = 20000;
    p.loop.return_to_start = 1;
    p.loop.n_channels = 2;
    const uint32_t exposure_us[2] = {20000, 50000};
    for (uint8_t i = 0; i < 2; i++) {
        seq::SeqChannel& c = p.channels[i];
        c.filter_wheel = seq::kNone;
        c.illum_ttl_mask = SEQ_SELFTEST_TTL_MASK;
        c.led_pattern = seq::kNone;
        c.intensity_dac = seq::kNone;  // never touch a laser intensity DAC from the self-test
        c.exposure_us = exposure_us[i];
        c.camera_mask = 0x01;
    }
    seq::SeqCameraConfig& cam = p.cams[0];
    cam.trigger_mode = (uint8_t)seq::TriggerMode::Level;
    cam.strobe_delay_us = 300;
    cam.readout_time_us = 25000;
#ifdef SEQ_SELFTEST_READY_LINE
    cam.ready_line = 0;
#else
    cam.ready_line = seq::kNone;
#endif
    cam.ready_active_high = 1;
    cam.readout_overlap_safe = 1;
    loaded = seq_load(p).error == seq::SeqError::None;
}

}  // namespace

void seq_selftest_tick() {
    if (!loaded) load_program();
    if (!loaded || seq_running()) return;
    if (millis() - last_start_ms < kPeriodMs) return;
    last_start_ms = millis();
    seq_start(kStackStart);
}

#endif  // SEQ_SELFTEST
