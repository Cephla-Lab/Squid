#include <unity.h>

#include "sequencer/seq_types.h"
// Include sources directly for native tests (same convention as test_crc8)
#include "sequencer/seq_engine.cpp"
#include "sequencer/seq_types.cpp"

#include "fake_hal.h"

using namespace seq;

static SeqLoop good_loop() {
    SeqLoop l{};
    l.stack_axis_type = (uint8_t)StackAxisType::Piezo;
    l.stack_axis_id = 7;  // DAC7 = piezo on current boards
    l.dz = 120;
    l.n_layers = 10;
    l.order = (uint8_t)Order::ChannelsInner;
    l.z_settle_us = 2000;
    l.return_to_start = 1;
    l.n_channels = 2;
    return l;
}

static SeqChannel good_channel() {
    SeqChannel c{};
    c.filter_wheel = kNone;
    c.filter_pos = 0;
    c.illum_ttl_mask = 0x01;
    c.led_pattern = kNone;
    c.intensity_dac = 0;
    c.intensity = 30000;
    c.exposure_us = 10000;
    c.camera_mask = 0x01;
    c.z_offset = 0;
    c.flags = 0;
    return c;
}

static SeqCameraConfig cam_level() {
    SeqCameraConfig c{};
    c.trigger_mode = (uint8_t)TriggerMode::Level;
    c.strobe_delay_us = 500;
    c.readout_time_us = 20000;
    c.min_trigger_period_us = 0;
    c.ready_line = kNone;
    c.ready_active_high = 1;
    c.readout_overlap_safe = 1;
    return c;
}

void setUp(void) {}
void tearDown(void) {}

// Simplest program: 1 layer, 1 channel, no filter, piezo stack axis, level trigger.
void test_single_frame_program_completes(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 1;
    l.n_channels = 1;
    l.z_settle_us = 1000;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None,
                            (uint8_t)e.load(l, ch, cams, 1, /*stack_start=*/40000).error);
    TEST_ASSERT_TRUE(e.start(hal.now_us, /*wait_timeout_us=*/5000000));
    run_until(e, hal, 2000000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT32(1, e.progress().frames_fired);
    TEST_ASSERT_EQUAL(1, (int)hal.plans.size());
    const ExposurePlan& p = hal.plans[0];
    // Level semantics: illum on at assert+strobe; deassert == illum_off;
    // pulse width = strobe + exposure.
    TEST_ASSERT_EQUAL_UINT32(p.t_assert_us + 500, p.t_illum_on_us);
    TEST_ASSERT_EQUAL_UINT32(p.t_illum_on_us + 10000, p.t_illum_off_us);
    TEST_ASSERT_EQUAL_UINT32(p.t_illum_off_us, p.t_deassert_us);
}

// Piezo stack axis: layer z = DAC steps of dz; settle honored before exposure.
void test_piezo_step_and_settle_gate_exposure(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 2;
    l.n_channels = 1;
    l.dz = 120;
    l.z_settle_us = 3000;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    cams[0].readout_time_us = 5000;
    e.load(l, ch, cams, 1, 40000);
    e.start(hal.now_us, 5000000);
    run_until(e, hal, 3000000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT32(2, e.progress().frames_fired);
    // DAC writes to the piezo (dac 7): layer0 = 40000, layer1 = 40120,
    // + return_to_start = 40000 at the end.
    int dac_writes = 0;
    uint16_t last = 0;
    for (auto& c : hal.calls) {
        if (c.what == "dac" && c.a == 7) {
            dac_writes++;
            last = (uint16_t)c.b;
        }
    }
    TEST_ASSERT_EQUAL(3, dac_writes);
    TEST_ASSERT_EQUAL_UINT16(40000, last);
    // Second exposure must start >= settle after the layer-1 DAC step.
    uint32_t t_dac1 = 0;
    for (auto& c : hal.calls) {
        if (c.what == "dac" && c.a == 7 && (uint16_t)c.b == 40120) t_dac1 = c.t_us;
    }
    TEST_ASSERT_TRUE(hal.plans[1].t_assert_us >= t_dac1 + 3000);
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_single_frame_program_completes);
    RUN_TEST(test_piezo_step_and_settle_gate_exposure);
    return UNITY_END();
}
