#include <Arduino.h>
#include "driver_probe.h"
#include "tmc2240.h"
#include "tmc2240_regs.h"
#include "stepper_driver.h"

/*
  Why this file votes instead of reading once.

  Every cover READ on this board is unreliable by construction. The TMC4361A
  datasheet (§10.3.6) wants the COVER_DONE event polled before COVER_DRV_LOW_RD
  holds a valid reply; tmc2240_cover_read() uses a fixed settle delay instead,
  and the automatic SPI output keeps running underneath. A single read can
  therefore return a stale word — most likely on the FIRST read after
  SPIOUT_CONF changes, when COVER_DRV_LOW_RD may still hold whatever it held
  before the format switch.

  This is the ONLY code in the tree that reads a TMC2240 register (everything
  else works from tmc4361A->tmc2240_shadow[], see tmc2240.cpp), which is exactly
  why it cannot afford to trust one sample. It runs once per axis at init, so
  three reads cost nothing anyone can measure.
*/
#define PROBE_READS 3

/* A STRICT majority: with PROBE_READS = 3 a verdict needs 2 of 3, so exactly
   one bad read of any kind is tolerated. Derived rather than written as the
   literal 2 so that raising PROBE_READS stays coherent — a hardcoded threshold
   next to a #defined sample count is how a 5-read probe silently becomes a
   2-of-5 probe. */
#define PROBE_MAJORITY ((PROBE_READS / 2) + 1)

/* Settle time after switching SPI_OUTPUT_FORMAT, before the first cover
   datagram goes out. octoaxes MotorControl.cpp:249 uses the same 500 us. */
#define PROBE_SPIOUT_SETTLE_US 500

/*
  The design §6.4 ladder, applied to ONE read.

  Order matters: liveness is tested BEFORE identity. 0x00000000 and 0xFFFFFFFF
  both have a VERSION byte of 0x00 and 0xFF respectively, so neither can be
  mistaken for a 2240 either way — but testing liveness first is what makes the
  "nothing is answering" case a distinct verdict rather than a 2660 by default.

  Note what this function does NOT do: it never returns DRIVER_TMC2660 on
  evidence about the 2660. There is none to have. It returns DRIVER_TMC2660 for
  "something answered, and it did not identify itself as a 2240", which is a
  weaker claim than the name suggests and is the whole reason M4's fail-safe is
  built on liveness.
*/
static uint8_t classify_response(uint32_t response)
{
    /* Nothing driving MISO reads as all-zeros; a shorted or stuck bus reads as
       all-ones. Either way no driver is answering. */
    if ((response == 0x00000000u) || (response == 0xFFFFFFFFu))
        return DRIVER_UNKNOWN;

    if (((response >> TMC2240_IOIN_VERSION_SHIFT) & 0xFFu) == TMC2240_IOIN_VERSION_VALUE)
        return DRIVER_TMC2240;

    return DRIVER_TMC2660;
}

uint8_t tmc_driver_probe(TMC4361ATypeDef *tmc4361A)
{
    /* Cover length 40 with the TMC2660 auto-format (0x0A): the 20-bit
       automatic SPI output then does not overwrite the 40-bit cover response.
       Without this the read is clobbered by the auto-SPI traffic.

       Left in place on exit. Both tmc2660_driver_init() and
       tmc2240_driver_init() write their own full SPIOUT_CONF word (and reset
       the TMC4361A first), so restoring anything here would be undone
       immediately and would mean guessing at an identity we have not yet
       decided. */
    tmc4361A_writeInt(tmc4361A, TMC4361A_SPIOUT_CONF, (int32_t)TMC_SPIOUT_CONF_PROBE);
    delayMicroseconds(PROBE_SPIOUT_SETTLE_US);

    /* Count the two positive verdicts only. A DRIVER_UNKNOWN read votes for
       NOTHING rather than voting for "unknown": it is an absence of evidence,
       and letting absences accumulate into a verdict would let one live 2240
       read plus two dead ones outvote nothing at all. Unknown is what we fall
       through to, not what we elect. */
    uint8_t votes_2240 = 0;
    uint8_t votes_2660 = 0;

    for (uint8_t i = 0; i < PROBE_READS; i++) {
        uint32_t response = tmc2240_cover_read(tmc4361A, TMC2240_REG_IOIN);

        /* Keep the LAST read verbatim. Two things about this design can only be
           settled by looking at the raw word on real silicon — whether a live
           TMC2660 can read all-zeros (which would make the liveness rule brick
           it) and whether a 2660 reply can carry 0x40 in byte [31:24] (which
           would make it misdetect as a 2240) — and neither is answerable from
           driver_type alone. Storing it makes that a log read on any machine in
           the field instead of a one-off instrumented build, and keeps the
           coincidence auditable for as long as the code ships.

           The LAST read, not a "best" one: any selection rule would make this
           field a summary rather than a measurement. If the three reads
           disagree, driver_type is already DRIVER_UNKNOWN and says so. */
        tmc4361A->driver_probe_raw = response;

        switch (classify_response(response)) {
            case DRIVER_TMC2240: votes_2240++; break;
            case DRIVER_TMC2660: votes_2660++; break;
            default:                           break;
        }
    }

    /* No majority -> DRIVER_UNKNOWN. Three reads that disagree mean the bus is
       flaky, and on a flaky bus a lone 0x40 is as likely to be noise as it is
       to be silicon. DRIVER_UNKNOWN disables the axis and rejects its moves
       (design M4), which is a loud failure; the alternative, guessing
       DRIVER_TMC2660, is a silent one — a real 2240 driven with 20-bit
       datagrams never enters direct_mode and simply never moves, while
       accepting every move command. */
    if (votes_2240 >= PROBE_MAJORITY)
        tmc4361A->driver_type = DRIVER_TMC2240;
    else if (votes_2660 >= PROBE_MAJORITY)
        tmc4361A->driver_type = DRIVER_TMC2660;
    else
        tmc4361A->driver_type = DRIVER_UNKNOWN;

    /* No logging here, unlike octoaxes MotorControl.cpp:257-262. Serial on this
       board carries the PacketSerial command protocol at 2 Mbaud; a debug print
       would corrupt a packet. Task 7 owns reporting the result. */
    return tmc4361A->driver_type;
}
