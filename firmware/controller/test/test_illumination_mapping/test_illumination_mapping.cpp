#include <unity.h>
#include <stdint.h>

// Include protocol constants and mapping utilities
#include "constants_protocol.h"
#include "utils/illumination_mapping.h"

void setUp(void) {}
void tearDown(void) {}

// Test illumination_source_to_port_index mapping

void test_source_d1_maps_to_port_0(void) {
    TEST_ASSERT_EQUAL_INT(0, illumination_source_to_port_index(ILLUMINATION_D1));
}

void test_source_d2_maps_to_port_1(void) {
    TEST_ASSERT_EQUAL_INT(1, illumination_source_to_port_index(ILLUMINATION_D2));
}

void test_source_d3_maps_to_port_2(void) {
    // Note: D3 has source code 14 (non-sequential!)
    TEST_ASSERT_EQUAL_INT(2, illumination_source_to_port_index(ILLUMINATION_D3));
    TEST_ASSERT_EQUAL_INT(2, illumination_source_to_port_index(14));
}

void test_source_d4_maps_to_port_3(void) {
    // Note: D4 has source code 13 (non-sequential!)
    TEST_ASSERT_EQUAL_INT(3, illumination_source_to_port_index(ILLUMINATION_D4));
    TEST_ASSERT_EQUAL_INT(3, illumination_source_to_port_index(13));
}

void test_source_d5_maps_to_port_4(void) {
    TEST_ASSERT_EQUAL_INT(4, illumination_source_to_port_index(ILLUMINATION_D5));
}

void test_unknown_source_returns_negative_one(void) {
    TEST_ASSERT_EQUAL_INT(-1, illumination_source_to_port_index(0));
    TEST_ASSERT_EQUAL_INT(-1, illumination_source_to_port_index(10));
    TEST_ASSERT_EQUAL_INT(-1, illumination_source_to_port_index(16));
    TEST_ASSERT_EQUAL_INT(-1, illumination_source_to_port_index(100));
}

// Test port_index_to_illumination_source mapping (reverse)

void test_port_0_maps_to_source_d1(void) {
    TEST_ASSERT_EQUAL_INT(ILLUMINATION_D1, port_index_to_illumination_source(0));
}

void test_port_1_maps_to_source_d2(void) {
    TEST_ASSERT_EQUAL_INT(ILLUMINATION_D2, port_index_to_illumination_source(1));
}

void test_port_2_maps_to_source_d3(void) {
    TEST_ASSERT_EQUAL_INT(ILLUMINATION_D3, port_index_to_illumination_source(2));
    TEST_ASSERT_EQUAL_INT(14, port_index_to_illumination_source(2));
}

void test_port_3_maps_to_source_d4(void) {
    TEST_ASSERT_EQUAL_INT(ILLUMINATION_D4, port_index_to_illumination_source(3));
    TEST_ASSERT_EQUAL_INT(13, port_index_to_illumination_source(3));
}

void test_port_4_maps_to_source_d5(void) {
    TEST_ASSERT_EQUAL_INT(ILLUMINATION_D5, port_index_to_illumination_source(4));
}

void test_invalid_port_returns_negative_one(void) {
    TEST_ASSERT_EQUAL_INT(-1, port_index_to_illumination_source(-1));
    TEST_ASSERT_EQUAL_INT(-1, port_index_to_illumination_source(5));
    TEST_ASSERT_EQUAL_INT(-1, port_index_to_illumination_source(100));
}

// Test round-trip mapping consistency

void test_round_trip_source_to_port_to_source(void) {
    // For each valid source, map to port and back to source
    int sources[] = {ILLUMINATION_D1, ILLUMINATION_D2, ILLUMINATION_D3, ILLUMINATION_D4, ILLUMINATION_D5};
    for (int i = 0; i < 5; i++) {
        int port = illumination_source_to_port_index(sources[i]);
        int source_back = port_index_to_illumination_source(port);
        TEST_ASSERT_EQUAL_INT_MESSAGE(sources[i], source_back, "Round trip failed");
    }
}

void test_round_trip_port_to_source_to_port(void) {
    // For each valid port, map to source and back to port
    for (int port = 0; port < 5; port++) {
        int source = port_index_to_illumination_source(port);
        int port_back = illumination_source_to_port_index(source);
        TEST_ASSERT_EQUAL_INT_MESSAGE(port, port_back, "Round trip failed");
    }
}

// Test non-sequential D3/D4 mapping specifically
void test_d3_d4_non_sequential_mapping(void) {
    // This is a critical test: D3 and D4 have swapped source codes
    // D3 = 14, D4 = 13 (not 13, 14 as you might expect)
    TEST_ASSERT_EQUAL_INT(2, illumination_source_to_port_index(14));  // D3
    TEST_ASSERT_EQUAL_INT(3, illumination_source_to_port_index(13));  // D4

    // Verify the constants match
    TEST_ASSERT_EQUAL_INT(14, ILLUMINATION_D3);
    TEST_ASSERT_EQUAL_INT(13, ILLUMINATION_D4);
}

int main(int argc, char **argv) {
    UNITY_BEGIN();

    // Source to port mapping
    RUN_TEST(test_source_d1_maps_to_port_0);
    RUN_TEST(test_source_d2_maps_to_port_1);
    RUN_TEST(test_source_d3_maps_to_port_2);
    RUN_TEST(test_source_d4_maps_to_port_3);
    RUN_TEST(test_source_d5_maps_to_port_4);
    RUN_TEST(test_unknown_source_returns_negative_one);

    // Port to source mapping
    RUN_TEST(test_port_0_maps_to_source_d1);
    RUN_TEST(test_port_1_maps_to_source_d2);
    RUN_TEST(test_port_2_maps_to_source_d3);
    RUN_TEST(test_port_3_maps_to_source_d4);
    RUN_TEST(test_port_4_maps_to_source_d5);
    RUN_TEST(test_invalid_port_returns_negative_one);

    // Round-trip consistency
    RUN_TEST(test_round_trip_source_to_port_to_source);
    RUN_TEST(test_round_trip_port_to_source_to_port);

    // Critical D3/D4 non-sequential mapping
    RUN_TEST(test_d3_d4_non_sequential_mapping);

    return UNITY_END();
}
