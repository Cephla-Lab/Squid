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
  pid_correction_watch_step(): bounded correction. Units are usteps and microseconds. TOL is the
  shipped Z deadband (2 encoder counts of 0.1 um at 170,667 usteps/mm = 34 usteps); PROG and
  TOTAL come from pid_correction_windows() at the qualified configuration (P 65535, watchdog
  200 um = 34,133 usteps, clamp 1 mm/s = 170,667 pps), so a retune of the header constants is
  asserted here too.
*/
static PidCorrectionWatch W;
#define TOL 34
static uint32_t PROG, TOTAL;
static void windows_at_qualified_config(void) {
    pid_correction_windows(65535, 34133, 170667u, &PROG, &TOTAL);
}

void test_error_inside_the_deadband_is_never_watched(void) {
    pid_correction_watch_reset(&W); windows_at_qualified_config();
    // Inside PID_TOLERANCE the chip does not drive: nothing to bound, however long it sits there.
    for (uint32_t t = 0; t < 120u * 1000000u; t += 1000u)
        TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, pid_correction_watch_step(&W, TOL, TOL, t, PROG, TOTAL));
    TEST_ASSERT_FALSE(W.active);
}

void test_frozen_encoder_just_outside_the_deadband_trips(void) {
    pid_correction_watch_reset(&W); windows_at_qualified_config();
    // R1: two counts outside the deadband, never changing. The chip drives at (P/256) x e for
    // ever; below the watchdog nothing else notices. Must trip within one progress window.
    uint8_t r = PID_CORRECTION_OK;
    uint32_t t = 0;
    for (; t <= PROG + 2000u && r == PID_CORRECTION_OK; t += 1000u)
        r = pid_correction_watch_step(&W, TOL + 2 * 17, TOL, t, PROG, TOTAL);
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_NO_PROGRESS, r);
}

void test_correction_that_converges_is_ok(void) {
    pid_correction_watch_reset(&W); windows_at_qualified_config();
    // 200 um error at the clamp (170 usteps/ms) for ~180 ms, then first-order decay at tau 3.9 ms:
    // the error shrinks every millisecond, done in ~200 ms, well inside TOTAL (~1 s here).
    int32_t dev = 34000;
    uint32_t t = 0;
    for (; dev > 3000; t += 1000u, dev -= 170)
        TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, pid_correction_watch_step(&W, dev, TOL, t, PROG, TOTAL));
    for (; dev > TOL; t += 1000u, dev = dev * 3 / 4)   /* ~1 ms steps at tau 3.9 ms: x0.77 per ms */
        TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, pid_correction_watch_step(&W, dev, TOL, t, PROG, TOTAL));
    // converged inside the deadband: watch disarms
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, pid_correction_watch_step(&W, 10, TOL, t + 1000u, PROG, TOTAL));
    TEST_ASSERT_FALSE(W.active);
}

void test_frozen_encoder_trips_no_progress_within_the_window(void) {
    pid_correction_watch_reset(&W); windows_at_qualified_config();
    // Encoder froze reporting 50 um (8500 usteps) below the 200 um watchdog: |dev| never changes.
    uint8_t r = PID_CORRECTION_OK;
    uint32_t t = 0;
    for (; t <= PROG + 2000u && r == PID_CORRECTION_OK; t += 1000u)
        r = pid_correction_watch_step(&W, 8500, TOL, t, PROG, TOTAL);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(PID_CORRECTION_NO_PROGRESS, r, "a constant error beyond arming must trip within one progress window");
    TEST_ASSERT_TRUE_MESSAGE(t <= PROG + 2000u, "tripped late");
}

void test_error_that_grows_trips_no_progress(void) {
    pid_correction_watch_reset(&W); windows_at_qualified_config();
    // Wrong-sign or decoupled: the correction makes it worse. best_abs never improves.
    uint8_t r = PID_CORRECTION_OK;
    int32_t dev = 200;
    for (uint32_t t = 0; t <= PROG + 2000u && r == PID_CORRECTION_OK; t += 1000u, dev += 50)
        r = pid_correction_watch_step(&W, dev, TOL, t, PROG, TOTAL);
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_NO_PROGRESS, r);
}

void test_progress_restarts_the_window_but_not_the_total_budget(void) {
    pid_correction_watch_reset(&W); windows_at_qualified_config();
    // Crawls: improves by exactly one deadband every 15 ms (inside the 20 ms window) for ever -
    // a stage inching along on friction. The progress window never fires; the total budget must.
    uint8_t r = PID_CORRECTION_OK;
    int32_t dev = 3000000;
    uint32_t t = 0;
    for (; r == PID_CORRECTION_OK && t < 15u * 1000000u; t += 15000u, dev -= TOL)
        r = pid_correction_watch_step(&W, dev, TOL, t, PROG, TOTAL);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(PID_CORRECTION_TIMEOUT, r, "steady tiny progress must still hit the total budget");
    TEST_ASSERT_TRUE_MESSAGE(t >= TOTAL && t <= TOTAL + 30000u, "timeout must fire at the total budget, not before");
}

