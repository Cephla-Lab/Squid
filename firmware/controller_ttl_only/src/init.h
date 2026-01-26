/**
 * TTL-Only Firmware - Initialization
 */

#ifndef INIT_H
#define INIT_H

#include "constants.h"
#include "globals.h"

// Initialize USB serial communication
void init_serial();

// Initialize TTL output pins for lasers
void init_laser_pins();

#endif // INIT_H
