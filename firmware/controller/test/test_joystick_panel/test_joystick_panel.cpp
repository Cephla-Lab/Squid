#include <unity.h>
#include "utils/joystick_panel.h"

// Include sources directly for native tests (env:native builds no src/ code into tests)
#include "utils/joystick_panel.cpp"

static JoystickPanelLink link;

void setUp(void) { jp_reset(&link); }
void tearDown(void) {}

// A packet as the panel builds it: big-endian two's complement, last byte 0.
static void make_packet(uint8_t p[JP_MSG_LENGTH], int32_t wheel, int16_t x, int16_t y) {
    uint32_t w = (uint32_t)wheel;
    p[0] = w >> 24; p[1] = w >> 16; p[2] = w >> 8; p[3] = w;
    p[4] = (uint16_t)x >> 8; p[5] = (uint16_t)x;
    p[6] = (uint16_t)y >> 8; p[7] = (uint16_t)y;
    p[8] = 1;
    p[9] = 0;
}

static bool feed(int32_t wheel, int16_t x, int16_t y, uint32_t now_ms, JoystickPanelInput *in) {
    uint8_t p[JP_MSG_LENGTH];
    make_packet(p, wheel, x, y);
    return jp_on_packet(&link, p, JP_MSG_LENGTH, now_ms, in);
}

static void arm(int32_t wheel, uint32_t *now_ms) {
    JoystickPanelInput in;
    for (int i = 0; i < JP_CENTERED_PACKETS; i++)
        TEST_ASSERT_TRUE(feed(wheel, 0, 0, (*now_ms)++, &in));
    TEST_ASSERT_TRUE(link.xy_armed);
}

void test_first_packet_takes_the_wheel_baseline_without_moving_z(void) {
    JoystickPanelInput in;
    TEST_ASSERT_TRUE(feed(123456, 0, 0, 0, &in));
    TEST_ASSERT_EQUAL_INT32(0, in.focus_delta);
    TEST_ASSERT_TRUE(feed(123456 + 320, 0, 0, 3, &in));
    TEST_ASSERT_EQUAL_INT32(320, in.focus_delta);
    TEST_ASSERT_TRUE(feed(123456 - 64, 0, 0, 6, &in));
    TEST_ASSERT_EQUAL_INT32(-384, in.focus_delta);
}

void test_reconnect_after_timeout_does_not_move_z(void) {
    JoystickPanelInput in;
    feed(0, 0, 0, 0, &in);
    feed(40000, 0, 0, 3, &in);   // a session of wheel turns
    TEST_ASSERT_EQUAL_INT32(40000, in.focus_delta);

    TEST_ASSERT_FALSE(jp_check_timeout(&link, 3 + JP_TIMEOUT_MS));
    TEST_ASSERT_TRUE(jp_check_timeout(&link, 4 + JP_TIMEOUT_MS));
    TEST_ASSERT_FALSE(jp_check_timeout(&link, 5000));   // fires once

    // The rebooted panel counts from 0 again: before the fix this was a -40000 move.
    TEST_ASSERT_TRUE(feed(0, 0, 0, 6000, &in));
    TEST_ASSERT_EQUAL_INT32(0, in.focus_delta);
    TEST_ASSERT_TRUE(feed(16, 0, 0, 6003, &in));
    TEST_ASSERT_EQUAL_INT32(16, in.focus_delta);
}

void test_no_timeout_before_the_first_packet(void) {
    TEST_ASSERT_FALSE(jp_check_timeout(&link, 100000));
}

void test_millis_wraparound_is_not_a_timeout(void) {
    JoystickPanelInput in;
    feed(0, 0, 0, 0xFFFFFFF0u, &in);
    TEST_ASSERT_FALSE(jp_check_timeout(&link, 0x00000010u));
    TEST_ASSERT_TRUE(jp_check_timeout(&link, 0xFFFFFFF0u + JP_TIMEOUT_MS + 1));
}

void test_joystick_reads_centred_until_armed(void) {
    JoystickPanelInput in;
    // Stick held (or zeroed off-centre) while plugging in: never passed through.
    for (uint32_t t = 0; t < 50; t++) {
        TEST_ASSERT_TRUE(feed(0, 500, -700, t, &in));
        TEST_ASSERT_EQUAL_INT16(0, in.delta_x);
        TEST_ASSERT_EQUAL_INT16(0, in.delta_y);
    }
    TEST_ASSERT_FALSE(link.xy_armed);

    // One short of the run, then a deflection: the count starts over.
    for (int i = 0; i < JP_CENTERED_PACKETS - 1; i++)
        feed(0, 0, 0, 100 + i, &in);
    feed(0, 1, 0, 120, &in);
    TEST_ASSERT_FALSE(link.xy_armed);
    TEST_ASSERT_EQUAL_INT16(0, in.delta_x);

    uint32_t t = 200;
    arm(0, &t);
    TEST_ASSERT_TRUE(feed(0, 500, -700, t, &in));
    TEST_ASSERT_EQUAL_INT16(500, in.delta_x);
    TEST_ASSERT_EQUAL_INT16(-700, in.delta_y);
    TEST_ASSERT_EQUAL_UINT8(1, in.buttons);
}

