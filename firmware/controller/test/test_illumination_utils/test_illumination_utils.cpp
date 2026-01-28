#include <unity.h>
#include <stdint.h>
#include <cmath>

#include "utils/illumination_utils.h"

void setUp(void) {}
void tearDown(void) {}

/***************************************************************************************************/
/********************************** Intensity Conversion Tests *************************************/
/***************************************************************************************************/

void test_intensity_zero_percent(void) {
    TEST_ASSERT_EQUAL_UINT16(0, intensity_percent_to_dac(0.0f));
}

void test_intensity_100_percent(void) {
    TEST_ASSERT_EQUAL_UINT16(65535, intensity_percent_to_dac(100.0f));
}

void test_intensity_50_percent(void) {
    uint16_t result = intensity_percent_to_dac(50.0f);
    // 50% should be approximately 32767-32768
    TEST_ASSERT_TRUE(result >= 32767 && result <= 32768);
}

void test_intensity_negative_clamps_to_zero(void) {
    TEST_ASSERT_EQUAL_UINT16(0, intensity_percent_to_dac(-10.0f));
    TEST_ASSERT_EQUAL_UINT16(0, intensity_percent_to_dac(-100.0f));
}

void test_intensity_over_100_clamps_to_max(void) {
    TEST_ASSERT_EQUAL_UINT16(65535, intensity_percent_to_dac(150.0f));
    TEST_ASSERT_EQUAL_UINT16(65535, intensity_percent_to_dac(200.0f));
}

void test_intensity_1_percent(void) {
    uint16_t result = intensity_percent_to_dac(1.0f);
    // 1% should be approximately 655
    TEST_ASSERT_TRUE(result >= 654 && result <= 656);
}

void test_intensity_round_trip(void) {
    // Test that converting to DAC and back gives approximately the same value
    float original = 75.0f;
    uint16_t dac = intensity_percent_to_dac(original);
    float recovered = dac_to_intensity_percent(dac);
    // Should be within 0.01% due to quantization
    TEST_ASSERT_FLOAT_WITHIN(0.01f, original, recovered);
}

void test_dac_to_percent_zero(void) {
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.0f, dac_to_intensity_percent(0));
}

void test_dac_to_percent_max(void) {
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 100.0f, dac_to_intensity_percent(65535));
}

/***************************************************************************************************/
/********************************** Firmware Version Tests *****************************************/
/***************************************************************************************************/

void test_version_encode_1_0(void) {
    TEST_ASSERT_EQUAL_HEX8(0x10, encode_firmware_version(1, 0));
}

void test_version_encode_2_3(void) {
    TEST_ASSERT_EQUAL_HEX8(0x23, encode_firmware_version(2, 3));
}

void test_version_encode_15_15(void) {
    TEST_ASSERT_EQUAL_HEX8(0xFF, encode_firmware_version(15, 15));
}

void test_version_encode_0_0(void) {
    TEST_ASSERT_EQUAL_HEX8(0x00, encode_firmware_version(0, 0));
}

void test_version_decode_major(void) {
    TEST_ASSERT_EQUAL_UINT8(1, decode_version_major(0x10));
    TEST_ASSERT_EQUAL_UINT8(2, decode_version_major(0x23));
    TEST_ASSERT_EQUAL_UINT8(15, decode_version_major(0xFF));
    TEST_ASSERT_EQUAL_UINT8(0, decode_version_major(0x00));
}

void test_version_decode_minor(void) {
    TEST_ASSERT_EQUAL_UINT8(0, decode_version_minor(0x10));
    TEST_ASSERT_EQUAL_UINT8(3, decode_version_minor(0x23));
    TEST_ASSERT_EQUAL_UINT8(15, decode_version_minor(0xFF));
    TEST_ASSERT_EQUAL_UINT8(0, decode_version_minor(0x00));
}

void test_version_round_trip(void) {
    for (uint8_t major = 0; major <= 15; major++) {
        for (uint8_t minor = 0; minor <= 15; minor++) {
            uint8_t encoded = encode_firmware_version(major, minor);
            TEST_ASSERT_EQUAL_UINT8(major, decode_version_major(encoded));
            TEST_ASSERT_EQUAL_UINT8(minor, decode_version_minor(encoded));
        }
    }
}

