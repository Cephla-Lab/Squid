/**
 * CRC-8 CCITT Implementation
 */

#ifndef CRC8_H
#define CRC8_H

#include <Arduino.h>

uint8_t crc8ccitt(const void *data, size_t size);

#endif // CRC8_H
