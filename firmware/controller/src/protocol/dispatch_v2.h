/**
 * Protocol v2 command dispatcher.
 *
 * Turns a decoded REQUEST frame (header + payload) into a RESPONSE frame whose
 * payload is a StandardResponse (slots + ring + machine state) optionally
 * followed by a command-specific extra payload (HELLO/GET_INFO/DIAG).
 *
 * Two command kinds:
 *   - immediate: system queries answered synchronously; no slot, no ring entry.
 *   - slotted:   claims-gated async commands routed through the SlotManager;
 *     RETRY of a completed command is answered from the ring without re-running.
 *
 * Machine state is read through an injected StateProvider (Phase C binds real
 * globals; tests fake it). The core is `build_response`, a pure function; the
 * FrameSink glue sends the built frame through a bound Framer.
 *
 * Pure, no heap; fixed buffers sized by the wire contract.
 */

#ifndef PROTOCOL_DISPATCH_V2_H
#define PROTOCOL_DISPATCH_V2_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "protocol/claims.h"
#include "protocol/frames.h"
#include "protocol/framer.h"
#include "protocol/slots.h"

namespace protocol {

class Dispatcher;  // forward declaration for the Handler signature

// Builds the command outcome and any extra payload appended after the
// StandardResponse. Handlers set status via the writer and append extra bytes;
// the dispatcher fills slots/ring and machine state around them.
class ResponseWriter {
public:
    ResponseWriter(StandardResponse& sr, uint8_t* extra, size_t extra_cap)
        : sr_(sr), extra_(extra), cap_(extra_cap), len_(0) {}

    StandardResponse& std_response() { return sr_; }

    void set_status(uint8_t status, uint8_t error_code = ERR_NONE, uint8_t detail0 = 0,
                    uint8_t detail1 = 0) {
        sr_.status = status;
        sr_.error_code = error_code;
        sr_.error_detail0 = detail0;
        sr_.error_detail1 = detail1;
    }

    bool append(const uint8_t* data, size_t n) {
        if (len_ + n > cap_) {
            return false;
        }
        for (size_t i = 0; i < n; ++i) {
            extra_[len_ + i] = data[i];
        }
        len_ += n;
        return true;
    }

    template <typename T>
    bool append_struct(const T& s) {
        return append(reinterpret_cast<const uint8_t*>(&s), sizeof(T));
    }

    size_t extra_len() const { return len_; }

private:
    StandardResponse& sr_;
    uint8_t* extra_;
    size_t cap_;
    size_t len_;
};

// Source of live machine state and info/diagnostic payloads. Implementations
// must fill ONLY the machine-state fields of StandardResponse in fill_state
// (mode, axes, dacs, illum, cam, seq, input_states, fw/protocol versions) —
// never status/error/details/slots/ring, which the dispatcher owns.
class StateProvider {
public:
    virtual ~StateProvider() {}
    virtual void fill_state(StandardResponse& r) = 0;
    virtual void fill_hello(HelloPayload& h) = 0;
    virtual void fill_info(InfoPayload& i) = 0;
    virtual void fill_diag_page0(DiagPayload& d) = 0;
    // Fill up to `cap` fault-ring entries for DIAG page >= 1; returns the count.
    virtual uint8_t fill_diag_faults(uint8_t page, FaultEntryWire* out, uint8_t cap) = 0;
};

typedef void (*Handler)(Dispatcher& d, const uint8_t* payload, size_t len, ResponseWriter& w);

class Dispatcher : public FrameSink {
public:
    Dispatcher(SlotManager& slots, StateProvider& provider);

    // Register a command. `immediate` commands answer synchronously without a
    // slot; slotted commands route through the SlotManager. Length bounds are
    // inclusive; claims are resolved via claims_for at dispatch time.
    void register_command(uint8_t cmd_type, bool immediate, uint16_t min_len, uint16_t max_len,
                          Handler handler);

    // Register HELLO / GET_INFO / GET_STATE / DIAG.
    void register_system_commands();

    // Pure core: build the RESPONSE frame (header + payload, no CRC) for a
    // REQUEST frame. Returns the response length, or 0 if the request is a runt
    // or the response would not fit in `out_cap`.
    size_t build_response(const uint8_t* req, size_t req_len, uint8_t* out, size_t out_cap);

    // FrameSink: build the response and send it through the bound Framer.
    void on_frame(const uint8_t* frame, size_t len) override;
    void set_framer(Framer& f) { framer_ = &f; }

    SlotManager& slots() { return slots_; }
    StateProvider& provider() { return provider_; }

private:
    struct Entry {
        bool registered;
        bool immediate;
        uint16_t min_len;
        uint16_t max_len;
        Handler handler;
    };

    SlotManager& slots_;
    StateProvider& provider_;
    Framer* framer_;
    Entry registry_[256];
    uint8_t resp_buf_[kMaxFrame];  // scratch for the on_frame send path
};

}  // namespace protocol

#endif  // PROTOCOL_DISPATCH_V2_H
