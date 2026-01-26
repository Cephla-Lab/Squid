/**
 * TTL-Only Firmware - Global Variables Implementation
 */

#include "globals.h"

// Serial communication
byte buffer_rx[512];
byte buffer_tx[MSG_LENGTH];
volatile int buffer_rx_ptr = 0;
byte cmd_id = 0;
bool checksum_error = false;

// Timing
elapsedMicros us_since_last_pos_update;

// Illumination state
int illumination_source = 0;
uint16_t illumination_intensity = 0;
float illumination_intensity_factor = 1.0;
bool illumination_is_on = false;

// Command callback map
CommandCallback cmd_map[256] = {0};
