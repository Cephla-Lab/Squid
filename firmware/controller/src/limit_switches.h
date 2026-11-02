#ifndef LIMIT_SWITCHES_H_
#define LIMIT_SWITCHES_H_

#include <stdint.h>

// Per-axis (X, Y, Z) limit-switch configuration: stored in EEPROM bytes 0-6, programmed into REFERENCE_CONF.
// The nibble format and the record layout live in utils/limit_switch_config.h.

// Read EEPROM 0-6 into the per-axis table; a blank or invalid record gives LSC_DEFAULT for every axis.
void limit_switches_load();

// Program one axis's two stop inputs from the stored config, then rewrite the REFERENCE_CONF stop nibble exactly
// and clear HOME_EVENT. internal_axis is x, y or z; flipped is the axis's flip_limit_switch_* setting.
void limit_switches_program(uint8_t internal_axis, bool flipped);

// Polarity (1 = active-high) of the input the axis homes on, TMC4361_homing_sw[axis].
uint8_t limit_switch_homing_polarity(uint8_t internal_axis);

// tmc4361A_readLimitSwitches / tmc4361A_readSwitchEvent of X, Y or Z with disabled inputs masked out. STATUS and
// EVENTS report every input, enabled or not, and homing and check_limits compare the result with exactly LEFT_SW /
// RGHT_SW: unmasked, a disabled input reading active hung a homing started on its switch. The event reads clear
// EVENTS as tmc4361A_readSwitchEvent does.
// Homing toward `toward`: the other input counts only when enabled; `toward` always counts (lsc_homing_mask).
uint8_t limit_switches_homing_read(uint8_t internal_axis, uint8_t toward);
uint8_t limit_switches_homing_event(uint8_t internal_axis, uint8_t toward);
// check_limits: events of enabled inputs only (a disabled input does not stop the motor, so it ends no move).
uint8_t limit_switches_read_event(uint8_t internal_axis);

// SET_LIMIT_SWITCH_CONFIG: protocol axis X/Y/Z only and config <= 0x0F, otherwise false and nothing changes.
// Stores the config, reprograms the axis, re-arms homing and saves the record to EEPROM.
bool limit_switches_set(uint8_t protocol_axis, uint8_t config);

#endif /* LIMIT_SWITCHES_H_ */
