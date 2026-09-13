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
    // Exactly on the zone boundary counts as INSIDE (inclusive on both ends, matching
    // check_closed_loop's in_zone): nothing engages at the edge, so nothing is pending there.
    TEST_ASSERT_FALSE(pid_engage_pending(true, true, false, 1000, 1000));
    TEST_ASSERT_TRUE(pid_engage_pending(true, true, false, 1000, 1001));
    TEST_ASSERT_FALSE(pid_engage_pending(true, true, false, 1000, -1000));   /* negative edge: inside too */
    TEST_ASSERT_TRUE(pid_engage_pending(true, true, false, 1000, -1001));
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
  pid_completion_encoder_ok(): the encoder leg of a commanded move's completion while the loop
  is engaged. The bound is on the ENCODER's distance to the target (counter - target, plus the
  chip's ENC_POS_DEV = encoder - counter), not on the two legs separately: that let the encoder
  sit 2*win from the target (0.6 um of real error acknowledged as settled on a 0.3 um window).
  The bound is the completion window when one is set, else the target-reached tolerance, and
  never tighter than the chip's deadband: inside the deadband the chip does not correct, so a
  tighter bound is a move that never completes. Inclusive throughout: the datasheet documents the
  deadband both ways (drives "until |PID_E| - PID_TOLERANCE <= 0"; "PID_E = 0 in case |PID_E| <
  PID_TOLERANCE"), the correction watch already treats |e| == deadband as idle, and an inclusive
  bound completes under either reading where a strict one could wait for ever at |e| == deadband
  with nothing watching.
  Arguments: (counter - target, ENC_POS_DEV, window, target tolerance, deadband).
*/

void test_encoder_beyond_the_window_is_not_settled(void) {
    // Counter 8 short of the target and the encoder 8 further out: 16 from the target,
    // yet each leg is inside a window of 10. This is the case the old form accepted.
    TEST_ASSERT_FALSE(pid_completion_encoder_ok(8, 8, 10, 2, 2));
}

void test_encoder_at_the_target_is_settled(void) {
    // Counter 8 past the target, encoder 8 behind the counter: the encoder IS the target.
    TEST_ASSERT_TRUE(pid_completion_encoder_ok(8, -8, 10, 2, 2));
}

void test_encoder_exactly_on_the_window_is_settled(void) {
    // Counter on the target, encoder a full window away: inclusive, like the counter leg.
    TEST_ASSERT_TRUE(pid_completion_encoder_ok(0, 10, 10, 2, 2));
}

void test_encoder_one_past_the_window_is_not_settled(void) {
    TEST_ASSERT_FALSE(pid_completion_encoder_ok(0, 11, 10, 2, 2));
    // Symmetric in sign.
    TEST_ASSERT_FALSE(pid_completion_encoder_ok(0, -11, 10, 2, 2));
    TEST_ASSERT_TRUE(pid_completion_encoder_ok(0, -10, 10, 2, 2));
}

void test_no_window_ack_before_convergence_is_not_settled(void) {
    // The bench row (2026-09-12, f1116052, 10 um steps at 10,667 usteps/mm): counter at the
    // target, ENC_POS_DEV 3..7 usteps (0.3-0.7 um) on the pass that re-engaged the rest-only
    // loop, tolerance two encoder counts = 2 usteps. Acknowledged then; must not be.
    TEST_ASSERT_FALSE(pid_completion_encoder_ok(0, 7, 0, 2, 2));
    TEST_ASSERT_FALSE(pid_completion_encoder_ok(0, -3, 0, 2, 2));
}

void test_no_window_inside_the_tolerance_is_settled(void) {
    TEST_ASSERT_TRUE(pid_completion_encoder_ok(0, 0, 0, 2, 2));
    TEST_ASSERT_TRUE(pid_completion_encoder_ok(0, 1, 0, 2, 2));
    TEST_ASSERT_TRUE(pid_completion_encoder_ok(0, -1, 0, 2, 2));
}

void test_no_window_bound_is_inclusive(void) {
    // |e| == tolerance completes: under the datasheet's inclusive reading the chip rests here,
    // and the correction watch treats it as idle - a strict bound would wait here for ever.
    TEST_ASSERT_TRUE(pid_completion_encoder_ok(0, 2, 0, 2, 2));
    TEST_ASSERT_TRUE(pid_completion_encoder_ok(0, -2, 0, 2, 2));
    TEST_ASSERT_FALSE(pid_completion_encoder_ok(0, 3, 0, 2, 2));
}

void test_no_window_bound_is_on_the_encoder_not_the_counter(void) {
    // Counter 5 past the target, encoder 5 behind the counter: the encoder is on the target.
    TEST_ASSERT_TRUE(pid_completion_encoder_ok(5, -5, 0, 2, 2));
    // The counter on the target is not enough on its own.
    TEST_ASSERT_FALSE(pid_completion_encoder_ok(0, 5, 0, 2, 2));
}

void test_window_replaces_the_tolerance_as_the_bound(void) {
    // A 10-ustep window accepts what a 2-ustep tolerance would not, and nothing beyond it.
    TEST_ASSERT_TRUE(pid_completion_encoder_ok(0, 7, 10, 2, 2));
    TEST_ASSERT_FALSE(pid_completion_encoder_ok(0, 11, 10, 2, 2));
}

void test_bound_is_never_tighter_than_the_deadband(void) {
    // SET_PID_TOLERANCE takes the two tolerances independently: target 2, deadband 5. The chip
    // parks anywhere inside 5, so a completion bound of 2 is a move that never completes and is
    // never watched (the watch is idle inside the deadband). The deadband is the floor.
    TEST_ASSERT_TRUE(pid_completion_encoder_ok(0, 4, 0, 2, 5));
    TEST_ASSERT_TRUE(pid_completion_encoder_ok(0, 5, 0, 2, 5));
    TEST_ASSERT_FALSE(pid_completion_encoder_ok(0, 6, 0, 2, 5));
    // Same with a completion window narrower than the deadband.
    TEST_ASSERT_TRUE(pid_completion_encoder_ok(0, 4, 3, 2, 5));
    TEST_ASSERT_FALSE(pid_completion_encoder_ok(0, 6, 3, 2, 5));
    // A window wider than the deadband is still the bound.
    TEST_ASSERT_TRUE(pid_completion_encoder_ok(0, 7, 10, 2, 5));
    TEST_ASSERT_FALSE(pid_completion_encoder_ok(0, 11, 10, 2, 5));
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
    pid_correction_windows(65535, 34133, 170667u, TOL, &PROG, &TOTAL);
}

void test_error_inside_the_deadband_is_never_watched(void) {
    pid_correction_watch_reset(&W); windows_at_qualified_config();
    // Inside PID_TOLERANCE the chip does not drive: nothing to bound, however long it sits there.
    for (uint32_t t = 0; t < 120u * 1000000u; t += 1000u)
        TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, pid_correction_watch_step(&W, TOL, TOL, t, PROG, TOTAL));
    TEST_ASSERT_FALSE(W.active);
}

