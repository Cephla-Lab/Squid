#include <Arduino.h>
#include "tmc2240.h"
#include "tmc2240_regs.h"
#include "driver_math.h"
#include "stepper_driver.h"
#include "../TMC4361A_Utils.h"

/*
  Chopper defaults — the octoaxes production set, which is the bench starting
  point.

  These are the only values proven on this silicon in this topology. Master's
  TMC2660 numbers are NOT transferable: that part uses an external sense
  resistor and a different hysteresis decode, so its chopper was tuned against
  different physics. Where the three sources disagree:

    field          this file / octoaxes axis.cpp:104-114   master 2660 (0x000900C3)
    TOFF           3                                       3
    HSTRT          0                                       4
    HEND           0 (raw 3)                               raw 1  (i.e. -2)
    TBL            2                                       2
    INTERPOLATION  false (see below)                       n/a
    IHOLDDELAY     7                                       n/a
    TPOWERDOWN     10                                      n/a

  Note octoaxes' own comment at axis.cpp:107 claims hstrt = 0 "aligns with old
  Squid CHOPCONF = 0x000900C3" — decode that word (tmc2660_regs.h:65-76) and its
  HSTRT is 4, so their comment is wrong about their own intent. The VALUE is
  still theirs and still what ran on the hardware; only the stated reason is
  wrong.

  INTERPOLATION is the one place this file still differs from octoaxes, which
  passes true (axis.cpp:105). CHOPCONF.INTPOL interpolates step/dir input up to
  256 microsteps and is inert under direct_mode, where the TMC4361A sends coil
  currents rather than steps — so the two settings are equivalent here. Left
  false because false is what an inert bit should read as.

  Design M5 gates any chopper change on a bench thermal check. Confirm on the
  bench before this ships.
*/
#define TMC2240_DEFAULT_TOFF        3
#define TMC2240_DEFAULT_HSTRT       0
#define TMC2240_DEFAULT_HEND        0
#define TMC2240_DEFAULT_TBL         2
#define TMC2240_DEFAULT_IHOLDDELAY  7
#define TMC2240_DEFAULT_TPOWERDOWN  10
#define TMC2240_COVER_SETTLE_US     50

/*
  40-bit cover datagram: COVER_HIGH_WR carries the address byte, COVER_LOW_WR
  the 32 data bits. Writing COVER_LOW_WR triggers the transfer.

  Writes are reliable — the TMC4361A serialises cover writes against the
  automatic SPI output. READS are not: the datasheet (§10.3.6) wants a
  COVER_DONE event and we use a fixed settle delay, so read-back values
  fluctuate. Only the probe reads, and it votes across repeats.

  Watch the two address spaces below. TMC4361A_COVER_LOW_WR is 0x6C and
  TMC4361A_COVER_HIGH_WR is 0x6D, which are numerically the same bytes as the
  TMC2240's CHOPCONF (0x6C) and COOLCONF (0x6D). tmc4361A_writeInt takes
  TMC4361A addresses; the `address` argument here is a TMC2240 address. They are
  never interchangeable.
*/
void tmc2240_cover_write(TMC4361ATypeDef *tmc4361A, uint8_t address, uint32_t value)
{
    tmc4361A_writeInt(tmc4361A, TMC4361A_COVER_HIGH_WR,
                      (int32_t)(uint32_t)(address | TMC2240_WRITE_BIT));
    tmc4361A_writeInt(tmc4361A, TMC4361A_COVER_LOW_WR, (int32_t)value);
    delayMicroseconds(TMC2240_COVER_SETTLE_US);

    /* tmc2240_shadow is indexed by raw register address and carries no bounds
       check of its own. SG4_RESULT (0x75) and SG4_IND (0x76) are real TMC2240
       addresses past the end of the array, and writing them would corrupt the
       struct members that follow. Both are read-only on the chip, so nothing
       should ever land here — the guard is what makes that true rather than
       assumed. */
    if (address < TMC2240_SHADOW_COUNT)
        tmc4361A->tmc2240_shadow[address] = value;
}

