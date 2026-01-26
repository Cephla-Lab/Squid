/**
 * TTL-Only Firmware - Serial Communication
 *
 * Handles USB serial protocol for receiving commands and sending responses.
 * Protocol is compatible with the main Squid software.
 */

#ifndef SERIAL_COMMUNICATION_H
#define SERIAL_COMMUNICATION_H

#include "constants.h"
#include "globals.h"
#include "utils/crc8.h"

// Process incoming serial messages
void process_serial_message();

// Send periodic status/position update
void send_position_update();

#endif // SERIAL_COMMUNICATION_H
