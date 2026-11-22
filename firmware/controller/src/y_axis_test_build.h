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

  X, Z and the filter wheels are unchanged. The host cannot tell this image from stock: it reports
  the stock firmware version and accepts every command as before.

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

// HOME_OR_ZERO, called before the command is looked at. True: it was a home of Y, and it is
// finished. False: carry on with the command; an XY home has by then become a home of X.
bool y_axis_test_take_home_command();

#endif /* Y_AXIS_TEST_BUILD */

#endif /* Y_AXIS_TEST_BUILD_H_ */
