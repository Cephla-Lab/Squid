/**
 * TTL-Only Firmware - Illumination Control
 *
 * Controls 5 TTL outputs with optional DAC-based intensity control.
 */

#ifndef ILLUMINATION_H
#define ILLUMINATION_H

#include "constants.h"
#include "globals.h"

// DAC functions
void init_dac();
void set_dac_output(int channel, uint16_t value);
void set_dac_gain(uint8_t div, uint8_t gains);

// Illumination control
void turn_on_illumination();
void turn_off_illumination();
void set_illumination(int source, uint16_t intensity);

// Turn off all lasers (for safety interlock)
void turn_off_all_lasers();

#endif // ILLUMINATION_H
