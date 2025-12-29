#ifndef SERIAL_COMMUNICATION_H
#define SERIAL_COMMUNICATION_H

#include "globals.h"
#include "utils/crc8.h"
#include "commands/commands.h"

void process_serial_message();
void send_position_update();

#endif // SERIAL_COMMUNICATION_H
