#ifndef TMC2240_DRIVER_H
#define TMC2240_DRIVER_H

#include <stdint.h>
#include "../TMC4361A.h"

/*
  TMC2240 power-stage operations.

  Ported from octoaxes tmc/motion/MotorControl.cpp WITH FOUR CORRECTIONS:
    1. The current formula includes the /sqrt(2) their version omits — see
       driver_math.h and design M8. Their form runs ~29% low against an RMS host.
    2. enable() sources TOFF from the shadow cache, never from a register read.
       This is their own later fix (new-W-axis 8136bff, 2026-06-11) which
       postdates the snapshot in PR #571: cover reads fluctuate, and a garbage
       read-back made the W axis fail to re-enable after a disable.
    3. config_stallguard() sources COOLCONF from the shadow too. Theirs does not:
       tmc2240_fieldWrite reads the register first, and because their register
       table marks COOLCONF (0x6D) readable, that read goes out over the same
       unreliable cover path correction 2 exists to avoid. Same latent bug, one
       register over.
    4. config_stallguard() routes filter_en to COOLCONF.SFILT, the StallGuard2
       filter, applied in the SAME read-modify-write as SGT. Theirs writes
       SG4_THRS.sg4_filt_en, which is StallGuard4 and inert under the SpreadCycle
       this driver configures — so their 2240 axes run unfiltered StallGuard
       while their 2660 axes, from the identical argument, run filtered.

  Every operation here writes; none of them reads a TMC2240 register in order to
  modify it. Only the probe reads, and it votes across repeats.

  init() also sets GENERAL_CONF.REVERSE_MOTOR_DIR, without which every TMC2240
  axis runs backwards under direct_mode. See the comment at the write itself.
*/

/* 40-bit cover datagram helpers, also used by the probe. */
void     tmc2240_cover_write(TMC4361ATypeDef *tmc4361A, uint8_t address, uint32_t value);
uint32_t tmc2240_cover_read(TMC4361ATypeDef *tmc4361A, uint8_t address);

void    tmc2240_driver_init(TMC4361ATypeDef *tmc4361A, uint32_t clk_Hz_TMC4361);
void    tmc2240_driver_set_current(TMC4361ATypeDef *tmc4361A, float current_rms_ma, float hold_ratio);
void    tmc2240_driver_set_microsteps(TMC4361ATypeDef *tmc4361A, uint16_t microsteps);
void    tmc2240_driver_enable(TMC4361ATypeDef *tmc4361A, bool enable);
int16_t tmc2240_driver_config_stallguard(TMC4361ATypeDef *tmc4361A, int8_t sensitivity,
                                         bool filter_en, uint32_t vstall_lim);

#endif /* TMC2240_DRIVER_H */
