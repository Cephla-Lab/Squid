#ifndef MOVE_SETTLE_POLICY_H
#define MOVE_SETTLE_POLICY_H
#include <stdint.h>
#include "constants_protocol.h"   /* PID_FAULT_*, MOVE_SETTLE_APPROACH_* (plain ints, host-compilable) */

/*
  Move-and-settle with encoder correction (LOOP_STRATEGY_MOVE_SETTLE): the feedforward alternative to the TMC4361A's PID.
  Pure policy, host-testable (test/test_move_settle_policy); the chip is driven by move_settle.cpp.

  WHY NOT THE CHIP'S LOOP. A stepper is a position source: an open-loop ramp lands the stage within
  a few encoder counts, fast and deterministically. What the encoder adds is the truth about where
  that was. The chip's PID uses it as a continuous velocity loop, and on this class of stage
  (lead screw + nut, ~1 um of lost motion on a reversal, one encoder count ~ one microstep, a
  lightly damped 113 Hz mode that rings for ~25 ms after every move - 2026-09-19 traces) a
  continuous loop has nothing good to choose from: at the gain that settles fast it chases the
  ring and limit-cycles through the lost motion (+-2-4 counts for 200 ms after the ack), at the
  gain that does not it creeps in over ~50 ms, and either way it keeps moving the stage during the
  exposure. It also acts on single encoder samples, so its deadband cannot be tighter than the
  quantisation it would otherwise chase.

  WHAT THIS DOES INSTEAD. Every move is planned in the ENCODER's frame and executed by the ramp
  generator; the encoder is only ever read at rest, averaged over one period of the ring:
    1. plan   - motor usteps = (target - encoder now), plus the lost motion on a reversal, minus an
                optional undershoot bias; short legs can be split in two half a ring period apart
                (a ZV input shaper) so they do not excite the ring in the first place;
    2. land   - the ramp runs open loop, exactly as it always has;
    3. measure- average ENC_POS over the window (one ring period: the ring cancels and dithers
                the quantisation, so the mean resolves a fraction of a count);
    4. decide - inside the tolerance: done. Short of the target: a TRIM, a small finite ramp in the
                direction the stage was already travelling (no reversal, the nut stays on its
                flank, the stage answers ~1:1). Past the target: never a small reverse nudge (it
                would vanish in the lost motion and wind up) but a BACK-OFF beyond the lost motion
                and a fresh approach. Every correction is a bounded move of the ramp generator, so
                travel limits and stop switches gate it like any other move;
    5. freeze - DONE hands the axis back at rest with nothing regulating: the motor holds its
                microstep, which is the quietest state the stage has. The counter is re-based to
                the target so XACTUAL stays the host's frame (the chip's PID kept that property by
                not counting its correction pulses; here it is one register write at rest).
  THE LOST-MOTION MODEL (bench 2026-09-20, Squid+ Z). The stage does not sit on the drive flank
  after every leg. A leg that PUSHES the stage a good way (fast_min usteps or more) leaves it `carry`
  beyond the flank that pushed it - 4-6 usteps here; a short push (a trim) leaves it nearly in
  contact; in between the carry grows with the push. So what a leg does to the stage depends on how
  the previous one ended. The model is a play operator: the stage sits inside a play of `lost` usteps
  between two flanks, `gap` ahead of the flank that pushed it last (`side`):
      free         = how far the flank that drives this leg is from the stage
                   = gap going the way of `side`, lost - gap going against it
      motor travel = free + push
      stage travel = push + carry x min(push, fast_min) / fast_min
  and the gap is ANCHORED BY EVERY PUSH (2026-09-21): a flank that has just pushed the stage has it, ahead
  by the carry of that push, less what the leg fell short of its plan by (a push that ends early has not
  carried the stage). Only a leg that did not reach the stage moves the gap by the book (gap -/+ motor
  travel), so a small correction one way and a step back the other is still not "a reversal" costing the
  whole lost motion, and a trim after a fast leg still knows it has the carry to close before anything
  moves. Until that date the gap was booked after EVERY leg as stage travel minus motor travel - an
  integrator with no way back: the play a reversal really crosses scatters around lost[] by more than
  the band, the scatter was booked as gap, every continuing leg carried it on and landed past by it,
  and the landings taught the carry to absorb it (bench trace settle14_trace_b: carry 13 -> 32 usteps,
  lost 69 -> 83 in one soak; back-offs and missed moves that did not heal within a run). With it goes
  the rule that the CARRY IS TAUGHT ONLY WHERE IT CAN BE OBSERVED: a fast leg that started from contact,
  and a trim that began with a take-up (its over-response is the carry's error). A fast leg that begins
  with a take-up lands where it does whatever the carry is - take-up and carry cancel.
  A chain of equal fast legs is self-consistent (take-up = carry every time), which is why an open-loop
  stack has uniform spacing - and why the first plane after a trim landed 4-6 usteps long and the
  plane after that short by as much, for as long as the tolerance kept asking for corrections. Every
  leg is therefore planned through this model (settle_plan_leg), and both numbers are LEARNED from the
  landings themselves: each measured leg is compared with what the plan expected and the difference
  goes, clipped and at learn_x16 / 16, into `lost` (a reversing leg) or `carry` (a fast continuing
  leg). The host's values are only where the learning starts.
  Three things the second bench night added (2026-09-20, 64 usteps/FS):
    - carry and lost are kept PER DIRECTION. On a vertical axis gravity makes them differ: with one
      number for both, up legs landed ~0.15 um short and down legs ~0.15 um long, at every length.
    - the SCREW'S LEAD is not the encoder's scale (1100 ppm here = 0.22 um per 200 um). It enters twice:
      the leg falls short, and the observed gap books that shortfall as "closer to the flank", which
      the next continuing leg pays again. `scale` (stage travel per motor travel, ppm) is seeded by the
      host and learned from long continuing legs; plans and the gap bookkeeping both go through it.
    - a leg that CROSSES the play scatters ~3x wider than one that continues (~0.25 um against ~0.09 um
      SD). Its own scatter is tracked, a first leg across the play aims short by that (up to the whole
      tolerance), and the approach after a back-off aims two sigmas short and lets a trim finish:
      landing past the target a second time would be a missed move.
  What the model does not describe (left to the trims): the first move after motion the policy did not
  issue (its place in the play is a guess), and long unshaped legs, whose carry is not that of the
  short shaped ones it is learned from (a 300 um reversing leg typically takes one or two trims).
  LONG LEGS END WITH A SHORT FINISHING LEG (bench 2026-09-26). After a fast leg of 0.5-3 mm the stage lands up
  to +-1 um from where the model expects, and not as a function of position (a lead map did not help): long
  legs trimmed or backed off (44 % first-leg landings, 8 % back-offs). A 50 um leg issued right after one lands
  like any short step (72 %, 3 %, 0.12 um median). So a move whose first leg would want more than `finish_from`
  is issued as two approach legs: the long one aimed `finish` short of the target, then, from its measured
  landing, an ordinary continuing leg for the rest. The long leg's landing is neither judged nor reported: it
  was never aimed at the target, so inside / short / past mean nothing there, and what the report describes
  is the leg that was - the finishing one, whose landing is the first landing of the move.
  A COUNTER-PLANNED LEG WHOSE LANDING WILL BE JUDGED ENDS THE SAME WAY (bench 2026-09-26, x9_home_a). Inside the
  home zone moves run open loop and the counter drifts 1-2 um from the stage. A move that starts in there is
  planned on the counter, so at a target outside the zone it landed past by the drift and backed off (first
  landing -56 usteps, 160 ms). It stops `finish` short instead, at any length - the reason is the counter's
  drift, not what a long leg does to the landing - and the finishing leg is planned from the encoder.

  BACK-OFFS ARE THE EXCEPTION (bench 2026-09-20: 1-7 % of planes, 70-120 ms each, two crossings of
  the play, and the source of every fault that night). So: the accepted band is asymmetric (tol short
  of the target, tol_over past it - an accepted overshoot does not accumulate, the next plan starts
  from the encoder); the first landing aims short by a learned fraction of the landing scatter; a
  trim aims at the near side of the band; and a back-off is left for what is beyond the band, i.e. a
  disturbance, once. A move whose budget runs out with the encoder answering is a MISSED move, not a
  fault: the command fails (CMD_EXECUTION_ERROR), the counter is put on the encoder's reading, and
  the axis stays usable - the host decides. Faults stay for feedback that cannot be trusted.

  Bounded by construction: at most max_trims trims per approach, max_reapproaches back-offs per
  move and a total correction budget; an encoder that does not follow the drive, or is further
  from the target than the watchdog allows, is a fault (the same PID_FAULT_* contract as the loop).

  Frames: `target` and every `goal` are in the host's frame, which IS the encoder's frame (ENC_POS
  is in usteps and was aligned with XACTUAL by homing / CONFIGURE / the post-homing realign).
  `x_*` values are in the counter's frame (XACTUAL / XTARGET). While a move-and-settle is busy the two
  differ by settle_offset(); at rest between move-and-settle sequences they agree.
*/