void test_timeout_disarms_the_joystick(void) {
    JoystickPanelInput in;
    uint32_t t = 0;
    arm(0, &t);
    feed(0, 300, 0, t, &in);
    TEST_ASSERT_TRUE(jp_check_timeout(&link, t + JP_TIMEOUT_MS + 1));
    TEST_ASSERT_TRUE(feed(0, 300, 0, t + 1000, &in));
    TEST_ASSERT_EQUAL_INT16(0, in.delta_x);
}

void test_wrong_size_and_nonzero_last_byte_are_dropped(void) {
    JoystickPanelInput in = {7, 7, 7, 7};
    uint8_t p[JP_MSG_LENGTH];
    make_packet(p, 1000, 0, 0);
    TEST_ASSERT_FALSE(jp_on_packet(&link, p, JP_MSG_LENGTH - 1, 0, &in));
    p[9] = 0x5A;
    TEST_ASSERT_FALSE(jp_on_packet(&link, p, JP_MSG_LENGTH, 0, &in));
    TEST_ASSERT_EQUAL_INT32(7, in.focus_delta);   // untouched
    TEST_ASSERT_FALSE(link.connected);            // and a dropped packet is not a sign of life
    TEST_ASSERT_FALSE(link.have_wheel_baseline);
}

void test_implausible_wheel_jump_is_dropped_and_rebaselined(void) {
    JoystickPanelInput in;
    uint32_t t = 0;
    arm(0, &t);
    TEST_ASSERT_TRUE(feed(JP_MAX_WHEEL_STEP, 0, 0, t++, &in));   // the largest accepted step
    TEST_ASSERT_EQUAL_INT32(JP_MAX_WHEEL_STEP, in.focus_delta);

    // Noise decoded as a packet: no Z move, joystick disarmed.
    TEST_ASSERT_FALSE(feed(-2000000000, 0, 0, t++, &in));
    TEST_ASSERT_FALSE(link.xy_armed);
    // The next real packet only takes the baseline again.
    TEST_ASSERT_TRUE(feed(JP_MAX_WHEEL_STEP + 16, 0, 0, t++, &in));
    TEST_ASSERT_EQUAL_INT32(0, in.focus_delta);
    TEST_ASSERT_TRUE(feed(JP_MAX_WHEEL_STEP + 32, 0, 0, t++, &in));
    TEST_ASSERT_EQUAL_INT32(16, in.focus_delta);
}

void test_wheel_jump_keeps_the_timeout_running(void) {
    // A stick move was running and the last thing the link delivered was garbage: the timeout must
    // still fire, from the last good packet, to stop it.
    JoystickPanelInput in;
    uint32_t t = 0;
    arm(0, &t);
    feed(0, 300, 0, 10, &in);
    TEST_ASSERT_FALSE(feed(0x7FFFFFFF, 0, 0, 20, &in));
    TEST_ASSERT_TRUE(jp_check_timeout(&link, 10 + JP_TIMEOUT_MS + 1));
}

void test_wheel_step_across_int32_extremes_is_rejected(void) {
    JoystickPanelInput in;
    feed(0x7FFFFFF0, 0, 0, 0, &in);
    // 32-bit subtraction would wrap this to a small step.
    TEST_ASSERT_FALSE(feed((int32_t)0x80000010, 0, 0, 1, &in));
}

void test_forget_wheel_keeps_the_joystick_armed(void) {
    JoystickPanelInput in;
    uint32_t t = 0;
    arm(5000, &t);
    jp_forget_wheel(&link);   // INITIALIZE
    TEST_ASSERT_TRUE(feed(5100, 200, 0, t, &in));
    TEST_ASSERT_EQUAL_INT32(0, in.focus_delta);
    TEST_ASSERT_EQUAL_INT16(200, in.delta_x);
}

int main(int argc, char **argv) {
    UNITY_BEGIN();
    RUN_TEST(test_first_packet_takes_the_wheel_baseline_without_moving_z);
    RUN_TEST(test_reconnect_after_timeout_does_not_move_z);
    RUN_TEST(test_no_timeout_before_the_first_packet);
    RUN_TEST(test_millis_wraparound_is_not_a_timeout);
    RUN_TEST(test_joystick_reads_centred_until_armed);
    RUN_TEST(test_timeout_disarms_the_joystick);
    RUN_TEST(test_wrong_size_and_nonzero_last_byte_are_dropped);
    RUN_TEST(test_implausible_wheel_jump_is_dropped_and_rebaselined);
    RUN_TEST(test_wheel_jump_keeps_the_timeout_running);
    RUN_TEST(test_wheel_step_across_int32_extremes_is_rejected);
    RUN_TEST(test_forget_wheel_keeps_the_joystick_armed);
    return UNITY_END();
}
