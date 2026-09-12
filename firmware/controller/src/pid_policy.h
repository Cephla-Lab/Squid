#ifndef PID_POLICY_H
#define PID_POLICY_H
#include <stdint.h>
#include "constants_protocol.h"   /* PID_FAULT_* cause codes (plain ints, host-compilable) */
/* Pure closed-loop policy predicates, host-testable (test/test_pid_policy). */

/* Bounded-correction tunables. Header-only so the native tests assert the same numbers
   the firmware runs with. The chip's loop is first-order with rate constant P/256 per
   second (time constant tau = 256/P s: 3.9 ms at P 65535, 62 ms at 4096, 250 ms at 1024)
   and its output is clamped at PID_DV_CLIP, so both budgets are derived per axis from the
   configured P, clamp and deadband rather than fixed.

   Two tiers, by error size:
   - |e| > PID_CORRECTION_ARM_TOLERANCES x deadband: a real correction. It must show progress
     (shrink by a deadband) every progress window AND finish within the total budget.
   - deadband < |e| <= 4 x deadband: a small residual. Only the total budget applies: stiction
     and reversal backlash hold a residual of a count or two for longer than one window on a
     healthy stage, so a progress requirement here would false-trip - but a frozen encoder
     two counts out is still driven for ever, so it is still bounded (at the qualified
     configuration: ~0.8 s at ~0.08 mm/s, about 65 um).
   The progress window is the larger of 5 tau (a converging loop has closed 99 %) and
   3 x deadband / clamp (a clamp-limited correction needs that long to move one deadband),
   never under 20 ms (two status-packet periods) nor over 2 s. The total is the larger of
   window + 4 x watchdog / clamp and 8 tau (a very slow loop's tail), never over 10 s. */
#define PID_CORRECTION_ARM_TOLERANCES   4          /* progress is required only above 4 x deadband */
#define PID_CORRECTION_TIME_CONSTANTS   5          /* progress window >= 5 tau */
#define PID_CORRECTION_CLAMP_MARGIN     3          /* progress window >= 3 x (deadband / clamp): quantisation + phase */
#define PID_CORRECTION_WINDOW_MIN_US    20000u     /* never tighter than 20 ms */
#define PID_CORRECTION_WINDOW_MAX_US    2000000u   /* never looser than 2 s */
#define PID_CORRECTION_TIMEOUT_FACTOR   4          /* total >= window + 4 x (max_dev / clamp) */
#define PID_CORRECTION_TIMEOUT_TAUS     8          /* total >= 8 tau */
#define PID_CORRECTION_TIMEOUT_UNKNOWN_US 1000000u /* clamp or watchdog unknown: window + 1 s */
#define PID_CORRECTION_TIMEOUT_MAX_US   10000000u  /* never longer than 10 s */

/* Progress and total budgets for one axis. p_gain is the TMC4361A P (0..65535, rate P/256
   per second); max_dev_usteps the deviation watchdog (0 = unset); dv_clip_pps the
   correction velocity clamp (0 = unknown); tol_usteps the chip's deadband. */
static inline void pid_correction_windows(int32_t p_gain, int32_t max_dev_usteps, uint32_t dv_clip_pps,
                                          int32_t tol_usteps, uint32_t *progress_us, uint32_t *total_us)
{
    uint64_t tau_us = p_gain > 0 ? 256000000ull / (uint64_t)p_gain : (uint64_t)PID_CORRECTION_WINDOW_MAX_US;

    uint64_t win = (uint64_t)PID_CORRECTION_TIME_CONSTANTS * tau_us;
    if (dv_clip_pps > 0 && tol_usteps > 0) {
        uint64_t per_deadband = (uint64_t)PID_CORRECTION_CLAMP_MARGIN * (uint64_t)tol_usteps * 1000000ull / (uint64_t)dv_clip_pps;
        if (per_deadband > win) win = per_deadband;
    }
    if (win < PID_CORRECTION_WINDOW_MIN_US) win = PID_CORRECTION_WINDOW_MIN_US;
    if (win > PID_CORRECTION_WINDOW_MAX_US) win = PID_CORRECTION_WINDOW_MAX_US;
    *progress_us = (uint32_t)win;

    uint64_t total;
    if (max_dev_usteps > 0 && dv_clip_pps > 0)
        total = win + (uint64_t)PID_CORRECTION_TIMEOUT_FACTOR * (uint64_t)max_dev_usteps * 1000000ull / (uint64_t)dv_clip_pps;
    else
        total = win + PID_CORRECTION_TIMEOUT_UNKNOWN_US;
    uint64_t taus = (uint64_t)PID_CORRECTION_TIMEOUT_TAUS * tau_us;
    if (taus > total) total = taus;
    if (total > PID_CORRECTION_TIMEOUT_MAX_US) total = PID_CORRECTION_TIMEOUT_MAX_US;
    *total_us = (uint32_t)total;
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
    bool     active;     /* armed: |dev| outside the deadband while engaged at rest */
    uint32_t start_us;   /* when it armed (total budget) */
    uint32_t mark_us;    /* last time |dev| improved by >= tol (progress budget) */
    int32_t  best_abs;   /* smallest |dev| seen since arming */
    /* travel and response (pid_correction_travel_step) */
    bool     has_last;
    uint32_t last_us;        /* previous sample time, for the velocity integral */
    uint64_t travel_pps_us;  /* integral of |PID_VEL| dt, in pps x us (/1e6 = usteps) */
    uint32_t resp_mark_us;   /* last time the encoder was seen responding to the drive */
};


