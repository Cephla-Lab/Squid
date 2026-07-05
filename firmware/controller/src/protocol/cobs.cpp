/**
 * Standard COBS (Cheshire & Baker) encode/decode, no heap.
 * See cobs.h for the contract.
 */

#include "protocol/cobs.h"

namespace protocol {

size_t cobs_max_encoded_len(size_t len) {
    // One code byte per block of up to 254 data bytes, plus a possible
    // trailing block: len + 1 + ceil(len / 254).
    return len + 1 + (len + 253) / 254;
}

size_t cobs_encode(const uint8_t* in, size_t len, uint8_t* out, size_t out_cap) {
    if (out_cap == 0) {
        return 0;  // no room even for the first code byte
    }

    size_t read = 0;
    size_t write = 1;       // out[0..] reserved for the running code byte
    size_t code_idx = 0;    // position of the code byte for the current block
    uint8_t code = 1;       // 1 + number of non-zero bytes in the current block

    while (read < len) {
        uint8_t b = in[read++];
        if (b == 0) {
            out[code_idx] = code;   // close the current block
            code_idx = write;       // reserve next code byte
            code = 1;
            if (write >= out_cap) {
                return 0;
            }
            write++;
        } else {
            if (write >= out_cap) {
                return 0;
            }
            out[write++] = b;
            code++;
            if (code == 0xFF) {     // block full (254 data bytes)
                out[code_idx] = code;
                code_idx = write;
                code = 1;
                if (write >= out_cap) {
                    return 0;
                }
                write++;
            }
        }
    }

    out[code_idx] = code;  // close the final block
    return write;
}

int32_t cobs_decode(const uint8_t* in, size_t len, uint8_t* out, size_t out_cap) {
    size_t read = 0;
    size_t write = 0;

    while (read < len) {
        uint8_t code = in[read++];
        if (code == 0) {
            return -1;  // 0x00 must never appear inside an encoded block
        }

        // Copy (code - 1) literal data bytes.
        for (uint8_t i = 1; i < code; ++i) {
            if (read >= len) {
                return -1;  // code points past end of input (truncated)
            }
            uint8_t b = in[read++];
            if (b == 0) {
                return -1;  // embedded zero inside the block
            }
            if (write >= out_cap) {
                return -1;  // decoded output would overflow
            }
            out[write++] = b;
        }

        // A block shorter than 0xFF encodes an implicit trailing zero, except
        // for the final block (when the input is exhausted).
        if (code != 0xFF && read < len) {
            if (write >= out_cap) {
                return -1;
            }
            out[write++] = 0;
        }
    }

    return (int32_t)write;
}

}  // namespace protocol
