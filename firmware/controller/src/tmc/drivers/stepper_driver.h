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

/* Pulled in for TMC4361ATypeDef, which the dispatch signatures below need.
   NOTE: this is why TMC4361A.h must NOT include this header — it would form a
   cycle that the include guards turn into "TMC4361ATypeDef undeclared" rather
   than into infinite recursion, because TMC4361A.h needs the struct DEFINED
   before this file's declarations are parsed. TMC4361A.h therefore keeps its
   own include of tmc2240_regs.h for TMC2240_SHADOW_COUNT. */
#include "../TMC4361A.h"

/* The fail-safe predicate: may this axis be commanded to move?

   An axis whose driver could not be identified is never commanded. The probe
   could not confirm that anything is answering on that axis's SPI, so its
   current scaling — and therefore its torque — is unknown (design M4). The
   DRIVER_UNKNOWN default set by tmc4361A_init() means a never-probed axis
   answers false here too, so the failure is safe rather than silent.

   It lives in this header, rather than only in the move callbacks that enforce
   it, so that it is reachable on the host: test_driver_sequence pins it against
   every row of the probe's decision table. stage_commands.cpp, where the eight
   enforcing call sites are, cannot be compiled by env:native — it reaches
   Arduino, FastLED, PacketSerial and the Teensy pin map through globals.h /
   functions.h — so without this seam the fail-safe decision would have no
   automated coverage at all. */
static inline bool tmc_driver_ready(const TMC4361ATypeDef *tmc4361A)
{
    return tmc4361A->driver_type != DRIVER_UNKNOWN;
}

/* The five dispatched operations. Implementations in tmc2660.cpp / tmc2240.cpp;
   dispatch bodies in TMC4361A_Utils.cpp. */
void    tmc_driver_init(TMC4361ATypeDef *tmc4361A, uint32_t clk_Hz_TMC4361);
void    tmc_driver_set_current(TMC4361ATypeDef *tmc4361A, float current_rms_ma, float hold_ratio);
void    tmc_driver_set_microsteps(TMC4361ATypeDef *tmc4361A, uint16_t microsteps);
void    tmc_driver_enable(TMC4361ATypeDef *tmc4361A, bool enable);
int16_t tmc_driver_config_stallguard(TMC4361ATypeDef *tmc4361A, int8_t sensitivity,
                                     bool filter_en, uint32_t vstall_lim);

#endif /* STEPPER_DRIVER_H */