void test_version_truncates_overflow(void) {
    // Values > 15 should be truncated to 4 bits
    TEST_ASSERT_EQUAL_HEX8(0x00, encode_firmware_version(16, 0));  // 16 & 0x0F = 0, result = 0x00
    TEST_ASSERT_EQUAL_HEX8(0x11, encode_firmware_version(17, 1));  // 17 & 0x0F = 1, 1 & 0x0F = 1, result = 0x11
}

/***************************************************************************************************/
/************************************ Port Mask Tests **********************************************/
/***************************************************************************************************/

void test_is_port_selected_single_port(void) {
    uint16_t mask = 0x0001;  // Only port 0 selected
    TEST_ASSERT_TRUE(is_port_selected(mask, 0));
    TEST_ASSERT_FALSE(is_port_selected(mask, 1));
    TEST_ASSERT_FALSE(is_port_selected(mask, 15));
}

void test_is_port_selected_multiple_ports(void) {
    uint16_t mask = 0x001F;  // Ports 0-4 selected (D1-D5)
    TEST_ASSERT_TRUE(is_port_selected(mask, 0));
    TEST_ASSERT_TRUE(is_port_selected(mask, 1));
    TEST_ASSERT_TRUE(is_port_selected(mask, 2));
    TEST_ASSERT_TRUE(is_port_selected(mask, 3));
    TEST_ASSERT_TRUE(is_port_selected(mask, 4));
    TEST_ASSERT_FALSE(is_port_selected(mask, 5));
}

void test_is_port_selected_invalid_index(void) {
    uint16_t mask = 0xFFFF;  // All ports selected
    TEST_ASSERT_FALSE(is_port_selected(mask, -1));
    TEST_ASSERT_FALSE(is_port_selected(mask, 16));
    TEST_ASSERT_FALSE(is_port_selected(mask, 100));
}

void test_should_port_be_on(void) {
    uint16_t on_mask = 0x0005;  // Ports 0 and 2 should be on
    TEST_ASSERT_TRUE(should_port_be_on(on_mask, 0));
    TEST_ASSERT_FALSE(should_port_be_on(on_mask, 1));
    TEST_ASSERT_TRUE(should_port_be_on(on_mask, 2));
    TEST_ASSERT_FALSE(should_port_be_on(on_mask, 3));
}

void test_create_port_mask_empty(void) {
    int ports[] = {};
    TEST_ASSERT_EQUAL_HEX16(0x0000, create_port_mask(ports, 0));
}

void test_create_port_mask_single(void) {
    int ports[] = {3};
    TEST_ASSERT_EQUAL_HEX16(0x0008, create_port_mask(ports, 1));
}

void test_create_port_mask_multiple(void) {
    int ports[] = {0, 2, 4};  // D1, D3, D5
    TEST_ASSERT_EQUAL_HEX16(0x0015, create_port_mask(ports, 3));
}

void test_create_port_mask_all_five(void) {
    int ports[] = {0, 1, 2, 3, 4};  // D1-D5
    TEST_ASSERT_EQUAL_HEX16(0x001F, create_port_mask(ports, 5));
}

void test_create_port_mask_ignores_invalid(void) {
    int ports[] = {0, -1, 16, 100, 4};  // Invalid indices ignored
    TEST_ASSERT_EQUAL_HEX16(0x0011, create_port_mask(ports, 5));  // Only 0 and 4
}

void test_count_selected_ports_none(void) {
    TEST_ASSERT_EQUAL_INT(0, count_selected_ports(0x0000));
}

void test_count_selected_ports_one(void) {
    TEST_ASSERT_EQUAL_INT(1, count_selected_ports(0x0001));
    TEST_ASSERT_EQUAL_INT(1, count_selected_ports(0x8000));
}

void test_count_selected_ports_five(void) {
    TEST_ASSERT_EQUAL_INT(5, count_selected_ports(0x001F));  // D1-D5
}

void test_count_selected_ports_all(void) {
    TEST_ASSERT_EQUAL_INT(16, count_selected_ports(0xFFFF));
}

/***************************************************************************************************/
/******************************** Multi-port Mask Scenario Tests ***********************************/
/***************************************************************************************************/

void test_scenario_turn_on_d1_d2(void) {
    // Scenario: Turn on D1 and D2 only
    uint16_t port_mask = 0x0003;  // Select D1 (bit 0) and D2 (bit 1)
    uint16_t on_mask = 0x0003;    // Both should be on

    TEST_ASSERT_TRUE(is_port_selected(port_mask, 0));
    TEST_ASSERT_TRUE(is_port_selected(port_mask, 1));
    TEST_ASSERT_FALSE(is_port_selected(port_mask, 2));

    TEST_ASSERT_TRUE(should_port_be_on(on_mask, 0));
    TEST_ASSERT_TRUE(should_port_be_on(on_mask, 1));
}

