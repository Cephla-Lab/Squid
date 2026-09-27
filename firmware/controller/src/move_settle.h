#ifndef MOVE_SETTLE_H
#define MOVE_SETTLE_H

#include "globals.h"
#include "move_settle_policy.h"

/*
  Move-and-settle on the chip: the hardware half of move_settle_policy.h (read that first).
  ENABLE_STAGE_PID on an axis whose SET_LOOP_STRATEGY is LOOP_STRATEGY_MOVE_SETTLE arms this instead
  of the TMC4361A's PID. From then on every commanded move of the axis is planned from the encoder,
  run by the ramp generator, measured at rest and - if it has to be - corrected by further finite
  ramps (settle_move_to + check_move_settle). Between moves nothing regulates: no register is read and
  no pulse is issued for the axis until the next command.

  What it keeps from the chip loop's contract (operations.cpp): the home zone (inside it completion
  is on the counter, the encoder is not evidence there), the bounded realignment at the first rest
  outside the zone after a homing, the deviation watchdog as a bound on what may be corrected, the
  latched fault and its recovery (DISABLE / validated ENABLE), and XACTUAL as the host's frame.
  What it does not need: the correction clamp, the stop-switch guard and the bounded-correction
  watch - every correction here is a finite move of the ramp generator, which the chip's own stop
  logic and travel limits gate like any other move.
*/

/* SET_MOVE_SETTLE_* as received, in wire units. Resolved to usteps when a move begins, so a later
   CONFIGURE_STEPPER_DRIVER (microstepping), SET_PID_TOLERANCE or SET_PID_LIMITS is always honoured. */
struct SettleConfig {
    uint8_t  wait_half_ms;          /* settle time before the window, 0.5 ms */
    uint8_t  window_half_ms;          /* averaging window, 0.5 ms */
    uint8_t  gain_x16;
    uint8_t  max_trims;
    uint8_t  max_reapproaches;
    uint8_t  approach;                /* MOVE_SETTLE_APPROACH_* */
    uint16_t lost_motion_c_um;        /* 0.01 um */
    uint8_t  carry_c_um;              /* 0.01 um */
    uint8_t  full_push_c_um;          /* 0.01 um */
    uint8_t  learn_x16;
    uint8_t  bias_sigma_x16;          /* learned undershoot bias, in landing-scatter sigmas, 1/16 */
    int16_t  scale_ppm;               /* stage travel per motor travel minus one, ppm: where the learning starts */
    uint8_t  bias_c_um;               /* 0.01 um */
    uint8_t  backoff_d_um;            /* 0.1 um */
    uint16_t split_half_period_10us;  /* 0 = off */
    uint8_t  split_first_x256;
    uint8_t  split_max_um;
    uint16_t tol_over_c_um;           /* 0 = the target tolerance */
    uint8_t  quiet_pp_c_um;          /* 0 = not required */
    uint8_t  max_quiet_windows;
    uint16_t finish_usteps;           /* finishing leg of a split long move, usteps (the wire's unit here); 0 = off */
    uint16_t finish_from_16usteps;    /* a first leg longer than this (16 usteps) is split; 0 = every leg longer than the finishing leg */
};

extern uint8_t loop_strategy[TOTAL_AXES];      /* LOOP_STRATEGY_* (SET_LOOP_STRATEGY) */
extern SettleConfig settle_config[TOTAL_AXES];
extern SettleState settle_state[TOTAL_AXES];

void settle_config_default(uint8_t axis);
/* the learned lost-motion model starts over from the configured values at the next move */
void settle_model_reseed(uint8_t axis);

/* the axis's strategy is move-and-settle (whether or not a loop is requested) */
bool settle_selected(uint8_t axis);
/* ... and ENABLE_STAGE_PID armed it: commanded moves on the axis are settled */
bool settle_armed(uint8_t axis);
/* a move-and-settle owns the axis right now: the move is not complete, the focus wheel waits, and
   XACTUAL is off the host's frame by settle_axis_offset() */
bool settle_axis_busy(uint8_t axis);
int32_t settle_axis_offset(uint8_t axis);

/* A commanded move to `target` (host frame, already clamped to the travel limits). Same return
   convention as tmc4361A_moveTo(): NO_ERR when a ramp was issued or none was needed. */
int8_t settle_move_to(uint8_t axis, int32_t target);

/* The axis was moved by something else (focus wheel, an open-loop command, zeroing): forget the cached
   reading. `travel_usteps` is what the counter was asked to move (0 = unknown): a long ramp leaves the
   stage on a known flank of the lost motion, anything else leaves its place in the play unknown. */
void settle_external_motion(uint8_t axis, int32_t travel_usteps);
/* A command is about to be planned on `axis`: account NOW for wheel motion still waiting to be measured
   (the counter is re-based onto the encoder, so a relative move starts from where the stage is). */
void settle_close_external(uint8_t axis);
/* Where the stage is, in the host's frame (the encoder), for a move relative to THAT; `fallback` (the
   counter) when it cannot be said - not armed, not at rest, the encoder not evidence here. */
int32_t settle_measured_position(uint8_t axis, int32_t fallback);
/* The target the last move-and-settle move on `axis` resolved (host frame), for the host's retry of it
   (MOVE_Z_RETRY_LAST): true only while that move ended MISSED, the axis is armed and at rest, and the encoder is
   evidence at that target. Otherwise the retry is the plain relative move the host sent with the flag. */
bool settle_last_target(uint8_t axis, int32_t *target);
/* ENABLE_STAGE_PID in move-and-settle: never moves the stage; at rest outside the home zone the counter
   adopts the encoder's reading (tools that enable after open-loop motion; the product enables at start). */
void settle_adopt_position(uint8_t axis);
/* The axis position the status packet shows: the counter in the host's frame, or the encoder while
   focus-wheel motion has not been measured yet (see move_settle.cpp). */
int32_t settle_report_position(uint8_t axis, int32_t counter);

/* Drop whatever is in flight (DISABLE, RESET, INITIALIZE, homing, a fault). With keep_frame the
   counter is put back on the host's frame as soon as the ramp is idle; without it the caller is
   about to redefine the frame anyway (homing, chip reset) or the position is suspect (fault). */
void settle_drop(uint8_t axis, bool keep_frame);

/* main loop, just before check_position() */
void check_move_settle();

/* status packet, ENCODER_REPORT_MOVE_SETTLE: bytes 20 and 21 */
uint8_t settle_report_landing(uint8_t axis);
uint8_t settle_report_bits(uint8_t axis);

#ifdef BENCH_SETTLE_TRACE   // BENCH BUILDS ONLY: BENCH_DUMP_SETTLE_TRACE (the settle trace, move_settle.cpp)
/* 0 = start dumping the ring from its oldest record (refused while a move-and-settle is in flight on Z),
   1 = clear the ring */
void bench_settle_trace_request(uint8_t what);
/* main loop, once per pass: a few lines of a dump in progress */
void bench_settle_trace_service();
#endif

#endif // MOVE_SETTLE_H