void test_frozen_encoder_just_outside_the_deadband_trips_on_the_total_budget(void) {
    pid_correction_watch_reset(&W); windows_at_qualified_config();
    // R1 / C2: two counts outside the deadband, never changing. Below the arming threshold
    // (4 x deadband) the progress window does NOT apply - stiction and reversal backlash can
    // hold a small residual for longer than one window on a healthy stage - but the total
    // budget does: the chip drives at (P/256) x e for ever otherwise. At the qualified config
    // that bounds the runaway to ~0.8 s x 0.08 mm/s = ~65 um.
    uint8_t r = PID_CORRECTION_OK;
    uint32_t t = 0;
    for (; t <= TOTAL + 2000u && r == PID_CORRECTION_OK; t += 1000u)
        r = pid_correction_watch_step(&W, TOL + 2 * 17, TOL, t, PROG, TOTAL);
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_TIMEOUT, r);
    TEST_ASSERT_TRUE_MESSAGE(t > PROG + 2000u, "a small residual must NOT trip on the progress window");
}

void test_small_residual_held_by_stiction_for_half_a_second_is_ok(void) {
    pid_correction_watch_reset(&W); windows_at_qualified_config();
    // C2: one count outside the deadband, mechanically held for 500 ms, then closes: healthy.
    uint32_t t = 0;
    for (; t < 500000u; t += 1000u)
        TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, pid_correction_watch_step(&W, TOL + 17, TOL, t, PROG, TOTAL));
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, pid_correction_watch_step(&W, 10, TOL, t, PROG, TOTAL));
    TEST_ASSERT_FALSE(W.active);
}