#define PID_CORRECTION_OK          0
#define PID_CORRECTION_NO_PROGRESS 1
#define PID_CORRECTION_TIMEOUT     2

static inline void pid_correction_watch_reset(PidCorrectionWatch *w)
{
    w->active = false; w->start_us = 0; w->mark_us = 0; w->best_abs = 0;
    w->has_last = false; w->last_us = 0; w->travel_pps_us = 0; w->resp_mark_us = 0;
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
    /* the progress requirement applies only to a real correction (above the arming band):
       a small residual may sit on friction for a while and still be healthy */
    if (abs_dev > PID_CORRECTION_ARM_TOLERANCES * tol && (uint32_t)(now_us - w->mark_us) > progress_us)
        return PID_CORRECTION_NO_PROGRESS;
    return PID_CORRECTION_OK;
}

#define PID_CORRECTION_TRAVEL      3
#define PID_CORRECTION_NO_RESPONSE 4
#define PID_CORRECTION_RESPONSE_RATIO_SHIFT 3   /* the encoder must move at >= |PID_VEL| / 8 */
#define PID_CORRECTION_RESPONSE_MIN_PPS     100 /* below this drive, response is not judged */
#define PID_CORRECTION_RESPONSE_WINDOWS     4   /* response window = 4 x progress window, >= 100 ms */
#define PID_CORRECTION_RESPONSE_MIN_US      100000u

/* Distance and response bounds on a correction, fed alongside pid_correction_watch_step()
   from the same pass (call this AFTER it, only while it is armed). pid_vel_abs_pps is
   |PID_VEL_RD| (the chip's correction output, pps); enc_vel_abs_pps is |V_ENC_MEAN_RD|.
   Trips: TRAVEL when the integral of |PID_VEL| since arming exceeds travel_limit_usteps
   (whatever the error, however slow: the correction may never travel farther than the
   declared watchdog distance without converging), NO_RESPONSE when the chip has been
   driving at >= RESPONSE_MIN_PPS and the encoder has not moved at >= 1/8 of that for a
   whole response window (frozen feedback or a stage that does not follow the motor). */
static inline uint8_t pid_correction_travel_step(PidCorrectionWatch *w, uint32_t pid_vel_abs_pps, uint32_t enc_vel_abs_pps,
                                                 uint32_t now_us, uint32_t travel_limit_usteps, uint32_t response_us)
{
    if (!w->active) return PID_CORRECTION_OK;         /* inside the deadband: the chip is not driving */
    if (!w->has_last) {
        w->has_last = true; w->last_us = now_us; w->resp_mark_us = now_us;
        return PID_CORRECTION_OK;
    }
    uint32_t dt = (uint32_t)(now_us - w->last_us);    /* wrap-safe */
    w->last_us = now_us;
    w->travel_pps_us += (uint64_t)pid_vel_abs_pps * (uint64_t)dt;
    if (travel_limit_usteps > 0 && w->travel_pps_us / 1000000ull > (uint64_t)travel_limit_usteps)
        return PID_CORRECTION_TRAVEL;
    if (pid_vel_abs_pps < PID_CORRECTION_RESPONSE_MIN_PPS || enc_vel_abs_pps >= (pid_vel_abs_pps >> PID_CORRECTION_RESPONSE_RATIO_SHIFT))
        w->resp_mark_us = now_us;                      /* not driving, or the encoder is following */
    else if ((uint32_t)(now_us - w->resp_mark_us) > response_us)
        return PID_CORRECTION_NO_RESPONSE;
    return PID_CORRECTION_OK;
}

/* Post-homing realignment takes the counter's frame as the encoder's. That absorbs the
   mechanical gap of a stage whose actuator homes below its stop - and would equally absorb
   lost motion during the first departure, or an encoder that never started following. The
   realignment only ever runs OUTSIDE the home zone, so an encoder frozen at home shows an
   offset equal to the resting position, which is always larger than the zone: the zone
   alone is therefore the evidence that the encoder moved (offset <= zone means the encoder
   covered at least position - zone). With no zone configured (a stage with no gap) the
   encoder must have followed from the start: the offset is bounded by the watchdog. Nothing
   configured at all (post-RESET, never re-configured) refuses. The host checks at startup
   that the zone covers its declared z_home_gap_mm. */
static inline bool pid_realign_allowed(int32_t frame_offset, int32_t zone_usteps, int32_t max_dev_usteps)
{
    int32_t bound = zone_usteps > 0 ? zone_usteps : (max_dev_usteps > 0 ? max_dev_usteps : 0);
    if (bound <= 0) return false;       /* nothing configured: nothing declared, nothing absorbed */
    return frame_offset <= bound && frame_offset >= -bound;
}
#endif