void test_watch_handles_micros_wraparound(void) {
    pid_correction_watch_reset(&W); windows_at_qualified_config();
    uint32_t t = 0xFFFFFFFFu - 10000u;   // arm 10 ms before micros() wraps
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, pid_correction_watch_step(&W, 8500, TOL, t, PROG, TOTAL));
    t += 15000u;                          // wrapped, 15 ms after arming: inside the 20 ms window
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, pid_correction_watch_step(&W, 8500, TOL, t, PROG, TOTAL));
    t += 10000u;                          // 25 ms after arming, still no progress
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_NO_PROGRESS, pid_correction_watch_step(&W, 8500, TOL, t, PROG, TOTAL));
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

void test_realign_refused_when_nothing_is_configured(void) {
    // R9: RESET zeroes zone and watchdog but leaves the encoder configured; an ENABLE + homing
    // without a fresh CONFIGURE would otherwise absorb any offset with no watchdog at all.
    TEST_ASSERT_FALSE(pid_realign_allowed(5000 * 170, 0, 0));
    TEST_ASSERT_FALSE(pid_realign_allowed(1, 0, 0));
}

/* pid_correction_windows(): budgets derived from P and the clamp (R3). */
void test_windows_at_the_qualified_gain_are_the_20ms_floor(void) {
    uint32_t prog, total;
    pid_correction_windows(65535, 34133, 170667u, &prog, &total);
    TEST_ASSERT_EQUAL_UINT32(PID_CORRECTION_WINDOW_MIN_US, prog);          // 5 x 3.9 ms = 19.5 ms -> floor 20 ms
    // total = 20 ms + 4 x (200 um / 1 mm/s = 200 ms) = 820 ms
    TEST_ASSERT_UINT32_WITHIN(1000u, 820000u, total);
}

void test_windows_scale_with_a_low_gain(void) {
    uint32_t prog, total;
    pid_correction_windows(1024, 34133, 170667u, &prog, &total);
    TEST_ASSERT_UINT32_WITHIN(1000u, 1250000u, prog);                      // 5 x 250 ms
    pid_correction_windows(4096, 34133, 170667u, &prog, &total);
    TEST_ASSERT_UINT32_WITHIN(1000u, 312500u, prog);                       // 5 x 62.5 ms
    pid_correction_windows(0, 34133, 170667u, &prog, &total);
    TEST_ASSERT_EQUAL_UINT32(PID_CORRECTION_WINDOW_MAX_US, prog);          // no gain: widest window
}

void test_total_budget_scales_with_the_clamp_and_is_capped(void) {
    uint32_t prog, total;
    // clamp 0.1 mm/s (17,067 pps), watchdog 200 um: 4 x 2 s = 8 s + 20 ms window
    pid_correction_windows(65535, 34133, 17067u, &prog, &total);
    TEST_ASSERT_UINT32_WITHIN(5000u, 8020000u, total);
    // clamp 0.01 mm/s: would be 80 s -> capped at 10 s
    pid_correction_windows(65535, 34133, 1707u, &prog, &total);
    TEST_ASSERT_EQUAL_UINT32(PID_CORRECTION_TIMEOUT_MAX_US, total);
    // clamp unknown: window + 1 s
    pid_correction_windows(65535, 34133, 0u, &prog, &total);
    TEST_ASSERT_EQUAL_UINT32(PID_CORRECTION_WINDOW_MIN_US + PID_CORRECTION_TIMEOUT_UNKNOWN_US, total);
}

void test_low_gain_converging_loop_does_not_false_trip(void) {
    // R3: P 1024 (tau 250 ms) closing a 50 um error: at t the error is e0 x exp(-t/tau). Sampled
    // every 5 ms the decrease per 1.25 s window is ~99 %, so progress is always seen in time.
    pid_correction_watch_reset(&W);
    uint32_t prog, total;
    pid_correction_windows(1024, 34133, 51200u, &prog, &total);   // clamp 0.3 mm/s
    int32_t dev = 8500;
    uint8_t r = PID_CORRECTION_OK;
    for (uint32_t t = 0; dev > TOL && r == PID_CORRECTION_OK; t += 5000u) {
        r = pid_correction_watch_step(&W, dev, TOL, t, prog, total);
        dev = dev - (dev * 5 + 125) / 250;   /* x exp(-5/250) per 5 ms step ~ x0.98 */
    }
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(PID_CORRECTION_OK, r, "a converging low-gain loop must not trip");
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
    RUN_TEST(test_error_inside_the_deadband_is_never_watched);
    RUN_TEST(test_frozen_encoder_just_outside_the_deadband_trips);
    RUN_TEST(test_correction_that_converges_is_ok);
    RUN_TEST(test_frozen_encoder_trips_no_progress_within_the_window);
    RUN_TEST(test_error_that_grows_trips_no_progress);
    RUN_TEST(test_progress_restarts_the_window_but_not_the_total_budget);
    RUN_TEST(test_watch_handles_micros_wraparound);
    RUN_TEST(test_realign_within_gap_config_is_allowed);
    RUN_TEST(test_realign_beyond_gap_config_is_refused);
    RUN_TEST(test_realign_refused_when_nothing_is_configured);
    RUN_TEST(test_windows_at_the_qualified_gain_are_the_20ms_floor);
    RUN_TEST(test_windows_scale_with_a_low_gain);
    RUN_TEST(test_total_budget_scales_with_the_clamp_and_is_capped);
    RUN_TEST(test_low_gain_converging_loop_does_not_false_trip);
    return UNITY_END();
}
