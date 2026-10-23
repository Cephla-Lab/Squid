#include <unity.h>
#include <stdint.h>
#include "utils/panel_lock.h"

// Include sources directly for native tests (env:native builds no src/ code into tests)
#include "utils/panel_lock.cpp"

void setUp(void) {}
void tearDown(void) {}

static JoystickPanelInput sample(void) {
    JoystickPanelInput in;
    in.focus_delta = 320;
    in.delta_x = -1200;
    in.delta_y = 450;
    in.buttons = 0x01;
    return in;
}

void test_unlocked_input_is_untouched(void) {
    JoystickPanelInput in = sample();
    pl_apply(false, &in);
    TEST_ASSERT_EQUAL_INT32(320, in.focus_delta);
    TEST_ASSERT_EQUAL_INT16(-1200, in.delta_x);
    TEST_ASSERT_EQUAL_INT16(450, in.delta_y);
    TEST_ASSERT_EQUAL_UINT8(0x01, in.buttons);
}

void test_locked_input_moves_nothing(void) {
    JoystickPanelInput in = sample();
    pl_apply(true, &in);
    TEST_ASSERT_EQUAL_INT32(0, in.focus_delta);
    TEST_ASSERT_EQUAL_INT16(0, in.delta_x);
    TEST_ASSERT_EQUAL_INT16(0, in.delta_y);
    TEST_ASSERT_EQUAL_UINT8(0x01, in.buttons);  // buttons still reach the host
}

int main(int argc, char **argv) {
    UNITY_BEGIN();
    RUN_TEST(test_unlocked_input_is_untouched);
    RUN_TEST(test_locked_input_moves_nothing);
    return UNITY_END();
}