#define SETTLE_X16 16            /* encoder means are carried in 1/16 ustep */

#define SETTLE_IDLE     0
#define SETTLE_MOVE_A   1        /* first part of a split leg is running; the rest follows at t_split */
#define SETTLE_MOVE     2        /* the ramp is running to x_cmd */
#define SETTLE_MEASURE  3        /* ramp idle: averaging ENC_POS over the window */

#define SETTLE_LEG_APPROACH 0    /* toward the target, in the approach direction */
#define SETTLE_LEG_TRIM     1    /* a continuing-direction correction after a short landing */
#define SETTLE_LEG_BACKOFF  2    /* away from the target, to come at it again */

typedef struct {
    int32_t  tol_usteps;            /* accept |target - mean| <= tol on the short side (inclusive) */
    int32_t  tol_over_usteps;       /* accept an overshoot up to this; < tol means tol (symmetric) */
    uint32_t wait_us;             /* wait this long after the ramp stops before the window opens */
    uint32_t window_us;             /* averaging window: one period of the stage's ring */
    uint8_t  min_samples;           /* a window never averages fewer encoder reads than this */
    uint8_t  gain_x16;              /* trim = gain x shortfall, in 1/16 (16 = the whole shortfall) */
    uint8_t  max_trims;             /* per approach */
    uint8_t  max_reapproaches;      /* back-off + fresh approach cycles per move */
    uint8_t  approach;              /* MOVE_SETTLE_APPROACH_* */
    int32_t  lost_motion_usteps;    /* lost motion flank to flank: where the learning starts */
    int32_t  carry_usteps;          /* how far a fast leg leaves the stage beyond the drive flank: where the learning starts */
    int32_t  fast_min_usteps;       /* a push at least this long carries in full; shorter ones in proportion */
    uint8_t  learn_x16;             /* share of a landing's prediction error that goes into the model, 1/16; 0 = fixed model */
    int32_t  scale_ppm;             /* stage travel per motor travel, minus one, in ppm (screw lead against the encoder's
                                       scale): where the learning starts. 1100 ppm is 0.11 um per 100 um */
    int32_t  scale_max_ppm;         /* ... and the bound it stays inside */
    int32_t  scale_min_usteps;      /* a continuing push at least this long teaches the scale; shorter ones teach the carry */
    int32_t  scale_ref_usteps;      /* below this length a landing teaches the scale in proportion to its length only */
    int32_t  learn_clip_usteps;     /* the length one landing may move a learned number by (x learn / 16; twice that on a
                                       leg against the last push), and the scale of what counts as scatter: a quarter of a
                                       full step, so that it means the same stage travel at any microstepping */
    int32_t  carry_max_usteps;      /* bounds of what may be learned */
    int32_t  lost_max_usteps;
    int32_t  bias_usteps;           /* the first landing aims at least this far short of the target */
    uint8_t  bias_sigma_x16;        /* ... and at least this many landing-scatter sigmas (1/16), learned; 0 = fixed bias only */
    int32_t  backoff_usteps;        /* how far beyond the target a back-off goes; must exceed the lost motion */
    int32_t  max_dev_usteps;        /* |target - mean| beyond this is a fault; 0 = no watchdog */
    int32_t  min_drive_usteps;      /* the encoder's response is judged once this much drive was commanded */
    uint32_t correct_budget_us;     /* from the first ramp stop of a move to DONE */
    uint32_t split_half_period_us;  /* ZV shaper: half the ring period; 0 = legs are never split */
    uint16_t split_first_x256;      /* share of a split leg that goes first, in 1/256 */
    int32_t  split_max_usteps;      /* only legs up to this length are split */
    int32_t  quiet_pp_usteps;      /* DONE also needs the window's peak-to-peak <= this; 0 = not required */
    uint8_t  max_quiet_windows;    /* ... for at most this many extra windows, then DONE anyway */
    int32_t  finish_usteps;        /* long legs end with a short finishing leg (see the head of this file): a first leg
                                      wanting more stage travel than finish_from is aimed this far short of the target,
                                      and a continuing approach leg from its measured landing does the rest. 0 = off */
    int32_t  finish_from_usteps;   /* ... the length above which that is done; below finish_usteps it means finish_usteps */
} SettleParams;

