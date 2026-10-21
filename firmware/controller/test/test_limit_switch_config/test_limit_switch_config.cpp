#include <unity.h>
#include <string.h>
#include "utils/limit_switch_config.h"

// Include sources directly for native tests (env:native builds no src/ code into tests)
#include "utils/limit_switch_config.cpp"
#include "utils/crc8.cpp"

void setUp(void) {}
void tearDown(void) {}

void test_apply_replaces_only_the_stop_nibble_and_clears_home_event(void) {
    TEST_ASSERT_EQUAL_HEX32(0x00F0C005u, lsc_apply(0x00F3C00Fu, 0x05));
    TEST_ASSERT_EQUAL_HEX32(0x00F0C00Au, lsc_apply(0x00F3C00Fu, 0xFA));  // only the nibble used
}

void test_apply_keeps_invert_stop_direction(void) {
    TEST_ASSERT_EQUAL_HEX32(0x0000001Fu, lsc_apply(0x00000010u, 0x0F));
    TEST_ASSERT_EQUAL_HEX32(0x00000013u, lsc_apply(0x0003001Cu, 0x03));
}

void test_polarity_reads_the_requested_side(void) {
    TEST_ASSERT_EQUAL_UINT8(1, lsc_polarity(0x08, 2));
    TEST_ASSERT_EQUAL_UINT8(0, lsc_polarity(0x04, 2));
    TEST_ASSERT_EQUAL_UINT8(1, lsc_polarity(0x04, 1));
    TEST_ASSERT_EQUAL_UINT8(0, lsc_polarity(0x03, 1));
}

void test_record_round_trips(void) {
    const uint8_t cfg[3] = {0x03, 0x03, 0x0F};
    uint8_t rec[LSC_RECORD_LEN];
    uint8_t out[3];
    lsc_record_pack(cfg, rec);
    TEST_ASSERT_TRUE(lsc_record_unpack(rec, out));
    TEST_ASSERT_EQUAL_HEX8_ARRAY(cfg, out, 3);
}

static void expect_rejected(const uint8_t *rec) {
    uint8_t out[3] = {0xAA, 0xAA, 0xAA};
    TEST_ASSERT_FALSE(lsc_record_unpack(rec, out));
    const uint8_t def[3] = {LSC_DEFAULT, LSC_DEFAULT, LSC_DEFAULT};
    TEST_ASSERT_EQUAL_HEX8_ARRAY(def, out, 3);
}

void test_record_unpack_rejects_blank_and_corrupt(void) {
    uint8_t rec[LSC_RECORD_LEN];
    memset(rec, 0xFF, sizeof(rec));
    expect_rejected(rec);
    memset(rec, 0x00, sizeof(rec));
    expect_rejected(rec);

    const uint8_t cfg[3] = {0x03, 0x05, 0x0F};
    lsc_record_pack(cfg, rec);
    rec[4] ^= 0x01;  // one flipped data bit
    expect_rejected(rec);

    lsc_record_pack(cfg, rec);
    rec[5] = 0x13;  // nibble out of range, checksum recomputed so only the range check can reject it
    rec[6] = crc8ccitt(rec, 6);
    expect_rejected(rec);
}

void test_record_unpack_rejects_wrong_marker_and_version(void) {
    const uint8_t cfg[3] = {0x03, 0x03, 0x03};
    uint8_t rec[LSC_RECORD_LEN];
    lsc_record_pack(cfg, rec);
    rec[0] = 'X';
    rec[6] = crc8ccitt(rec, 6);
    expect_rejected(rec);
    lsc_record_pack(cfg, rec);
    rec[2] = 2;
    rec[6] = crc8ccitt(rec, 6);
    expect_rejected(rec);
}

void test_status_bits_land_in_byte_18_bits_2_and_3(void) {
    TEST_ASSERT_EQUAL_HEX8(0x00, lsc_status_bits(0x00));
    TEST_ASSERT_EQUAL_HEX8(0x04, lsc_status_bits(0x01));
    TEST_ASSERT_EQUAL_HEX8(0x08, lsc_status_bits(0x02));
    TEST_ASSERT_EQUAL_HEX8(0x0C, lsc_status_bits(0xFF));
}

int main(int argc, char **argv) {
    UNITY_BEGIN();
    RUN_TEST(test_apply_replaces_only_the_stop_nibble_and_clears_home_event);
    RUN_TEST(test_apply_keeps_invert_stop_direction);
    RUN_TEST(test_polarity_reads_the_requested_side);
    RUN_TEST(test_record_round_trips);
    RUN_TEST(test_record_unpack_rejects_blank_and_corrupt);
    RUN_TEST(test_record_unpack_rejects_wrong_marker_and_version);
    RUN_TEST(test_status_bits_land_in_byte_18_bits_2_and_3);
    return UNITY_END();
}