uint32_t tmc2240_cover_read(TMC4361ATypeDef *tmc4361A, uint8_t address)
{
    /* Two transfers: the first issues the read request, the second clocks the
       reply out of the driver. Mirrors readRegisterSPI() in the octoaxes
       TMC2240 HAL. Deliberately does NOT touch the shadow: the shadow's whole
       value is that it holds what was last WRITTEN. */
    for (int pass = 0; pass < 2; pass++) {
        tmc4361A_writeInt(tmc4361A, TMC4361A_COVER_HIGH_WR,
                          (int32_t)(uint32_t)(address & TMC2240_ADDRESS_MASK));
        tmc4361A_writeInt(tmc4361A, TMC4361A_COVER_LOW_WR, 0);
        delayMicroseconds(TMC2240_COVER_SETTLE_US);
    }
    return (uint32_t)tmc4361A_readInt(tmc4361A, TMC4361A_COVER_DRV_LOW_RD);
}

/*
  TMC4361A-side current scaling.

  On a TMC2240 axis the TMC4361A drives the coil currents over SPI
  (SPI_OUTPUT_FORMAT 0x0D, GCONF.direct_mode), and SCALE_VALUES is what sets the
  amplitude it transmits — exactly as it does for the TMC2660's 0x0A format.
  Leave SCALE_VALUES at its reset state and the TMC4361A sends ZERO current and
  the motor never moves, which octoaxes calls out explicitly at
  MotorControl.cpp:417-431.

  This repeats the second half of tmc4361A_cScaleInit() on purpose. The FIRST
  half of that function writes a TMC2660 SGCSCONF cover datagram, so a TMC2240
  axis cannot call it: under format 0x0D that 20-bit word would go out as a
  40-bit frame addressed at whatever its top byte happens to be. The TMC2660
  path is frozen for this branch, so the shared half is duplicated here rather
  than factored out of TMC4361A_Utils.cpp; see the Task 5 report.
*/
static void tmc2240_write_scale_values(TMC4361ATypeDef *tmc4361A)
{
    /* Each operand is cast to uint32_t BEFORE shifting. cscaleParam is a signed
       int32_t, and 255 << 24 overflows a signed int — undefined behaviour, and
       reachable at hold_ratio = 1.0. tmc4361A_cScaleInit has the same construct
       but is frozen for this branch; this is new code and does not inherit it. */
    tmc4361A_writeInt(tmc4361A, TMC4361A_SCALE_VALUES,
                      (int32_t)(((uint32_t)tmc4361A->cscaleParam[HOLDSCALE_IDX] << TMC4361A_HOLD_SCALE_VAL_SHIFT)
                              | ((uint32_t)tmc4361A->cscaleParam[DRV2SCALE_IDX] << TMC4361A_DRV2_SCALE_VAL_SHIFT)
                              | ((uint32_t)tmc4361A->cscaleParam[DRV1SCALE_IDX] << TMC4361A_DRV1_SCALE_VAL_SHIFT)
                              | ((uint32_t)tmc4361A->cscaleParam[BSTSCALE_IDX]  << TMC4361A_BOOST_SCALE_VAL_SHIFT)));
    tmc4361A_setBits(tmc4361A, TMC4361A_CURRENT_CONF, TMC4361A_DRIVE_CURRENT_SCALE_EN_MASK);
    tmc4361A_setBits(tmc4361A, TMC4361A_CURRENT_CONF, TMC4361A_HOLD_CURRENT_SCALE_EN_MASK);
}

