#include <unity.h>

#include "hal/board.h"
#include "protocol/frames.h"

// Include the v2 board source directly for native tests. (No -DBOARD_SQUID_V2
// needed: board_squid_v2.cpp is self-contained and always defines the v2
// descriptor; the firmware build selects it via the platformio src filter.)
#include "hal/boards/board_squid_v2.cpp"

using protocol::InfoPayload;

void setUp(void) {}
void tearDown(void) {}

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

void test_v2_descriptor(void) {
    const InfoPayload& d = hal::board_descriptor();
    check_wire_invariants(d);

    TEST_ASSERT_EQUAL_UINT8(2, d.board_id);
    TEST_ASSERT_EQUAL_UINT8(8, d.n_cam_triggers);   // 8 triggers
    TEST_ASSERT_EQUAL_UINT8(10, d.n_ready_inputs);  // 2 direct + 8 expander
    TEST_ASSERT_EQUAL_UINT8(1, d.has_led_matrix);

    // v2 is known TMC2240 (not runtime-probed) on the populated axes.
    for (int i = 0; i < d.n_axes; ++i) {
        TEST_ASSERT_EQUAL_UINT8(hal::DRIVER_TMC2240, d.axis_driver[i]);
    }
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_v2_descriptor);
    return UNITY_END();
}
