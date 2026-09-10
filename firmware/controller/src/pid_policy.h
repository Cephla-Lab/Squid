#ifndef PID_POLICY_H
#define PID_POLICY_H
#include <stdint.h>
/* Pure closed-loop policy predicates, host-testable (test/test_pid_policy). */

/* True while a requested loop is still held open for a move outside the home zone: the loop
   re-engages (or faults) on the next check_closed_loop() pass, so a move on this axis must
   not be reported complete yet - the encoder correction has not run. Inside the zone the loop
   is held open by design and completion is on the counter; while homing likewise. */
static inline bool pid_engage_pending(bool requested, bool zone_hold, bool homing,
                                      int32_t zone_usteps, int32_t pos_usteps)
{
    if (!requested || !zone_hold || homing) return false;
    bool in_zone = (zone_usteps > 0) && (pos_usteps > -zone_usteps) && (pos_usteps < zone_usteps);
    return !in_zone;
}

/* Completion window with a closed loop: the ENCODER must be within `win` of the target, not
   merely the counter within `win` and the encoder within `win` of the counter (which allows
   2*win). `dev` is ENC_POS_DEV as read from the chip; `enc_minus_counter` converts it to
   (encoder - counter) under the chip's sign convention (see operations.cpp). */
static inline bool encoder_within_window(int32_t counter_minus_target, int32_t enc_minus_counter, int32_t win)
{
    int32_t enc_minus_target = counter_minus_target + enc_minus_counter;
    return (enc_minus_target < 0 ? -enc_minus_target : enc_minus_target) <= win;
}
#endif
