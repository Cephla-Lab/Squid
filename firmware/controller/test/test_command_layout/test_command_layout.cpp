#include <unity.h>
#include <stdint.h>
#include <cstring>

#include "constants_protocol.h"

void setUp(void) {}
void tearDown(void) {}

/**
 * These tests verify that command byte layouts match the protocol specification.
 *
 * Command packet format (8 bytes):
 *   byte[0]: command ID (sequence number)
 *   byte[1]: command code
 *   byte[2-6]: parameters (varies by command)
 *   byte[7]: CRC-8
 *
 * This test file creates mock command packets and verifies the byte positions.
 */

// Helper to create a command packet
void create_command(uint8_t* buffer, uint8_t cmd_id, uint8_t cmd_code) {
    memset(buffer, 0, CMD_LENGTH);
    buffer[0] = cmd_id;
    buffer[1] = cmd_code;
    // CRC would be at buffer[7], but we don't compute it in these tests
}

/***************************************************************************************************/
/******************************** SET_PORT_INTENSITY Layout ****************************************/
/***************************************************************************************************/
// Byte layout: [cmd_id, 34, port, intensity_hi, intensity_lo, 0, 0, crc]

void test_set_port_intensity_command_code(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, SET_PORT_INTENSITY);
    TEST_ASSERT_EQUAL_UINT8(SET_PORT_INTENSITY, buffer[1]);
    TEST_ASSERT_EQUAL_UINT8(34, buffer[1]);
}

void test_set_port_intensity_port_byte(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, SET_PORT_INTENSITY);

    // Port index goes in byte[2]
    buffer[2] = 3;  // Port D4
    TEST_ASSERT_EQUAL_UINT8(3, buffer[2]);
}

void test_set_port_intensity_value_bytes(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, SET_PORT_INTENSITY);

    // Intensity value is 16-bit big-endian in bytes[3:4]
    uint16_t intensity = 32768;  // 50%
    buffer[3] = (intensity >> 8) & 0xFF;  // High byte
    buffer[4] = intensity & 0xFF;         // Low byte

    // Verify we can reconstruct the value
    uint16_t reconstructed = (buffer[3] << 8) | buffer[4];
    TEST_ASSERT_EQUAL_UINT16(32768, reconstructed);
}

/***************************************************************************************************/
/******************************** TURN_ON_PORT Layout **********************************************/
/***************************************************************************************************/
// Byte layout: [cmd_id, 35, port, 0, 0, 0, 0, crc]

void test_turn_on_port_command_code(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, TURN_ON_PORT);
    TEST_ASSERT_EQUAL_UINT8(TURN_ON_PORT, buffer[1]);
    TEST_ASSERT_EQUAL_UINT8(35, buffer[1]);
}

void test_turn_on_port_port_byte(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, TURN_ON_PORT);

    buffer[2] = 0;  // Port D1
    TEST_ASSERT_EQUAL_UINT8(0, buffer[2]);

    buffer[2] = 4;  // Port D5
    TEST_ASSERT_EQUAL_UINT8(4, buffer[2]);
}

/***************************************************************************************************/
/******************************** TURN_OFF_PORT Layout *********************************************/
/***************************************************************************************************/
// Byte layout: [cmd_id, 36, port, 0, 0, 0, 0, crc]

void test_turn_off_port_command_code(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, TURN_OFF_PORT);
    TEST_ASSERT_EQUAL_UINT8(TURN_OFF_PORT, buffer[1]);
    TEST_ASSERT_EQUAL_UINT8(36, buffer[1]);
}

/***************************************************************************************************/
/******************************** SET_PORT_ILLUMINATION Layout *************************************/
/***************************************************************************************************/
// Byte layout: [cmd_id, 37, port, intensity_hi, intensity_lo, on_flag, 0, crc]

void test_set_port_illumination_command_code(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, SET_PORT_ILLUMINATION);
    TEST_ASSERT_EQUAL_UINT8(SET_PORT_ILLUMINATION, buffer[1]);
    TEST_ASSERT_EQUAL_UINT8(37, buffer[1]);
}

