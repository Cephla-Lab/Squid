#include <unity.h>

#include <stdint.h>
#include <string.h>
#include <vector>

#include "protocol/frames.h"
#include "protocol/framer.h"

// Include sources directly for native tests.
#include "protocol/cobs.cpp"
#include "protocol/crc16.cpp"
#include "protocol/framer.cpp"

using protocol::ByteSink;
using protocol::Framer;
using protocol::FrameSink;

// --- Test doubles ---------------------------------------------------------

class TestSink : public FrameSink {
public:
    std::vector<std::vector<uint8_t>> frames;
    void on_frame(const uint8_t* frame, size_t len) override {
        frames.emplace_back(frame, frame + len);
    }
};

class TestByteSink : public ByteSink {
public:
    size_t avail = (size_t)1 << 30;  // effectively unlimited by default
    std::vector<uint8_t> written;
    size_t writable() override { return avail; }
    void write(const uint8_t* b, size_t n) override {
        written.insert(written.end(), b, b + n);
    }
};

// --- Helpers --------------------------------------------------------------

static void feed_bytes(Framer& f, const uint8_t* b, size_t n) {
    for (size_t i = 0; i < n; ++i) {
        f.feed_rx(b[i]);
    }
}

// Assemble header+payload+CRC and COBS-encode into `out`. Returns encoded len.
static size_t make_encoded_frame(const uint8_t* frame, size_t len, uint8_t* out, size_t out_cap) {
    uint8_t dec[protocol::kMaxFrame];
    memcpy(dec, frame, len);
    uint16_t crc = protocol::crc16_ccitt(frame, len);
    dec[len] = (uint8_t)(crc & 0xFF);
    dec[len + 1] = (uint8_t)(crc >> 8);
    return protocol::cobs_encode(dec, len + 2, out, out_cap);
}

// Simple deterministic PRNG (fixed seed) for the corruption sweep.
struct Lcg {
    uint32_t s;
    uint32_t next() {
        s = s * 1103515245u + 12345u;
        return (s >> 16) & 0x7FFF;
    }
};

void setUp(void) {}
void tearDown(void) {}

// --- (a) happy path -------------------------------------------------------

void test_happy_path_exact_bytes(void) {
    TestSink sink;
    TestByteSink out;
    Framer f(sink, out);

    const uint8_t frame[] = {protocol::RESPONSE, 0x2A, protocol::GET_STATE, 0x00, 0x10, 0x20, 0x30};
    uint8_t enc[64];
    size_t n = make_encoded_frame(frame, sizeof(frame), enc, sizeof(enc));
    feed_bytes(f, enc, n);
    f.feed_rx(0x00);  // delimiter

    TEST_ASSERT_EQUAL_UINT32(1, f.counters().frames_ok);
    TEST_ASSERT_EQUAL_size_t(1, sink.frames.size());
    TEST_ASSERT_EQUAL_size_t(sizeof(frame), sink.frames[0].size());
    TEST_ASSERT_EQUAL_MEMORY(frame, sink.frames[0].data(), sizeof(frame));
}

// --- (b) back-to-back frames ---------------------------------------------

void test_back_to_back_frames(void) {
    TestSink sink;
    TestByteSink out;
    Framer f(sink, out);

    const uint8_t f1[] = {protocol::RESPONSE, 1, protocol::HELLO, 0, 0xAA};
    const uint8_t f2[] = {protocol::RESPONSE, 2, protocol::DIAG, 0, 0xBB, 0xCC, 0xDD};
    uint8_t e1[64], e2[64];
    size_t n1 = make_encoded_frame(f1, sizeof(f1), e1, sizeof(e1));
    size_t n2 = make_encoded_frame(f2, sizeof(f2), e2, sizeof(e2));

    feed_bytes(f, e1, n1);
    f.feed_rx(0x00);
    feed_bytes(f, e2, n2);
    f.feed_rx(0x00);

    TEST_ASSERT_EQUAL_UINT32(2, f.counters().frames_ok);
    TEST_ASSERT_EQUAL_size_t(2, sink.frames.size());
    TEST_ASSERT_EQUAL_MEMORY(f1, sink.frames[0].data(), sizeof(f1));
    TEST_ASSERT_EQUAL_MEMORY(f2, sink.frames[1].data(), sizeof(f2));
}

// --- (c) corrupted CRC dropped, next frame recovers -----------------------

