// Y-axis test image (src/y_axis_test_build.h): the two status bytes it adds. The same vectors are in
// firmware/tools/y_switch_monitor.py --self-test, so the firmware's packing and the monitor's reading agree.
#include <unity.h>

#include "y_axis_test_build.h"

void setUp(void) {}
void tearDown(void) {}

void test_pack_status_layout(void)
{
    uint8_t out[2];

    // nothing active, stock configs (both inputs enabled, active-low) on both axes
    y_axis_test_pack_status(0x00, 0x00, 0x03, 0x03, out);
    TEST_ASSERT_EQUAL_HEX8(0x80, out[0]);
    TEST_ASSERT_EQUAL_HEX8(0x33, out[1]);

    // Y left active, X right active; Y stored as left active-high (0x07), X stock
    y_axis_test_pack_status(0x01, 0x02, 0x07, 0x03, out);
    TEST_ASSERT_EQUAL_HEX8(0xA1, out[0]);
    TEST_ASSERT_EQUAL_HEX8(0x37, out[1]);

    // both Y inputs active, Y stored as both disabled (0x00), X stored 0x0F; bits outside the fields are dropped
    y_axis_test_pack_status(0xFF, 0x00, 0xF0, 0xFF, out);
    TEST_ASSERT_EQUAL_HEX8(0x83, out[0]);
    TEST_ASSERT_EQUAL_HEX8(0xF0, out[1]);
}

int main(int, char **)
{
    UNITY_BEGIN();
    RUN_TEST(test_pack_status_layout);
    return UNITY_END();
}
