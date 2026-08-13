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
   own FULL word after the probe has rewritten it — see design §6.2. Restoring
   anything other than TMC_SPIOUT_CONF_2660 on a 2660 axis silently violates M5.

   Verified against master: TMC_SPIOUT_CONF_2660 is the exact word written at
   master 856bc0ee TMC4361A_TMC2660_Utils.cpp:460 (that file is TMC4361A_Utils.cpp
   here; the line numbers below are master's). The COVER_DATA_LENGTH field is SPIOUT_CONF
   bits [19:13] (TMC4361A_Fields.h:258-259): 0 in the 2660 word ("set to 0 for
   TMCx drivers"), 40 in both 40-bit words below. SPI_OUTPUT_FORMAT is bits
   [3:0] (TMC4361A_Fields.h:192-197).

   These words differ in MORE THAN length and format, which is why an init()
   must write its own constant rather than read-modify-write someone else's.
   TMC_SPIOUT_CONF_2660 ^ TMC_SPIOUT_CONF_PROBE = 0x00051080: bits 18 and 16
   are the COVER_DATA_LENGTH 0 -> 40 change, but bits 12 and 7 are also SET in
   the 2660 word and CLEAR in both 40-bit words. Those two are functional, not
   cosmetic, and their meaning depends on SPI_OUTPUT_FORMAT — the Fields.h
   SPI_OUT_CONF block redefines the same bit under several names, one group per
   format:
     bit 12 = AUTO_DOUBLE_CHOPSYNC (Fields.h:200) or
              COVER_DONE_ONLY_FOR_COVER (Fields.h:218)
     bit  7 = STALL_FLAG_INSTEAD_OF_UV_EN (Fields.h:206) or
              AUTOREPEAT_COVER_EN (Fields.h:216)
   So "the probe word is the 2660 word with a longer datagram" is WRONG, and
   deriving one of these constants from another by masking in a new length or
   format would silently change chopSync/COVER_DONE/stall-flag behaviour. */
#define TMC_SPIOUT_CONF_2660  0x4440108Au  /* master's exact value */
#define TMC_SPIOUT_CONF_2240  0x4445000Du  /* cover data length 40, format 0x0D */
#define TMC_SPIOUT_CONF_PROBE 0x4445000Au  /* length 40, 2660 auto-format 0x0A */

#endif /* STEPPER_DRIVER_H */
