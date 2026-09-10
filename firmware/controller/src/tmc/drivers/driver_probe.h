#ifndef TMC_DRIVER_PROBE_H
#define TMC_DRIVER_PROBE_H

#include <stdint.h>
#include "../TMC4361A.h"
/* For DRIVER_UNKNOWN / DRIVER_TMC2660 / DRIVER_TMC2240 and tmc_driver_name().
   The return value below is one of those constants and is meaningless without
   them, so this header pulls them in rather than making every caller remember
   to. No cycle: stepper_driver.h includes TMC4361A.h, and neither includes
   this file. */
#include "stepper_driver.h"

/*
  Identify the power stage on one axis. Sets tmc4361A->driver_type and returns
  it.

  A TMC2240 is positively identifiable (IOIN.VERSION == 0x40). A TMC2660 is
  NOT — it has no ID register, so "not a 2240" is the only evidence available.
  The fail-safe (design M4) is therefore built on LIVENESS: an all-zeros or
  all-ones response means nothing is answering, which yields DRIVER_UNKNOWN.

  This detects an unpopulated axis, a dead chip, or a stuck SPI bus. It cannot
  detect a chip that answers like a 2660 but is not the part you think it is.

  Cover reads are unreliable by construction (see driver_probe.cpp), so the
  probe reads three times and returns the verdict a strict majority agrees on.
  With no majority the answer is DRIVER_UNKNOWN — three reads that disagree
  mean the bus is flaky, and neither identity has been established.

  The raw word of the last read is kept in tmc4361A->driver_probe_raw for the
  bench gate below.

  UNVERIFIED ASSUMPTION, AND THE BENCH GATE THAT CLOSES IT
  --------------------------------------------------------
  The liveness rule is safe only if a LIVE TMC2660 can never reply all-zeros.
  The argument that it cannot: the 2660 is a 20-bit shift register, so past bit
  20 of a 40-bit frame it shifts our own transmitted bits back out, putting our
  address byte 0x04 into COVER_DRV_LOW_RD[19:12] and making a zero word
  impossible. The alignment half of that is confirmed (the octoaxes
  TMC4361A.cpp cover path recovers replies right-aligned to frame length, so
  the trailing bits really are received). The PASS-THROUGH half — that SDO
  keeps shifting rather than tri-stating or holding after bit 20 — is confirmed
  nowhere: no datasheet text, and octoaxes' own probe is no evidence either
  way, because it has no liveness test and behaves identically whether a 2660
  replies 0x04000 or 0x00000.

  If the assumption is false, every TMC2660 axis reads DRIVER_UNKNOWN and
  refuses to move. Measure driver_probe_raw on a known-2660 axis before
  trusting this. If it comes back all-zeros, the remedy is to DROP THE ZEROS
  HALF of the liveness test and keep only all-ones. Do NOT reach for
  COVER_DRV_HIGH_RD as a tiebreaker: under the confirmed alignment it holds
  reply bits [19:12], which are MSTEP[9:2] at RDSEL=0 and SG[9:5]/SE[4:3] at
  RDSEL=2 — the bits MOST likely to be zero at standstill. If LOW is zeros,
  HIGH very probably is too.

  MEASURE BOTH PROBE PATHS — THEY DIFFER IN RDSEL
  -----------------------------------------------
  At cold boot the 2660 is unconfigured: SDOFF=0, RDSEL=0, so the reply carries
  MSTEP and the status flags. But init_filterwheel_axis() (commands.cpp:162)
  calls tmc4361A_init(), which resets driver_type, so callback_initfilterwheel
  re-probes an ALREADY-CONFIGURED 2660 at runtime: tmc2660_drvconf_datagram()
  has written 0x00A1, i.e. SDOFF=1 and RDSEL=2, the SG/SE readback. At
  standstill with zero current SG and SE are both zero, which makes the warm
  filter-wheel re-probe the path most likely to read all-zeros — and it is the
  path cold-boot bench testing never exercises. Check the filter wheel too.

  SIDE EFFECT ON A TMC2660: this probe's read datagram is a WRITE from the
  2660's point of view. It latches the last 20 bits of each 40-bit frame, so
  the six datagrams write DRVCTRL = 0 six times. At cold boot (SDOFF=0) that
  zeroes MRES/DEDGE/INTPOL, and tmc2660_driver_init() never writes DRVCTRL to
  put them back. Benign: DRVCONF sets SDOFF=1 immediately afterwards, which
  repurposes DRVCTRL as the coil-current register that the automatic SPI output
  then overwrites continuously.

  Must be called AFTER SPI.begin() and after tmc4361A_init() for this axis.
  Leaves SPIOUT_CONF at TMC_SPIOUT_CONF_PROBE; the selected driver's init()
  restores its own value.
*/
uint8_t tmc_driver_probe(TMC4361ATypeDef *tmc4361A);

#endif /* TMC_DRIVER_PROBE_H */