void test_scenario_turn_off_d3_leave_others(void) {
    // Scenario: Turn off D3 only, leave others unchanged
    uint16_t port_mask = 0x0004;  // Select only D3 (bit 2)
    uint16_t on_mask = 0x0000;    // Turn it off

    TEST_ASSERT_TRUE(is_port_selected(port_mask, 2));
    TEST_ASSERT_FALSE(is_port_selected(port_mask, 0));
    TEST_ASSERT_FALSE(is_port_selected(port_mask, 1));

    TEST_ASSERT_FALSE(should_port_be_on(on_mask, 2));
}

void test_scenario_turn_on_d1_off_d2(void) {
    // Scenario: Turn on D1, turn off D2 in one command
    uint16_t port_mask = 0x0003;  // Select D1 and D2
    uint16_t on_mask = 0x0001;    // D1 on, D2 off

    TEST_ASSERT_TRUE(should_port_be_on(on_mask, 0));   // D1 on
    TEST_ASSERT_FALSE(should_port_be_on(on_mask, 1));  // D2 off
}

void test_scenario_all_five_on(void) {
    // Scenario: Turn on all five ports (D1-D5)
    uint16_t port_mask = 0x001F;
    uint16_t on_mask = 0x001F;

    TEST_ASSERT_EQUAL_INT(5, count_selected_ports(port_mask));
    for (int i = 0; i < 5; i++) {
        TEST_ASSERT_TRUE(is_port_selected(port_mask, i));
        TEST_ASSERT_TRUE(should_port_be_on(on_mask, i));
    }
}

void test_scenario_all_five_off(void) {
    // Scenario: Turn off all five ports (D1-D5)
    uint16_t port_mask = 0x001F;
    uint16_t on_mask = 0x0000;

    TEST_ASSERT_EQUAL_INT(5, count_selected_ports(port_mask));
    for (int i = 0; i < 5; i++) {
        TEST_ASSERT_TRUE(is_port_selected(port_mask, i));
        TEST_ASSERT_FALSE(should_port_be_on(on_mask, i));
    }
}

int main(int argc, char **argv) {
    UNITY_BEGIN();

    // Intensity conversion tests
    RUN_TEST(test_intensity_zero_percent);
    RUN_TEST(test_intensity_100_percent);
    RUN_TEST(test_intensity_50_percent);
    RUN_TEST(test_intensity_negative_clamps_to_zero);
    RUN_TEST(test_intensity_over_100_clamps_to_max);
    RUN_TEST(test_intensity_1_percent);
    RUN_TEST(test_intensity_round_trip);
    RUN_TEST(test_dac_to_percent_zero);
    RUN_TEST(test_dac_to_percent_max);

    // Firmware version tests
    RUN_TEST(test_version_encode_1_0);
    RUN_TEST(test_version_encode_2_3);
    RUN_TEST(test_version_encode_15_15);
    RUN_TEST(test_version_encode_0_0);
    RUN_TEST(test_version_decode_major);
    RUN_TEST(test_version_decode_minor);
    RUN_TEST(test_version_round_trip);
    RUN_TEST(test_version_truncates_overflow);

    // Port mask tests
    RUN_TEST(test_is_port_selected_single_port);
    RUN_TEST(test_is_port_selected_multiple_ports);
    RUN_TEST(test_is_port_selected_invalid_index);
    RUN_TEST(test_should_port_be_on);
    RUN_TEST(test_create_port_mask_empty);
    RUN_TEST(test_create_port_mask_single);
    RUN_TEST(test_create_port_mask_multiple);
    RUN_TEST(test_create_port_mask_all_five);
    RUN_TEST(test_create_port_mask_ignores_invalid);
    RUN_TEST(test_count_selected_ports_none);
    RUN_TEST(test_count_selected_ports_one);
    RUN_TEST(test_count_selected_ports_five);
    RUN_TEST(test_count_selected_ports_all);

    // Scenario tests
    RUN_TEST(test_scenario_turn_on_d1_d2);
    RUN_TEST(test_scenario_turn_off_d3_leave_others);
    RUN_TEST(test_scenario_turn_on_d1_off_d2);
    RUN_TEST(test_scenario_all_five_on);
    RUN_TEST(test_scenario_all_five_off);

    return UNITY_END();
}
