/**
 * CRC-16 CCITT implementation for protocol v2.
 *
 * Uses polynomial 0x1021 with initial value 0xFFFF (CRC-16/CCITT-FALSE).
 */

#ifndef CRC16_H
#define CRC16_H

#include <stdint.h>

/**
 * Calculate CRC-16 CCITT over a data buffer.
 *
 * @param data Pointer to data buffer
 * @param length Number of bytes to process
 * @return 16-bit CRC value
 */
uint16_t crc16_ccitt(const uint8_t* data, uint16_t length);

#endif // CRC16_H
