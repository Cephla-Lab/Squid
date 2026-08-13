#ifndef TMC_DRIVER_MATH_H
#define TMC_DRIVER_MATH_H

#include <stdint.h>

/*
  Pure current and microstep math for the TMC2660 and TMC2240 power stages.

  NO Arduino, NO SPI, NO register writes — this header is compiled into
  env:native so that the numbers deciding motor current are unit-tested on the
  host. Keep it that way; the current math is the one part of driver support
  that can damage hardware.

  Design: AI-docs Squid/to-do/2026-08-12-tmc2240-driver-support-design.md
*/

/* ------------------------------------------------------------------------ */
/* TMC2660 — external sense resistor                                        */
/* ------------------------------------------------------------------------ */
/*
  This reproduces master's historical formula BIT-IDENTICALLY for every
  in-range input (design M5).

  It is known to be 4-7% low. tmc2660_init writes DRVCONF = 0x000E00A1, whose
  bit 6 is VSENSE = 0, selecting V_FS = 0.310 V; this formula scales for
  0.2298 = 0.325/sqrt(2), and it divides by 31 rather than using the
  datasheet's (CS+1)/32. Correcting it raises motor current on every fielded
  machine, so it is deliberately a SEPARATE PR with its own bench thermal
  check. Do not "fix" it here.

  Out-of-range handling is the one addition, and it is NOT a clamp. cmd 21
  accepts a u16 milliamp value, so the host can request more current than this
  formula can express. Master's uint8_t result then wrapped mod 32 inside
  SGCSCONF's 5-bit CS field, landing arbitrarily HIGHER or LOWER than asked for
  — 1100 mA on X (R = 0.22) produced a raw 32, i.e. CS = 0, minimum current.
  Returning a sentinel makes that failure explicit and matches tmc2240_irun.
  In-range results are untouched, which is what the bit-identity constraint
  actually requires.
*/
#define TMC_CURRENT_OUT_OF_RANGE 0xFF

static inline uint8_t tmc2660_current_scale(float current_rms_ma, float r_sense_ohm)
{
    float cscale = (current_rms_ma / 1000.0f) * r_sense_ohm / 0.2298f;
    if (cscale < 0.0f) cscale = 0.0f;
    if (cscale > 1.0f) return TMC_CURRENT_OUT_OF_RANGE;
    return (uint8_t)(cscale * 31.0f);
}

/* ------------------------------------------------------------------------ */
/* TMC2240 — integrated current sense, no external resistor                 */
/* ------------------------------------------------------------------------ */
/*
  I_RMS = (GLOBALSCALER/256) * ((IRUN+1)/32) * I_FS_peak / sqrt(2)
    =>   IRUN = (I_RMS / I_FS_rms) * 32 - 1          with GLOBALSCALER = 256

  CURRENT_RANGE selects the SINE PEAK full-scale current, not RMS:
    - ADI spec: "IRMS = 2.1 A RMS (3 A sine wave peak)"; 3/sqrt(2) = 2.121
    - Klipper klippy/extras/tmc2240.py: _get_ifs_rms() divides by sqrt(2)
    - terjeio/Trinamic-library: identical KIFS constants

  octoaxes MotorControl.cpp:173-185 OMITS the sqrt(2). Do not simplify back to
  their form: against an RMS host it runs every axis ~29% low, which is the
  same defect they already found and fixed on their own TMC2660 path
  (2026-05-11) and never propagated.
*/
#define TMC2240_R_REF_OHM (12000.0f)  /* confirmed on the Squid 2240 board, 2026-08-12 */
#define TMC2240_SQRT2     (1.41421356f)
/* Alias kept for readability at 2240 call sites; same value as the shared sentinel. */
#define TMC2240_IRUN_OUT_OF_RANGE TMC_CURRENT_OUT_OF_RANGE

static inline float tmc2240_ifs_peak_a(uint8_t current_range)
{
    /* KIFS values are in ohm-amps; divide by R_ref to get amps. */
    static const float KIFS[4] = {11750.0f, 24000.0f, 36000.0f, 36000.0f};
    return KIFS[current_range & 0x03] / TMC2240_R_REF_OHM;
}

static inline float tmc2240_ifs_rms_a(uint8_t current_range)
{
    return tmc2240_ifs_peak_a(current_range) / TMC2240_SQRT2;
}

/*
  Returns 0..31, or TMC2240_IRUN_OUT_OF_RANGE when the requested current
  exceeds what this CURRENT_RANGE can deliver. Callers MUST treat out-of-range
  as a configuration error rather than clamping: a silently under-currented
  filter wheel stalls under load instead of reporting a fault.

  IRUN is floored, never rounded — matching master's TMC2660 truncation, and
  because overshooting the requested current is the unsafe direction.
*/
static inline uint8_t tmc2240_irun(float current_rms_ma, uint8_t current_range)
{
    float ifs_rms  = tmc2240_ifs_rms_a(current_range);
    float target_a = current_rms_ma / 1000.0f;

    if (target_a < 0.0f) target_a = 0.0f;
    if (target_a > ifs_rms) return TMC_CURRENT_OUT_OF_RANGE;

    float cs = (target_a / ifs_rms) * 32.0f - 1.0f;
    if (cs < 0.0f) cs = 0.0f;
    if (cs > 31.0f) cs = 31.0f;
    return (uint8_t)cs;
}

/* ------------------------------------------------------------------------ */
/* Microstep resolution code                                                */
/* ------------------------------------------------------------------------ */
/*
  256 -> 0, 128 -> 1, ... 1 -> 8. The TMC4361A STEP_CONF low nibble
  (MSTEP_PER_FS) and the TMC2240 CHOPCONF.MRES field use the SAME encoding,
  which is why tmc2240_set_microsteps can mirror this value directly.
*/
#define TMC_MRES_INVALID 0xFF

static inline uint8_t tmc_microsteps_to_mres(uint16_t microsteps)
{
    if (microsteps == 0 || microsteps > 256) return TMC_MRES_INVALID;
    if (microsteps & (microsteps - 1))       return TMC_MRES_INVALID; /* not a power of 2 */

    uint8_t  bits = 0;
    uint16_t m    = microsteps;
    while (m > 0) { bits++; m >>= 1; }
    return (uint8_t)(9 - bits);
}

#endif /* TMC_DRIVER_MATH_H */
