#ifndef INIT_H
#define INIT_H

#include <PacketSerial.h>
#include <SPI.h>

#include "tmc/TMC4361A.h"
#include "tmc/TMC4361A_TMC2660_Utils.h"

#include "globals.h"
#include "functions.h"

void init_serial_communication();
void init_lasers_and_led_driver();
void init_power();
void init_camera();
void init_io();
void init_stages();

#endif // INIT_H
