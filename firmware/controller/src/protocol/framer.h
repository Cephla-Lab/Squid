/**
 * COBS frame pump for protocol v2 — RX byte accumulation + TX framing.
 *
 * RX: bytes are accumulated until a 0x00 delimiter, then COBS-decoded and
 * CRC-16 checked; a valid frame (header + payload, WITHOUT the trailing CRC)
 * is delivered to a FrameSink. Because 0x00 never occurs inside an encoded
 * frame, every delimiter is a clean resync point: a single corrupted byte can
 * damage only the frame it lands in — never a neighbour.
 *
 * TX: send_frame() appends CRC-16, COBS-encodes, and emits frame + 0x00 to a
 * ByteSink. It never blocks: if the sink lacks room for the whole wire frame
 * it drops the frame and increments tx_drop.
 *
 * Fixed buffers, no heap, no Arduino. IO and the frame sink are injected so
 * the whole class is native-testable.
 */

#ifndef PROTOCOL_FRAMER_H
#define PROTOCOL_FRAMER_H

#include <stddef.h>
#include <stdint.h>

#include "protocol/frames.h"

namespace protocol {

struct FramerCounters {
    uint32_t crc_err;      // decoded OK but CRC mismatch
    uint32_t resync;       // malformed COBS / runt frame
    uint32_t rx_overflow;  // frame exceeded the max size (encoded or decoded)
    uint32_t tx_drop;      // send dropped because the TX sink was full
    uint32_t frames_ok;    // valid frames delivered to the sink
};

// Receives decoded, CRC-valid frames (header + payload, no CRC).
class FrameSink {
public:
    virtual ~FrameSink() {}
    virtual void on_frame(const uint8_t* frame, size_t len) = 0;
};

// Byte-oriented output (maps to Serial.availableForWrite()/write() in Phase C).
class ByteSink {
public:
    virtual ~ByteSink() {}
    virtual size_t writable() = 0;                       // free space, in bytes
    virtual void write(const uint8_t* b, size_t n) = 0;  // caller guarantees room
};

class Framer {
public:
    Framer(FrameSink& sink, ByteSink& out);

    // Feed one received byte. On a 0x00 delimiter, decode + CRC-check the
    // accumulated frame and, if valid, deliver it to the FrameSink.
    void feed_rx(uint8_t byte);

    // Append CRC-16, COBS-encode, and emit frame + 0x00 to the ByteSink.
    // Returns false (and increments tx_drop) if the sink lacks room for the
    // whole wire frame, or if frame + CRC would exceed the protocol max.
    // Never blocks. `len` is the header + payload length (no CRC).
    bool send_frame(const uint8_t* frame, size_t len);

    const FramerCounters& counters() const { return counters_; }

    // Worst-case COBS-encoded size of a max decoded frame (kMaxFrame).
    static const size_t kBufCap = kMaxFrame + 1 + (kMaxFrame + 253) / 254;  // 516

private:
    void process_frame(const uint8_t* enc, size_t enc_len);

    FrameSink& sink_;
    ByteSink& out_;
    FramerCounters counters_;

    uint8_t rx_buf_[kBufCap];  // encoded bytes accumulated since last delimiter
    size_t rx_len_;
    bool rx_overflowed_;        // discarding the current oversize frame

    uint8_t dec_buf_[kBufCap];  // RX decode scratch (sized to detect > kMaxFrame)
    uint8_t tx_dec_[kMaxFrame]; // TX: frame + CRC before encoding
    uint8_t tx_enc_[kBufCap];   // TX: COBS-encoded output
};

}  // namespace protocol

#endif  // PROTOCOL_FRAMER_H
