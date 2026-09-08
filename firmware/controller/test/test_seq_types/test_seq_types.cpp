#include <unity.h>
#include "sequencer/seq_types.h"

// Include source directly for native tests (same convention as test_crc8)
#include "sequencer/seq_types.cpp"

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

void test_valid_program_passes(void) {
    SeqLoop l = good_loop();
    SeqChannel ch[2] = {good_channel(), good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    ValidationResult r = validate(l, ch, cams, 1, 8, 8);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None, (uint8_t)r.error);
}

void test_zero_layers_rejected(void) {
    SeqLoop l = good_loop();
    l.n_layers = 0;
    SeqChannel ch[2] = {good_channel(), good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadLayerCount,
                            (uint8_t)validate(l, ch, cams, 1, 8, 8).error);
}

void test_zero_exposure_rejected_with_channel_index(void) {
    SeqLoop l = good_loop();
    SeqChannel ch[2] = {good_channel(), good_channel()};
    ch[1].exposure_us = 0;
    SeqCameraConfig cams[1] = {cam_level()};
    ValidationResult r = validate(l, ch, cams, 1, 8, 8);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadExposure, (uint8_t)r.error);
    TEST_ASSERT_EQUAL_UINT8(1, r.detail);
}

void test_camera_mask_beyond_configured_cameras_rejected(void) {
    SeqLoop l = good_loop();
    SeqChannel ch[2] = {good_channel(), good_channel()};
    ch[0].camera_mask = 0x02;  // camera 1, but only 1 camera configured
    SeqCameraConfig cams[1] = {cam_level()};
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadCamera,
                            (uint8_t)validate(l, ch, cams, 1, 8, 8).error);
}

void test_stepper_axis_out_of_range_rejected(void) {
    SeqLoop l = good_loop();
    l.stack_axis_type = (uint8_t)StackAxisType::Stepper;
    l.stack_axis_id = 8;
    SeqChannel ch[2] = {good_channel(), good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadStackAxis,
                            (uint8_t)validate(l, ch, cams, 1, 8, 8).error);
}

void test_channel_count_bounds(void) {
    SeqLoop l = good_loop();
    l.n_channels = 0;
    SeqChannel ch[2] = {good_channel(), good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadChannelCount,
                            (uint8_t)validate(l, ch, cams, 1, 8, 8).error);
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_valid_program_passes);
    RUN_TEST(test_zero_layers_rejected);
    RUN_TEST(test_zero_exposure_rejected_with_channel_index);
    RUN_TEST(test_camera_mask_beyond_configured_cameras_rejected);
    RUN_TEST(test_stepper_axis_out_of_range_rejected);
    RUN_TEST(test_channel_count_bounds);
    return UNITY_END();
}
