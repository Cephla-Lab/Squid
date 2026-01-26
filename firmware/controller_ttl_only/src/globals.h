/**
 * TTL-Only Firmware - Global Variables
 */

#ifndef GLOBALS_H
#define GLOBALS_H

#include "constants.h"
#include <Arduino.h>

// Serial communication buffers
extern byte buffer_rx[512];
extern byte buffer_tx[MSG_LENGTH];
extern volatile int buffer_rx_ptr;
extern byte cmd_id;
extern bool checksum_error;

// Timing
extern elapsedMicros us_since_last_pos_update;

// Illumination state
extern int illumination_source;
extern uint16_t illumination_intensity;
extern float illumination_intensity_factor;
extern bool illumination_is_on;

// Command callback map
extern CommandCallback cmd_map[256];

#endif // GLOBALS_H
