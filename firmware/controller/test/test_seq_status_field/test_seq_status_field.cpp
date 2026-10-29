#include <unity.h>

#include "sequencer/seq_status_field.h"

using namespace seq;

void setUp(void) {}
void tearDown(void) {}

// Status bytes 14-17 are a multiplexed field: master's firmware puts the encoder position or the
// focus-wheel count there and says which in byte 19. The sequencer is one more owner of the same
// field, and the packet builder asks this object who owns it right now.

void test_nobody_owns_the_field_before_the_sequencer_did_anything(void) {
    StatusField f;
    TEST_ASSERT_FALSE(f.owned(/*now_ms=*/0, /*running=*/false));
    TEST_ASSERT_FALSE(f.owned(123456, false));
}

void test_a_running_sequence_owns_the_field_whatever_the_clock_says(void) {
    StatusField f;
    TEST_ASSERT_TRUE(f.owned(0, true));
    TEST_ASSERT_TRUE(f.owned(999999, true));
}

void test_the_field_stays_with_the_sequencer_for_the_hold_window_after_activity(void) {
    // The host reads the terminal status (Done / Failed, frames fired) right after SEQ_RUN
    // completes; a refusal's status (NOT_COMMITTED, BAD_PROGRAM) the same way.
    StatusField f;
    f.note_activity(5000);
    TEST_ASSERT_TRUE(f.owned(5000, false));
    TEST_ASSERT_TRUE(f.owned(5000 + kStatusHoldMs - 1, false));
    TEST_ASSERT_FALSE(f.owned(5000 + kStatusHoldMs, false));  // back to the encoder / focus wheel
}

void test_activity_restarts_the_hold_window(void) {
    StatusField f;
    f.note_activity(1000);
    f.note_activity(1000 + kStatusHoldMs - 10);
    TEST_ASSERT_TRUE(f.owned(1000 + kStatusHoldMs + 500, false));
}

void test_the_hold_window_survives_the_millis_wrap(void) {
    StatusField f;
    f.note_activity(0xFFFFFFF0u);  // 16 ms before millis() wraps
    TEST_ASSERT_TRUE(f.owned(0x00000010u, false));  // 32 ms later, across the wrap
    TEST_ASSERT_FALSE(f.owned(0x00000010u + kStatusHoldMs, false));
}

int main(void) {
    UNITY_BEGIN();
    RUN_TEST(test_nobody_owns_the_field_before_the_sequencer_did_anything);
    RUN_TEST(test_a_running_sequence_owns_the_field_whatever_the_clock_says);
    RUN_TEST(test_the_field_stays_with_the_sequencer_for_the_hold_window_after_activity);
    RUN_TEST(test_activity_restarts_the_hold_window);
    RUN_TEST(test_the_hold_window_survives_the_millis_wrap);
    return UNITY_END();
}