typedef struct {
    uint8_t  state;                 /* SETTLE_IDLE ... */
    uint8_t  leg;                   /* SETTLE_LEG_* of the leg in flight / just measured */
    int8_t   dir;                   /* approach direction of this move: +1 / -1 */
    bool     leg_from_encoder;      /* the leg was planned from an encoder mean: its response can be judged */
    bool     limited;               /* a ramp target was clamped to the travel limits: accept what is reachable */
    bool     finish_pending;        /* the leg in flight is the long leg of a split move: a finishing approach leg follows its landing */
    int32_t  target;                /* the commanded position, host frame */
    int32_t  goal;                  /* this leg's goal, host frame: the target, or the back-off point */
    int32_t  x_leg;                 /* this leg's final counter target */
    int32_t  x_cmd;                 /* the counter target last written (differs from x_leg during MOVE_A) */
    uint32_t t_split_us;            /* MOVE_A: when the rest of the leg is issued */
    uint32_t t_meas_us;             /* MEASURE: start of the current window */
    int64_t  sum;                   /* window accumulator */
    uint16_t n;
    int32_t  mn, mx;
    uint8_t  quiet_windows;
    uint8_t  trims;
    uint8_t  reapproaches;
    bool     landed_once;           /* the first landing of this move has been recorded */
    bool     stopped_once;          /* the correction budget runs from the first ramp stop */
    uint32_t t_first_stop_us;
    int64_t  drive_usteps;          /* drive commanded in drive_dir since resp_ref was taken */
    int8_t   drive_dir;
    int32_t  resp_ref_x16;          /* encoder mean the response is judged against */
    int32_t  plan_ref_x16;          /* encoder mean the leg being issued was planned from */
    int64_t  trim_expect_x16;       /* stage travel the plans of those trims expected */
    int64_t  trim_drive_x16;        /* motor travel of the trims since the stage last answered one, and the encoder mean */
    int32_t  trim_ref_x16;          /* that run of trims started from (the response watch above, at the scale of a trim) */
    /* carried from move to move */
    int32_t  last_mean_x16;         /* the last window mean, and whether / when it was taken */
    bool     last_mean_valid;
    uint32_t last_mean_us;
    int8_t   last_dir;              /* direction of the last motor motion this policy issued; 0 = unknown */
    /* the lost-motion model, learned (see the head of this file) */
    bool     model_init;            /* false: carry / lost are (re)seeded from the parameters at the next move */
    int32_t  carry_x16[2];          /* by leg direction, [0] going negative, [1] going positive: on a vertical axis gravity
                                       makes them differ (bench 2026-09-20: up legs landed ~0.15 um short and down legs
                                       ~0.15 um long of one shared number, at every length from 1 to 100 um) */
    int32_t  lost_x16[2];           /* by the direction of the leg that crosses the play, same order: what a reversal loses
                                       includes wind-up, and gravity does not wind a vertical screw the same both ways */
    int8_t   side;                  /* direction of the push that last moved the stage; 0 = unknown (the next push anchors it) */
    int32_t  gap_x16;               /* how far ahead of that flank the stage rests */
    bool     teach_hold;            /* something other than this policy moved the stage (focus wheel, open-loop ramp, a
                                       re-based counter): side / gap are reckoned, not measured. The next move is PLANNED
                                       on them and teaches nothing; its measured landing lifts the hold. Bench 2026-09-20:
                                       a slow wheel reversal crosses less play than lost[] (which includes the wind-up of a
                                       fast leg), the move after it lands past, and that lesson made every reversing leg of
                                       the series land short; the same after an ENABLE that adopted the encoder */
    /* the leg in flight, as the plan sees it */
    bool     leg_learn;             /* the plan was a prediction (side known, leg not clamped): the landing may teach */
    bool     leg_against;           /* the leg drives with the other flank than the one that pushed last */
    bool     leg_observed;          /* planned from an encoder mean: the stage travel of the leg can be measured */
    int8_t   leg_dir;
    int32_t  leg_n_x16;             /* motor travel issued, signed */
    int32_t  leg_from_x16;          /* encoder mean the leg was planned from */
    int32_t  leg_free_x16;          /* how far the driving flank was believed to be from the stage */
    int32_t  leg_push_x16;          /* how much of the leg pushes the stage (the rest is free travel) */
    int32_t  leg_expect_x16;        /* stage travel the plan expects of it, unsigned */
    /* report of the last move (status packet, ENCODER_REPORT_MOVE_SETTLE) */
    int8_t   rep_first_landing;     /* target - mean after the approach leg, usteps, + = short in the approach direction, clipped */
    uint8_t  rep_trims;
    uint8_t  rep_reapproaches;
    bool     rep_missed;            /* the move ended outside the tolerance with its budget spent */
    int32_t  scale_ppm_x16;         /* learned: stage travel per motor travel, minus one, 1/16 ppm */
    int32_t  rev_mad_x256;          /* learned: mean absolute prediction error of first landings that crossed the play */
    int32_t  land_mad_x256;         /* learned: mean absolute prediction error of first landings, 1/256 ustep (sigma ~ 1.25 x this) */
    bool     rep_limited;
} SettleState;

typedef struct {
    uint32_t now_us;
    bool     ramp_running;          /* MOVE: the ramp generator has not reached x_cmd yet */
    int32_t  xactual;               /* MOVE, once the ramp is idle: where the counter stopped */
    bool     enc_valid;             /* MEASURE: one ENC_POS read this pass */
    int32_t  enc;
    bool     correct_allowed;       /* the encoder may be acted on here (outside the home zone, frames aligned) */
    bool     split_allowed;         /* settle_begin only: and at the split point too, `finish` short of the target on the
                                       side the move comes from - a counter-planned leg may stop there to be measured */
    int32_t  limit_lo, limit_hi;    /* counter-frame travel limits for every ramp target */
} SettleInputs;

typedef struct {
    bool     move;                  /* write XTARGET = move_to */
    int32_t  move_to;
    bool     done;                  /* move-and-settle finished: re-base the counter to rebase_to if it differs */
    bool     stopped_short;         /* the chip stopped the ramp before x_cmd (switch / virtual limit): re-base by
                                       -offset, fail the move */
    int32_t  rebase_to;
    bool     missed;                /* budget spent, encoder answering, still outside the tolerance: re-base the counter
                                       to rebase_to (where the stage IS) and fail the move - no fault is latched */
    uint8_t  fault;                 /* PID_FAULT_*; 0 = none */
} SettleActions;

static inline void settle_params_default(SettleParams *p)
{
    p->tol_usteps = 2; p->tol_over_usteps = 3;
    /* Bench 2026-09-20 (Squid+ Z): a window opened AT the ramp stop reads the stage's arrival
       transient, not its rest position - the mean sat up to 5 usteps beyond where the stage came to
       rest, and the next plan started from that phantom. The 2026-09-19 traces put numbers on it:
       window 0-9 ms after the stop = settled position +-1.6 usteps (SD), 5-14 ms = +-0.5. */
    p->wait_us = 5000u;
    p->window_us = 9000u; p->min_samples = 4;
    p->gain_x16 = 12; p->max_trims = 6; p->max_reapproaches = 1;
    p->approach = MOVE_SETTLE_APPROACH_MOVE_DIRECTION;
    p->lost_motion_usteps = 0; p->bias_usteps = 0; p->bias_sigma_x16 = 8; p->backoff_usteps = 32;
    p->learn_clip_usteps = 4;
    p->scale_ppm = 0; p->scale_max_ppm = 5000; p->scale_min_usteps = 512; p->scale_ref_usteps = 4096;
    p->carry_usteps = 0; p->fast_min_usteps = 10; p->learn_x16 = 4;
    p->carry_max_usteps = 16; p->lost_max_usteps = 32;
    p->max_dev_usteps = 0; p->min_drive_usteps = 32;
    p->correct_budget_us = 1000000u;
    p->split_half_period_us = 0; p->split_first_x256 = 128; p->split_max_usteps = 0;
    p->quiet_pp_usteps = 0; p->max_quiet_windows = 0;
    p->finish_usteps = 0; p->finish_from_usteps = 0;
}

static inline void settle_state_init(SettleState *s)
{
    SettleState z = {};
    *s = z;
}

static inline bool settle_busy(const SettleState *s) { return s->state != SETTLE_IDLE; }

/* Counter frame minus host frame while a move-and-settle is busy (0 at rest): the leg in flight ends at
   x_leg in the counter's frame and at `goal` in the host's. A relative move commanded meanwhile
   starts from XACTUAL - offset, and an absolute one is re-planned as target + offset. */
static inline int32_t settle_offset(const SettleState *s)
{
    return settle_busy(s) ? s->x_leg - s->goal : 0;
}

static inline int64_t settle_abs64(int64_t v) { return v < 0 ? -v : v; }

/* The counter at rest: true once `xactual` has not changed for `limit_us`. What a wait on the ramp
   generator is bounded with - TARGET_REACHED never comes for a ramp the chip stopped short, nor for one
   whose target something else has rewritten. Fed at whatever spacing the caller samples XACTUAL at. */
typedef struct
{
    bool     armed;
    int32_t  x;
    uint32_t since_us;
} SettleRestWatch;

static inline void settle_rest_reset(SettleRestWatch *w) { w->armed = false; }

static inline bool settle_rest_step(SettleRestWatch *w, int32_t xactual, uint32_t now_us, uint32_t limit_us)
{
    if (!w->armed || xactual != w->x)
    {
        w->armed = true; w->x = xactual; w->since_us = now_us;
        return false;
    }
    return (uint32_t)(now_us - w->since_us) >= limit_us;
}

/* a / b rounded to nearest, halves away from zero; b > 0 */
static inline int64_t settle_div_round(int64_t a, int64_t b)
{
    return a >= 0 ? (a + b / 2) / b : -((-a + b / 2) / b);
}

static inline void settle_model_seed(SettleState *s, const SettleParams *p)
{
    if (s->model_init) return;
    s->model_init = true;
    s->carry_x16[0] = s->carry_x16[1] = p->carry_usteps * SETTLE_X16;
    s->lost_x16[0] = s->lost_x16[1] = p->lost_motion_usteps * SETTLE_X16;
    s->scale_ppm_x16 = p->scale_ppm * 16;
    s->side = 0; s->gap_x16 = 0;
}

