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

/*
  pid_correction_watch_step(): bounded correction. Units are usteps and microseconds; tol 25,
  arm 100 (4 x tol), progress 50 ms, total 1 s - the constants the firmware uses.
*/
static PidCorrectionWatch W;
#define TOL 25
#define ARM 100
#define PROG 50000u
#define TOTAL 1000000u

void test_correction_below_arming_is_never_watched(void) {
    pid_correction_watch_reset(&W);
    // A 3-count stiction residual just outside the deadband sits there for minutes: benign.
    for (uint32_t t = 0; t < 120u * 1000000u; t += 1000u)
        TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, pid_correction_watch_step(&W, 60, TOL, ARM, t, PROG, TOTAL));
    TEST_ASSERT_FALSE(W.active);
}

void test_correction_that_converges_is_ok(void) {
    pid_correction_watch_reset(&W);
    // 200 um-ish error closing at the clamp: shrinks every millisecond, done in ~200 ms.
    int32_t dev = 34000;
    for (uint32_t t = 0; dev > 0; t += 1000u, dev -= 170)
        TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, pid_correction_watch_step(&W, dev, TOL, ARM, t, PROG, TOTAL));
    // converged below the arming threshold: watch disarms
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, pid_correction_watch_step(&W, 10, TOL, ARM, 300000u, PROG, TOTAL));
    TEST_ASSERT_FALSE(W.active);
}

void test_frozen_encoder_trips_no_progress_within_the_window(void) {
    pid_correction_watch_reset(&W);
    // Encoder froze reporting 50 um (8500 usteps) below the 200 um watchdog: |dev| never changes.
    uint8_t r = PID_CORRECTION_OK;
    uint32_t t = 0;
    for (; t <= PROG + 2000u && r == PID_CORRECTION_OK; t += 1000u)
        r = pid_correction_watch_step(&W, 8500, TOL, ARM, t, PROG, TOTAL);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(PID_CORRECTION_NO_PROGRESS, r, "a constant error beyond arming must trip within one progress window");
    TEST_ASSERT_TRUE_MESSAGE(t <= PROG + 2000u, "tripped late");
}

void test_error_that_grows_trips_no_progress(void) {
    pid_correction_watch_reset(&W);
    // Wrong-sign or decoupled: the correction makes it worse. best_abs never improves.
    uint8_t r = PID_CORRECTION_OK;
    int32_t dev = 200;
    for (uint32_t t = 0; t <= PROG + 2000u && r == PID_CORRECTION_OK; t += 1000u, dev += 50)
        r = pid_correction_watch_step(&W, dev, TOL, ARM, t, PROG, TOTAL);
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_NO_PROGRESS, r);
}

void test_progress_restarts_the_window_but_not_the_total_budget(void) {
    pid_correction_watch_reset(&W);
    // Crawls: improves by exactly one tolerance every 40 ms (inside the 50 ms window) forever.
    uint8_t r = PID_CORRECTION_OK;
    int32_t dev = 30000;
    uint32_t t = 0;
    for (; r == PID_CORRECTION_OK && t < 5u * 1000000u; t += 40000u, dev -= TOL)
        r = pid_correction_watch_step(&W, dev, TOL, ARM, t, PROG, TOTAL);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(PID_CORRECTION_TIMEOUT, r, "steady tiny progress must still hit the total budget");
    TEST_ASSERT_TRUE_MESSAGE(t >= TOTAL && t <= TOTAL + 80000u, "timeout must fire at the total budget, not before");
}

void test_watch_handles_micros_wraparound(void) {
    pid_correction_watch_reset(&W);
    uint32_t t = 0xFFFFFFFFu - 20000u;   // arm 20 ms before micros() wraps
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, pid_correction_watch_step(&W, 8500, TOL, ARM, t, PROG, TOTAL));
    t += 25000u;                          // wrapped
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, pid_correction_watch_step(&W, 8500, TOL, ARM, t, PROG, TOTAL));
    t += 30000u;                          // 55 ms after arming, still no progress
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_NO_PROGRESS, pid_correction_watch_step(&W, 8500, TOL, ARM, t, PROG, TOTAL));
}

/* pid_realign_allowed(): the absorbed post-homing offset is bounded by zone + watchdog. */
void test_realign_within_gap_config_is_allowed(void) {
    // Gap stage: zone 700 um, watchdog 200 um, offset 640 um (usteps at 170 per um)
    TEST_ASSERT_TRUE(pid_realign_allowed(-640 * 170, 700 * 170, 200 * 170));
    TEST_ASSERT_TRUE(pid_realign_allowed(640 * 170, 700 * 170, 200 * 170));
}

void test_realign_beyond_gap_config_is_refused(void) {
    // No-gap stage (zone 0), watchdog 200 um: a 640 um offset at the first engage is lost motion, not a gap.
    TEST_ASSERT_FALSE(pid_realign_allowed(-640 * 170, 0, 200 * 170));
    // Gap stage but the offset is beyond zone + watchdog
    TEST_ASSERT_FALSE(pid_realign_allowed(950 * 170, 700 * 170, 200 * 170));
}

void test_realign_unbounded_when_nothing_is_configured(void) {
    TEST_ASSERT_TRUE(pid_realign_allowed(5000 * 170, 0, 0));
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
    RUN_TEST(test_correction_below_arming_is_never_watched);
    RUN_TEST(test_correction_that_converges_is_ok);
    RUN_TEST(test_frozen_encoder_trips_no_progress_within_the_window);
    RUN_TEST(test_error_that_grows_trips_no_progress);
    RUN_TEST(test_progress_restarts_the_window_but_not_the_total_budget);
    RUN_TEST(test_watch_handles_micros_wraparound);
    RUN_TEST(test_realign_within_gap_config_is_allowed);
    RUN_TEST(test_realign_beyond_gap_config_is_refused);
    RUN_TEST(test_realign_unbounded_when_nothing_is_configured);
    return UNITY_END();
}
