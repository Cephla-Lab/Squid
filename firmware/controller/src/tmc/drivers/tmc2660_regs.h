#ifndef TMC2660_REGS_H
#define TMC2660_REGS_H

#include <stdint.h>

/*
  Pure builders for the TMC2660's 20-bit SPI datagrams, sent through the
  TMC4361A cover-datagram passthrough (COVER_LOW_WR).

  Every constant here is transcribed from master's tmc4361A_tmc2660_init and
  tmc4361A_cScaleInit. The native tests pin the resulting words to master's
  exact values, which is what makes design M5's "bit-identical" claim
  checkable rather than a promise.

  Register selector bits are [19:17]:
    DRVCTRL  000  (unused: SDOFF=1 means motion comes over SPI, not step/dir)
    CHOPCONF 100  -> 0x080000 (master's words read 0x09xxxx because TBL bit 1,
                               at bit 16, is set — that is a chopper field,
                               not part of the address)
    SMARTEN  101  -> 0x0A0000
    SGCSCONF 110  -> 0x0C0000
    DRVCONF  111  -> 0x0E0000
*/

#define TMC2660_CHOPCONF_ADDR 0x080000u
#define TMC2660_SMARTEN_ADDR  0x0A0000u
#define TMC2660_SGCSCONF_ADDR 0x0C0000u
#define TMC2660_DRVCONF_ADDR  0x0E0000u
#define TMC2660_SFILT         0x010000u

/* CHOPCONF field positions. The [19:17] selector is already folded into
   TMC2660_CHOPCONF_ADDR, so these are the chopper fields below it. */
#define TMC2660_CHOPCONF_TOFF_SHIFT  0   /* [3:0]   */
#define TMC2660_CHOPCONF_HSTRT_SHIFT 4   /* [6:4]   */
#define TMC2660_CHOPCONF_HEND_SHIFT  7   /* [10:7]  */
#define TMC2660_CHOPCONF_HDEC_SHIFT  11  /* [12:11] */
#define TMC2660_CHOPCONF_RNDTF_SHIFT 13  /* [13]    */
#define TMC2660_CHOPCONF_CHM_SHIFT   14  /* [14]    */
#define TMC2660_CHOPCONF_TBL_SHIFT   15  /* [16:15] */

/*
  CHOPCONF: master writes 0x000900C3 to enable and 0x000900C0 to disable
  (TMC4361A_TMC2660_Utils.cpp:462, :496, :521).

  The body is spelled out as named fields rather than as master's opaque
  constant, so the bit layout lives in code that the pinned tests check instead
  of in a comment that can drift. Three prose-versus-silicon errors have already
  been caught on this branch, every one of them in a comment sitting above
  correct code; this removes a place for a fourth.

  Only TOFF is parameterised — it is the sole difference between the enable and
  disable datagrams. Every other field is master's fixed chopper setting and
  must not change without a bench thermal check (M5).

  HEND: the raw field value is 1, which is derived directly from master's bits
  and is certain. The TMC26x family stores HEND with a +3 offset (%0000 = -3
  ... %1111 = 12), so raw 1 means a hysteresis end of -2; that reading depends
  on the offset convention rather than on our bits. It is the same convention
  tmc2240_chopconf_value applies when it computes hend + 3. Note the asymmetry
  between the two builders: this one takes no hysteresis argument and therefore
  carries the RAW field value, whereas tmc2240_chopconf_value takes the
  offset-free value and adds the 3 itself.
*/
static inline uint32_t tmc2660_chopconf_datagram(uint8_t toff)
{
    return TMC2660_CHOPCONF_ADDR                 /* [19:17] 100         CHOPCONF selector    */
         | (2u << TMC2660_CHOPCONF_TBL_SHIFT)    /* [16:15] TBL   = 2   36-clock blanking    */
         | (0u << TMC2660_CHOPCONF_CHM_SHIFT)    /* [14]    CHM   = 0   spreadCycle chopper  */
         | (0u << TMC2660_CHOPCONF_RNDTF_SHIFT)  /* [13]    RNDTF = 0   fixed chopper off    */
         | (0u << TMC2660_CHOPCONF_HDEC_SHIFT)   /* [12:11] HDEC  = 0                        */
         | (1u << TMC2660_CHOPCONF_HEND_SHIFT)   /* [10:7]  HEND  = 1   raw; -2 after offset */
         | (4u << TMC2660_CHOPCONF_HSTRT_SHIFT)  /* [6:4]   HSTRT = 4                        */
         | ((uint32_t)(toff & 0x0Fu) << TMC2660_CHOPCONF_TOFF_SHIFT);
                                                 /* [3:0]   TOFF  = the toff argument        */
}

/* SMARTEN: master writes 0x000A0000 — CoolStep disabled. */
static inline uint32_t tmc2660_smarten_datagram(void)
{
    return TMC2660_SMARTEN_ADDR;
}

/*
  SGCSCONF: CS [4:0], SGT [14:8] (signed 7-bit, -64..63), SFILT bit 16.
  Master's boot value is 0x000C000A (CS = 10, SGT = 0, filter off);
  cScaleInit then rewrites it as SGCSCONF | SFILT | cs.

  This is a PURE ENCODER: it masks, it does not validate. Master's
  tmc4361A_config_init_stallGuard constrains sgt to -64..63 and reports
  failure outside that range BEFORE encoding. Callers must keep doing that —
  passing sgt = 100 here silently encodes -28, whereas master clamped to +63.
  For every in-range input the two are bit-identical (verified exhaustively
  over sgt x cs x sfilt).

  DANGER — DO NOT LAUNDER THE SENTINEL INTO cs. tmc2660_current_scale returns
  TMC_CURRENT_OUT_OF_RANGE (0xFF) when the requested milliamps cannot be
  encoded. 0xFF & 0x1F == 31, so forwarding that value here yields CS = 31,
  i.e. MAXIMUM motor current — precisely inverting the "reject, never clamp"
  contract stated at driver_math.h:99-102. A pure value builder has no error
  channel, so the caller MUST compare against TMC_CURRENT_OUT_OF_RANGE and
  fail the command BEFORE calling this.
*/
static inline uint32_t tmc2660_sgcsconf_datagram(uint8_t cs, int8_t sgt, bool sfilt)
{
    uint32_t d = TMC2660_SGCSCONF_ADDR;
    d |= (uint32_t)(cs & 0x1Fu);
    d |= ((uint32_t)((uint8_t)sgt & 0x7Fu)) << 8;
    if (sfilt) d |= TMC2660_SFILT;
    return d;
}

/*
  DRVCONF: master writes 0x000E00A1.
  0x00A1 = SDOFF(bit 7) | RDSEL=2(bits [5:4]) | 0x01.
  VSENSE (bit 6) = 0 selects the high sense range, V_FS = 0.310 V. Note that
  master's current formula assumes 0.325 V — see design §5; the mismatch is
  deliberate and preserved here (M5).
*/
static inline uint32_t tmc2660_drvconf_datagram(void)
{
    return TMC2660_DRVCONF_ADDR | 0x00A1u;
}

#endif /* TMC2660_REGS_H */