static inline int32_t settle_carry_full(const SettleState *s, int8_t dir) { return s->carry_x16[dir > 0 ? 1 : 0]; }

/* carry a push of push_x16 in direction dir leaves behind: in full from fast_min on, in proportion below */
static inline int64_t settle_carry_of(const SettleState *s, const SettleParams *p, int8_t dir, int64_t push_x16)
{
    int64_t full = (int64_t)p->fast_min_usteps * SETTLE_X16;
    if (push_x16 <= 0) return 0;
    if (full <= 0 || push_x16 >= full) return settle_carry_full(s, dir);
    return (int64_t)settle_carry_full(s, dir) * push_x16 / full;
}

/* stage travel per million motor usteps of push */
static inline int64_t settle_scale(const SettleState *s) { return 1000000 + s->scale_ppm_x16 / 16; }

static inline int32_t settle_clamp_gap(const SettleParams *p, int64_t g)
{
    int64_t hi = (int64_t)p->lost_max_usteps * SETTLE_X16;
    return (int32_t)(g < 0 ? 0 : (g > hi ? hi : g));
}

/* Motor usteps (signed) for a leg that must move the STAGE by want_x16 (1/16 usteps, signed), planned
   from the encoder mean from_x16, through the lost-motion model. Records what the plan expects, so
   that the landing can be held against it (settle_model_learn). */
static inline int32_t settle_plan_leg(SettleState *s, const SettleParams *p, int64_t want_x16, int32_t from_x16)
{
    s->leg_learn = false; s->leg_observed = true;
    if (want_x16 == 0) { s->leg_dir = 0; return 0; }
    int8_t dir = want_x16 > 0 ? 1 : -1;
    int64_t want = settle_abs64(want_x16);
    bool against = s->side != 0 && s->side != dir;
    int64_t free = 0;                              /* side unknown: plan as if in contact, the landing anchors it */
    if (s->side == dir) free = s->gap_x16;
    else if (against) free = s->lost_x16[dir > 0 ? 1 : 0] > s->gap_x16 ? s->lost_x16[dir > 0 ? 1 : 0] - s->gap_x16 : 0;
    /* want = push + carry(push): solve for the push */
    int64_t full = (int64_t)p->fast_min_usteps * SETTLE_X16;
    int64_t carry = settle_carry_full(s, dir);
    int64_t push = want - carry;
    if (push < full) push = full > 0 ? want * full / (full + carry) : want;
    /* the screw's lead is not the encoder's scale: the motor travel that pushes the stage by `push` */
    int64_t k = settle_scale(s);
    int64_t mag = settle_div_round(free + push * 1000000 / k, SETTLE_X16);
    if (mag < 1) mag = 1;
    /* what the plan expects of the WHOLE usteps actually issued, as stage travel */
    int64_t push_r = (mag * SETTLE_X16 - free) * k / 1000000;
    if (push_r < 0) push_r = 0;
    s->leg_dir = dir; s->leg_against = against;
    s->leg_n_x16 = (int32_t)(dir * mag * SETTLE_X16);
    s->leg_from_x16 = from_x16;
    s->leg_free_x16 = (int32_t)free;
    s->leg_push_x16 = (int32_t)push_r;
    s->leg_expect_x16 = (int32_t)(push_r > 0 ? push_r + settle_carry_of(s, p, dir, push_r) : 0);
    s->leg_learn = s->side != 0 && !s->teach_hold; /* with the stage's place in the play unknown - or only reckoned - there is no prediction to learn from */
    return (int32_t)(dir * mag);
}

/* The leg has been measured at mean_x16: observe where it left the stage in the play, and move the
   model toward what the stage actually did. One landing moves a number by at most learn_clip usteps
   (twice that on a leg against the last push) x learn / 16, and neither leaves its bounds: a wild
   reading cannot wreck the next plans. */
static inline void settle_model_learn(SettleState *s, const SettleParams *p, int32_t mean_x16)
{
    if (s->leg_dir == 0) return;                   /* no leg was issued since the last measurement */
    int8_t dir = s->leg_dir;
    s->leg_dir = 0;
    int64_t n = settle_abs64(s->leg_n_x16) * settle_scale(s) / 1000000;      /* how far the drive flank went, in the encoder's units */
    if (!s->leg_observed)
    {
        /* a counter-planned ramp: nothing was measured before it. A long one leaves the stage carried on
           the flank that drove it; after a short one its place in the play is unknown */
        if (n >= (int64_t)(p->fast_min_usteps + p->lost_max_usteps) * SETTLE_X16) { s->side = dir; s->gap_x16 = settle_carry_full(s, dir); }
        else { s->side = 0; s->gap_x16 = 0; }
        return;
    }
    int64_t went = ((int64_t)mean_x16 - s->leg_from_x16) * dir;                  /* stage travel along the leg */
    int64_t unit = (int64_t)(p->learn_clip_usteps > 0 ? p->learn_clip_usteps : 1) * SETTLE_X16;
    bool pushed = went > unit / 8;
    int32_t *lost = &s->lost_x16[dir > 0 ? 1 : 0];
    int64_t lost_before = *lost;
    if (s->leg_learn && !s->limited && pushed && !s->landed_once && s->leg == SETTLE_LEG_APPROACH && !s->finish_pending)
    {
        /* how well a first leg lands where the plan said: the scatter the undershoot bias is sized from. A leg
           that crossed the play is a different population (bench 2026-09-20: ~0.25 um SD against ~0.09), and the
           long leg of a split move is no first landing at all - it scatters ~10x wider and is finished, not judged */
        int64_t a = settle_abs64(went - s->leg_expect_x16);
        int64_t cap = s->leg_against ? 2 * unit : unit;
        int32_t *mad = s->leg_against ? &s->rev_mad_x256 : &s->land_mad_x256;
        if (a > cap) a = cap;
        *mad += (int32_t)((a * 16 - *mad) / 8);         /* finer units: an integer average must not stall short of its input */
    }
    if (s->leg_learn && !s->limited && pushed && p->learn_x16 > 0 && s->leg_push_x16 > 0)
    {
        int64_t full = (int64_t)p->fast_min_usteps * SETTLE_X16;
        int64_t err = went - s->leg_expect_x16;                                  /* + = further than the plan expected */
        int64_t clip = s->leg_against ? 2 * unit : unit;
        if (err > clip) err = clip;
        if (err < -clip) err = -clip;
        int64_t step = err * p->learn_x16 / 16;
        if (s->leg_against) *lost -= (int32_t)step;                              /* crossed the play and went further: the play is smaller */
        else if (s->leg_push_x16 >= (int64_t)p->scale_min_usteps * SETTLE_X16)
        {
            /* a long continuing push: what it missed by is the screw's lead against the encoder's scale. The
               unclipped error over the length (not less than scale_ref: a short leg says little), at most
               2000 ppm a landing */
            int64_t ref = s->leg_push_x16;
            if (ref < (int64_t)p->scale_ref_usteps * SETTLE_X16) ref = (int64_t)p->scale_ref_usteps * SETTLE_X16;
            int64_t innov = (went - s->leg_expect_x16) * 16 * 1000000 / ref;     /* 1/16 ppm */
            if (innov > 2000 * 16) innov = 2000 * 16;
            if (innov < -2000 * 16) innov = -2000 * 16;
            s->scale_ppm_x16 += (int32_t)(innov * p->learn_x16 / 16);
            if (s->scale_ppm_x16 > p->scale_max_ppm * 16) s->scale_ppm_x16 = p->scale_max_ppm * 16;
            if (s->scale_ppm_x16 < -p->scale_max_ppm * 16) s->scale_ppm_x16 = -p->scale_max_ppm * 16;
        }
        else if (s->leg == SETTLE_LEG_TRIM && s->leg_free_x16 > unit / 8)
        {
            /* A trim that began with a take-up measures the carry directly: it went further than planned by
               exactly what the believed gap was too large, and the gap a fast leg leaves IS the carry. */
            int32_t *c = &s->carry_x16[dir > 0 ? 1 : 0];
            *c -= (int32_t)step;
            if (*c < 0) *c = 0;
            if (*c > p->carry_max_usteps * SETTLE_X16) *c = p->carry_max_usteps * SETTLE_X16;
        }
        else if (2 * (int64_t)s->leg_push_x16 >= full && s->leg_free_x16 <= unit / 8)
        {   /* a push long enough to say something about the carry - and FROM CONTACT: a leg that began with a
               take-up lands where it does whatever the carry is (take-up and carry cancel), so its error is
               noise or a wrong gap, and taught to the carry it made a self-consistent wrong state (bench
               2026-09-21, settle14_trace_b: carry[up] 13 -> 32 usteps in one soak) */
            int32_t *c = &s->carry_x16[dir > 0 ? 1 : 0];
            *c += (int32_t)step;
            if (*c < 0) *c = 0;
            if (*c > p->carry_max_usteps * SETTLE_X16) *c = p->carry_max_usteps * SETTLE_X16;
        }
        if (*lost < 0) *lost = 0;
        if (*lost > p->lost_max_usteps * SETTLE_X16) *lost = p->lost_max_usteps * SETTLE_X16;
    }
    /* where the leg left the stage: exact motor travel, measured stage travel */
    if (s->side == 0)
    {
        if (pushed) { s->side = dir; s->gap_x16 = settle_clamp_gap(p, settle_carry_of(s, p, dir, went)); }   /* the push anchors it */
    }
    else if (pushed)
    {
        /* A PUSH RE-ANCHORS THE GAP: the flank that has just pushed the stage has it, ahead by the carry of that
           push, whatever the books said. The books (gap + went - n; across the play went - (n - lost)) are an
           integrator with no way back: the play a reversal crosses scatters around lost[], the scatter was booked
           as gap, every continuing leg carried it on unchanged and landed past by it (bench 2026-09-21: a reversal
           4.6 usteps long of its plan, booked gap 20.9, the next plane 16.8 too far, a back-off). */
        (void)lost_before;
        if (s->leg_against) s->side = dir;
        /* ... less what THIS leg fell short of its plan by: a push that ends early has not carried the stage, it
           sits that much closer to the flank (bench: trims after a short landing went 8-13 usteps too far on a
           take-up of the full carry). Only this leg's own shortfall - nothing is carried over from earlier legs -
           and only a shortfall: a leg that went FURTHER than planned met the stage earlier than it thought, which
           says nothing about where it left it. */
        int64_t anchored = settle_carry_of(s, p, dir, went);
        if (s->leg_expect_x16 > 0 && went < s->leg_expect_x16) anchored -= s->leg_expect_x16 - went;   /* side was known: the plan was a prediction */
        s->gap_x16 = settle_clamp_gap(p, anchored);
    }
    else if (!s->leg_against)
        s->gap_x16 = settle_clamp_gap(p, (int64_t)s->gap_x16 - n);                /* the flank closed in, not there yet */
    else
        s->gap_x16 = settle_clamp_gap(p, (int64_t)s->gap_x16 + n);                /* the old flank backed away, the new one has not arrived */
    if (s->limited) { s->side = 0; s->gap_x16 = 0; }
}