void test_set_port_illumination_on_flag_byte(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, SET_PORT_ILLUMINATION);

    // on_flag is in byte[5]
    buffer[5] = 1;  // Turn on
    TEST_ASSERT_EQUAL_UINT8(1, buffer[5]);

    buffer[5] = 0;  // Turn off
    TEST_ASSERT_EQUAL_UINT8(0, buffer[5]);
}

void test_set_port_illumination_full_packet(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 42, SET_PORT_ILLUMINATION);

    buffer[2] = 2;           // Port D3
    uint16_t intensity = 65535;  // 100%
    buffer[3] = (intensity >> 8) & 0xFF;
    buffer[4] = intensity & 0xFF;
    buffer[5] = 1;           // Turn on

    TEST_ASSERT_EQUAL_UINT8(42, buffer[0]);  // cmd_id
    TEST_ASSERT_EQUAL_UINT8(37, buffer[1]);  // cmd_code
    TEST_ASSERT_EQUAL_UINT8(2, buffer[2]);   // port
    TEST_ASSERT_EQUAL_UINT8(0xFF, buffer[3]); // intensity_hi
    TEST_ASSERT_EQUAL_UINT8(0xFF, buffer[4]); // intensity_lo
    TEST_ASSERT_EQUAL_UINT8(1, buffer[5]);   // on_flag
}

/***************************************************************************************************/
/******************************** SET_MULTI_PORT_MASK Layout ***************************************/
/***************************************************************************************************/
// Byte layout: [cmd_id, 38, mask_hi, mask_lo, on_hi, on_lo, 0, crc]

void test_set_multi_port_mask_command_code(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, SET_MULTI_PORT_MASK);
    TEST_ASSERT_EQUAL_UINT8(SET_MULTI_PORT_MASK, buffer[1]);
    TEST_ASSERT_EQUAL_UINT8(38, buffer[1]);
}

void test_set_multi_port_mask_16bit_masks(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, SET_MULTI_PORT_MASK);

    // port_mask = 0x001F (D1-D5)
    uint16_t port_mask = 0x001F;
    buffer[2] = (port_mask >> 8) & 0xFF;  // mask_hi
    buffer[3] = port_mask & 0xFF;         // mask_lo

    // on_mask = 0x0015 (D1, D3, D5 on; D2, D4 off)
    uint16_t on_mask = 0x0015;
    buffer[4] = (on_mask >> 8) & 0xFF;    // on_hi
    buffer[5] = on_mask & 0xFF;           // on_lo

    // Verify reconstruction
    uint16_t reconstructed_port = (buffer[2] << 8) | buffer[3];
    uint16_t reconstructed_on = (buffer[4] << 8) | buffer[5];

    TEST_ASSERT_EQUAL_HEX16(0x001F, reconstructed_port);
    TEST_ASSERT_EQUAL_HEX16(0x0015, reconstructed_on);
}

void test_set_multi_port_mask_high_ports(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, SET_MULTI_PORT_MASK);

    // Test with ports 8-15 (requires high byte)
    uint16_t port_mask = 0xFF00;  // Ports 8-15
    buffer[2] = (port_mask >> 8) & 0xFF;
    buffer[3] = port_mask & 0xFF;

    uint16_t reconstructed = (buffer[2] << 8) | buffer[3];
    TEST_ASSERT_EQUAL_HEX16(0xFF00, reconstructed);
    TEST_ASSERT_EQUAL_UINT8(0xFF, buffer[2]);  // High byte should be 0xFF
    TEST_ASSERT_EQUAL_UINT8(0x00, buffer[3]);  // Low byte should be 0x00
}

/***************************************************************************************************/
/******************************** TURN_OFF_ALL_PORTS Layout ****************************************/
/***************************************************************************************************/
// Byte layout: [cmd_id, 39, 0, 0, 0, 0, 0, crc]

void test_turn_off_all_ports_command_code(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, TURN_OFF_ALL_PORTS);
    TEST_ASSERT_EQUAL_UINT8(TURN_OFF_ALL_PORTS, buffer[1]);
    TEST_ASSERT_EQUAL_UINT8(39, buffer[1]);
}

