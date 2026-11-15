#include <unity.h>

#include <stdint.h>
#include <string.h>

#include "protocol/crc16.h"

// Include source directly for native tests (grants access to the file-scope
// CRC16_TABLE for spot-checks).
#include "protocol/crc16.cpp"

using protocol::crc16_ccitt;

void setUp(void) {}
void tearDown(void) {}

// --- Known-answer vectors -------------------------------------------------

void test_crc16_empty(void) {
    // Empty buffer returns the init value unchanged.
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, crc16_ccitt(nullptr, 0));
}

void test_crc16_check_value(void) {
    // CRC-16/CCITT-FALSE canonical check value over "123456789".
    const uint8_t msg[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    TEST_ASSERT_EQUAL_HEX16(0x29B1, crc16_ccitt(msg, sizeof(msg)));
}

void test_crc16_single_byte(void) {
    // One 0x00 byte: crc = (0xFFFF<<8) ^ table[(0xFF ^ 0x00)] = table[0xFF]
    // combined with the high byte shifted in. Just assert determinism + shape.
    const uint8_t z = 0x00;
    uint16_t a = crc16_ccitt(&z, 1);
    uint16_t b = crc16_ccitt(&z, 1);
    TEST_ASSERT_EQUAL_HEX16(a, b);
    // A single 0x00 must not leave the CRC at the init value.
    TEST_ASSERT_NOT_EQUAL(0xFFFF, a);
}

// --- 506-byte payload (max protocol-v2 payload) ---------------------------

void test_crc16_max_payload_deterministic(void) {
    uint8_t buf[506];
    for (size_t i = 0; i < sizeof(buf); ++i) {
        buf[i] = (uint8_t)(i * 31 + 7);  // arbitrary deterministic pattern
    }
    uint16_t first = crc16_ccitt(buf, sizeof(buf));
    uint16_t second = crc16_ccitt(buf, sizeof(buf));
    TEST_ASSERT_EQUAL_HEX16(first, second);

    // A single-bit flip must change the CRC (error detection sanity).
    buf[253] ^= 0x01;
    TEST_ASSERT_NOT_EQUAL(first, crc16_ccitt(buf, sizeof(buf)));
}

// --- Lookup table spot-checks --------------------------------------------

void test_crc16_table_spot_checks(void) {
    TEST_ASSERT_EQUAL_HEX16(0x0000, protocol::CRC16_TABLE[0]);
    TEST_ASSERT_EQUAL_HEX16(0x1021, protocol::CRC16_TABLE[1]);
    TEST_ASSERT_EQUAL_HEX16(0x1EF0, protocol::CRC16_TABLE[255]);
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_crc16_empty);
    RUN_TEST(test_crc16_check_value);
    RUN_TEST(test_crc16_single_byte);
    RUN_TEST(test_crc16_max_payload_deterministic);
    RUN_TEST(test_crc16_table_spot_checks);
    return UNITY_END();
}
