#pragma once
#include <stdint.h>

// Who owns status-packet bytes 14-17 right now. Pure C++11, no Arduino deps (natively tested).
//
// Firmware 1.6 / 1.8 made those bytes a multiplexed field: the encoder position of the reported
// axis, or the focus wheel's count, with byte 19 saying which. The hardware sequencer is one more
// owner of the same field. It holds the field while a sequence runs and for kStatusHoldMs after
// any sequencer activity (a command, a terminal state), long enough for the host to read the
// terminal status or a refusal, then gives it back. The packet builder sets SEQ_FLAG_STATUS in
// byte 19 whenever the sequencer owns the field, so the host never mistakes one owner's bytes
// for another's.

namespace seq {

constexpr uint32_t kStatusHoldMs = 1000;

class StatusField {
public:
    void note_activity(uint32_t now_ms) {
        last_activity_ms_ = now_ms;
        active_ = true;
    }
    bool owned(uint32_t now_ms, bool running) const {
        if (running) return true;
        if (!active_) return false;
        return (uint32_t)(now_ms - last_activity_ms_) < kStatusHoldMs;  // wrap-safe
    }

private:
    uint32_t last_activity_ms_ = 0;
    bool active_ = false;
};

}  // namespace seq
