/**
 * TTL-Only Firmware - Initialization Implementation
 */

#include "init.h"

void init_serial()
{
    // Initialize Native USB port at 2Mbps (same as main firmware)
    SerialUSB.begin(2000000);
    delay(500);
    SerialUSB.setTimeout(200);
}

void init_laser_pins()
{
    // TTL outputs for laser enable - all start LOW (off)
    pinMode(LASER_405nm, OUTPUT);
    digitalWrite(LASER_405nm, LOW);

    pinMode(LASER_488nm, OUTPUT);
    digitalWrite(LASER_488nm, LOW);

    pinMode(LASER_561nm, OUTPUT);
    digitalWrite(LASER_561nm, LOW);

    pinMode(LASER_638nm, OUTPUT);
    digitalWrite(LASER_638nm, LOW);

    pinMode(LASER_730nm, OUTPUT);
    digitalWrite(LASER_730nm, LOW);
}