void tmc2240_driver_init(TMC4361ATypeDef *tmc4361A, uint32_t clk_Hz_TMC4361)
{
    tmc4361A_writeInt(tmc4361A, TMC4361A_RESET_REG, 0x52535400);
    tmc4361A_writeInt(tmc4361A, TMC4361A_CLK_FREQ, clk_Hz_TMC4361);

    /* Restore this driver's SPI output format after the probe. */
    tmc4361A_writeInt(tmc4361A, TMC4361A_SPIOUT_CONF, (int32_t)TMC_SPIOUT_CONF_2240);

    tmc2240_cover_write(tmc4361A, TMC2240_REG_DRV_CONF,
                        tmc2240_drv_conf_value(tmc4361A->current_range));

    /* GLOBALSCALER register value 0 means 256/256, i.e. full scale. Every IRUN
       figure in the design assumes this. */
    tmc2240_cover_write(tmc4361A, TMC2240_REG_GLOBAL_SCALER, 0);

    /* Current is applied by the caller via tmc_driver_set_current(); seed a
       safe zero so the driver is never energised at an unknown current between
       init and configuration. */
    tmc2240_cover_write(tmc4361A, TMC2240_REG_IHOLD_IRUN,
                        tmc2240_ihold_irun_value(0, 0, TMC2240_DEFAULT_IHOLDDELAY));

    tmc2240_cover_write(tmc4361A, TMC2240_REG_TPOWERDOWN, TMC2240_DEFAULT_TPOWERDOWN);

    /* SpreadCycle, to match the TMC2660 axes. direct_mode is mandatory. */
    tmc2240_cover_write(tmc4361A, TMC2240_REG_GCONF, tmc2240_gconf_value(false));

    /* tmc_microsteps_to_mres returns TMC_MRES_INVALID (0xFF) for a microstep
       count that is zero, above 256, or not a power of two, and 0xFF & 0x0F is
       15 — a RESERVED MRES code. Fall back to 0 (256 microsteps), which is what
       octoaxes' own mstepVal computation produces for the same bad input. */
    uint8_t mres = tmc_microsteps_to_mres(tmc4361A->microsteps);
    if (mres == TMC_MRES_INVALID) mres = 0;   /* 256 microsteps */

    uint32_t chopconf = tmc2240_chopconf_value(TMC2240_DEFAULT_TOFF, TMC2240_DEFAULT_HSTRT,
                                               TMC2240_DEFAULT_HEND, TMC2240_DEFAULT_TBL,
                                               mres, false);
    tmc2240_cover_write(tmc4361A, TMC2240_REG_CHOPCONF, chopconf);

    /* Cache TOFF for enable(). Read it back out of the CHOPCONF word that was
       just written rather than re-stating the constant: tmc4361A_init() seeds
       driver_toff = 3 from driver-agnostic code, which is a TMC2660-shaped
       value that this axis must not inherit even when the two happen to agree.
       Extracting it through TMC2240_TOFF_MASK also bounds it to 0..15, which is
       what lets tmc2240_chopconf_with_toff's masking be safe for the full
       domain of driver_toff. */
    tmc4361A->driver_toff = (uint8_t)((chopconf & TMC2240_TOFF_MASK) >> TMC2240_TOFF_SHIFT);

    /* Clear the reset flag. */
    tmc2240_cover_write(tmc4361A, TMC2240_REG_GSTAT, 0x07);

    /* Reverse the TMC4361A's internal microstep table.

       Under GCONF.direct_mode the TMC2240's SHAFT bit does nothing, so rotation
       direction is set entirely by the phase sequence the TMC4361A transmits —
       and SPI_OUTPUT_FORMAT 0x0D maps those phases opposite to the TMC2660's
       0x0A. Without this bit every TMC2240 axis runs backwards, which on the
       first power-on means homing drives away from the limit switch and into
       the hard stop. octoaxes sets it for exactly this reason and only for
       DRIVER_TMC2240 (MotorControl.cpp:309-315).

       setBits, not a whole-register write: GENERAL_CONF also carries
       USE_ASTART_AND_VSTART, which tmc4361A_sRampInit maintains. This is a
       TMC4361A register read, which is reliable — it is not a TMC2240 cover
       read, so the "never read to modify" rule is not in play. */
    tmc4361A_setBits(tmc4361A, TMC4361A_GENERAL_CONF, TMC4361A_REVERSE_MOTOR_DIR_MASK);

    /* Zero current until set_current() runs, matching the IHOLD_IRUN seed
       above: cscaleParam is all zeros out of tmc4361A_init(). The TMC2660 path
       reaches the same state through tmc4361A_cScaleInit(), in this same slot
       between the cover writes and writeMicrosteps/writeSPR. */
    tmc2240_write_scale_values(tmc4361A);

    tmc4361A_writeMicrosteps(tmc4361A);
    tmc4361A_writeSPR(tmc4361A);
}

