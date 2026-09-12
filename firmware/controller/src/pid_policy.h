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
/* ---- Bounded correction (frozen feedback / stage on its stop) -------------------------
   The chip nulls XACTUAL - ENC_POS. Motion the correction generates does not move XACTUAL,
   so the travel limits see nothing; and if the encoder stops counting the deviation never
   grows, so the deviation watchdog never trips. The clamp bounds speed, not distance. This
   watch bounds distance: while the loop is engaged, the ramp idle, and |dev| beyond an
   arming threshold, the error must shrink by at least one tolerance every `progress_us`
   and the whole correction must finish within `total_us`; otherwise the loop is a fault.
   Errors below the arming threshold are never watched: a small stiction residual the loop
   cannot close is benign and must not trip anything. */

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
static inline uint8_t pid_correction_watch_step(PidCorrectionWatch *w, int32_t abs_dev, int32_t tol, int32_t arm,
                                                uint32_t now_us, uint32_t progress_us, uint32_t total_us)
{
    if (abs_dev <= arm) {               /* inside the benign band: nothing to watch (also: converged) */
        pid_correction_watch_reset(w);
        return PID_CORRECTION_OK;
    }
    if (!w->active) {                   /* arm on the first sample beyond the threshold */
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
   (where the stage may be decoupled) plus the watchdog. With neither configured there is no
   bound (open configuration). */
static inline bool pid_realign_allowed(int32_t frame_offset, int32_t zone_usteps, int32_t max_dev_usteps)
{
    int32_t bound = (zone_usteps > 0 ? zone_usteps : 0) + (max_dev_usteps > 0 ? max_dev_usteps : 0);
    if (bound <= 0) return true;        /* nothing configured: no bound (open configuration) */
    return frame_offset <= bound && frame_offset >= -bound;
}
#endif
