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

  Must be called AFTER SPI.begin() and after tmc4361A_init() for this axis.
  Leaves SPIOUT_CONF at TMC_SPIOUT_CONF_PROBE; the selected driver's init()
  restores its own value.
*/
uint8_t tmc_driver_probe(TMC4361ATypeDef *tmc4361A);

#endif /* TMC_DRIVER_PROBE_H */
