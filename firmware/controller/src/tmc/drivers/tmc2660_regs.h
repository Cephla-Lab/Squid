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
    CHOPCONF 100  -> 0x080000, master writes 0x09xxxx (bit 16 set)
    SMARTEN  101  -> 0x0A0000
    SGCSCONF 110  -> 0x0C0000
    DRVCONF  111  -> 0x0E0000
*/

#define TMC2660_CHOPCONF_ADDR 0x080000u
#define TMC2660_SMARTEN_ADDR  0x0A0000u
#define TMC2660_SGCSCONF_ADDR 0x0C0000u
#define TMC2660_DRVCONF_ADDR  0x0E0000u
#define TMC2660_SFILT         0x010000u

/*
  CHOPCONF: master writes 0x000900C3 to enable and 0x000900C0 to disable.
  0x0900C3 = CHOPCONF_ADDR(0x080000) | 0x010000 | 0x00C0 | TOFF(3).
  The 0x00C0 body and the 0x010000 bit are master's fixed chopper settings;
  only TOFF varies between the enable and disable datagrams.
*/
static inline uint32_t tmc2660_chopconf_datagram(uint8_t toff)
{
    return TMC2660_CHOPCONF_ADDR | 0x010000u | 0x00C0u | (uint32_t)(toff & 0x0Fu);
}

/* SMARTEN: master writes 0x000A0000 — CoolStep disabled. */
static inline uint32_t tmc2660_smarten_datagram(void)
{
    return TMC2660_SMARTEN_ADDR;
}

/*
  SGCSCONF: CS [4:0], SGT [12:8] (signed 7-bit), SFILT bit 16.
  Master's boot value is 0x000C000A (CS = 10, SGT = 0, filter off);
  cScaleInit then rewrites it as SGCSCONF | SFILT | cs.
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
