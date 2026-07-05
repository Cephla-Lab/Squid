/**
 * CRC-16/CCITT-FALSE for protocol v2 framing.
 *
 * Polynomial 0x1021, initial value 0xFFFF, no reflection, no final XOR
 * (CRC-16/CCITT-FALSE, aka CRC-16/IBM-3740). Table-driven.
 *
 * Ported verbatim from the protocol-v2-phase1 branch (commit da8e2af0,
 * firmware/controller/src/utils/crc16). Pure C++, no Arduino dependencies.
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