/* `delay_us`: the window opens that long from now (samples before it are not taken) */
static inline void settle_start_window(SettleState *s, uint32_t now_us, uint32_t delay_us)
{
    s->state = SETTLE_MEASURE;
    s->t_meas_us = now_us + delay_us;
    s->sum = 0; s->n = 0; s->mn = INT32_MAX; s->mx = INT32_MIN;
}

static inline void settle_finish(SettleState *s, SettleActions *out)
{
    s->rep_trims = s->trims; s->rep_reapproaches = s->reapproaches; s->rep_limited = s->limited;
    if (s->landed_once) s->teach_hold = false;     /* a measured landing of our own: side / gap are observations again */
    s->state = SETTLE_IDLE; s->finish_pending = false;
    out->done = true;
    out->rebase_to = s->target;
}

static inline void settle_miss(SettleState *s, int32_t mean_x16, SettleActions *out)
{
    s->rep_trims = s->trims; s->rep_reapproaches = s->reapproaches; s->rep_limited = s->limited; s->rep_missed = true;
    s->state = SETTLE_IDLE; s->finish_pending = false;
    out->missed = true;
    out->rebase_to = (int32_t)settle_div_round(mean_x16, SETTLE_X16);
}

static inline void settle_fault(SettleState *s, uint8_t cause, SettleActions *out)
{
    s->rep_trims = s->trims; s->rep_reapproaches = s->reapproaches; s->rep_limited = s->limited;
    s->state = SETTLE_IDLE; s->finish_pending = false;
    s->last_mean_valid = false;
    out->fault = cause;
}

/* Issue one leg of `n` motor usteps from the counter position `xactual`. */
static inline void settle_issue_leg(SettleState *s, const SettleParams *p, const SettleInputs *in, int32_t xactual,
                                   int32_t n, SettleActions *out)
{
    int64_t x = (int64_t)xactual + n;
    if (x < in->limit_lo) { x = in->limit_lo; s->limited = true; }
    if (x > in->limit_hi) { x = in->limit_hi; s->limited = true; }
    s->x_leg = (int32_t)x;
    int32_t travel = s->x_leg - xactual;
    if (travel != n) { s->leg_learn = false; s->leg_n_x16 = travel * SETTLE_X16; }   /* clamped to a limit: the landing says nothing about the model */
    if (travel == 0) s->leg_dir = 0;
    if (s->leg_from_encoder && travel != 0)
    {
        /* Response bookkeeping: drive accumulates over legs in ONE direction and is compared with the
           encoder's net travel over the same legs. A leg the other way starts a new account - a
           back-off and the approach after it cancel in net travel but not in drive. */
        int8_t leg_dir = travel > 0 ? 1 : -1;
        if (leg_dir != s->drive_dir) { s->drive_dir = leg_dir; s->drive_usteps = 0; s->resp_ref_x16 = s->plan_ref_x16; }
        s->drive_usteps += travel < 0 ? -(int64_t)travel : travel;
    }
    if (travel == 0)
    {
        /* nothing to move (already there, or pinned on a limit): measure where the stage is -
           unless the encoder may not be acted on here, then the counter's word is final */
        s->x_cmd = s->x_leg;
        if (!s->stopped_once) { s->stopped_once = true; s->t_first_stop_us = in->now_us; }
        if (!in->correct_allowed && !s->leg_from_encoder) { settle_finish(s, out); s->last_mean_valid = false; return; }
        settle_start_window(s, in->now_us, 0);          /* nothing moved: nothing to settle */
        return;
    }
    s->last_dir = travel > 0 ? 1 : -1;
    int32_t mag = travel < 0 ? -travel : travel;
    if (p->split_half_period_us > 0 && s->leg != SETTLE_LEG_BACKOFF && mag >= 4 && mag <= p->split_max_usteps)
    {
        /* ZV shaper: two impulses half a ring period apart cancel the ring each would leave */
        int32_t first = (int32_t)(((int64_t)mag * p->split_first_x256 + 128) / 256);
        if (first < 1) first = 1;
        if (first > mag - 1) first = mag - 1;
        s->x_cmd = xactual + (travel > 0 ? first : -first);
        s->t_split_us = in->now_us + p->split_half_period_us;
        s->state = SETTLE_MOVE_A;
    }
    else
    {
        s->x_cmd = s->x_leg;
        s->state = SETTLE_MOVE;
    }
    out->move = true;
    out->move_to = s->x_cmd;
}

