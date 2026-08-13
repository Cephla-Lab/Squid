#ifndef INIT_H
#define INIT_H

#include <PacketSerial.h>
#include <SPI.h>

#include "tmc/TMC4361A.h"
#include "tmc/TMC4361A_Utils.h"

#include "globals.h"
#include "functions.h"

// Boot-time driver report (design M7). Call ONLY for an axis that has just been
// probed: the raw word is meaningless - and actively misleading - for one that
// has not. `axis` is an internal index (def_v1.h: y=0, x=1, z=2, w=3, w2=4).
void report_driver_probe(uint8_t axis);

void init_serial_communication();
void init_lasers_and_led_driver();
void init_power();
void init_camera();
void init_io();
void init_stages();

#endif // INIT_H
