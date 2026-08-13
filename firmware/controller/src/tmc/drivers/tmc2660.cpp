#include "tmc2660.h"
#include "tmc2660_regs.h"
#include "driver_math.h"
#include "stepper_driver.h"
#include "../TMC4361A_Utils.h"

/*
  Extracted from master 856bc0ee TMC4361A_TMC2660_Utils.cpp. The register
  writes and their order are unchanged:

    init()             RESET, CLK_FREQ, SPIOUT_CONF,
                       CHOPCONF -> SMARTEN -> SGCSCONF -> DRVCONF,
                       then cScaleInit / writeMicrosteps / writeSPR   (:454-472)
    enable(false)      CHOPCONF with TOFF = 0                          (:495-497)
    enable(true)       CHOPCONF with TOFF = 3                          (:520-522)
    config_stallguard  SGCSCONF, VSTALL_LIMIT_WR, REFERENCE_CONF x2   (:2131-2160)

  The literal words master wrote are pinned in test/test_driver_regs against
  the builders used here, so this file is responsible only for the SEQUENCE.
*/

/* Master's boot chopper off-time. init() writes it and caches it in the struct
   so enable() can restore the same value rather than re-hardcoding it. */
#define TMC2660_BOOT_TOFF 3

void tmc2660_driver_init(TMC4361ATypeDef *tmc4361A, uint32_t clk_Hz_TMC4361)
{
    tmc4361A_writeInt(tmc4361A, TMC4361A_RESET_REG, 0x52535400);
    tmc4361A_writeInt(tmc4361A, TMC4361A_CLK_FREQ, clk_Hz_TMC4361);

    /* Restore THIS driver's SPI output format. The probe leaves SPIOUT_CONF at
       TMC_SPIOUT_CONF_PROBE, and anything other than master's exact value here
       is an invisible violation of M5. */
    tmc4361A_writeInt(tmc4361A, TMC4361A_SPIOUT_CONF, (int32_t)TMC_SPIOUT_CONF_2660);

    /* Cover datagrams for the TMC2660. DRVCONF sets SDOFF = 1 -> SPI mode. */
    tmc4361A_writeInt(tmc4361A, TMC4361A_COVER_LOW_WR, (int32_t)tmc2660_chopconf_datagram(TMC2660_BOOT_TOFF));
    tmc4361A_writeInt(tmc4361A, TMC4361A_COVER_LOW_WR, (int32_t)tmc2660_smarten_datagram());
    tmc4361A_writeInt(tmc4361A, TMC4361A_COVER_LOW_WR, (int32_t)tmc2660_sgcsconf_datagram(10, 0, false));
    tmc4361A_writeInt(tmc4361A, TMC4361A_COVER_LOW_WR, (int32_t)tmc2660_drvconf_datagram());

    tmc4361A->driver_toff = TMC2660_BOOT_TOFF;

    /* current scaling */
    tmc4361A_cScaleInit(tmc4361A);
    /* microstepping setting */
    tmc4361A_writeMicrosteps(tmc4361A);
    tmc4361A_writeSPR(tmc4361A);
}

/*
  NOT REACHED YET. Master computed the current scale in the caller and handed
  tmc4361A_tmc2660_config a pre-divided float; Task 7 is what populates
  tmc4361A->r_sense and moves the call sites onto this milliamp-based entry
  point. Until then nothing calls this and r_sense is 0, which makes every
  request encode as CS = 0 rather than as a wrong current.
*/
void tmc2660_driver_set_current(TMC4361ATypeDef *tmc4361A, float current_rms_ma, float hold_ratio)
{
    uint8_t cs = tmc2660_current_scale(current_rms_ma, tmc4361A->r_sense);
    if (cs == TMC_CURRENT_OUT_OF_RANGE) {
        /* Requested current exceeds what this R_sense can express. Refuse rather
           than saturate, matching the TMC2240 path; the axis keeps its previous
           current. Letting the sentinel through to tmc2660_sgcsconf_datagram
           would mask 0xFF to CS = 31, i.e. MAXIMUM current. */
        return;
    }

    tmc4361A->cscaleParam[CSCALE_IDX]    = cs;
    /* uint8_t truncation matches tmc4361A_tmc2660_config's uint8_t(x * 255). */
    tmc4361A->cscaleParam[HOLDSCALE_IDX] = (uint8_t)(hold_ratio * 255.0f);
    tmc4361A->cscaleParam[DRV2SCALE_IDX] = 255;
    tmc4361A->cscaleParam[DRV1SCALE_IDX] = 255;
    tmc4361A->cscaleParam[BSTSCALE_IDX]  = 255;
    /* cScaleInit writes SGCSCONF (via cover) plus the TMC4361A SCALE_VALUES,
       exactly as master does. */
    tmc4361A_cScaleInit(tmc4361A);
}

