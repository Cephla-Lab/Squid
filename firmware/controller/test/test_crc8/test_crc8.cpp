#include <unity.h>
#include "utils/crc8.h"

// Include source directly for native tests
#include "utils/crc8.cpp"

void setUp(void) {
    // Set up before each test
}

void tearDown(void) {
    // Clean up after each test
}

void test_crc8_empty_data(void) {
    uint8_t data[] = {};
    TEST_ASSERT_EQUAL_UINT8(0x00, crc8ccitt(data, 0));
}

void test_crc8_single_byte_zero(void) {
    uint8_t data[] = {0x00};
    TEST_ASSERT_EQUAL_UINT8(0x00, crc8ccitt(data, 1));
}

void test_crc8_single_byte_nonzero(void) {
    uint8_t data[] = {0x01};
    TEST_ASSERT_EQUAL_UINT8(0x07, crc8ccitt(data, 1));
}

void test_crc8_multiple_bytes(void) {
    uint8_t data[] = {0x01, 0x02, 0x03};
    uint8_t crc = crc8ccitt(data, 3);
    // Verify CRC is deterministic
    TEST_ASSERT_EQUAL_UINT8(crc, crc8ccitt(data, 3));
}

void test_crc8_command_packet(void) {
    // Simulate a typical 7-byte command packet (excluding CRC byte)
    uint8_t packet[] = {0x00, 0x01, 0x00, 0x00, 0x10, 0x00, 0x00};
    uint8_t crc = crc8ccitt(packet, 7);
    // CRC should be non-zero for this data
    TEST_ASSERT_NOT_EQUAL(0x00, crc);
}

void test_crc8_different_data_different_crc(void) {
    uint8_t data1[] = {0x01, 0x02, 0x03};
    uint8_t data2[] = {0x01, 0x02, 0x04};
    TEST_ASSERT_NOT_EQUAL(crc8ccitt(data1, 3), crc8ccitt(data2, 3));
}

void test_crc8_known_value(void) {
    // Test with known CRC-8-CCITT values
    // "123456789" should produce CRC = 0xF4 for CRC-8-CCITT
    uint8_t data[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    TEST_ASSERT_EQUAL_UINT8(0xF4, crc8ccitt(data, 9));
}

int main(int argc, char **argv) {
    UNITY_BEGIN();

    RUN_TEST(test_crc8_empty_data);
    RUN_TEST(test_crc8_single_byte_zero);
    RUN_TEST(test_crc8_single_byte_nonzero);
    RUN_TEST(test_crc8_multiple_bytes);
    RUN_TEST(test_crc8_command_packet);
    RUN_TEST(test_crc8_different_data_different_crc);
    RUN_TEST(test_crc8_known_value);

    return UNITY_END();
}