void test_clamp_limited_correction_does_not_false_trip(void) {
    // C1: clamp 0.05 mm/s (8,533 pps), deadband 2 um (341 usteps), P 65535. The chip can only
    // shrink the error by 171 usteps per 20 ms - less than one deadband - so a window derived
    // from P alone would fault a healthy correction. The window must cover tol / clamp.
    pid_correction_watch_reset(&W);
    uint32_t prog, total;
    pid_correction_windows(65535, 34133, 8533u, 341, &prog, &total);
    TEST_ASSERT_TRUE_MESSAGE(prog >= 3u * 341u * 1000000u / 8533u, "window must cover >= 3 x (tol / clamp)");
    int32_t dev = 683;   /* 4 um off after a move */
    uint8_t r = PID_CORRECTION_OK;
    uint32_t t = 0;
    for (; dev > 341 && r == PID_CORRECTION_OK; t += 1000u, dev -= 9)   /* 8.5 usteps per ms at the clamp */
        r = pid_correction_watch_step(&W, dev, 341, t, prog, total);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(PID_CORRECTION_OK, r, "a clamp-limited correction that is converging must not trip");
}

void test_very_low_gain_tail_is_covered_by_the_total_floor(void) {
    // C7: P 64 (tau 4 s). The last deadband of shrink takes tau x ln2 = 2.8 s; the progress
    // window is capped at 2 s but the tail is inside the small-error band where only the
    // total budget applies, and the total has a floor of 8 tau (capped at 10 s).
    pid_correction_watch_reset(&W);
    uint32_t prog, total;
    pid_correction_windows(64, 34133, 170667u, TOL, &prog, &total);
    TEST_ASSERT_EQUAL_UINT32(PID_CORRECTION_TIMEOUT_MAX_US, total);
    double e = 69.0;
    uint8_t r = PID_CORRECTION_OK;
    uint32_t t = 0;
    for (; (int32_t)e > TOL && r == PID_CORRECTION_OK; t += 10000u) {
        r = pid_correction_watch_step(&W, (int32_t)e, TOL, t, prog, total);
        e *= 0.9975031;   /* exp(-10 ms / 4 s) per 10 ms step */
    }
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, r);
    TEST_ASSERT_TRUE_MESSAGE(t > 2500000u && t < 3200000u, "the tail should take ~2.8 s (tau x ln 2)");
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

/* pid_realign_allowed(): the absorbed post-homing offset is bounded by the home ZONE alone when
   one is configured (the realignment only runs outside the zone, so a frozen encoder's offset is
   the resting position itself, always > zone: the zone is the evidence the encoder moved), else by
   the watchdog. */
void test_realign_within_gap_config_is_allowed(void) {
    // Gap stage: zone 700 um, watchdog 200 um, offset 640 um (usteps at 170 per um)
    TEST_ASSERT_TRUE(pid_realign_allowed(-640 * 170, 700 * 170, 200 * 170, 110 * 170, 8 * TOL));
    TEST_ASSERT_TRUE(pid_realign_allowed(640 * 170, 700 * 170, 200 * 170, 110 * 170, 8 * TOL));
    // No-gap stage: a stiction offset inside the watchdog is fine
    TEST_ASSERT_TRUE(pid_realign_allowed(150 * 170, 0, 200 * 170, 600 * 170, 8 * TOL));
}

void test_realign_frozen_encoder_at_the_first_park_is_refused(void) {
    // Codex counterexample (2026-09-12): zone 700, watchdog 200, first move to 750 um with the
    // encoder frozen at home -> offset 750 um. zone + watchdog (900) would have absorbed it and
    // erased the evidence; the zone alone (700) refuses it.
    TEST_ASSERT_FALSE(pid_realign_allowed(750 * 170, 700 * 170, 200 * 170, 0, 8 * TOL));
    TEST_ASSERT_FALSE(pid_realign_allowed(-750 * 170, 700 * 170, 200 * 170, 0, 8 * TOL));
}

