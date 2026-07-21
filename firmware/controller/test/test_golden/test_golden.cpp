#include <unity.h>

#include <stdint.h>
#include <string.h>

#include "protocol/cobs.h"
#include "protocol/crc16.h"
#include "protocol/frames.h"  // kMaxFrame

// Include sources directly for native tests.
#include "protocol/cobs.cpp"
#include "protocol/crc16.cpp"

// Generated golden vectors (by software/tools/gen_protocol_golden.py).
#include "golden_cases.h"

using protocol::cobs_decode;
using protocol::cobs_encode;
using protocol::crc16_ccitt;

void setUp(void) {}
void tearDown(void) {}

// Encode header+payload+CRC via COBS and confirm the wire bytes match the
// Python-generated golden vector byte-for-byte.
void test_golden_encode_matches(void) {
    for (size_t c = 0; c < kNumGoldenCases; ++c) {
        const GoldenCase& g = kGoldenCases[c];

        uint8_t decoded[protocol::kMaxFrame];
        decoded[0] = g.type;
        decoded[1] = g.cmd_id;
        decoded[2] = g.cmd_type;
        decoded[3] = g.flags;
        memcpy(decoded + 4, g.payload, g.payload_len);
        size_t frame_len = 4 + g.payload_len;

        uint16_t crc = crc16_ccitt(decoded, frame_len);
        decoded[frame_len] = (uint8_t)(crc & 0xFF);
        decoded[frame_len + 1] = (uint8_t)(crc >> 8);
        size_t decoded_len = frame_len + 2;

        uint8_t enc[protocol::kMaxFrame + 8];
        size_t enc_len = cobs_encode(decoded, decoded_len, enc, sizeof(enc));
        TEST_ASSERT_TRUE_MESSAGE(enc_len > 0, g.name);

        // Wire = encoded + 0x00 delimiter.
        TEST_ASSERT_EQUAL_size_t_MESSAGE(g.wire_len, enc_len + 1, g.name);
        TEST_ASSERT_EQUAL_MEMORY_MESSAGE(g.wire, enc, enc_len, g.name);
        TEST_ASSERT_EQUAL_UINT8_MESSAGE(0x00, g.wire[enc_len], g.name);
    }
}

// Decode each golden wire vector and confirm it reproduces the frame + a valid
// CRC — the mirror of the Python decode path.
void test_golden_decode_matches(void) {
    for (size_t c = 0; c < kNumGoldenCases; ++c) {
        const GoldenCase& g = kGoldenCases[c];

        // Strip the trailing 0x00 delimiter before COBS-decoding.
        uint8_t dec[protocol::kMaxFrame];
        int32_t dec_len = cobs_decode(g.wire, g.wire_len - 1, dec, sizeof(dec));
        TEST_ASSERT_TRUE_MESSAGE(dec_len >= 6, g.name);

        // Header echoes the case.
        TEST_ASSERT_EQUAL_UINT8_MESSAGE(g.type, dec[0], g.name);
        TEST_ASSERT_EQUAL_UINT8_MESSAGE(g.cmd_id, dec[1], g.name);
        TEST_ASSERT_EQUAL_UINT8_MESSAGE(g.cmd_type, dec[2], g.name);
        TEST_ASSERT_EQUAL_UINT8_MESSAGE(g.flags, dec[3], g.name);

        // Payload matches.
        TEST_ASSERT_EQUAL_size_t_MESSAGE(g.payload_len, (size_t)dec_len - 6, g.name);
        if (g.payload_len > 0) {
            TEST_ASSERT_EQUAL_MEMORY_MESSAGE(g.payload, dec + 4, g.payload_len, g.name);
        }

        // CRC over the frame (header + payload) validates.
        size_t body = (size_t)dec_len - 2;
        uint16_t rx_crc = (uint16_t)(dec[body] | ((uint16_t)dec[body + 1] << 8));
        TEST_ASSERT_EQUAL_HEX16_MESSAGE(crc16_ccitt(dec, body), rx_crc, g.name);
    }
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_golden_encode_matches);
    RUN_TEST(test_golden_decode_matches);
    return UNITY_END();
}
