/**
 * CRC-16/CCITT-FALSE for protocol v2 framing.
 *
 * Standard CRC-16/CCITT-FALSE (aka CRC-16/IBM-3740): polynomial 0x1021,
 * initial value 0xFFFF, no input/output reflection, no final XOR. Table-driven.
 * Canonical check value: crc16("123456789") == 0x29B1.
 *
 * This is the wire-integrity contract: it must agree byte-for-byte with the
 * host codec (software/control/protocol_v2/crc16.py). Pure C++, no Arduino
 * dependencies, so it is included directly in native unit tests.
 */

#ifndef PROTOCOL_CRC16_H
#define PROTOCOL_CRC16_H

#include <stddef.h>
#include <stdint.h>

namespace protocol {

/**
 * Calculate CRC-16/CCITT-FALSE over a data buffer.
 *
 * @param data   Pointer to data buffer (may be null iff length == 0)
 * @param length Number of bytes to process
 * @return 16-bit CRC value (0xFFFF for an empty buffer)
 */
uint16_t crc16_ccitt(const uint8_t* data, size_t length);

}  // namespace protocol

#endif  // PROTOCOL_CRC16_H