void tmc2660_driver_set_microsteps(TMC4361ATypeDef *tmc4361A, uint16_t microsteps)
{
    /* Nothing to write to the driver: under DRVCONF.SDOFF = 1 the TMC4361A's
       STEP_CONF owns microstepping. tmc4361A_writeMicrosteps handles it. */
    (void)tmc4361A;
    (void)microsteps;
}

void tmc2660_driver_enable(TMC4361ATypeDef *tmc4361A, bool enable)
{
    /* driver_toff is set to 3 by tmc4361A_init and again by init() above, so
       the fallback only covers a struct that somehow never saw either. Master
       wrote the constant 0x000900C3 unconditionally; this reaches the same word. */
    uint8_t toff = enable ? (tmc4361A->driver_toff > 0 ? tmc4361A->driver_toff : TMC2660_BOOT_TOFF) : 0;
    tmc4361A_writeInt(tmc4361A, TMC4361A_COVER_LOW_WR, (int32_t)tmc2660_chopconf_datagram(toff));
}

/*
  Master CLAMPED out-of-range arguments and configured stall detection anyway,
  reporting the clamp through the return value; it did not skip the writes. That
  is preserved deliberately — refusing outright would leave STOP_ON_STALL
  unconfigured on an axis whose caller ignores the result, which is strictly
  less safe than stalling at a clamped sensitivity.

  The return value is master's too, and it is NOT the NO_ERR/ERR_OUT_OF_RANGE
  convention the rest of TMC4361A_Utils uses: master returned the bool `success`,
  so 1 means accepted and 0 means clamped. Both in-tree call sites (init.cpp:187,
  :188) discard it. Left alone rather than "corrected" here, because flipping the
  sense of a returned status in a refactor commit is how a later caller silently
  inverts its check.
*/
int16_t tmc2660_driver_config_stallguard(TMC4361ATypeDef *tmc4361A, int8_t sensitivity,
                                         bool filter_en, uint32_t vstall_lim)
{
    /* First, ensure values are within limits. */
    bool success = true;
    if ((sensitivity > 63) || (sensitivity < -64) || (vstall_lim >= (1UL << 24))) {
        success = false;
    }
    if (sensitivity > 63)  sensitivity = 63;
    if (sensitivity < -64) sensitivity = -64;
    if (vstall_lim > ((1UL << 24) - 1)) vstall_lim = (1UL << 24) - 1;

    /* Master ORs cscaleParam[CSCALE_IDX] in UNMASKED. Build the SGT/SFILT part
       with cs = 0 and OR the raw value, so a cs above 31 reproduces master's
       word exactly. cs > 31 is reachable: callback_configure_stepper_driver
       accepts a u16 milliamp value, and the current formula stores whatever
       uint8_t it yields. Passing the raw value through the masking builder
       would silently change SGCSCONF on X/Y.

       Same reasoning as tmc4361A_cScaleInit, which also ORs unmasked. The two
       SGCSCONF writers must agree; if one masks and the other does not, the
       word an axis ends up with depends on which ran last. */
    uint32_t datagram = tmc2660_sgcsconf_datagram(0, sensitivity, filter_en);
    datagram |= (uint32_t)tmc4361A->cscaleParam[CSCALE_IDX];
    tmc4361A_writeInt(tmc4361A, TMC4361A_COVER_LOW_WR, (int32_t)datagram);

    /* TMC4361A-side stall reaction is driver-agnostic and stays here so both
       drivers get identical stop-on-stall behavior. */
    tmc4361A_writeInt(tmc4361A, TMC4361A_VSTALL_LIMIT_WR, (int32_t)vstall_lim);
    tmc4361A_setBits(tmc4361A, TMC4361A_REFERENCE_CONF, TMC4361A_STOP_ON_STALL_MASK);
    tmc4361A_rstBits(tmc4361A, TMC4361A_REFERENCE_CONF, TMC4361A_DRV_AFTER_STALL_MASK);

    return success;
}