void test_corrupted_crc_then_recover(void) {
    TestSink sink;
    TestByteSink out;
    Framer f(sink, out);

    // Structurally valid COBS but a deliberately wrong CRC.
    const uint8_t frame[] = {protocol::RESPONSE, 1, protocol::GET_STATE, 0, 0xAA, 0xBB};
    uint8_t dec[protocol::kMaxFrame];
    memcpy(dec, frame, sizeof(frame));
    uint16_t bad = (uint16_t)(protocol::crc16_ccitt(frame, sizeof(frame)) ^ 0xFFFF);
    dec[sizeof(frame)] = (uint8_t)(bad & 0xFF);
    dec[sizeof(frame) + 1] = (uint8_t)(bad >> 8);
    uint8_t enc[64];
    size_t n = protocol::cobs_encode(dec, sizeof(frame) + 2, enc, sizeof(enc));

    feed_bytes(f, enc, n);
    f.feed_rx(0x00);
    TEST_ASSERT_EQUAL_UINT32(1, f.counters().crc_err);
    TEST_ASSERT_EQUAL_UINT32(0, f.counters().frames_ok);
    TEST_ASSERT_EQUAL_size_t(0, sink.frames.size());

    // A subsequent valid frame is still received.
    uint8_t good[64];
    size_t gn = make_encoded_frame(frame, sizeof(frame), good, sizeof(good));
    feed_bytes(f, good, gn);
    f.feed_rx(0x00);
    TEST_ASSERT_EQUAL_UINT32(1, f.counters().frames_ok);
    TEST_ASSERT_EQUAL_size_t(1, sink.frames.size());
    TEST_ASSERT_EQUAL_MEMORY(frame, sink.frames[0].data(), sizeof(frame));
}

// --- (d) truncated frame -> resync, next frame recovers -------------------

void test_truncated_resync_then_recover(void) {
    TestSink sink;
    TestByteSink out;
    Framer f(sink, out);

    // Code byte 0x04 promises 3 data bytes but only 2 arrive before delimiter.
    const uint8_t bad[] = {0x04, 0x11, 0x22};
    feed_bytes(f, bad, sizeof(bad));
    f.feed_rx(0x00);
    TEST_ASSERT_EQUAL_UINT32(1, f.counters().resync);
    TEST_ASSERT_EQUAL_UINT32(0, f.counters().crc_err);
    TEST_ASSERT_EQUAL_UINT32(0, f.counters().frames_ok);

    const uint8_t frame[] = {protocol::RESPONSE, 7, protocol::GET_INFO, 0, 0x01};
    uint8_t enc[64];
    size_t n = make_encoded_frame(frame, sizeof(frame), enc, sizeof(enc));
    feed_bytes(f, enc, n);
    f.feed_rx(0x00);
    TEST_ASSERT_EQUAL_UINT32(1, f.counters().frames_ok);
    TEST_ASSERT_EQUAL_MEMORY(frame, sink.frames[0].data(), sizeof(frame));
}

// --- (e) garbage burst then valid frame -----------------------------------

void test_garbage_burst_then_valid(void) {
    TestSink sink;
    TestByteSink out;
    Framer f(sink, out);

    uint8_t garbage[50];
    for (size_t i = 0; i < sizeof(garbage); ++i) {
        garbage[i] = (uint8_t)((i % 254) + 1);  // never 0x00
    }
    feed_bytes(f, garbage, sizeof(garbage));
    f.feed_rx(0x00);  // terminate the garbage as one (malformed) frame

    const uint8_t frame[] = {protocol::RESPONSE, 9, protocol::GET_STATE, 0, 0x55, 0x66};
    uint8_t enc[64];
    size_t n = make_encoded_frame(frame, sizeof(frame), enc, sizeof(enc));
    feed_bytes(f, enc, n);
    f.feed_rx(0x00);

    // The valid frame arrives; at most the garbage "frame" was lost.
    TEST_ASSERT_EQUAL_UINT32(1, f.counters().frames_ok);
    TEST_ASSERT_EQUAL_size_t(1, sink.frames.size());
    TEST_ASSERT_EQUAL_MEMORY(frame, sink.frames[0].data(), sizeof(frame));
    uint32_t dropped = f.counters().resync + f.counters().crc_err + f.counters().rx_overflow;
    TEST_ASSERT_TRUE_MESSAGE(dropped >= 1, "garbage burst should register as a drop");
}

// --- (f) oversize frame dropped, next frame recovers ----------------------

void test_oversize_dropped_then_recover(void) {
    TestSink sink;
    TestByteSink out;
    Framer f(sink, out);

    // 520 non-zero bytes encode to > kBufCap, tripping the accumulation guard.
    uint8_t big[520];
    for (size_t i = 0; i < sizeof(big); ++i) {
        big[i] = (uint8_t)((i % 254) + 1);
    }
    uint8_t enc[600];
    size_t n = protocol::cobs_encode(big, sizeof(big), enc, sizeof(enc));
    feed_bytes(f, enc, n);
    f.feed_rx(0x00);

    TEST_ASSERT_TRUE_MESSAGE(f.counters().rx_overflow >= 1, "oversize frame should count rx_overflow");
    TEST_ASSERT_EQUAL_UINT32(0, f.counters().frames_ok);
    TEST_ASSERT_EQUAL_size_t(0, sink.frames.size());

    const uint8_t frame[] = {protocol::RESPONSE, 3, protocol::GET_STATE, 0, 0x77};
    uint8_t gen[64];
    size_t gn = make_encoded_frame(frame, sizeof(frame), gen, sizeof(gen));
    feed_bytes(f, gen, gn);
    f.feed_rx(0x00);
    TEST_ASSERT_EQUAL_UINT32(1, f.counters().frames_ok);
    TEST_ASSERT_EQUAL_MEMORY(frame, sink.frames[0].data(), sizeof(frame));
}

