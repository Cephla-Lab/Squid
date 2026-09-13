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
    bool in_zone = (zone_usteps > 0) && (pos_usteps >= -zone_usteps) && (pos_usteps <= zone_usteps);   /* the edge is inside */
    return !in_zone;
}

/* Encoder leg of a commanded move's completion while the loop is engaged. `counter_minus_target`
   is XACTUAL - target and `enc_minus_counter` ENC_POS_DEV as the chip reports it (ENC_POS -
   XACTUAL), so their sum is the ENCODER's distance to the target: the bound is on that, not on
   the two legs separately (which allows 2*win). The bound is the completion window when one is
   set, else the target-reached tolerance - and never tighter than the chip's deadband
   (PID_TOLERANCE): inside it the chip does not correct, so a tighter bound is a move that never
   completes and, since the correction watch is idle inside the deadband too, is never faulted
   either (SET_PID_TOLERANCE takes the two tolerances independently). Inclusive throughout: the
   datasheet documents the deadband both ways ("moves with vPID until |PID_E| - PID_TOLERANCE <= 0";
   "PID_E = 0 in case |PID_E| < PID_TOLERANCE") and calls it a hysteresis, so the chip may rest at
   |e| == deadband; an inclusive bound completes under either reading. Fed from ENC_POS_DEV, which
   the chip keeps from the encoder edges whatever the PID state, not from PID_E (see
   commanded_move_complete in operations.cpp). */
static inline bool pid_completion_encoder_ok(int32_t counter_minus_target, int32_t enc_minus_counter,
                                             int32_t win, int32_t target_tol, int32_t deadband)
{
    int32_t e = counter_minus_target + enc_minus_counter;
    if (e < 0) e = -e;
    int32_t bound = win > 0 ? win : target_tol;
    if (bound < deadband) bound = deadband;
    return e <= bound;
}
/* The correction clamp in the unit PID_DV_CLIP (0x5E) and PID_VEL (0x5A) use: integer pulses per
   second, like VACTUAL - not the 24.8 fixed point of VMAX that tmc4361A_vmmToMicrosteps() produces
   (datasheet: VMAX "24 digits and 8 decimal places", PID_VEL / PID_DV_CLIP no such note; the bench
   trace's PID_VEL is exactly 65535/256 x the error in usteps). The firmware wrote the 24.8 value into
   PID_DV_CLIP from the first closed-loop firmware through 6a8b12cd, so a "1 mm/s" clamp was
   2,730,667 pps = 256 mm/s, i.e. no clamp at all, while the correction watch budgeted against the
   shifted number. One conversion, used by both writers and the budgets. Rounded to the nearest pps;
   0 for no velocity or no pitch. */
static inline uint32_t pid_clamp_pps(float mm_per_s, uint32_t microsteps, uint32_t steps_per_rev, float pitch_mm)
{
    if (mm_per_s <= 0.0f || pitch_mm <= 0.0f) return 0u;
    float pps = mm_per_s * (float)(microsteps * steps_per_rev) / pitch_mm;
    return (uint32_t)(pps + 0.5f);
}
/* Completion dwell (closed loop). A rest-only loop re-engages with the open-loop residual as its
   error and rings about the target before it settles - on the 2240 bench (P 65535, 2-count
   deadband) 25-40 ms with a 5-9 ustep amplitude, PID_VEL changing sign every few ms. One pass with
   the encoder inside the bound is therefore a zero crossing as often as the settled position
   (traced 2026-09-13/14: ENC_POS_DEV -1 at the accepted pass, +6 in the packet 5 ms later, then
   -5, -7, +1, -4 ...). So the encoder must stay inside the bound for PID_COMPLETION_DWELL_US
   without a break. A ring of amplitude A >= 2.5 x the band crosses the +-band in a small fraction
   of its period (well under 5 ms of a 10-14 ms period here), so it cannot satisfy the dwell; a
   settled loop satisfies it once. Cost: +dwell on every closed-loop acknowledgment, plus whatever
   settling the loop actually needed - which is the point: the ack now means "inside the bound and
   staying there", the contract SET_COMPLETION_WINDOW and the target tolerance describe.
   `*inside_since_us` is per-axis state: 0 = not inside (or consumed by a completion); the caller
   passes the same slot every pass and clears it whenever an earlier leg of the rule fails. */