void test_realign_beyond_gap_config_is_refused(void) {
    // No-gap stage (zone 0), watchdog 200 um: a 640 um offset at the first engage is lost motion, not a gap.
    TEST_ASSERT_FALSE(pid_realign_allowed(-640 * 170, 0, 200 * 170, 110 * 170, 8 * TOL));
    // Gap stage, offset beyond the zone
    TEST_ASSERT_FALSE(pid_realign_allowed(950 * 170, 700 * 170, 200 * 170, 110 * 170, 8 * TOL));
}

/* pid_correction_travel_step(): distance and response bounds. Qualified config: watchdog
   34,133 usteps (200 um); response window 100 ms. */
void test_travel_bound_is_independent_of_the_time_budgets(void) {
    pid_correction_watch_reset(&W); windows_at_qualified_config();
    // arm at a large error, then integrate a 34 kpps drive: 34133 usteps takes ~1.0 s
    pid_correction_watch_step(&W, 8500, TOL, 0, PROG, 100000000u);   /* huge total: not the limiter */
    uint8_t r = PID_CORRECTION_OK;
    uint32_t t = 0;
    for (; t < 5000000u && r == PID_CORRECTION_OK; t += 1000u) {
        /* keep the progress window quiet by reporting shrinking error */
        pid_correction_watch_step(&W, 8500 - (int32_t)(t / 1000u), TOL, t, PROG, 100000000u);
        r = pid_correction_travel_step(&W, 34000u, 8500 - (int32_t)(t / 1000u) * 34, t, 34133u, 100000u, 512u);   /* encoder follows the drive */
    }
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_TRAVEL, r);
    TEST_ASSERT_TRUE_MESSAGE(t > 950000u && t < 1100000u, "34133 usteps at 34 kpps is ~1.0 s");
}

void test_frozen_feedback_trips_no_response_within_the_response_window(void) {
    // Encoder frozen: the chip drives at 13 kpps and the encoder POSITION never changes. The
    // check must not lean on the velocity registers - V_ENC / V_ENC_MEAN retain their last value
    // until ENC_VEL_ZERO (left at 0xFFFFFF, ~1.05 s at 16 MHz) expires, so a frozen encoder
    // reads as "still moving" for a second (Codex 2026-09-12). Must trip within ~100 ms on the
    // position, bounding the travel to ~1.3 kusteps (~8 um) - not 157 um.
    pid_correction_watch_reset(&W); windows_at_qualified_config();
    uint8_t r = PID_CORRECTION_OK;
    uint32_t t = 0;
    for (; t < 1000000u && r == PID_CORRECTION_OK; t += 1000u) {
        pid_correction_watch_step(&W, TOL + 17, TOL, t, PROG, TOTAL);
        r = pid_correction_travel_step(&W, 13000u, TOL + 17, t, 34133u, 100000u, 512u);   /* encoder position never changes */
    }
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_NO_RESPONSE, r);
    TEST_ASSERT_TRUE_MESSAGE(t <= 102000u, "must trip within one response window");
    TEST_ASSERT_TRUE_MESSAGE(W.travel_pps_us / 1000000u < 2000u, "travel before the trip must be a few um, not 157");
}

void test_responding_encoder_never_trips_no_response(void) {
    pid_correction_watch_reset(&W); windows_at_qualified_config();
    uint8_t r = PID_CORRECTION_OK;
    for (uint32_t t = 0; t < 500000u && r == PID_CORRECTION_OK; t += 1000u) {
        pid_correction_watch_step(&W, 8500, TOL, t, PROG, 100000000u);
        r = pid_correction_travel_step(&W, 13000u, 8500 - (int32_t)(t / 1000u) * 3, t, 100000000u, 100000u, 512u);   /* encoder moves ~1/4 of the drive */
    }
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, r);
}

void test_response_is_not_judged_below_the_drive_floor(void) {
    // A 50 pps drive on a 1-count residual: too slow to judge, and too slow to matter.
    pid_correction_watch_reset(&W); windows_at_qualified_config();
    uint8_t r = PID_CORRECTION_OK;
    for (uint32_t t = 0; t < 2000000u && r == PID_CORRECTION_OK; t += 1000u) {
        pid_correction_watch_step(&W, TOL + 1, TOL, t, PROG, 100000000u);
        r = pid_correction_travel_step(&W, 50u, TOL + 1, t, 34133u, 100000u, 512u);
    }
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, r);
}