// --- (g) non-blocking TX --------------------------------------------------

void test_tx_blocked_returns_false_then_sends(void) {
    TestSink sink;
    TestByteSink out;
    Framer f(sink, out);

    const uint8_t frame[] = {protocol::REQUEST, 5, protocol::GET_STATE, 0};

    out.avail = 0;
    bool ok = f.send_frame(frame, sizeof(frame));
    TEST_ASSERT_FALSE(ok);
    TEST_ASSERT_EQUAL_UINT32(1, f.counters().tx_drop);
    TEST_ASSERT_EQUAL_size_t(0, out.written.size());

    out.avail = 1024;
    ok = f.send_frame(frame, sizeof(frame));
    TEST_ASSERT_TRUE(ok);
    TEST_ASSERT_EQUAL_UINT32(1, f.counters().tx_drop);  // unchanged
    TEST_ASSERT_TRUE(out.written.size() > 0);
    TEST_ASSERT_EQUAL_UINT8(0x00, out.written.back());  // delimiter is last

    // Round-trip: the emitted wire bytes decode back to the original frame.
    TestSink sink2;
    TestByteSink out2;
    Framer g(sink2, out2);
    feed_bytes(g, out.written.data(), out.written.size());
    TEST_ASSERT_EQUAL_UINT32(1, g.counters().frames_ok);
    TEST_ASSERT_EQUAL_size_t(1, sink2.frames.size());
    TEST_ASSERT_EQUAL_size_t(sizeof(frame), sink2.frames[0].size());
    TEST_ASSERT_EQUAL_MEMORY(frame, sink2.frames[0].data(), sizeof(frame));
}

// --- (h) deterministic corruption sweep: <= 1 frame lost per corruption ---

void test_corruption_sweep_single_byte(void) {
    const int N = 200;
    Lcg rng{0xC0FFEEu};

    std::vector<uint8_t> stream;
    std::vector<size_t> offs(N), elens(N);

    for (int i = 0; i < N; ++i) {
        uint8_t frame[64];
        frame[0] = protocol::RESPONSE;
        frame[1] = (uint8_t)i;
        frame[2] = protocol::GET_STATE;
        frame[3] = 0;
        size_t plen = rng.next() % 40;  // 0..39
        for (size_t k = 0; k < plen; ++k) {
            frame[4 + k] = (uint8_t)(rng.next() & 0xFF);
        }
        size_t flen = 4 + plen;
        uint8_t enc[128];
        size_t n = make_encoded_frame(frame, flen, enc, sizeof(enc));
        offs[i] = stream.size();
        elens[i] = n;
        stream.insert(stream.end(), enc, enc + n);
        stream.push_back(0x00);  // delimiter (never corrupted)
    }

    // Sanity: the clean stream fully decodes.
    {
        TestSink s;
        TestByteSink o;
        Framer f(s, o);
        feed_bytes(f, stream.data(), stream.size());
        TEST_ASSERT_EQUAL_UINT32((uint32_t)N, f.counters().frames_ok);
    }

    // Every 7th frame, corrupt each byte position in turn; each corruption may
    // lose at most that one frame.
    int corrupted_runs = 0;
    for (int i = 0; i < N; i += 7) {
        for (size_t p = 0; p < elens[i]; ++p) {
            size_t idx = offs[i] + p;
            uint8_t mask = (uint8_t)((rng.next() % 255) + 1);  // 1..255 (always changes)
            stream[idx] ^= mask;

            TestSink s;
            TestByteSink o;
            Framer f(s, o);
            feed_bytes(f, stream.data(), stream.size());
            TEST_ASSERT_TRUE_MESSAGE(f.counters().frames_ok >= (uint32_t)(N - 1),
                                     "single-byte corruption lost more than one frame");

            stream[idx] ^= mask;  // restore
            ++corrupted_runs;
        }
    }
    TEST_ASSERT_TRUE(corrupted_runs > 0);
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_happy_path_exact_bytes);
    RUN_TEST(test_back_to_back_frames);
    RUN_TEST(test_corrupted_crc_then_recover);
    RUN_TEST(test_truncated_resync_then_recover);
    RUN_TEST(test_garbage_burst_then_valid);
    RUN_TEST(test_oversize_dropped_then_recover);
    RUN_TEST(test_tx_blocked_returns_false_then_sends);
    RUN_TEST(test_corruption_sweep_single_byte);
    return UNITY_END();
}
