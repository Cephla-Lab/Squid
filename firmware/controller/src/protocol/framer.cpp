/**
 * COBS frame pump implementation. See framer.h for the contract.
 */

#include "protocol/framer.h"

#include "protocol/cobs.h"
#include "protocol/crc16.h"

namespace protocol {

Framer::Framer(FrameSink& sink, ByteSink& out)
    : sink_(sink), out_(out), counters_(), rx_len_(0), rx_overflowed_(false) {}

void Framer::feed_rx(uint8_t byte) {
    if (byte == 0x00) {
        // Delimiter: end of the current frame (or an idle/duplicate delimiter).
        if (rx_overflowed_) {
            // The oversize frame was already counted; resync on this boundary.
            rx_len_ = 0;
            rx_overflowed_ = false;
            return;
        }
        if (rx_len_ != 0) {
            process_frame(rx_buf_, rx_len_);
        }
        rx_len_ = 0;
        return;
    }

    if (rx_overflowed_) {
        return;  // discard the rest of the oversize frame until the delimiter
    }
    if (rx_len_ >= kBufCap) {
        // Frame exceeds the worst-case encoded size: too big to be valid.
        counters_.rx_overflow++;
        rx_overflowed_ = true;
        return;
    }
    rx_buf_[rx_len_++] = byte;
}

void Framer::process_frame(const uint8_t* enc, size_t enc_len) {
    int32_t dec_len = cobs_decode(enc, enc_len, dec_buf_, sizeof(dec_buf_));
    if (dec_len < 0) {
        counters_.resync++;  // malformed COBS structure
        return;
    }
    if ((size_t)dec_len > kMaxFrame) {
        counters_.rx_overflow++;  // decoded frame exceeds the protocol max
        return;
    }
    if ((size_t)dec_len < sizeof(FrameHeader) + 2) {
        counters_.resync++;  // runt: no room for header + CRC
        return;
    }

    size_t body = (size_t)dec_len - 2;  // header + payload (CRC stripped)
    uint16_t rx_crc = (uint16_t)(dec_buf_[body] | ((uint16_t)dec_buf_[body + 1] << 8));
    uint16_t calc = crc16_ccitt(dec_buf_, body);
    if (rx_crc != calc) {
        counters_.crc_err++;
        return;
    }

    counters_.frames_ok++;
    sink_.on_frame(dec_buf_, body);
}

bool Framer::send_frame(const uint8_t* frame, size_t len) {
    if (len + 2 > kMaxFrame) {
        return false;  // frame + CRC would exceed the protocol max
    }

    for (size_t i = 0; i < len; ++i) {
        tx_dec_[i] = frame[i];
    }
    uint16_t crc = crc16_ccitt(frame, len);
    tx_dec_[len] = (uint8_t)(crc & 0xFF);
    tx_dec_[len + 1] = (uint8_t)(crc >> 8);

    size_t enc_len = cobs_encode(tx_dec_, len + 2, tx_enc_, sizeof(tx_enc_));
    if (enc_len == 0) {
        return false;  // unreachable: tx_enc_ is sized for the worst case
    }

    size_t wire_len = enc_len + 1;  // encoded bytes + 0x00 delimiter
    if (out_.writable() < wire_len) {
        counters_.tx_drop++;
        return false;  // never block
    }

    out_.write(tx_enc_, enc_len);
    const uint8_t delim = 0x00;
    out_.write(&delim, 1);
    return true;
}

}  // namespace protocol