void test_travel_step_is_inert_until_the_watch_is_armed(void) {
    pid_correction_watch_reset(&W);
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, pid_correction_travel_step(&W, 50000u, 0, 0, 1u, 1u, 512u));
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, pid_correction_travel_step(&W, 50000u, 0, 1000000u, 1u, 1u, 512u));
    TEST_ASSERT_EQUAL_UINT64(0u, W.travel_pps_us);
}

void test_realign_refused_when_nothing_is_configured(void) {
    // R9: RESET zeroes zone and watchdog but leaves the encoder configured; an ENABLE + homing
    // without a fresh CONFIGURE would otherwise absorb any offset with no watchdog at all.
    TEST_ASSERT_FALSE(pid_realign_allowed(5000 * 170, 0, 0, 5000 * 170, 8 * TOL));
    TEST_ASSERT_FALSE(pid_realign_allowed(1, 0, 0, 1, 8 * TOL));
}

/* pid_correction_windows(): budgets derived from P and the clamp (R3). */
void test_windows_at_the_qualified_gain_are_the_20ms_floor(void) {
    uint32_t prog, total;
    pid_correction_windows(65535, 34133, 170667u, TOL, &prog, &total);
    TEST_ASSERT_EQUAL_UINT32(PID_CORRECTION_WINDOW_MIN_US, prog);          // 5 x 3.9 ms = 19.5 ms -> floor 20 ms
    // total = 20 ms + 4 x (200 um / 1 mm/s = 200 ms) = 820 ms
    TEST_ASSERT_UINT32_WITHIN(1000u, 820000u, total);
}

void test_windows_scale_with_a_low_gain(void) {
    uint32_t prog, total;
    pid_correction_windows(1024, 34133, 170667u, TOL, &prog, &total);
    TEST_ASSERT_UINT32_WITHIN(1000u, 1250000u, prog);                      // 5 x 250 ms
    TEST_ASSERT_UINT32_WITHIN(2000u, 2050000u, total);                     // window + 4 x 200 ms (> 8 tau = 2 s)
    pid_correction_windows(4096, 34133, 170667u, TOL, &prog, &total);
    TEST_ASSERT_UINT32_WITHIN(1000u, 312500u, prog);                       // 5 x 62.5 ms
    pid_correction_windows(0, 34133, 170667u, TOL, &prog, &total);
    TEST_ASSERT_EQUAL_UINT32(PID_CORRECTION_WINDOW_MAX_US, prog);          // no gain: widest window
}

void test_total_budget_scales_with_the_clamp_and_is_capped(void) {
    uint32_t prog, total;
    // clamp 0.1 mm/s (17,067 pps), watchdog 200 um: 4 x 2 s = 8 s + 20 ms window
    pid_correction_windows(65535, 34133, 17067u, TOL, &prog, &total);
    TEST_ASSERT_UINT32_WITHIN(5000u, 8020000u, total);
    // clamp 0.01 mm/s: would be 80 s -> capped at 10 s
    pid_correction_windows(65535, 34133, 1707u, TOL, &prog, &total);
    TEST_ASSERT_EQUAL_UINT32(PID_CORRECTION_TIMEOUT_MAX_US, total);
    // clamp unknown: window + 1 s
    pid_correction_windows(65535, 34133, 0u, TOL, &prog, &total);
    TEST_ASSERT_EQUAL_UINT32(PID_CORRECTION_WINDOW_MIN_US + PID_CORRECTION_TIMEOUT_UNKNOWN_US, total);
}

void test_low_gain_converging_loop_does_not_false_trip(void) {
    // R3: P 1024 (tau 250 ms) closing a 50 um error: at t the error is e0 x exp(-t/tau). Sampled
    // every 5 ms the decrease per 1.25 s window is ~99 %, so progress is always seen in time.
    pid_correction_watch_reset(&W);
    uint32_t prog, total;
    pid_correction_windows(1024, 34133, 51200u, TOL, &prog, &total);   // clamp 0.3 mm/s
    int32_t dev = 8500;
    uint8_t r = PID_CORRECTION_OK;
    for (uint32_t t = 0; dev > TOL && r == PID_CORRECTION_OK; t += 5000u) {
        r = pid_correction_watch_step(&W, dev, TOL, t, prog, total);
        dev = dev - (dev * 5 + 125) / 250;   /* x exp(-5/250) per 5 ms step ~ x0.98 */
    }
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(PID_CORRECTION_OK, r, "a converging low-gain loop must not trip");
}