void tmc2240_driver_set_current(TMC4361ATypeDef *tmc4361A, float current_rms_ma, float hold_ratio)
{
    uint8_t irun = tmc2240_irun(current_rms_ma, tmc4361A->current_range);
    if (irun == TMC2240_IRUN_OUT_OF_RANGE) {
        /* Requested current exceeds this CURRENT_RANGE. Refuse rather than
           clamp: a silently under-currented axis stalls under load instead of
           reporting a fault. The axis stays at whatever current it had.
           Returning here is also what keeps the 0xFF sentinel away from
           tmc2240_ihold_irun_value, whose 0x1F mask would turn it into IRUN 31,
           i.e. MAXIMUM current. */
        return;
    }

    /* irun is now 0..31, so ihold only needs hold_ratio bounded. Clamp in FLOAT
       space, before the cast: a negative or NaN hold_ratio makes the conversion
       to uint8_t undefined behaviour rather than merely wrong, and on a
       saturating conversion it can land at 0xFF -> IHOLD 31, full hold current.
       The `!(x > 0)` form catches NaN as well as negatives. */
    float ihold_f = (float)irun * hold_ratio;
    if (!(ihold_f > 0.0f)) ihold_f = 0.0f;
    if (ihold_f > 31.0f)   ihold_f = 31.0f;
    uint8_t ihold = (uint8_t)ihold_f;

    tmc2240_cover_write(tmc4361A, TMC2240_REG_IHOLD_IRUN,
                        tmc2240_ihold_irun_value(ihold, irun, TMC2240_DEFAULT_IHOLDDELAY));

    /* The TMC4361A-side scale values are driver-agnostic and still apply.
       HOLD_SCALE_VAL is an 8-bit field at bit 24 of SCALE_VALUES and
       cscaleParam is a signed int32_t, so an out-of-range hold_ratio would
       shift garbage across the top of the word instead of wrapping harmlessly.
       Clamp for the same reason as above. */
    float hold_scale_f = hold_ratio * 255.0f;
    if (!(hold_scale_f > 0.0f)) hold_scale_f = 0.0f;
    if (hold_scale_f > 255.0f)  hold_scale_f = 255.0f;

    tmc4361A->cscaleParam[HOLDSCALE_IDX] = (int32_t)hold_scale_f;
    tmc4361A->cscaleParam[DRV2SCALE_IDX] = 255;
    tmc4361A->cscaleParam[DRV1SCALE_IDX] = 255;
    tmc4361A->cscaleParam[BSTSCALE_IDX]  = 255;
    tmc2240_write_scale_values(tmc4361A);
}

void tmc2240_driver_set_microsteps(TMC4361ATypeDef *tmc4361A, uint16_t microsteps)
{
    /* 0xFF & 0x0F is 15, a reserved MRES code, so the sentinel must not reach
       tmc2240_chopconf_with_mres. Unlike init() there is no safe fallback here:
       the caller asked for a specific resolution and the TMC4361A's STEP_CONF
       is set from the same number, so quietly substituting 256 would desync the
       two. Reject instead. */
    uint8_t mres = tmc_microsteps_to_mres(microsteps);
    if (mres == TMC_MRES_INVALID) return;

    /* CHOPCONF.MRES must track the TMC4361A's STEP_CONF. Source the current
       CHOPCONF from the SHADOW — a cover read here could return garbage and
       land TOFF = 0, silently killing the driver. */
    uint32_t chopconf = tmc4361A->tmc2240_shadow[TMC2240_REG_CHOPCONF];
    tmc2240_cover_write(tmc4361A, TMC2240_REG_CHOPCONF,
                        tmc2240_chopconf_with_mres(chopconf, mres));
}

