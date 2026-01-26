/**
 * TTL-Only Firmware - Command Handlers
 *
 * Registers callbacks for supported commands.
 * Unsupported commands go to callback_default() which ACKs without execution.
 */

#ifndef COMMANDS_H
#define COMMANDS_H

#include "constants.h"
#include "globals.h"
#include "illumination.h"

// Initialize command callback map
void init_callbacks();

// Default callback for unsupported commands (ACK only, no execution)
void callback_default();

// Illumination callbacks
void callback_turn_on_illumination();
void callback_turn_off_illumination();
void callback_set_illumination();
void callback_set_illumination_intensity_factor();
void callback_set_dac_gain();
void callback_analog_write_dac();

// System callbacks
void callback_initialize();
void callback_reset();

#endif // COMMANDS_H
