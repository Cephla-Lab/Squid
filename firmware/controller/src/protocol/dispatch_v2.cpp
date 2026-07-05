/**
 * Protocol v2 dispatcher implementation. See dispatch_v2.h for the contract.
 */

#include "protocol/dispatch_v2.h"

namespace protocol {

// --- System command handlers ---------------------------------------------

// GET_STATE needs no extra payload — the StandardResponse carries everything.
static void h_get_state(Dispatcher&, const uint8_t*, size_t, ResponseWriter&) {}

// HELLO returns session info and starts a fresh session (clears all slots).
static void h_hello(Dispatcher& d, const uint8_t*, size_t, ResponseWriter& w) {
    HelloPayload hp;
    memset(&hp, 0, sizeof(hp));
    d.provider().fill_hello(hp);
    w.append_struct(hp);
    d.slots().reset();
}

static void h_get_info(Dispatcher& d, const uint8_t*, size_t, ResponseWriter& w) {
    InfoPayload ip;
    memset(&ip, 0, sizeof(ip));
    d.provider().fill_info(ip);
    w.append_struct(ip);
}

// DIAG page 0 returns counters; page >= 1 returns fault-ring entries.
static void h_diag(Dispatcher& d, const uint8_t* payload, size_t len, ResponseWriter& w) {
    uint8_t page = (len >= 1) ? payload[0] : 0;
    if (page == 0) {
        DiagPayload dp;
        memset(&dp, 0, sizeof(dp));
        d.provider().fill_diag_page0(dp);
        dp.page = 0;
        w.append_struct(dp);
    } else {
        FaultEntryWire faults[16];
        memset(faults, 0, sizeof(faults));
        uint8_t n = d.provider().fill_diag_faults(page, faults, 16);
        if (n > 16) {
            n = 16;
        }
        for (uint8_t i = 0; i < n; ++i) {
            w.append_struct(faults[i]);
        }
    }
}

// --- Dispatcher -----------------------------------------------------------

Dispatcher::Dispatcher(SlotManager& slots, StateProvider& provider)
    : slots_(slots), provider_(provider), framer_(nullptr), resp_buf_{} {
    memset(registry_, 0, sizeof(registry_));
}

void Dispatcher::register_command(uint8_t cmd_type, bool immediate, uint16_t min_len,
                                  uint16_t max_len, Handler handler) {
    Entry& e = registry_[cmd_type];
    e.registered = true;
    e.immediate = immediate;
    e.min_len = min_len;
    e.max_len = max_len;
    e.handler = handler;
}

void Dispatcher::register_system_commands() {
    register_command(HELLO, true, 0, 0, h_hello);
    register_command(GET_INFO, true, 0, 0, h_get_info);
    register_command(GET_STATE, true, 0, 0, h_get_state);
    register_command(DIAG, true, 1, 1, h_diag);
}

size_t Dispatcher::build_response(const uint8_t* req, size_t req_len, uint8_t* out,
                                  size_t out_cap) {
    if (req_len < sizeof(FrameHeader) || out_cap < sizeof(FrameHeader) + sizeof(StandardResponse)) {
        return 0;  // runt request or output can't hold a StandardResponse
    }

    const uint8_t cmd_id = req[1];
    const uint8_t cmd_type = req[2];
    const uint8_t flags = req[3];
    const uint8_t* payload = req + sizeof(FrameHeader);
    const size_t plen = req_len - sizeof(FrameHeader);

    StandardResponse sr;
    memset(&sr, 0, sizeof(sr));
    sr.status = STATUS_OK;
    sr.error_code = ERR_NONE;

    // Extra payload is bounded so the whole response fits within kMaxPayload.
    uint8_t extra[kMaxPayload - sizeof(StandardResponse)];
    ResponseWriter w(sr, extra, sizeof(extra));

    const Entry& e = registry_[cmd_type];
    if (!e.registered) {
        w.set_status(STATUS_REJECTED, ERR_UNKNOWN_COMMAND, cmd_type, 0);
    } else if (plen < e.min_len || plen > e.max_len) {
        w.set_status(STATUS_REJECTED, ERR_BAD_LENGTH, (uint8_t)plen, 0);
    } else if (e.immediate) {
        // Synchronous system query: no slot, no ring entry.
        e.handler(*this, payload, plen, w);
    } else {
        // Slotted async command routed through the SlotManager.
        const bool retry = (flags & FLAG_RETRY) != 0;
        const uint32_t claims = claims_for(cmd_type, payload, plen);
        uint8_t conflict_res = 0, holder = 0, ring_status = 0, ring_error = 0;
        AcceptResult ar = slots_.try_accept(cmd_id, cmd_type, claims, retry, &conflict_res,
                                            &holder, &ring_status, &ring_error);
        switch (ar) {
            case AcceptResult::NewCommand:
                w.set_status(STATUS_ACCEPTED);
                e.handler(*this, payload, plen, w);  // start the command
                break;
            case AcceptResult::ActiveDuplicate:
                w.set_status(STATUS_ACCEPTED);  // already in flight
                break;
            case AcceptResult::CompletedDuplicate:
                w.set_status(ring_status, ring_error);  // replay outcome, no re-run
                break;
            case AcceptResult::RejectBusy:
                w.set_status(STATUS_REJECTED, ERR_RESOURCE_BUSY, conflict_res, holder);
                break;
            case AcceptResult::RejectNoSlots:
                w.set_status(STATUS_REJECTED, ERR_NO_SLOTS);
                break;
        }
    }

    // Fill machine state and the slots/ring section AFTER dispatch so they
    // reflect any accept/complete/reset the handler performed. These touch
    // disjoint fields from the status set above.
    provider_.fill_state(sr);
    slots_.fill_response(sr);

    const size_t extra_len = w.extra_len();
    const size_t total = sizeof(FrameHeader) + sizeof(StandardResponse) + extra_len;
    if (total > out_cap) {
        return 0;
    }

    out[0] = RESPONSE;
    out[1] = cmd_id;
    out[2] = cmd_type;
    out[3] = 0;  // response flags
    memcpy(&out[sizeof(FrameHeader)], &sr, sizeof(StandardResponse));
    memcpy(&out[sizeof(FrameHeader) + sizeof(StandardResponse)], extra, extra_len);
    return total;
}

void Dispatcher::on_frame(const uint8_t* frame, size_t len) {
    size_t n = build_response(frame, len, resp_buf_, sizeof(resp_buf_));
    if (n == 0 || framer_ == nullptr) {
        return;
    }
    framer_->send_frame(resp_buf_, n);
}

}  // namespace protocol