void tmc2240_driver_enable(TMC4361ATypeDef *tmc4361A, bool enable)
{
    uint8_t toff = enable ? (tmc4361A->driver_toff > 0 ? tmc4361A->driver_toff : TMC2240_DEFAULT_TOFF) : 0;
    uint32_t chopconf = tmc4361A->tmc2240_shadow[TMC2240_REG_CHOPCONF];
    tmc2240_cover_write(tmc4361A, TMC2240_REG_CHOPCONF,
                        tmc2240_chopconf_with_toff(chopconf, toff));
}

int16_t tmc2240_driver_config_stallguard(TMC4361ATypeDef *tmc4361A, int8_t sensitivity,
                                         bool filter_en, uint32_t vstall_lim)
{
    /* Same contract as the TMC2660 path (see tmc2660.cpp): clamp, always write,
       return bool success where 1 = accepted. That is master's convention and
       NOT the NO_ERR/ERR_OUT_OF_RANGE convention the rest of TMC4361A_Utils
       uses. Keeping the two drivers' contracts identical matters because both
       are reached through the same dispatcher. */
    bool success = true;
    if ((sensitivity > 63) || (sensitivity < -64) || (vstall_lim >= (1UL << 24)))
        success = false;
    if (sensitivity > 63)  sensitivity = 63;
    if (sensitivity < -64) sensitivity = -64;
    if (vstall_lim >= (1UL << 24)) vstall_lim = (1UL << 24) - 1;

    /* StallGuard2: threshold in COOLCONF.SGT, filter in COOLCONF.SFILT (bit 24).

       StallGuard2, not StallGuard4 — init() selects SpreadCycle
       (GCONF.en_pwm_mode clear), and StallGuard4 only operates under
       StealthChop. SG4_THRS is inert in this configuration. StallGuard2's flag
       is also the only one the TMC4361A's STOP_ON_STALL below can consume.

       filter_en must reach SFILT specifically. The TMC2660 path routes the same
       argument to SGCSCONF.SFILT, so sending it anywhere else would leave a
       2240 axis running UNFILTERED StallGuard from an identical call — several
       times the per-fullstep variance, so the M6 bench tuning would find an SGT
       that is stable on 2660 axes and trips spuriously mid-scan on 2240 axes.

       ONE read-modify-write: SGT and SFILT share COOLCONF, so two separate
       writes would each clobber the other's field. Sourced from the SHADOW.
       octoaxes uses tmc2240_fieldWrite here, which reads the register first;
       their register table marks COOLCONF readable, so that read goes out over
       the cover path — the same unreliable path whose garbage read-backs broke
       enable() on their W axis. Never read a TMC2240 register to modify it.

       The numeric sensitivity scale differs from the TMC2660's, so the
       equivalent of the 2660's 12 is found empirically on the bench (design M6,
       bench checklist step 6).

       SGT is a signed 7-bit field. The (uint8_t) cast before masking is what
       makes the two's-complement representation land correctly: -64 becomes
       0xC0, masked to 0x40, which is -64 in 7 bits. */
    uint32_t coolconf = tmc4361A->tmc2240_shadow[TMC2240_REG_COOLCONF];
    coolconf = (coolconf & ~(TMC2240_SGT_MASK | TMC2240_SFILT_MASK))
             | ((((uint32_t)(uint8_t)sensitivity) << TMC2240_SGT_SHIFT) & TMC2240_SGT_MASK)
             | (filter_en ? TMC2240_SFILT_MASK : 0u);
    tmc2240_cover_write(tmc4361A, TMC2240_REG_COOLCONF, coolconf);

    /* Identical TMC4361A-side stall reaction to the 2660 path. */
    tmc4361A_writeInt(tmc4361A, TMC4361A_VSTALL_LIMIT_WR, (int32_t)vstall_lim);
    tmc4361A_setBits(tmc4361A, TMC4361A_REFERENCE_CONF, TMC4361A_STOP_ON_STALL_MASK);
    tmc4361A_rstBits(tmc4361A, TMC4361A_REFERENCE_CONF, TMC4361A_DRV_AFTER_STALL_MASK);
    return success;
}
