/**
 * libFuzzer harness for the protocol-v2 RX -> dispatch -> TX path.
 *
 * Arbitrary bytes are fed into a Framer whose FrameSink is a Dispatcher backed
 * by fake state; the dispatcher's responses are re-framed back out. This
 * exercises COBS decode, CRC check, command dispatch, slot management, and
 * COBS encode against untrusted input. ASAN + libFuzzer assert no crash / UB.
 *
 * CI runs the coverage-guided libFuzzer build. Locally (Apple clang ships no
 * libFuzzer runtime) build with -DFUZZ_STANDALONE for an ASAN smoke driver,
 * linking fuzz_framer.cpp against the src/protocol sources under
 * -fsanitize=address -I src.
 */

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "protocol/dispatch_v2.h"
#include "protocol/frames.h"
#include "protocol/framer.h"
#include "protocol/slots.h"

using namespace protocol;

namespace {

class ZeroProvider : public StateProvider {
public:
    void fill_state(StandardResponse&) override {}
    void fill_hello(HelloPayload& h) override { memset(&h, 0, sizeof(h)); }
    void fill_info(InfoPayload& i) override { memset(&i, 0, sizeof(i)); }
    void fill_diag_page0(DiagPayload& d) override { memset(&d, 0, sizeof(d)); }
    uint8_t fill_diag_faults(uint8_t, FaultEntryWire*, uint8_t) override { return 0; }
};

class NullByteSink : public ByteSink {
public:
    size_t avail;
    NullByteSink() : avail(4096) {}
    size_t writable() override { return avail; }
    void write(const uint8_t*, size_t) override {}
};

void noop_handler(Dispatcher&, const uint8_t*, size_t, ResponseWriter&) {}

}  // namespace

extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
    ZeroProvider provider;
    SlotManager slots;
    Dispatcher dispatcher(slots, provider);
    dispatcher.register_system_commands();
    dispatcher.register_command(0x01, false, 0, 64, noop_handler);  // a slotted command

    NullByteSink out;
    Framer framer(dispatcher, out);
    dispatcher.set_framer(framer);

    // Let the input steer TX backpressure so send_frame's drop path is reached.
    if (size > 0) {
        out.avail = (data[0] & 1) ? 0 : 4096;
    }
    for (size_t i = 0; i < size; ++i) {
        framer.feed_rx(data[i]);
    }
    return 0;
}

#ifdef FUZZ_STANDALONE
// Local ASAN smoke driver (no libFuzzer runtime on Apple clang).
#include <stdio.h>

#include "protocol/cobs.h"
#include "protocol/crc16.h"

namespace {

uint32_t g_lcg = 0x1234567u;

uint32_t lcg_next() {
    g_lcg = g_lcg * 1103515245u + 12345u;
    return (g_lcg >> 8) & 0xFFFFFF;
}

void feed_valid_and_corruptions(uint8_t type, uint8_t id, uint8_t ct, uint8_t fl,
                                const uint8_t* pl, size_t pn) {
    uint8_t frame[128];
    frame[0] = type;
    frame[1] = id;
    frame[2] = ct;
    frame[3] = fl;
    if (pn) {
        memcpy(frame + 4, pl, pn);
    }
    size_t flen = 4 + pn;
    uint16_t crc = crc16_ccitt(frame, flen);
    frame[flen] = (uint8_t)(crc & 0xFF);
    frame[flen + 1] = (uint8_t)(crc >> 8);

    uint8_t wire[160];
    size_t enc = cobs_encode(frame, flen + 2, wire, sizeof(wire));
    wire[enc] = 0x00;
    size_t wlen = enc + 1;

    LLVMFuzzerTestOneInput(wire, wlen);
    for (size_t p = 0; p < wlen; ++p) {  // single-byte corruptions
        uint8_t save = wire[p];
        wire[p] ^= 0xFF;
        LLVMFuzzerTestOneInput(wire, wlen);
        wire[p] = save;
    }
    uint8_t dbl[320];  // back-to-back frames
    memcpy(dbl, wire, wlen);
    memcpy(dbl + wlen, wire, wlen);
    LLVMFuzzerTestOneInput(dbl, 2 * wlen);
}

}  // namespace

int main() {
    LLVMFuzzerTestOneInput(nullptr, 0);

    uint8_t buf[640];
    for (int iter = 0; iter < 100000; ++iter) {
        size_t n = lcg_next() % (sizeof(buf) + 1);
        for (size_t i = 0; i < n; ++i) {
            buf[i] = (uint8_t)lcg_next();
        }
        LLVMFuzzerTestOneInput(buf, n);
    }

    const uint8_t p_diag[] = {0x00};
    const uint8_t p_cmd[] = {0x11, 0x22, 0x33};
    feed_valid_and_corruptions(REQUEST, 1, GET_STATE, 0, nullptr, 0);
    feed_valid_and_corruptions(REQUEST, 2, HELLO, 0, nullptr, 0);
    feed_valid_and_corruptions(REQUEST, 3, DIAG, 0, p_diag, sizeof(p_diag));
    feed_valid_and_corruptions(REQUEST, 4, 0x01, 0, p_cmd, sizeof(p_cmd));
    feed_valid_and_corruptions(REQUEST, 4, 0x01, FLAG_RETRY, p_cmd, sizeof(p_cmd));

    printf("standalone fuzz driver: OK (no crash / no ASAN finding)\n");
    return 0;
}
#endif  // FUZZ_STANDALONE