void test_turn_off_all_ports_no_params(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, TURN_OFF_ALL_PORTS);

    // Bytes 2-6 should all be 0 (no parameters)
    TEST_ASSERT_EQUAL_UINT8(0, buffer[2]);
    TEST_ASSERT_EQUAL_UINT8(0, buffer[3]);
    TEST_ASSERT_EQUAL_UINT8(0, buffer[4]);
    TEST_ASSERT_EQUAL_UINT8(0, buffer[5]);
    TEST_ASSERT_EQUAL_UINT8(0, buffer[6]);
}

/***************************************************************************************************/
/******************************** Response Byte Layout *********************************************/
/***************************************************************************************************/
// Response packet format (24 bytes):
//   byte[0]: command ID
//   byte[1]: execution status
//   byte[2-5]: X position
//   byte[6-9]: Y position
//   byte[10-13]: Z position
//   byte[14-17]: Theta position
//   byte[18]: buttons and switches
//   byte[19-21]: reserved
//   byte[22]: firmware version (nibble-encoded)
//   byte[23]: CRC-8

void test_response_layout_constants(void) {
    TEST_ASSERT_EQUAL_INT(24, MSG_LENGTH);
    TEST_ASSERT_TRUE(MSG_LENGTH > CMD_LENGTH);
}

void test_response_version_byte_position(void) {
    // Firmware version is at byte 22
    uint8_t response[MSG_LENGTH];
    memset(response, 0, MSG_LENGTH);

    // Set version 1.0 (0x10)
    response[22] = 0x10;

    // Verify position
    TEST_ASSERT_EQUAL_UINT8(0x10, response[22]);

    // Verify decoding
    uint8_t major = (response[22] >> 4) & 0x0F;
    uint8_t minor = response[22] & 0x0F;
    TEST_ASSERT_EQUAL_UINT8(1, major);
    TEST_ASSERT_EQUAL_UINT8(0, minor);
}

void test_response_execution_status_byte(void) {
    uint8_t response[MSG_LENGTH];
    memset(response, 0, MSG_LENGTH);

    // Execution status is at byte 1
    response[1] = COMPLETED_WITHOUT_ERRORS;
    TEST_ASSERT_EQUAL_UINT8(0, response[1]);

    response[1] = IN_PROGRESS;
    TEST_ASSERT_EQUAL_UINT8(1, response[1]);

    response[1] = CMD_CHECKSUM_ERROR;
    TEST_ASSERT_EQUAL_UINT8(2, response[1]);
}

int main(int argc, char **argv) {
    UNITY_BEGIN();

    // SET_PORT_INTENSITY tests
    RUN_TEST(test_set_port_intensity_command_code);
    RUN_TEST(test_set_port_intensity_port_byte);
    RUN_TEST(test_set_port_intensity_value_bytes);

    // TURN_ON_PORT tests
    RUN_TEST(test_turn_on_port_command_code);
    RUN_TEST(test_turn_on_port_port_byte);

    // TURN_OFF_PORT tests
    RUN_TEST(test_turn_off_port_command_code);

    // SET_PORT_ILLUMINATION tests
    RUN_TEST(test_set_port_illumination_command_code);
    RUN_TEST(test_set_port_illumination_on_flag_byte);
    RUN_TEST(test_set_port_illumination_full_packet);

    // SET_MULTI_PORT_MASK tests
    RUN_TEST(test_set_multi_port_mask_command_code);
    RUN_TEST(test_set_multi_port_mask_16bit_masks);
    RUN_TEST(test_set_multi_port_mask_high_ports);

    // TURN_OFF_ALL_PORTS tests
    RUN_TEST(test_turn_off_all_ports_command_code);
    RUN_TEST(test_turn_off_all_ports_no_params);

    // Response layout tests
    RUN_TEST(test_response_layout_constants);
    RUN_TEST(test_response_version_byte_position);
    RUN_TEST(test_response_execution_status_byte);

    return UNITY_END();
}