/* pid_stop_blocks_correction(): the chip's ramp rule (STOPL blocks VACTUAL < 0, STOPR blocks
   VACTUAL > 0) applied to the correction velocity vPID, which the stop does not gate itself. */
void test_stop_switch_blocks_a_correction_driving_toward_it(void) {
    TEST_ASSERT_TRUE(pid_stop_blocks_correction(true, false, -13000, false));   /* STOPL, driving negative */
    TEST_ASSERT_TRUE(pid_stop_blocks_correction(false, true, 13000, false));    /* STOPR, driving positive */
}

void test_stop_switch_does_not_block_a_correction_driving_away(void) {
    TEST_ASSERT_FALSE(pid_stop_blocks_correction(true, false, 13000, false));   /* on STOPL, backing away */
    TEST_ASSERT_FALSE(pid_stop_blocks_correction(false, true, -13000, false));  /* on STOPR, backing away */
}

void test_resting_on_a_switch_with_no_drive_is_not_a_fault(void) {
    /* the home switch right after homing, zone 0: the loop engages at XACTUAL 0 with vPID 0 */
    TEST_ASSERT_FALSE(pid_stop_blocks_correction(false, true, 0, false));
    TEST_ASSERT_FALSE(pid_stop_blocks_correction(true, true, 0, false));
}

void test_no_active_switch_never_blocks(void) {
    TEST_ASSERT_FALSE(pid_stop_blocks_correction(false, false, -50000, false));
    TEST_ASSERT_FALSE(pid_stop_blocks_correction(false, false, 50000, true));
}

void test_inverted_stop_direction_swaps_the_switches(void) {
    /* invert_stop_direction = 1: STOPL is the right switch and STOPR the left one */
    TEST_ASSERT_TRUE(pid_stop_blocks_correction(true, false, 13000, true));
    TEST_ASSERT_FALSE(pid_stop_blocks_correction(true, false, -13000, true));
    TEST_ASSERT_TRUE(pid_stop_blocks_correction(false, true, -13000, true));
    TEST_ASSERT_FALSE(pid_stop_blocks_correction(false, true, 13000, true));
}

void test_response_is_not_judged_until_the_drive_has_moved_a_minimum(void) {
    // A 1-count residual driven at 4.3 kpps advances 430 usteps per 100 ms window - under the
    // 512-ustep minimum (two full steps): a stepper can wind up that far against friction
    // before the stage moves. Not judged by response; the total budget still bounds it.
    pid_correction_watch_reset(&W); windows_at_qualified_config();
    uint8_t r = PID_CORRECTION_OK;
    for (uint32_t t = 0; t < 600000u && r == PID_CORRECTION_OK; t += 1000u) {
        pid_correction_watch_step(&W, TOL + 1, TOL, t, PROG, 100000000u);
        r = pid_correction_travel_step(&W, 4300u, TOL + 1, t, 100000000u, 100000u, 512u);
    }
    TEST_ASSERT_EQUAL_UINT8(PID_CORRECTION_OK, r);
}

/* Home-zone boundary (Codex 2026-09-12): a position exactly at the zone edge must count as
   inside (no engage there), and the realignment needs positive evidence that the encoder has
   moved since homing zeroed it - an offset equal to the zone is not evidence. */
void test_zone_boundary_counts_as_inside(void) {
    TEST_ASSERT_FALSE(pid_engage_pending(true, true, false, 700 * 170, 700 * 170));   /* at the edge: in zone, no engage */
    TEST_ASSERT_TRUE(pid_engage_pending(true, true, false, 700 * 170, 700 * 170 + 1));
    TEST_ASSERT_FALSE(pid_engage_pending(true, true, false, 700 * 170, -700 * 170));
}

