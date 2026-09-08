#ifndef JOYSTICK_H
#define JOYSTICK_H

#include <Arduino.h>

// Initialize the hand controller (Serial5 + PacketSerial, called in setup)
void joystick_init();

// called from the main loop (PacketSerial receive + 30ms periodic motion update + focus-wheel control)
void joystick_update();

// print protocol-frame statistics counters (legacy/crc_ok/crc_fail), used by S:JOYSTICK_STATS
void joystick_print_stats();

#endif // JOYSTICK_H
