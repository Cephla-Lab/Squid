#ifndef PID_POLICY_H
#define PID_POLICY_H
#include <stdint.h>
#include "constants_protocol.h"   /* PID_FAULT_* cause codes (plain ints, host-compilable) */
/* Pure closed-loop policy predicates, host-testable (test/test_pid_policy). */

/* Bounded-correction tunables. Header-only so the native tests assert the same numbers
   the firmware runs with. The chip's loop is first-order with rate constant P/256 per
   second (time constant tau = 256/P s: 3.9 ms at P 65535, 62 ms at 4096, 250 ms at 1024)
   and its output is clamped at PID_DV_CLIP, so both budgets are derived per axis from the
   configured P and clamp rather than fixed - a fixed 50 ms would fault a converging loop
   at low P, a fixed 1 s a large correction at a small clamp. */
#define PID_CORRECTION_TIME_CONSTANTS   5          /* progress window = 5 tau: a converging loop has closed 99 % */
#define PID_CORRECTION_WINDOW_MIN_US    20000u     /* never tighter than 20 ms (two status-packet periods) */
#define PID_CORRECTION_WINDOW_MAX_US    2000000u   /* never looser than 2 s */
#define PID_CORRECTION_TIMEOUT_FACTOR   4          /* total = window + 4 x (max_dev / clamp) */
#define PID_CORRECTION_TIMEOUT_UNKNOWN_US 1000000u /* clamp or watchdog unknown: window + 1 s */
#define PID_CORRECTION_TIMEOUT_MAX_US   10000000u  /* never longer than 10 s */

/* Progress and total budgets for one axis. p_gain is the TMC4361A P (0..65535, rate P/256
   per second); max_dev_usteps the deviation watchdog (0 = unset); dv_clip_pps the
   correction velocity clamp (0 = unknown). */
static inline void pid_correction_windows(int32_t p_gain, int32_t max_dev_usteps, uint32_t dv_clip_pps,
                                          uint32_t *progress_us, uint32_t *total_us)
{
    uint32_t win;
    if (p_gain <= 0) win = PID_CORRECTION_WINDOW_MAX_US;
    else {
        /* 5 x tau = 5 x 256 / P seconds = 5 x 256e6 / P microseconds */
        uint64_t w = (uint64_t)PID_CORRECTION_TIME_CONSTANTS * 256000000ull / (uint64_t)p_gain;
        win = w > PID_CORRECTION_WINDOW_MAX_US ? PID_CORRECTION_WINDOW_MAX_US : (uint32_t)w;
    }
    if (win < PID_CORRECTION_WINDOW_MIN_US) win = PID_CORRECTION_WINDOW_MIN_US;
    *progress_us = win;

    uint64_t total;
    if (max_dev_usteps > 0 && dv_clip_pps > 0)
        total = (uint64_t)win + (uint64_t)PID_CORRECTION_TIMEOUT_FACTOR * (uint64_t)max_dev_usteps * 1000000ull / (uint64_t)dv_clip_pps;
    else
        total = (uint64_t)win + PID_CORRECTION_TIMEOUT_UNKNOWN_US;
    *total_us = total > PID_CORRECTION_TIMEOUT_MAX_US ? PID_CORRECTION_TIMEOUT_MAX_US : (uint32_t)total;
}

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
/* ---- Bounded correction (frozen feedback / stage on its stop) -------------------------
   The chip nulls XACTUAL - ENC_POS. Motion the correction generates does not move XACTUAL,
   so the travel limits see nothing; and if the encoder stops counting the deviation never
   grows, so the deviation watchdog never trips. The clamp bounds speed, not distance. This
   watch bounds distance: whenever the loop is engaged, the ramp idle, and |dev| outside the
   chip's deadband (PID_TOLERANCE - inside it the chip does not drive at all), the error must
   shrink by at least one deadband every progress window and the whole correction must finish
   within the total budget; otherwise the loop is a fault. Any error the chip is driving on
   is watched, whatever its size: a frozen encoder two counts outside the deadband is driven
   toward forever just like one 50 um out. */

struct PidCorrectionWatch {
    bool     active;     /* armed: |dev| went beyond `arm` while engaged at rest */
    uint32_t start_us;   /* when it armed (total budget) */
    uint32_t mark_us;    /* last time |dev| improved by >= tol (progress budget) */
    int32_t  best_abs;   /* smallest |dev| seen since arming */
};

#define PID_CORRECTION_OK          0
#define PID_CORRECTION_NO_PROGRESS 1
#define PID_CORRECTION_TIMEOUT     2

static inline void pid_correction_watch_reset(PidCorrectionWatch *w)
{
    w->active = false; w->start_us = 0; w->mark_us = 0; w->best_abs = 0;
}

/* Feed one sample taken while the loop is engaged and the ramp idle. Returns one of the
   PID_CORRECTION_* codes; on a non-OK code the caller trips the fault and resets. Callers
   must reset the watch whenever the loop is not engaged at rest (opened for a move, held by
   the zone, disabled), so a correction is only ever judged against its own timeline. */
static inline uint8_t pid_correction_watch_step(PidCorrectionWatch *w, int32_t abs_dev, int32_t tol,
                                                uint32_t now_us, uint32_t progress_us, uint32_t total_us)
{
    if (abs_dev <= tol) {               /* inside the deadband: the chip is not driving (also: converged) */
        pid_correction_watch_reset(w);
        return PID_CORRECTION_OK;
    }
    if (!w->active) {                   /* arm on the first sample outside the deadband */
        w->active = true;
        w->start_us = now_us;
        w->mark_us = now_us;
        w->best_abs = abs_dev;
        return PID_CORRECTION_OK;
    }
    if (abs_dev <= w->best_abs - tol) { /* real progress: restart the progress window only */
        w->best_abs = abs_dev;
        w->mark_us = now_us;
    }
    /* unsigned subtraction is wrap-safe for spans below 2^32 us (~71 min) */
    if ((uint32_t)(now_us - w->start_us) > total_us) return PID_CORRECTION_TIMEOUT;
    if ((uint32_t)(now_us - w->mark_us) > progress_us) return PID_CORRECTION_NO_PROGRESS;
    return PID_CORRECTION_OK;
}

/* Post-homing realignment takes the counter's frame as the encoder's. That absorbs the
   mechanical gap of a stage whose actuator homes below its stop - and would equally absorb
   lost motion during the first departure, or an encoder that never started following. The
   absorbed offset is therefore bounded by what the configuration declares: the home zone
   (where the stage may be decoupled) plus the watchdog. CONFIGURE_STAGE_PID always leaves a
   watchdog (0.25 mm default), so a zero bound means the axis was RESET and never
   re-configured - refuse, do not absorb blindly. The host checks at startup that
   zone + watchdog covers its declared z_home_gap_mm. */
static inline bool pid_realign_allowed(int32_t frame_offset, int32_t zone_usteps, int32_t max_dev_usteps)
{
    int32_t bound = (zone_usteps > 0 ? zone_usteps : 0) + (max_dev_usteps > 0 ? max_dev_usteps : 0);
    if (bound <= 0) return false;       /* nothing configured: nothing declared, nothing absorbed */
    return frame_offset <= bound && frame_offset >= -bound;
}
#endif
