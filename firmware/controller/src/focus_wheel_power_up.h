#ifndef FOCUS_WHEEL_POWER_UP_H_
#define FOCUS_WHEEL_POWER_UP_H_

#include <stdint.h>

// The focus wheel's power-up mode, kept in EEPROM bytes 8-11 (record layout in utils/focus_wheel.h). It is the mode
// at power-up and after RESET and INITIALIZE; SET_FOCUS_WHEEL_MODE with FOCUS_WHEEL_SAVE_AS_POWER_UP changes it.

// Read the record; a blank or invalid one gives FOCUS_WHEEL_DRIVES_Z.
uint8_t focus_wheel_power_up_load();

// Store the record. EEPROM.update writes only bytes that differ, so saving an unchanged mode costs no wear.
void focus_wheel_power_up_save(uint8_t mode);

#endif /* FOCUS_WHEEL_POWER_UP_H_ */