/* The aim of a first landing, `d_x16` of stage travel, less the undershoot bias. Sigma ~ 1.25 x the mean
   absolute error. A continuing leg: never more than half the tolerance - the bias must not by itself push
   landings out of the band on the short side. A leg that has to cross the play scatters several times
   wider, lands outside the band a third of the time anyway, and what it must not do is land PAST it (a
   back-off costs four trims): up to the whole tolerance there. Only on a leg long enough that the bias is
   small against it - a two-ustep focus nudge must not be biased to nothing. The finishing leg of a split
   move gets the same treatment (settle_decide): it is the landing that counts. */
static inline int64_t settle_biased_aim(const SettleState *s, const SettleParams *p, int64_t d_x16)
{
    bool against = s->side != 0 && s->side != s->dir;
    int64_t bias = (int64_t)p->bias_usteps * SETTLE_X16;
    int64_t learned = (int64_t)(against ? s->rev_mad_x256 : s->land_mad_x256) / 16 * 5 / 4 * p->bias_sigma_x16 / 16;
    int64_t cap = (int64_t)p->tol_usteps * SETTLE_X16 / (against ? 1 : 2);
    if (learned > bias) bias = learned;
    if (bias > cap) bias = cap;
    if (bias > 0 && settle_abs64(d_x16) > 4 * bias) d_x16 -= (int64_t)s->dir * bias;
    return d_x16;
}

/* A new commanded move. `xactual` is the counter now; `enc_x16` the encoder estimate the plan
   starts from, used only when `enc_trusted` (at rest, outside the home zone, frames aligned).
   Untrusted - or while another move-and-settle is still busy - the leg is planned on the counter, as an
   open-loop move always was, and the encoder is consulted after it stops if that is allowed then. */
static inline void settle_begin(SettleState *s, const SettleParams *p, int32_t target, int32_t enc_x16, bool enc_trusted,
                               const SettleInputs *in, SettleActions *out)
{
    bool was_busy = settle_busy(s);
    int32_t offset = settle_offset(s);

    s->target = target; s->goal = target;
    s->leg = SETTLE_LEG_APPROACH;
    s->limited = false;
    s->trims = 0; s->reapproaches = 0; s->quiet_windows = 0;
    s->stopped_once = false; s->t_first_stop_us = 0; s->landed_once = false; s->finish_pending = false;
    s->trim_drive_x16 = 0; s->trim_expect_x16 = 0;
    s->drive_usteps = 0; s->drive_dir = 0;
    s->rep_first_landing = 0; s->rep_trims = 0; s->rep_reapproaches = 0; s->rep_limited = false; s->rep_missed = false;
    settle_model_seed(s, p);
    s->leg_dir = 0; s->leg_learn = false;

    if (!enc_trusted || was_busy)
    {
        int32_t x = target + offset;              /* the host's target, in the counter's frame */
        s->leg_from_encoder = false;
        /* a counter-planned ramp: its length decides where it leaves the stage, nothing is learned from it */
        s->leg_observed = false;
        /* Its landing will be judged, and the counter it is planned on has drifted from the stage where the encoder
           is not evidence (head of this file): it stops `finish` short, where the encoder is evidence too, and
           settle_decide plans the finishing leg from the measured landing. Whatever finish_from says - the reason
           is the counter's drift, not the leg's length. Not a command that interrupts a move: its frame is only
           settled when it ends. */
        if (!was_busy && in->correct_allowed && in->split_allowed && p->finish_usteps > 0
            && settle_abs64((int64_t)x - in->xactual) > p->finish_usteps
            && (p->max_dev_usteps <= 0 || p->finish_usteps < p->max_dev_usteps))
        {
            x -= (x > in->xactual ? 1 : -1) * p->finish_usteps;
            s->goal = x;                          /* where THIS leg ends (not busy: the counter's frame is the host's) */
            s->finish_pending = true;
        }
        s->leg_n_x16 = (int32_t)(((int64_t)x - in->xactual) * SETTLE_X16);
        s->leg_dir = x > in->xactual ? 1 : (x < in->xactual ? -1 : 0);
        s->dir = x > in->xactual ? 1 : (x < in->xactual ? -1 : (s->last_dir ? s->last_dir : 1));
        s->last_mean_valid = false;
        settle_issue_leg(s, p, in, in->xactual, x - in->xactual, out);
        return;
    }

    /* Planning from the encoder moves the stage by (target - encoder), not (target - counter): the
       difference between the two IS the frame disagreement, and it is carried out as motion. Bound it
       by the watchdog before anything moves - the same limit a validated ENABLE applies - so a frame
       that slipped (a chip reset mid-travel, lost steps by the hundred) is a fault, not a journey. */
    if (p->max_dev_usteps > 0
        && settle_abs64((int64_t)in->xactual * SETTLE_X16 - enc_x16) > (int64_t)p->max_dev_usteps * SETTLE_X16)
    { settle_fault(s, PID_FAULT_WATCHDOG, out); return; }

    int64_t d_x16 = (int64_t)target * SETTLE_X16 - enc_x16;
    int8_t dir = d_x16 > 0 ? 1 : (d_x16 < 0 ? -1 : (s->last_dir ? s->last_dir : 1));
    int8_t pref = p->approach == MOVE_SETTLE_APPROACH_POSITIVE ? 1 : (p->approach == MOVE_SETTLE_APPROACH_NEGATIVE ? -1 : 0);
    s->leg_from_encoder = true;
    s->plan_ref_x16 = enc_x16;
    if (pref != 0 && dir != pref && d_x16 != 0)
    {
        /* fixed approach direction and the target lies the other way: go beyond it first */
        s->dir = pref;
        s->leg = SETTLE_LEG_BACKOFF;
        s->goal = target - pref * p->backoff_usteps;
        settle_issue_leg(s, p, in, in->xactual, settle_plan_leg(s, p, (int64_t)s->goal * SETTLE_X16 - enc_x16, enc_x16), out);
        return;
    }
    s->dir = pref != 0 ? pref : dir;
    {
        /* Long legs end with a short finishing leg (head of this file): the long one aims `finish` short and
           settle_decide issues the rest from its measured landing. Not where the watchdog would fault a landing
           that far from the target. `from` is never below the finishing leg: the long leg must go the move's way. */
        int64_t from = p->finish_from_usteps > p->finish_usteps ? p->finish_from_usteps : p->finish_usteps;
        if (p->finish_usteps > 0 && settle_abs64(d_x16) > from * SETTLE_X16
            && (p->max_dev_usteps <= 0 || p->finish_usteps < p->max_dev_usteps))
        {
            s->finish_pending = true;
            /* where THIS leg ends, in the host's frame: left at the target, settle_offset() was `finish` off for as
               long as the long leg ran (the position reported, a ramp stopped short, a command or a drop meanwhile) */
            s->goal = target - s->dir * p->finish_usteps;
            settle_issue_leg(s, p, in, in->xactual,
                             settle_plan_leg(s, p, d_x16 - (int64_t)s->dir * p->finish_usteps * SETTLE_X16, enc_x16), out);
            return;
        }
    }
    /* the optional undershoot bias comes off the first landing's aim, but only on a leg long enough that
       it is small against it - a two-ustep focus nudge must not be biased to nothing */
    d_x16 = settle_biased_aim(s, p, d_x16);
    settle_issue_leg(s, p, in, in->xactual, settle_plan_leg(s, p, d_x16, enc_x16), out);
}

/* Drop the move-and-settle in flight (homing, RESET, a fault raised elsewhere). The caller owns the
   counter frame: if the ramp is idle it re-bases by -settle_offset() BEFORE calling this. */
static inline void settle_abort(SettleState *s)
{
    s->state = SETTLE_IDLE; s->finish_pending = false;
    s->last_mean_valid = false;
}

