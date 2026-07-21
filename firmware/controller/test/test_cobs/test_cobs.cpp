#include <unity.h>

#include <stdint.h>
#include <string.h>

#include "protocol/cobs.h"

// Include source directly for native tests.
#include "protocol/cobs.cpp"

using protocol::cobs_decode;
using protocol::cobs_encode;
using protocol::cobs_max_encoded_len;

void setUp(void) {}
void tearDown(void) {}

// Encode `in`, assert the output has no 0x00 and respects the overhead bound,
// then decode and assert the result equals the original bytes.
static void assert_roundtrip(const uint8_t* in, size_t len) {
    uint8_t enc[600];
    uint8_t dec[600];

    size_t n = cobs_encode(in, len, enc, sizeof(enc));
    TEST_ASSERT_TRUE_MESSAGE(n > 0, "encode returned 0 (buffer too small?)");
    TEST_ASSERT_TRUE_MESSAGE(n <= cobs_max_encoded_len(len), "encoded exceeds max bound");

    // Overhead bound: n <= len + 1 + ceil(len/254).
    size_t max_overhead = 1 + (len + 253) / 254;
    TEST_ASSERT_TRUE_MESSAGE(n <= len + max_overhead, "overhead exceeds 1 + len/254");

    // No zero bytes in the encoded stream.
    for (size_t i = 0; i < n; ++i) {
        TEST_ASSERT_NOT_EQUAL_MESSAGE(0x00, enc[i], "encoded output contains a 0x00 byte");
    }

    int32_t d = cobs_decode(enc, n, dec, sizeof(dec));
    TEST_ASSERT_EQUAL_INT32_MESSAGE((int32_t)len, d, "decoded length mismatch");
    if (len > 0) {
        TEST_ASSERT_EQUAL_MEMORY_MESSAGE(in, dec, len, "decoded bytes differ from original");
    }
}

// --- Round-trip across representative lengths -----------------------------

void test_roundtrip_empty(void) {
    assert_roundtrip(nullptr, 0);
}

void test_roundtrip_various_lengths_all_nonzero(void) {
    const size_t lengths[] = {1, 2, 253, 254, 255, 506};
    uint8_t buf[506];
    for (size_t li = 0; li < sizeof(lengths) / sizeof(lengths[0]); ++li) {
        size_t len = lengths[li];
        for (size_t i = 0; i < len; ++i) {
            buf[i] = (uint8_t)((i % 255) + 1);  // never 0
        }
        assert_roundtrip(buf, len);
    }
}

void test_roundtrip_zeros_head_middle_tail(void) {
    const size_t lengths[] = {1, 2, 253, 254, 255, 506};
    uint8_t buf[506];
    for (size_t li = 0; li < sizeof(lengths) / sizeof(lengths[0]); ++li) {
        size_t len = lengths[li];
        for (size_t i = 0; i < len; ++i) {
            buf[i] = (uint8_t)((i % 255) + 1);
        }
        buf[0] = 0x00;                 // head
        buf[len / 2] = 0x00;           // middle
        buf[len - 1] = 0x00;           // tail
        assert_roundtrip(buf, len);
    }
}

void test_roundtrip_all_zeros(void) {
    uint8_t buf[300];
    memset(buf, 0, sizeof(buf));
    assert_roundtrip(buf, 300);
}

// --- max_encoded_len ------------------------------------------------------

void test_max_encoded_len(void) {
    // Function is defined as len + 1 + ceil(len/254) (a safe upper bound).
    TEST_ASSERT_EQUAL_size_t(1, cobs_max_encoded_len(0));
    TEST_ASSERT_EQUAL_size_t(3, cobs_max_encoded_len(1));
    TEST_ASSERT_EQUAL_size_t(256, cobs_max_encoded_len(254));
    TEST_ASSERT_EQUAL_size_t(509, cobs_max_encoded_len(506));
}

// --- encode capacity ------------------------------------------------------

void test_encode_returns_zero_when_out_too_small(void) {
    uint8_t in[10] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10};
    uint8_t out[4];  // needs 11, only 4 available
    TEST_ASSERT_EQUAL_size_t(0, cobs_encode(in, sizeof(in), out, sizeof(out)));
}

// --- decode rejections ----------------------------------------------------

void test_decode_rejects_embedded_zero(void) {
    // A valid encoding of {0x41,0x42} is {0x03,0x41,0x42}. Inject a 0x00.
    uint8_t bad[] = {0x03, 0x41, 0x00};
    uint8_t out[16];
    TEST_ASSERT_EQUAL_INT32(-1, cobs_decode(bad, sizeof(bad), out, sizeof(out)));
}

void test_decode_rejects_truncated(void) {
    // Code byte 0x05 promises 4 data bytes but only 2 follow.
    uint8_t bad[] = {0x05, 0x41, 0x42};
    uint8_t out[16];
    TEST_ASSERT_EQUAL_INT32(-1, cobs_decode(bad, sizeof(bad), out, sizeof(out)));
}

void test_decode_rejects_code_past_end(void) {
    // Single code byte pointing far past the end of input.
    uint8_t bad[] = {0xFF};
    uint8_t out[16];
    TEST_ASSERT_EQUAL_INT32(-1, cobs_decode(bad, sizeof(bad), out, sizeof(out)));
}

void test_decode_rejects_output_overflow(void) {
    uint8_t in[10] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10};
    uint8_t enc[32];
    size_t n = cobs_encode(in, sizeof(in), enc, sizeof(enc));
    uint8_t small_out[3];
    TEST_ASSERT_EQUAL_INT32(-1, cobs_decode(enc, n, small_out, sizeof(small_out)));
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_roundtrip_empty);
    RUN_TEST(test_roundtrip_various_lengths_all_nonzero);
    RUN_TEST(test_roundtrip_zeros_head_middle_tail);
    RUN_TEST(test_roundtrip_all_zeros);
    RUN_TEST(test_max_encoded_len);
    RUN_TEST(test_encode_returns_zero_when_out_too_small);
    RUN_TEST(test_decode_rejects_embedded_zero);
    RUN_TEST(test_decode_rejects_truncated);
    RUN_TEST(test_decode_rejects_code_past_end);
    RUN_TEST(test_decode_rejects_output_overflow);
    return UNITY_END();
}
