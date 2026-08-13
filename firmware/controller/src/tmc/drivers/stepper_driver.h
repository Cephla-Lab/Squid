#ifndef STEPPER_DRIVER_H
#define STEPPER_DRIVER_H

#include <stdint.h>

/*
  The stepper-driver seam.

  The TMC4361A is the motion controller: ramp generation, XACTUAL/XTARGET,
  limit switches, virtual stops, encoder and PID all live in TMC4361A_Utils and
  are driver-agnostic. Only these five operations differ between power stages,
  and they are dispatched on tmc4361A->driver_type.

  Design: AI-docs Squid/to-do/2026-08-12-tmc2240-driver-support-design.md §6.2
*/

#define DRIVER_UNKNOWN 0
#define DRIVER_TMC2660 1
#define DRIVER_TMC2240 2

static inline const char *tmc_driver_name(uint8_t driver_type)
{
    switch (driver_type) {
        case DRIVER_TMC2660: return "TMC2660";
        case DRIVER_TMC2240: return "TMC2240";
        default:             return "UNKNOWN";
    }
}

/* SPIOUT_CONF values. Each driver's init() is responsible for restoring its
   own after the probe has rewritten it — see design §6.2. Restoring anything
   other than TMC_SPIOUT_CONF_2660 on a 2660 axis silently violates M5.

   Verified against master: TMC_SPIOUT_CONF_2660 is the exact word written at
   TMC4361A_TMC2660_Utils.cpp:460. The COVER_DATA_LENGTH field is SPIOUT_CONF
   bits [19:13] (TMC4361A_Fields.h:258): 0 in the 2660 word ("set to 0 for TMCx
   drivers"), 40 in both 40-bit words below. */
#define TMC_SPIOUT_CONF_2660  0x4440108Au  /* master's exact value */
#define TMC_SPIOUT_CONF_2240  0x4445000Du  /* cover data length 40, format 0x0D */
#define TMC_SPIOUT_CONF_PROBE 0x4445000Au  /* length 40, 2660 auto-format 0x0A */

#endif /* STEPPER_DRIVER_H */
