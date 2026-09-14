#ifndef PID_CLAMP_H
#define PID_CLAMP_H
#include <stdint.h>
#include "pid_policy.h"   /* pid_clamp_pps */

/* The correction clamp's ONE path from a requested velocity to the chip's PID_DV_CLIP and to
   the copy the correction watch budgets against. Host-compilable: the register write is a
   function the caller supplies (production: tmc4361A_set_PID_dv_clip through the chip driver;
   test/test_pid_clamp: a recorder), so the value actually handed to the register is what the
   native test asserts - the handler wrote VMAX's 24.8 format (pps x 256) into PID_DV_CLIP from
   the first closed-loop firmware through 6a8b12cd and no test could see it. Both command
   handlers (CONFIGURE_STAGE_PID, SET_PID_LIMITS) and the RESET path go through here. */

typedef struct {
    uint32_t override_pps;    /* SET_PID_LIMITS value; 0 = none, the axis default applies at CONFIGURE */
    uint32_t effective_pps;   /* what the last CONFIGURE / SET_PID_LIMITS wrote to PID_DV_CLIP, and what the watch
                                 budgets against; 0 = nothing written since the chip was last reset */
} PidClamp;

typedef struct {
    uint32_t microsteps;      /* per full step */
    uint32_t steps_per_rev;   /* full steps per revolution */
    float pitch_mm;           /* mm per revolution (1 on the wheels: their "mm" is a revolution) */
} PidClampGeometry;

/* Writes `pps` to PID_DV_CLIP. `ctx` is whatever the caller passed (production: the axis's chip). */
typedef void (*pid_clamp_write_fn)(void *ctx, uint32_t pps);

/* RESET: back to the firmware default, the host's override included. */
static inline void pid_clamp_reset(PidClamp *c)
{
    c->override_pps = 0u;
    c->effective_pps = 0u;
}

/* INITIALIZE / INITFILTERWHEEL reset the chip (PID_DV_CLIP back to 0) and clear encoder_configured:
   nothing is in effect until the next CONFIGURE writes it again. The host's override survives -
   SET_PID_LIMITS is documented as re-applied by every later CONFIGURE_STAGE_PID. */
static inline void pid_clamp_chip_reset(PidClamp *c)
{
    c->effective_pps = 0u;
}

/* CONFIGURE_STAGE_PID: the host's override if it sent one, else the axis default; written to the
   chip and made effective. Returns the value written. */
static inline uint32_t pid_clamp_configure(PidClamp *c, float default_mm_s, PidClampGeometry g,
                                           pid_clamp_write_fn write, void *ctx)
{
    uint32_t pps = c->override_pps != 0u
        ? c->override_pps
        : pid_clamp_pps(default_mm_s, g.microsteps, g.steps_per_rev, g.pitch_mm);
    write(ctx, pps);
    c->effective_pps = pps;
    return pps;
}

/* SET_PID_LIMITS: record the override. With the encoder configured it goes to the chip now and
   takes effect; before that it waits for the next CONFIGURE, which writes it (the chip's loop
   registers are only ever written by CONFIGURE, after a reset or otherwise). */
static inline void pid_clamp_set_limit(PidClamp *c, float mm_s, PidClampGeometry g, bool encoder_configured,
                                       pid_clamp_write_fn write, void *ctx)
{
    c->override_pps = pid_clamp_pps(mm_s, g.microsteps, g.steps_per_rev, g.pitch_mm);
    if (!encoder_configured) return;
    write(ctx, c->override_pps);
    c->effective_pps = c->override_pps;
}

#endif /* PID_CLAMP_H */
