/**
 * Consistent Overhead Byte Stuffing (COBS) codec for protocol v2.
 *
 * Removes all 0x00 bytes from a frame so that 0x00 can serve as an
 * unambiguous frame delimiter on the wire. Pure C++, no heap, no Arduino.
 *
 * Standard COBS (Cheshire & Baker): the encoder emits blocks of 1 code byte
 * plus up to 254 non-zero data bytes; the worst case adds one overhead byte
 * per 254 data bytes plus a possible trailing block.
 */

#ifndef PROTOCOL_COBS_H
#define PROTOCOL_COBS_H

#include <stddef.h>
#include <stdint.h>

namespace protocol {

// Upper bound on the encoded length for a payload of `len` bytes
// (excludes the trailing 0x00 delimiter the framer appends).
size_t cobs_max_encoded_len(size_t len);

// Encodes `len` bytes from `in` into `out`. Returns the encoded length, or 0
// if `out_cap` is too small. The output never contains a 0x00 byte.
size_t cobs_encode(const uint8_t* in, size_t len, uint8_t* out, size_t out_cap);

// Decodes `len` COBS bytes from `in` into `out`. Returns the decoded length,
// or -1 on malformed input: an embedded 0x00, a code byte pointing past the
// end (truncated), or an output that would exceed `out_cap`.
int32_t cobs_decode(const uint8_t* in, size_t len, uint8_t* out, size_t out_cap);

}  // namespace protocol

#endif  // PROTOCOL_COBS_H