/* Motion this policy did not issue (focus wheel, joystick, homing, zeroing, an open-loop command):
   the cached mean is gone. `travel_usteps` is what the counter was asked to move, 0 when unknown: a
   long ramp leaves the stage carried on the flank that drove it, anything else leaves its place in
   the play unknown until the next push anchors it. */
static inline void settle_note_external_motion(SettleState *s, const SettleParams *p, int32_t travel_usteps)
{
    s->last_mean_valid = false;
    s->leg_dir = 0; s->leg_learn = false;
    s->teach_hold = true;
    int64_t travel = travel_usteps < 0 ? -(int64_t)travel_usteps : travel_usteps;
    if (travel_usteps != 0) s->last_dir = travel_usteps > 0 ? 1 : -1;
    if (s->model_init && travel >= (int64_t)p->fast_min_usteps + p->lost_max_usteps)
    { s->side = s->last_dir; s->gap_x16 = settle_carry_full(s, s->last_dir); }
    else { s->side = 0; s->gap_x16 = 0; }
}

/* Motion this policy did not issue (the focus wheel) has come to rest and the stage has been MEASURED:
   since the last known state the counter went motor_usteps (exact) and the stage stage_x16 (encoder),
   ending in direction last_dir. The motor is a position source, so the difference between the two is
   how far the stage moved relative to the flanks - whatever was done in between, back and forth
   included. `reckon` is false when the start was not measured or the travel is too long for the
   scale to be trusted over it: then only a clear push the way the motion ended anchors the state. */
static inline void settle_observe_external(SettleState *s, const SettleParams *p, int32_t motor_usteps, int64_t stage_x16,
                                          int8_t last_dir, bool reckon)
{
    s->leg_dir = 0; s->leg_learn = false;
    s->teach_hold = true;
    if (last_dir != 0) s->last_dir = last_dir;
    settle_model_seed(s, p);
    int64_t flank = (int64_t)motor_usteps * SETTLE_X16 * settle_scale(s) / 1000000;
    int64_t slip = stage_x16 - flank;                       /* + = the stage gained on the screw */
    int64_t lost_max = (int64_t)p->lost_max_usteps * SETTLE_X16;
    if (reckon && s->side != 0 && settle_abs64(slip) <= lost_max)
    {
        int64_t g = (int64_t)s->gap_x16 + slip * s->side;  /* ahead of the flank that pushed last */
        int64_t room = s->lost_x16[s->side > 0 ? 0 : 1];    /* the play as crossed going AGAINST side */
        if (g >= room) { s->side = (int8_t)-s->side; s->gap_x16 = 0; }          /* the other flank has it now */
        else s->gap_x16 = settle_clamp_gap(p, g);
        return;
    }
    /* no reckoning: hand-wheel motion is slow, so a stage that followed the way it ended is in contact */
    int64_t unit = (int64_t)(p->learn_clip_usteps > 0 ? p->learn_clip_usteps : 1) * SETTLE_X16;
    if (last_dir != 0 && stage_x16 * last_dir > lost_max + unit) { s->side = last_dir; s->gap_x16 = 0; }
    else { s->side = 0; s->gap_x16 = 0; }
}

