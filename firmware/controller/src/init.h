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
//
// BOOT ONLY in a default build. Calling this once loop() is running corrupts the
// host's view of the status stream - it accepts a misaligned 24-byte window and
// reports a garbage stage position as real (mechanism in init.cpp). The warm
// filter-wheel path the bench gate also needs is therefore compiled out unless
// the image is built with -D TMC_PROBE_REPORT_RUNTIME; see platformio.ini.
void report_driver_probe(uint8_t axis);

void init_serial_communication();
void init_lasers_and_led_driver();
void init_power();
void init_camera();
void init_io();
void init_stages();

#endif // INIT_H
