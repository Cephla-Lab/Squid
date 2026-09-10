#include "stepper_driver.h"
#include "tmc2660.h"
#include "tmc2240.h"

/* ---- Driver dispatch (design 6.2) ---------------------------------------
   The five operations that differ between power stages. Everything else in
   TMC4361A_Utils.cpp is TMC4361A-only and driver-agnostic, with one deliberate
   exception: tmc4361A_cScaleInit() also emits the TMC2660 SGCSCONF cover
   datagram, kept there byte-for-byte as master wrote it (M5). It is called only
   from the TMC2660 path in tmc2660.cpp.

   These bodies live in their own translation unit, and one that deliberately
   includes NOTHING but this seam's three headers, so that they are reachable
   from env:native: test_driver_sequence #includes this file alongside
   tmc2660.cpp / tmc2240.cpp / driver_probe.cpp and pins the dispatch contract
   for a DRIVER_UNKNOWN axis. In TMC4361A_Utils.cpp, where they used to live,
   they were unreachable on the host — that file needs <SPI.h> and the real
   TMC4361A primitives.

   DRIVER_UNKNOWN axes are never touched: the probe could not confirm anything is
   answering, so writing driver registers would be writing into the dark. That
   leaves the power stage unconfigured and therefore unenergised, which is
   design M4's fail-safe half. The other half - rejecting that axis's moves so
   the failure is loud rather than a stage that silently does not move - is now
   in place, across three files: the axis_driver_ready() helper (defined in
   stage_commands.cpp) gates the host move and home commands there and the
   ENABLE_STAGE_PID command in commands.cpp, and operations.cpp gates the
   joystick and focus-wheel paths directly. All three are pinned by
   test_command_layout.

   The two dispatchers that return a value agree on what DRIVER_UNKNOWN means:
   nothing was written, so it is reported as a refusal, not as success. */

void tmc_driver_init(TMC4361ATypeDef *tmc4361A, uint32_t clk_Hz_TMC4361) {
  if (tmc4361A->driver_type == DRIVER_TMC2240) tmc2240_driver_init(tmc4361A, clk_Hz_TMC4361);
  else if (tmc4361A->driver_type == DRIVER_TMC2660) tmc2660_driver_init(tmc4361A, clk_Hz_TMC4361);
}

/* Returns true only when the requested current actually reached a driver.
   DRIVER_UNKNOWN returns false for the same reason config_stallguard below
   returns 0: no register was written, so success would be a lie - and this
   particular lie is expensive, because tmc4361A_motor_config() hands the bool
   to cmd 21 CONFIGURE_STEPPER_DRIVER, whose arms commit the requested value to
   the *_MOTOR_RMS_CURRENT_mA globals when it is true. INITIALIZE then re-applies
   those globals on every run. */
bool tmc_driver_set_current(TMC4361ATypeDef *tmc4361A, float current_rms_ma, float hold_ratio) {
  if (tmc4361A->driver_type == DRIVER_TMC2240) return tmc2240_driver_set_current(tmc4361A, current_rms_ma, hold_ratio);
  if (tmc4361A->driver_type == DRIVER_TMC2660) return tmc2660_driver_set_current(tmc4361A, current_rms_ma, hold_ratio);
  return false;  // no identified driver: nothing was written, so nothing was applied
}

void tmc_driver_set_microsteps(TMC4361ATypeDef *tmc4361A, uint16_t microsteps) {
  if (tmc4361A->driver_type == DRIVER_TMC2240) tmc2240_driver_set_microsteps(tmc4361A, microsteps);
  else if (tmc4361A->driver_type == DRIVER_TMC2660) tmc2660_driver_set_microsteps(tmc4361A, microsteps);
}

void tmc_driver_enable(TMC4361ATypeDef *tmc4361A, bool enable) {
  if (tmc4361A->driver_type == DRIVER_TMC2240) tmc2240_driver_enable(tmc4361A, enable);
  else if (tmc4361A->driver_type == DRIVER_TMC2660) tmc2660_driver_enable(tmc4361A, enable);
}

/* Returns the drivers' shared bool contract: 1 = accepted, 0 = clamped. That is
   master's tmc4361A_config_init_stallGuard convention and NOT the
   NO_ERR (0) / ERR_OUT_OF_RANGE (-1) convention used elsewhere in
   TMC4361A_Utils.cpp, so do not "normalise" it here - both driver
   implementations return the same sense, and inverting it in the dispatcher
   would silently flip the meaning for every future caller. DRIVER_UNKNOWN
   returns 0, rejected: nothing was configured, so reporting success would be a
   lie. */
int16_t tmc_driver_config_stallguard(TMC4361ATypeDef *tmc4361A, int8_t sensitivity, bool filter_en, uint32_t vstall_lim) {
  if (tmc4361A->driver_type == DRIVER_TMC2240) return tmc2240_driver_config_stallguard(tmc4361A, sensitivity, filter_en, vstall_lim);
  if (tmc4361A->driver_type == DRIVER_TMC2660) return tmc2660_driver_config_stallguard(tmc4361A, sensitivity, filter_en, vstall_lim);
  return 0;
}
