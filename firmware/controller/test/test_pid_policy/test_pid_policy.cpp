#include <unity.h>
#include "pid_policy.h"

void setUp(void) {}
void tearDown(void) {}

/*
  pid_engage_pending() closes the gap between check_closed_loop() and
  check_position(). They run one after the other in loop() but read STATUS
  separately, ~0.36 ms apart: if the ramp stops in between, check_position sees
  XACTUAL == target with the axis no longer running and acknowledges COMPLETED
  while the rest-only loop is still held open, so the host is told the move is
  done before the encoder correction has run at all.
*/

void test_not_requested_is_not_pending(void) {
    // No loop asked for on this axis: completion is on the counter, as it always was.
    TEST_ASSERT_FALSE(pid_engage_pending(false, true, false, 1000, 5000));
}

void test_requested_but_not_held_is_not_pending(void) {
    // The loop is already engaged (nothing is holding it open), so there is
    // nothing to wait for.
    TEST_ASSERT_FALSE(pid_engage_pending(true, false, false, 1000, 5000));
}

void test_homing_is_not_pending(void) {
    // Homing owns the axis and runs open-loop from first move to last; the loop
    // is held open by design and check_homing_* reports the result.
    TEST_ASSERT_FALSE(pid_engage_pending(true, true, true, 1000, 5000));
}

void test_held_outside_the_home_zone_is_pending(void) {
    // The case the bug lived in: rest-only loop opened for the move, ramp stopped,
    // re-engage due on the next pass. Not done yet.
    TEST_ASSERT_TRUE(pid_engage_pending(true, true, false, 1000, 5000));
    // Symmetric on the negative side of the zone.
    TEST_ASSERT_TRUE(pid_engage_pending(true, true, false, 1000, -5000));
    // Exactly on the zone boundary counts as outside: check_closed_loop's own
    // in_zone test is strict on both ends, and the two must not disagree.
    TEST_ASSERT_TRUE(pid_engage_pending(true, true, false, 1000, 1000));
    TEST_ASSERT_TRUE(pid_engage_pending(true, true, false, 1000, -1000));
}

void test_held_inside_the_home_zone_is_not_pending(void) {
    // Inside the zone the loop is deliberately held open (the stage may be resting
    // on its stop), so it will never re-engage there and completion is on the counter.
    TEST_ASSERT_FALSE(pid_engage_pending(true, true, false, 1000, 0));
    TEST_ASSERT_FALSE(pid_engage_pending(true, true, false, 1000, 999));
    TEST_ASSERT_FALSE(pid_engage_pending(true, true, false, 1000, -999));
}

void test_zone_disabled_is_pending_even_at_zero(void) {
    // Zone 0 means no exclusion zone at all, so there is no position at which the
    // loop stays open by design: a held loop always re-engages.
    TEST_ASSERT_TRUE(pid_engage_pending(true, true, false, 0, 0));
}

/*
  encoder_within_window() replaces two separate legs with one. The old test accepted
  |counter - target| <= win AND |dev| <= win, which lets the encoder sit 2*win from
  the target: on a 0.3 um window that is 0.6 um of real error acknowledged as settled.
*/

void test_encoder_beyond_the_window_is_not_settled(void) {
    // Counter 8 short of the target and the encoder 8 further out: 16 from the target,
    // yet each leg is inside a window of 10. This is the case the old form accepted.
    TEST_ASSERT_FALSE(encoder_within_window(8, 8, 10));
}

void test_encoder_at_the_target_is_settled(void) {
    // Counter 8 past the target, encoder 8 behind the counter: the encoder IS the target.
    TEST_ASSERT_TRUE(encoder_within_window(8, -8, 10));
}

void test_encoder_exactly_on_the_window_is_settled(void) {
    // Counter on the target, encoder a full window away: inclusive, like the counter leg.
    TEST_ASSERT_TRUE(encoder_within_window(0, 10, 10));
}

void test_encoder_one_past_the_window_is_not_settled(void) {
    TEST_ASSERT_FALSE(encoder_within_window(0, 11, 10));
    // Symmetric in sign.
    TEST_ASSERT_FALSE(encoder_within_window(0, -11, 10));
    TEST_ASSERT_TRUE(encoder_within_window(0, -10, 10));
}

int main(int argc, char **argv) {
    UNITY_BEGIN();
    RUN_TEST(test_not_requested_is_not_pending);
    RUN_TEST(test_requested_but_not_held_is_not_pending);
    RUN_TEST(test_homing_is_not_pending);
    RUN_TEST(test_held_outside_the_home_zone_is_pending);
    RUN_TEST(test_held_inside_the_home_zone_is_not_pending);
    RUN_TEST(test_zone_disabled_is_pending_even_at_zero);
    RUN_TEST(test_encoder_beyond_the_window_is_not_settled);
    RUN_TEST(test_encoder_at_the_target_is_settled);
    RUN_TEST(test_encoder_exactly_on_the_window_is_settled);
    RUN_TEST(test_encoder_one_past_the_window_is_not_settled);
    return UNITY_END();
}
