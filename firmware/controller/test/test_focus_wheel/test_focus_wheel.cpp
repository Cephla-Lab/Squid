#include <unity.h>
#include <stdint.h>
#include "utils/focus_wheel.h"
#include "constants_protocol.h"

// Include sources directly for native tests (env:native builds no src/ code into tests)
#include "utils/focus_wheel.cpp"
#include "utils/crc8.cpp"

void setUp(void) {}
void tearDown(void) {}

void test_wheel_delta_is_the_difference(void) {
    TEST_ASSERT_EQUAL_INT32(5, fw_wheel_delta(105, 100));
    TEST_ASSERT_EQUAL_INT32(-7, fw_wheel_delta(-7, 0));
}

void test_wheel_delta_wraps_across_the_panel_counter_limit(void) {
    TEST_ASSERT_EQUAL_INT32(2, fw_wheel_delta(INT32_MIN + 1, INT32_MAX));
    TEST_ASSERT_EQUAL_INT32(-2, fw_wheel_delta(INT32_MAX, INT32_MIN + 1));
}

void test_drives_z_moves_focus_position_only(void) {
    int32_t focus = 1000, count = 42;
    fw_route_delta(FOCUS_WHEEL_DRIVES_Z, -30, &focus, &count);
    TEST_ASSERT_EQUAL_INT32(970, focus);
    TEST_ASSERT_EQUAL_INT32(42, count);
}

void test_to_host_counts_and_leaves_focus_position(void) {
    int32_t focus = 1000, count = 0;
    fw_route_delta(FOCUS_WHEEL_TO_HOST, 12, &focus, &count);
    fw_route_delta(FOCUS_WHEEL_TO_HOST, -5, &focus, &count);
    TEST_ASSERT_EQUAL_INT32(1000, focus);
    TEST_ASSERT_EQUAL_INT32(7, count);
}

void test_host_count_wraps(void) {
    int32_t focus = 0, count = INT32_MAX;
    fw_route_delta(FOCUS_WHEEL_TO_HOST, 1, &focus, &count);
    TEST_ASSERT_EQUAL_INT32(INT32_MIN, count);
}

void test_mode_allowed(void) {
    TEST_ASSERT_TRUE(fw_mode_allowed(FOCUS_WHEEL_DRIVES_Z, false));
    TEST_ASSERT_TRUE(fw_mode_allowed(FOCUS_WHEEL_DRIVES_Z, true));   // handing the wheel back is always fine
    TEST_ASSERT_TRUE(fw_mode_allowed(FOCUS_WHEEL_TO_HOST, false));
    TEST_ASSERT_FALSE(fw_mode_allowed(FOCUS_WHEEL_TO_HOST, true));   // encoder reporting holds bytes 14-17
    TEST_ASSERT_FALSE(fw_mode_allowed(2, false));
    TEST_ASSERT_FALSE(fw_mode_allowed(0xFF, false));
}

void test_encoder_reporting_allowed(void) {
    TEST_ASSERT_TRUE(fw_encoder_reporting_allowed(true, FOCUS_WHEEL_DRIVES_Z));
    TEST_ASSERT_FALSE(fw_encoder_reporting_allowed(true, FOCUS_WHEEL_TO_HOST));
    TEST_ASSERT_TRUE(fw_encoder_reporting_allowed(false, FOCUS_WHEEL_TO_HOST));  // turning it off is always fine
}

void test_status_fields_pack_count_and_flag(void) {
    uint8_t out[8] = {0xAA, 0xAA, 0xAA, 0xAA, 0x5C, 0xAA, 0xAA, 0xAA};
    fw_status_fields(-2, out);
    const uint8_t expected[8] = {0xFF, 0xFF, 0xFF, 0xFE, 0x5C, 0x80, 0x00, 0x00};
    TEST_ASSERT_EQUAL_HEX8_ARRAY(expected, out, 8);   // byte 18 (out[4]) untouched

    fw_status_fields(0x01020304, out);
    TEST_ASSERT_EQUAL_HEX8(0x01, out[0]);
    TEST_ASSERT_EQUAL_HEX8(0x02, out[1]);
    TEST_ASSERT_EQUAL_HEX8(0x03, out[2]);
    TEST_ASSERT_EQUAL_HEX8(0x04, out[3]);
}

void test_status_flag_is_not_an_encoder_report(void) {
    uint8_t out[8] = {0};
    fw_status_fields(0, out);
    TEST_ASSERT_EQUAL_UINT8(0, out[5] & (1 << ENC_FLAG_REPORTING));
}

void test_power_up_record_round_trips(void) {
    uint8_t rec[FW_RECORD_LEN];
    fw_record_pack(FOCUS_WHEEL_TO_HOST, rec);
    TEST_ASSERT_EQUAL_UINT8('F', rec[0]);
    TEST_ASSERT_EQUAL_UINT8('W', rec[1]);
    TEST_ASSERT_EQUAL_UINT8(FOCUS_WHEEL_TO_HOST, fw_record_unpack(rec));
    fw_record_pack(FOCUS_WHEEL_DRIVES_Z, rec);
    TEST_ASSERT_EQUAL_UINT8(FOCUS_WHEEL_DRIVES_Z, fw_record_unpack(rec));
    // the record sits after the limit-switch record (EEPROM 0-6)
    TEST_ASSERT_TRUE(FW_RECORD_ADDR >= 7);
}

void test_blank_or_invalid_power_up_record_drives_z(void) {
    uint8_t blank[FW_RECORD_LEN] = {0xFF, 0xFF, 0xFF, 0xFF};
    TEST_ASSERT_EQUAL_UINT8(FOCUS_WHEEL_DRIVES_Z, fw_record_unpack(blank));
    uint8_t rec[FW_RECORD_LEN];
    fw_record_pack(FOCUS_WHEEL_TO_HOST, rec);
    rec[3] ^= 0x01;  // bad checksum
    TEST_ASSERT_EQUAL_UINT8(FOCUS_WHEEL_DRIVES_Z, fw_record_unpack(rec));
    fw_record_pack(7, rec);  // unknown mode, valid checksum
    TEST_ASSERT_EQUAL_UINT8(FOCUS_WHEEL_DRIVES_Z, fw_record_unpack(rec));
}

int main(int argc, char **argv) {
    UNITY_BEGIN();
    RUN_TEST(test_wheel_delta_is_the_difference);
    RUN_TEST(test_wheel_delta_wraps_across_the_panel_counter_limit);
    RUN_TEST(test_drives_z_moves_focus_position_only);
    RUN_TEST(test_to_host_counts_and_leaves_focus_position);
    RUN_TEST(test_host_count_wraps);
    RUN_TEST(test_mode_allowed);
    RUN_TEST(test_encoder_reporting_allowed);
    RUN_TEST(test_status_fields_pack_count_and_flag);
    RUN_TEST(test_status_flag_is_not_an_encoder_report);
    RUN_TEST(test_power_up_record_round_trips);
    RUN_TEST(test_blank_or_invalid_power_up_record_drives_z);
    return UNITY_END();
}