/* The window is complete: decide. `mean_x16` and `pp` (peak-to-peak, usteps) describe it. */
static inline void settle_decide(SettleState *s, const SettleParams *p, const SettleInputs *in, int32_t mean_x16, int32_t pp,
                                SettleActions *out)
{
    if (!in->correct_allowed)
    {
        /* home zone, or frames not aligned yet: the encoder is not evidence here (a stage resting on
           its stop reads a "deviation" that is a mechanical gap), so neither corrected nor judged */
        settle_finish(s, out);
        s->last_mean_valid = false;
        return;
    }
    s->last_mean_x16 = mean_x16; s->last_mean_valid = true; s->last_mean_us = in->now_us;
    /* Did the stage answer the trims? Judged over the run of them, before the model forgets the leg: one
       trim can be half an encoder count, so a single standing reading says nothing - but once the run has
       driven two smallest trims' worth and the stage has not gone a quarter of that, the flank has not
       reached it. */
    bool trims_unanswered = false;
    if (s->leg == SETTLE_LEG_TRIM && s->leg_dir != 0)
    {
        if (s->trim_drive_x16 == 0) { s->trim_ref_x16 = s->leg_from_x16; s->trim_expect_x16 = 0; }
        s->trim_drive_x16 += settle_abs64(s->leg_n_x16);
        /* judged against the stage travel the PLAN expected, not the motor travel: a trim after a fast leg
           begins with the take-up of the carry, and that part is not supposed to move anything (bench
           2026-09-20: counted as drive, it made ordinary trims look unanswered, the next one was grown for
           nothing and the planes after it landed short - up stack 87 % first-leg against 93 %) */
        s->trim_expect_x16 += s->leg_expect_x16;
        int64_t moved = ((int64_t)mean_x16 - s->trim_ref_x16) * s->leg_dir;
        int64_t visible = (int64_t)p->tol_usteps * SETTLE_X16;            /* two smallest trims (half a tolerance each) */
        if (visible < 2 * SETTLE_X16) visible = 2 * SETTLE_X16;
        if (moved * 4 > s->trim_expect_x16) { s->trim_drive_x16 = 0; s->trim_expect_x16 = 0; }   /* answered: the account starts over */
        else trims_unanswered = s->trim_expect_x16 >= visible;
    }
    else { s->trim_drive_x16 = 0; s->trim_expect_x16 = 0; }
    settle_model_learn(s, p, mean_x16);

    /* Response first, it is the more specific verdict: the drive was commanded and the encoder did
       not follow (frozen feedback, decoupled stage). Judged only on legs planned from an encoder
       mean, and only once enough drive has accumulated in one direction that lost motion and
       stiction cannot explain a standing encoder (min_drive: a couple of full steps). */
    if (s->leg_from_encoder && s->drive_usteps >= p->min_drive_usteps)
    {
        int64_t moved = settle_abs64((int64_t)mean_x16 - s->resp_ref_x16);
        if (moved * 8 < s->drive_usteps * SETTLE_X16) { settle_fault(s, PID_FAULT_NO_RESPONSE, out); return; }
        s->drive_usteps = 0; s->resp_ref_x16 = mean_x16;
    }
    s->plan_ref_x16 = mean_x16;                       /* every later leg is planned - and judged - from here */
    if (!s->leg_from_encoder) { s->drive_usteps = 0; s->drive_dir = 0; }
    int64_t e_x16 = (int64_t)s->target * SETTLE_X16 - mean_x16;      /* + = the stage is below the target */
    if (p->max_dev_usteps > 0 && settle_abs64(e_x16) > (int64_t)p->max_dev_usteps * SETTLE_X16)
    { settle_fault(s, PID_FAULT_WATCHDOG, out); return; }
    if (in->now_us - s->t_first_stop_us > p->correct_budget_us)      /* wrap-safe */
    { settle_miss(s, mean_x16, out); return; }

    if (s->leg == SETTLE_LEG_APPROACH && s->finish_pending)
    {
        /* The long leg of a split move has landed: neither judged (it was never aimed at the target) nor reported
           (landed_once stays false - the finishing leg's landing is this move's first landing). An ordinary
           continuing leg for the rest, planned from where the stage was measured: the long leg's scatter is
           exactly what it takes out. */
        s->finish_pending = false;
        s->goal = s->target;                          /* the long leg's was the split point */
        s->leg_from_encoder = true;
        int64_t rest_x16 = settle_biased_aim(s, p, (int64_t)s->target * SETTLE_X16 - mean_x16);   /* biased like any first landing */
        settle_issue_leg(s, p, in, s->x_leg, settle_plan_leg(s, p, rest_x16, mean_x16), out);
        return;
    }

    if (s->leg == SETTLE_LEG_BACKOFF)
    {
        /* Backed off: come at the target again, which reverses the motor by construction. If the
           stage is not beyond the target yet (the back-off was eaten by lost motion), back off more. */
        bool beyond = s->dir > 0 ? e_x16 > 0 : e_x16 < 0;
        s->leg_from_encoder = true;
        if (!beyond)
        {
            if (s->reapproaches >= p->max_reapproaches) { settle_miss(s, mean_x16, out); return; }
            s->reapproaches++;
            s->goal -= s->dir * p->backoff_usteps;
            settle_issue_leg(s, p, in, s->x_leg, settle_plan_leg(s, p, (int64_t)s->goal * SETTLE_X16 - mean_x16, mean_x16), out);
            return;
        }
        s->leg = SETTLE_LEG_APPROACH;
        s->goal = s->target;
        s->trims = 0;
        /* This leg crosses the play, so it scatters like a reversal - and landing past the target a second
           time is a missed move (bench 2026-09-20). Aim two sigmas of that scatter short (not less than the
           tolerance, not more than four) and let a trim, which does not cross anything, finish it. */
        int64_t margin = (int64_t)s->rev_mad_x256 / 16 * 5 / 2;
        int64_t tol_x16 = (int64_t)p->tol_usteps * SETTLE_X16;
        if (margin < tol_x16) margin = tol_x16;
        if (margin > 4 * tol_x16) margin = 4 * tol_x16;
        if (settle_abs64(e_x16) > 2 * margin) e_x16 -= (int64_t)s->dir * margin;
        settle_issue_leg(s, p, in, s->x_leg, settle_plan_leg(s, p, e_x16, mean_x16), out);
        return;
    }

    int64_t short_x16 = s->dir > 0 ? e_x16 : -e_x16;                /* + = short of the target in the approach direction */
    if (!s->landed_once)
    {
        s->landed_once = true;
        int64_t r = settle_div_round(short_x16, SETTLE_X16);
        s->rep_first_landing = (int8_t)(r > 127 ? 127 : (r < -127 ? -127 : r));
    }
    int64_t tol_over = p->tol_over_usteps > p->tol_usteps ? p->tol_over_usteps : p->tol_usteps;
    bool inside = short_x16 <= (int64_t)p->tol_usteps * SETTLE_X16 && short_x16 >= -tol_over * SETTLE_X16;

    if (inside || s->limited)
    {
        /* Optionally hold the acknowledgement until the ring has died down: bounded, never a fault */
        if (inside && p->quiet_pp_usteps > 0 && pp > p->quiet_pp_usteps && s->quiet_windows < p->max_quiet_windows)
        {
            s->quiet_windows++;
            settle_start_window(s, in->now_us, 0);
            return;
        }
        settle_finish(s, out);
        return;
    }
    s->leg_from_encoder = true;
    if (short_x16 > 0)
    {
        /* Short: keep going the way the stage was travelling, by gain x the shortfall (at least one
           ustep of stage travel). The model adds what the motor has to take up first: after a fast leg
           the stage sits `carry` ahead of the flank, and a trim that does not close that gap moves
           nothing (bench 2026-09-20: the first of two trims was wasted every time). */
        if (s->trims >= p->max_trims) { settle_miss(s, mean_x16, out); return; }
        s->trims++;
        s->leg = SETTLE_LEG_TRIM;
        /* aim at the near side of the band (half a tolerance short of the target): a trim that lets go
           with a jump then ends inside the band, not past it */
        int64_t want = (short_x16 - (int64_t)p->tol_usteps * SETTLE_X16 / 2) * p->gain_x16 / 16;
        int64_t want_min = (int64_t)p->tol_usteps * SETTLE_X16 / 2;     /* a trim the encoder can see: half a tolerance, */
        if (want_min < SETTLE_X16) want_min = SETTLE_X16;               /* never less than one ustep */
        if (want < want_min) want = want_min;
        if (trims_unanswered)
        {
            /* The last trim never reached the stage: the flank is further from it than the model believed
               (the gap is only reckoned after a wheel episode or an open-loop ramp; a fast leg can leave
               the stage further ahead than the learned carry). The same few usteps again cross half a
               micron of play in thirty trims and the budget is six (bench 2026-09-20, settle9_inject_fix:
               9 usteps short, six trims of 4, MISSED). The next trim is as long as the whole unanswered
               run before it (4, 4, 8, 16 ...) - but never more than the shortfall plus half the accepted
               overshoot, so that wherever in the trim the flank meets the stage, the landing is inside
               the band. */
            int64_t grown = s->trim_drive_x16;
            int64_t cap = short_x16 + tol_over * SETTLE_X16 / 2;
            if (grown > cap) grown = cap;
            if (want < grown) want = grown;
        }
        settle_issue_leg(s, p, in, s->x_leg, settle_plan_leg(s, p, s->dir > 0 ? want : -want, mean_x16), out);
        return;
    }
    /* Past the target. A small reverse nudge would disappear in the lost motion and wind the screw
       up for the next move; back off beyond it and approach afresh. */
    if (s->reapproaches >= p->max_reapproaches) { settle_miss(s, mean_x16, out); return; }
    s->reapproaches++;
    s->leg = SETTLE_LEG_BACKOFF;
    s->goal = s->target - s->dir * p->backoff_usteps;
    settle_issue_leg(s, p, in, s->x_leg, settle_plan_leg(s, p, (int64_t)s->goal * SETTLE_X16 - mean_x16, mean_x16), out);
}

/* One pass. The caller reads only what the state needs: nothing in MOVE_A, the ramp status (and
   XACTUAL once it is idle) in MOVE, one ENC_POS in MEASURE. */
static inline void settle_step(SettleState *s, const SettleParams *p, const SettleInputs *in, SettleActions *out)
{
    switch (s->state)
    {
    case SETTLE_MOVE_A:
        if ((int32_t)(in->now_us - s->t_split_us) >= 0)             /* wrap-safe "now >= t_split" */
        {
            s->x_cmd = s->x_leg;
            s->state = SETTLE_MOVE;
            out->move = true;
            out->move_to = s->x_cmd;
        }
        return;
    case SETTLE_MOVE:
        if (in->ramp_running) return;
        if (in->xactual != s->x_cmd)
        {
            /* The chip stopped the ramp short (stop switch, virtual limit): not ours to correct */
            out->stopped_short = true;
            out->rebase_to = in->xactual - settle_offset(s);
            s->state = SETTLE_IDLE; s->finish_pending = false;
            s->last_mean_valid = false;
            return;
        }
        if (!s->stopped_once) { s->stopped_once = true; s->t_first_stop_us = in->now_us; }
        if (!in->correct_allowed && !s->leg_from_encoder)
        {
            /* a counter-planned move that ended where the encoder may not be acted on (home zone,
               frames not aligned yet): complete on the counter, as open loop always has */
            settle_model_learn(s, p, 0);
            settle_finish(s, out);
            s->last_mean_valid = false;
            return;
        }
        settle_start_window(s, in->now_us, p->wait_us);
        return;
    case SETTLE_MEASURE:
        if ((int32_t)(in->now_us - s->t_meas_us) < 0) return;      /* settling: the window is not open yet (wrap-safe) */
        if (in->enc_valid)
        {
            s->sum += in->enc; s->n++;
            if (in->enc < s->mn) s->mn = in->enc;
            if (in->enc > s->mx) s->mx = in->enc;
        }
        if ((uint32_t)(in->now_us - s->t_meas_us) < p->window_us || s->n < p->min_samples)
        {
            /* no samples for eight windows: the encoder cannot be read at all */
            if (s->n == 0 && (uint32_t)(in->now_us - s->t_meas_us) > 8u * p->window_us + 50000u)
                settle_fault(s, PID_FAULT_NO_RESPONSE, out);
            return;
        }
        settle_decide(s, p, in, (int32_t)settle_div_round(s->sum * SETTLE_X16, s->n), s->mx - s->mn, out);
        return;
    default:
        return;
    }
}
#endif
