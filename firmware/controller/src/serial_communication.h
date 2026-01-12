#ifndef SERIAL_COMMUNICATION_H
#define SERIAL_COMMUNICATION_H

#include "globals.h"
#include "constants.h"

#ifdef USE_PROTOCOL_V2
#include "protocol_v2.h"
#else
#include "utils/crc8.h"
#include "commands/commands.h"
#endif

void init_protocol();
void process_serial_message();
void send_position_update();

#endif // SERIAL_COMMUNICATION_H