void test_realign_frozen_encoder_parked_exactly_at_the_zone_is_refused(void) {
    // zone 700, parked at 700 (if it ever engaged there), encoder frozen at home: offset -700
    // == zone, encoder travel since homing 0. No evidence of coupling -> refuse.
    TEST_ASSERT_FALSE(pid_realign_allowed(-700 * 170, 700 * 170, 200 * 170, 0, 8 * TOL));
    TEST_ASSERT_FALSE(pid_realign_allowed(700 * 170, 700 * 170, 200 * 170, 0, 8 * TOL));
}

void test_realign_requires_the_encoder_to_have_moved_since_homing(void) {
    // Gap stage, parked at 750 with gap 640: the encoder moved 110 um since homing zeroed it.
    TEST_ASSERT_TRUE(pid_realign_allowed(-640 * 170, 700 * 170, 200 * 170, 110 * 170, 8 * TOL));
    // Same offset but the encoder reports only 3 counts of travel: below the 8-count minimum.
    TEST_ASSERT_FALSE(pid_realign_allowed(-640 * 170, 700 * 170, 200 * 170, 3 * 17, 8 * TOL));
    // Encoder travel counts whichever way it went (negative on an inverted encoder is still travel)
    TEST_ASSERT_TRUE(pid_realign_allowed(-640 * 170, 700 * 170, 200 * 170, -110 * 170, 8 * TOL));
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
    RUN_TEST(test_no_window_ack_before_convergence_is_not_settled);
    RUN_TEST(test_no_window_inside_the_tolerance_is_settled);
    RUN_TEST(test_no_window_bound_is_inclusive);
    RUN_TEST(test_no_window_bound_is_on_the_encoder_not_the_counter);
    RUN_TEST(test_window_replaces_the_tolerance_as_the_bound);
    RUN_TEST(test_bound_is_never_tighter_than_the_deadband);
    RUN_TEST(test_error_inside_the_deadband_is_never_watched);
    RUN_TEST(test_frozen_encoder_just_outside_the_deadband_trips_on_the_total_budget);
    RUN_TEST(test_small_residual_held_by_stiction_for_half_a_second_is_ok);
    RUN_TEST(test_clamp_limited_correction_does_not_false_trip);
    RUN_TEST(test_very_low_gain_tail_is_covered_by_the_total_floor);
    RUN_TEST(test_correction_that_converges_is_ok);
    RUN_TEST(test_frozen_encoder_trips_no_progress_within_the_window);
    RUN_TEST(test_error_that_grows_trips_no_progress);
    RUN_TEST(test_progress_restarts_the_window_but_not_the_total_budget);
    RUN_TEST(test_watch_handles_micros_wraparound);
    RUN_TEST(test_realign_within_gap_config_is_allowed);
    RUN_TEST(test_realign_frozen_encoder_at_the_first_park_is_refused);
    RUN_TEST(test_realign_beyond_gap_config_is_refused);
    RUN_TEST(test_travel_bound_is_independent_of_the_time_budgets);
    RUN_TEST(test_frozen_feedback_trips_no_response_within_the_response_window);
    RUN_TEST(test_responding_encoder_never_trips_no_response);
    RUN_TEST(test_response_is_not_judged_below_the_drive_floor);
    RUN_TEST(test_travel_step_is_inert_until_the_watch_is_armed);
    RUN_TEST(test_response_is_not_judged_until_the_drive_has_moved_a_minimum);
    RUN_TEST(test_zone_boundary_counts_as_inside);
    RUN_TEST(test_realign_frozen_encoder_parked_exactly_at_the_zone_is_refused);
    RUN_TEST(test_realign_requires_the_encoder_to_have_moved_since_homing);
    RUN_TEST(test_stop_switch_blocks_a_correction_driving_toward_it);
    RUN_TEST(test_stop_switch_does_not_block_a_correction_driving_away);
    RUN_TEST(test_resting_on_a_switch_with_no_drive_is_not_a_fault);
    RUN_TEST(test_no_active_switch_never_blocks);
    RUN_TEST(test_inverted_stop_direction_swaps_the_switches);
    RUN_TEST(test_realign_refused_when_nothing_is_configured);
    RUN_TEST(test_windows_at_the_qualified_gain_are_the_20ms_floor);
    RUN_TEST(test_windows_scale_with_a_low_gain);
    RUN_TEST(test_total_budget_scales_with_the_clamp_and_is_capped);
    RUN_TEST(test_low_gain_converging_loop_does_not_false_trip);
    return UNITY_END();
}
