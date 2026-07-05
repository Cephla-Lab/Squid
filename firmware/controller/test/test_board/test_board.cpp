#include <unity.h>

#include "hal/board.h"
#include "protocol/frames.h"

// Include the v1 board source directly for native tests.
#include "hal/boards/board_squid_v1.cpp"

using protocol::InfoPayload;

void setUp(void) {}
void tearDown(void) {}

// Fields must fit their wire-response widths (StandardResponse in frames.h).
static void check_wire_invariants(const InfoPayload& d) {
    TEST_ASSERT_TRUE_MESSAGE(d.board_id != 0, "board_id must be set");
    TEST_ASSERT_TRUE_MESSAGE(d.n_axes <= 8, "n_axes exceeds axes[8]");
    TEST_ASSERT_TRUE_MESSAGE(d.n_dacs <= 8, "n_dacs exceeds dac_values[8]");
    TEST_ASSERT_TRUE_MESSAGE(d.n_illum_ttl <= 8, "n_illum_ttl exceeds illum_ttl_mask");
    TEST_ASSERT_TRUE_MESSAGE(d.n_cam_triggers <= 8, "n_cam_triggers exceeds cam_trigger_states");
    for (int i = 0; i < 8; ++i) {
        TEST_ASSERT_TRUE_MESSAGE(d.axis_driver[i] <= hal::DRIVER_TMC2240, "invalid axis driver id");
    }
}

void test_v1_descriptor(void) {
    const InfoPayload& d = hal::board_descriptor();
    check_wire_invariants(d);

    TEST_ASSERT_EQUAL_UINT8(1, d.board_id);
    TEST_ASSERT_EQUAL_UINT8(5, d.n_axes);            // X, Y, Z, FILTER1, FILTER2
    TEST_ASSERT_EQUAL_UINT8(8, d.n_dacs);            // DAC80508
    TEST_ASSERT_EQUAL_UINT8(5, d.n_illum_ttl);
    TEST_ASSERT_EQUAL_UINT8(1, d.has_led_matrix);
    TEST_ASSERT_EQUAL_UINT8(4, d.n_cam_triggers);    // pins 29-32
    TEST_ASSERT_EQUAL_UINT8(1, d.n_ready_inputs);    // one shared ready line
    TEST_ASSERT_EQUAL_UINT8(16, d.max_program_channels);

    // v1 stepper driver is probed at runtime (Phase M); descriptor is 0 for now.
    for (int i = 0; i < 8; ++i) {
        TEST_ASSERT_EQUAL_UINT8(hal::DRIVER_NONE, d.axis_driver[i]);
    }
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_v1_descriptor);
    return UNITY_END();
}