#define PID_COMPLETION_DWELL_US 5000u
static inline bool pid_completion_dwell_step(uint32_t *inside_since_us, bool inside, uint32_t now_us,
                                             uint32_t dwell_us)
{
    if (!inside) { *inside_since_us = 0; return false; }
    if (*inside_since_us == 0) {
        if (dwell_us == 0) return true;   /* no dwell configured: the old single-pass rule */
        /* micros() can be 0 at the first pass after boot: keep 0 as the "not started" value */
        *inside_since_us = now_us ? now_us : 1u;
        return false;
    }
    if ((uint32_t)(now_us - *inside_since_us) < dwell_us) return false;   /* wrap-safe */
    *inside_since_us = 0;   /* consumed: the next move starts its own dwell */
    return true;
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
    uint32_t resp_mark_us;   /* start of the current response window */
    int32_t  resp_dev_ref;   /* deviation at the start of the response window */
    uint64_t resp_travel_ref;/* travel integral at the start of the response window */
};


#define PID_CORRECTION_OK          0
#define PID_CORRECTION_NO_PROGRESS 1
#define PID_CORRECTION_TIMEOUT     2

static inline void pid_correction_watch_reset(PidCorrectionWatch *w)
{
    w->active = false; w->start_us = 0; w->mark_us = 0; w->best_abs = 0;
    w->has_last = false; w->last_us = 0; w->travel_pps_us = 0; w->resp_mark_us = 0;
    w->resp_dev_ref = 0; w->resp_travel_ref = 0;
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
#define PID_CORRECTION_RESPONSE_RATIO_SHIFT 3   /* the encoder must move >= 1/8 of the drive's advance */
#define PID_REALIGN_MIN_ENC_TRAVEL_TOLERANCES 8 /* realign needs >= 8 x deadband of encoder travel since homing */
#define PID_CORRECTION_RESPONSE_MIN_DRIVE_FULLSTEPS 2 /* judge response only once the drive advanced >= 2 full steps in the window */
#define PID_CORRECTION_RESPONSE_WINDOWS     4   /* response window = 4 x progress window, >= 100 ms */
#define PID_CORRECTION_RESPONSE_MIN_US      100000u

/* Distance and response bounds on a correction, fed alongside pid_correction_watch_step()
   from the same pass (call this AFTER it, only while it is armed). pid_vel_abs_pps is
   |PID_VEL_RD| (the chip's correction output, pps); dev_now is ENC_POS - XACTUAL as read this
   pass. With the ramp idle XACTUAL does not move, so a change in dev IS the encoder's
   displacement.
   Trips: TRAVEL when the integral of |PID_VEL| since arming exceeds travel_limit_usteps
   (whatever the error, however slow: the correction may never travel farther than the
   declared watchdog distance without converging); NO_RESPONSE when, over a whole response
   window in which the drive advanced at least min_drive_usteps, the encoder moved less than
   1/8 of that advance (frozen feedback or a stage that does not follow the motor).
   The response is judged on encoder DISPLACEMENT, not on the chip's encoder-velocity
   registers: V_ENC / V_ENC_MEAN hold their last value until ENC_VEL_ZERO clocks pass with
   no edge (0xFFFFFF at reset, ~1 s at 16 MHz), so a frozen encoder reads as "still moving"
   for a second. min_drive_usteps (a couple of full steps) keeps a stepper's wind-up against
   friction from counting as no response. */
static inline uint8_t pid_correction_travel_step(PidCorrectionWatch *w, uint32_t pid_vel_abs_pps, int32_t dev_now,
                                                 uint32_t now_us, uint32_t travel_limit_usteps, uint32_t response_us,
                                                 uint32_t min_drive_usteps)
{
    if (!w->active) return PID_CORRECTION_OK;         /* inside the deadband: the chip is not driving */
    if (!w->has_last) {
        w->has_last = true; w->last_us = now_us;
        w->resp_mark_us = now_us; w->resp_dev_ref = dev_now; w->resp_travel_ref = 0;
        return PID_CORRECTION_OK;
    }
    uint32_t dt = (uint32_t)(now_us - w->last_us);    /* wrap-safe */
    w->last_us = now_us;
    w->travel_pps_us += (uint64_t)pid_vel_abs_pps * (uint64_t)dt;
    if (travel_limit_usteps > 0 && w->travel_pps_us / 1000000ull > (uint64_t)travel_limit_usteps)
        return PID_CORRECTION_TRAVEL;

    uint64_t drive = (w->travel_pps_us - w->resp_travel_ref) / 1000000ull;   /* usteps the drive advanced this window */
    int32_t moved = dev_now - w->resp_dev_ref;
    if (moved < 0) moved = -moved;
    if (drive >= (uint64_t)min_drive_usteps) {
        if ((uint64_t)moved >= (drive >> PID_CORRECTION_RESPONSE_RATIO_SHIFT)) {
            /* the encoder followed: start a new window from here */
            w->resp_mark_us = now_us; w->resp_dev_ref = dev_now; w->resp_travel_ref = w->travel_pps_us;
        } else if ((uint32_t)(now_us - w->resp_mark_us) > response_us) {
            return PID_CORRECTION_NO_RESPONSE;
        }
    } else if ((uint32_t)(now_us - w->resp_mark_us) > response_us) {
        /* too little drive to judge in this window: slide the window, keep watching */
        w->resp_mark_us = now_us; w->resp_dev_ref = dev_now; w->resp_travel_ref = w->travel_pps_us;
    }
    return PID_CORRECTION_OK;
}

/* Post-homing realignment takes the counter's frame as the encoder's. That absorbs the
   mechanical gap of a stage whose actuator homes below its stop - and would equally absorb
   lost motion during the first departure, or an encoder that never started following. Two
   conditions, both required:
   - the offset is within what the configuration declares: the home ZONE when one is
     configured (the realignment only runs outside the zone), else the watchdog; nothing
     configured (post-RESET, never re-configured) refuses;
   - the encoder has actually MOVED since homing zeroed it, by at least min_enc_travel: an
     encoder frozen at home shows zero travel whatever the offset, and an offset that merely
     equals the zone is not evidence either (a park exactly at the zone edge, Codex
     2026-09-12). The host keeps the Z floor above the zone so a real gap stage always
     departs past the zone with margin. */
static inline bool pid_realign_allowed(int32_t frame_offset, int32_t zone_usteps, int32_t max_dev_usteps,
                                       int32_t enc_travel_since_home, int32_t min_enc_travel)
{
    int32_t bound = zone_usteps > 0 ? zone_usteps : (max_dev_usteps > 0 ? max_dev_usteps : 0);
    if (bound <= 0) return false;       /* nothing configured: nothing declared, nothing absorbed */
    if (frame_offset > bound || frame_offset < -bound) return false;
    int32_t moved = enc_travel_since_home < 0 ? -enc_travel_since_home : enc_travel_since_home;
    return moved >= min_enc_travel;     /* positive evidence that the feedback is alive */
}

/* ---- Stop switches under closed-loop correction ---------------------------------------
   The chip's reference switches stop the RAMP: "the velocity ramp stops in case STOPL matches
   pol_stop_left and VACTUAL < 0" (STOPR: VACTUAL > 0), and a hard stop sets VACTUAL = 0. The
   closed loop's correction vPID is added AFTER the ramp (VEL_ACT_PID = VACTUAL + vPID, or = vPID
   with the base pulse generator at 0, which is how this firmware engages the loop), and nothing
   in the datasheet gates vPID on a stop (TMC4330A/4361A datasheet §8.1, §12.2.2, 2026-09-12
   reading). So a correction can drive an axis through an active stop switch. This predicate
   applies the chip's own ramp rule to vPID: a stop is blocking when it is active AND the
   correction drives toward it. Resting on a switch with no drive (the home switch right after
   homing, with no home zone configured) is not a fault. `inverted` mirrors REFERENCE_CONF's
   invert_stop_direction (STOPL acts as the right switch and vice versa). */
static inline bool pid_stop_blocks_correction(bool stopl_active, bool stopr_active, int32_t pid_vel, bool inverted)
{
    if (pid_vel == 0) return false;
    bool left  = inverted ? stopr_active : stopl_active;   /* the switch that blocks negative motion */
    bool right = inverted ? stopl_active : stopr_active;   /* the switch that blocks positive motion */
    return (left && pid_vel < 0) || (right && pid_vel > 0);
}
#endif
