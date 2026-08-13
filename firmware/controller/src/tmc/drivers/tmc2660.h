#ifndef TMC2660_DRIVER_H
#define TMC2660_DRIVER_H

#include <stdint.h>
#include "../TMC4361A.h"

/*
  TMC2660 power-stage operations, extracted from master 856bc0ee
  TMC4361A_TMC2660_Utils.cpp (now TMC4361A_Utils.cpp, whose exported
  tmc4361A_tmc2660_* names delegate here). Behavior is bit-identical to master
  (design M5); the register words come from tmc2660_regs.h and are pinned by
  test/test_driver_regs.
*/

void    tmc2660_driver_init(TMC4361ATypeDef *tmc4361A, uint32_t clk_Hz_TMC4361);
void    tmc2660_driver_set_current(TMC4361ATypeDef *tmc4361A, float current_rms_ma, float hold_ratio);
void    tmc2660_driver_set_microsteps(TMC4361ATypeDef *tmc4361A, uint16_t microsteps);
void    tmc2660_driver_enable(TMC4361ATypeDef *tmc4361A, bool enable);
int16_t tmc2660_driver_config_stallguard(TMC4361ATypeDef *tmc4361A, int8_t sensitivity,
                                         bool filter_en, uint32_t vstall_lim);

#endif /* TMC2660_DRIVER_H */
