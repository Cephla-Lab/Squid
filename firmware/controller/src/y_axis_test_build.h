#ifndef Y_AXIS_TEST_BUILD_H_
#define Y_AXIS_TEST_BUILD_H_

/*
  TEMPORARY TEST IMAGE. Not for shipping and not for merging.

  For running a stage whose Y limit switch is suspect without letting Y hurt itself. On this branch
  every build is the test image (the define below); delete the define to get stock firmware back.

    1. Y's limit switches are ignored. Both Y stop inputs are programmed as disabled, whatever the
       EEPROM record holds and whatever SET_LIMIT_SWITCH_CONFIG asks for (limit_switches.cpp), so
       neither input stops Y or ends a Y move. What the host sends is still stored, so stock
       firmware flashed afterwards finds the machine's own settings.
    2. A home of Y does not look for the switch and does not move Y. It makes Y's present position 0,
       as a zero does, and the command is finished at once; Y never counts as homed (firmware 1.9
       startup state). An XY home homes X as usual. See y_axis_test_take_home_command().
    3. Y's run current is capped at Y_AXIS_TEST_MAX_RMS_CURRENT_MA, whatever CONFIGURE_STEPPER_DRIVER
       asks for: init.cpp sets the cap on the axis and tmc_driver_set_current() applies it, which is
       the one function every current setting goes through (boot, CONFIGURE_STEPPER_DRIVER,
       INITIALIZE).
    4. Status bytes 20-21, which stock firmware sends as 0, report the X and Y limit inputs as they
       read now and the limit-switch settings stored in the controller (y_axis_test_pack_status below;
       firmware/tools/y_switch_monitor.py prints them). Not while encoder reporting is on, and not in
       a startup verdict packet: those own the two bytes.

  X, Z and the filter wheels are unchanged. Squid cannot tell this image from stock: it reports the
  stock firmware version, accepts every command as before, and no Squid version reads bytes 20-21 of
  an ordinary packet.

  With Y's switches ignored, the low current is the only thing that limits what Y does at the end of
  its travel, and Y's position is counted from wherever the stage stood at the last "home".
*/
#define Y_AXIS_TEST_BUILD

#ifdef Y_AXIS_TEST_BUILD

// mA rms. On a TMC2660 axis (R_sense 0.22 ohm) 100 mA is current scale 2, i.e. 3/32 of full scale;
// the stock 1000 mA is scale 29, 30/32. So this is a tenth of the stock run current. The hold
// current keeps its ratio to the run current.
#ifndef Y_AXIS_TEST_MAX_RMS_CURRENT_MA
#define Y_AXIS_TEST_MAX_RMS_CURRENT_MA 100
#endif

#include <stdint.h>

// Status bytes 20-21. Inputs are in tmc4361A_readLimitSwitches format (bit 0 left, bit 1 right) and
// configs are the stored nibbles (utils/limit_switch_config.h).
//   byte 20: bit 7 = 1 (this image), bits 5-4 = X inputs active now, bits 1-0 = Y inputs active now
//   byte 21: high nibble = X's stored config, low nibble = Y's stored config
// X is read under its stored config, as stock firmware reads it. Y is read under this image's forced
// config, both inputs active-low: "active" means the input is low, whatever polarity is stored for Y.
static inline void y_axis_test_pack_status(uint8_t y_inputs, uint8_t x_inputs, uint8_t y_stored, uint8_t x_stored,
                                           uint8_t out[2])
{
    out[0] = (uint8_t)(0x80 | ((x_inputs & 0x03) << 4) | (y_inputs & 0x03));
    out[1] = (uint8_t)(((x_stored & 0x0F) << 4) | (y_stored & 0x0F));
}

// send_position_update: fill status bytes 20-21 (out = &buffer_tx[20]).
void y_axis_test_status_fields(uint8_t out[2]);

// HOME_OR_ZERO, called before the command is looked at. True: it was a home of Y, and it is
// finished. False: carry on with the command; an XY home has by then become a home of X.
bool y_axis_test_take_home_command();

#endif /* Y_AXIS_TEST_BUILD */

#endif /* Y_AXIS_TEST_BUILD_H_ */
