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
    c.filter_target = 0;
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

// Each duration is bounded on its own above, but the engine's deadlines add lead + strobe + exposure +
// readout of one exposure: that sum has to respect the bound too (it is what the wrap-safe comparisons
// see), and the channel is named.
void test_exposure_deadline_sum_is_bounded(void) {
    SeqLoop l = good_loop();
    SeqChannel ch[2] = {good_channel(), good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    cams[0].strobe_delay_us = kMaxDurationUs / 2;
    cams[0].readout_time_us = 2;
    ch[1].exposure_us = kMaxDurationUs / 2;  // in bounds alone; over it together with the strobe + readout
    ValidationResult r = validate(l, ch, cams, 1, 8, 8);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadDuration, (uint8_t)r.error);
    TEST_ASSERT_EQUAL_UINT8(1, r.detail);
    ch[1].exposure_us = kMaxDurationUs / 2 - 2;
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None, (uint8_t)validate(l, ch, cams, 1, 8, 8).error);
}

// A TTL channel lights one shared mask for all its cameras, with one OFF edge per camera: the cameras'
// strobe delays have to agree, or the earliest OFF darkens the others. An LED-only channel is free to mix.
void test_ttl_channel_cameras_must_share_a_strobe_delay(void) {
    SeqLoop l = good_loop();
    SeqChannel ch[2] = {good_channel(), good_channel()};
    ch[0].camera_mask = 0x03;
    SeqCameraConfig cams[2] = {cam_level(), cam_level()};
    cams[1].strobe_delay_us = 2000;
    ValidationResult r = validate(l, ch, cams, 2, 8, 8);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadCamera, (uint8_t)r.error);
    TEST_ASSERT_EQUAL_UINT8(0, r.detail);
    ch[0].illum_ttl_mask = 0;
    ch[0].led_pattern = 1;
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None, (uint8_t)validate(l, ch, cams, 2, 8, 8).error);
    cams[1].strobe_delay_us = cams[0].strobe_delay_us;
    ch[0].illum_ttl_mask = 0x01;
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None, (uint8_t)validate(l, ch, cams, 2, 8, 8).error);
}

// SeqError travels in a status byte and is mirrored by number in software/control/sequencer_program.py:
// an enumerator may be renamed (ReadyTimeout -> ReadyLineStuck, 2026-09-21), never moved.
void test_error_codes_keep_their_wire_numbers(void) {
    TEST_ASSERT_EQUAL_UINT8(0, (uint8_t)SeqError::None);
    TEST_ASSERT_EQUAL_UINT8(7, (uint8_t)SeqError::WaitTimeout);
    TEST_ASSERT_EQUAL_UINT8(9, (uint8_t)SeqError::ReadyLineStuck);
    TEST_ASSERT_EQUAL_UINT8(10, (uint8_t)SeqError::Canceled);
    TEST_ASSERT_EQUAL_UINT8(11, (uint8_t)SeqError::StackOutOfRange);
    TEST_ASSERT_EQUAL_UINT8(12, (uint8_t)SeqError::InterlockOpen);
}

// The structs are wire format for the v1 shim (seq_wire.h) — sizes are frozen.
void test_wire_struct_sizes_are_frozen(void) {
    TEST_ASSERT_EQUAL(15, (int)sizeof(seq::SeqLoop));
    TEST_ASSERT_EQUAL(24, (int)sizeof(seq::SeqChannel));  // v2: + illum_lead_us + reserved
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_valid_program_passes);
    RUN_TEST(test_zero_layers_rejected);
    RUN_TEST(test_zero_exposure_rejected_with_channel_index);
    RUN_TEST(test_camera_mask_beyond_configured_cameras_rejected);
    RUN_TEST(test_stepper_axis_out_of_range_rejected);
    RUN_TEST(test_channel_count_bounds);
    RUN_TEST(test_exposure_deadline_sum_is_bounded);
    RUN_TEST(test_ttl_channel_cameras_must_share_a_strobe_delay);
    RUN_TEST(test_error_codes_keep_their_wire_numbers);
    RUN_TEST(test_wire_struct_sizes_are_frozen);
    return UNITY_END();
}
