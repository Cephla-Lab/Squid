/**
 * Five-slot concurrent command manager with an 8-entry completion ring.
 *
 * The dispatcher computes a command's resource claims (claims_for) and offers
 * it here. try_accept() gates concurrency: it dedups retries, rejects
 * resource conflicts (ERR_RESOURCE_BUSY) and slot exhaustion (ERR_NO_SLOTS),
 * or reserves a slot. On completion the slot is freed and its outcome recorded
 * in the ring so a later RETRY can be answered without re-execution.
 *
 * Pure, no heap; fixed arrays sized by the wire contract (frames.h). All
 * state is software-tracked (filter/rotary axes have no position encoder).
 */

#ifndef PROTOCOL_SLOTS_H
#define PROTOCOL_SLOTS_H

#include <stddef.h>
#include <stdint.h>

#include "protocol/frames.h"

namespace protocol {

enum SlotState : uint8_t {
    SLOT_EMPTY = 0,
    SLOT_ACTIVE = 1,
};

enum class AcceptResult : uint8_t {
    NewCommand,         // fresh command reserved a slot
    ActiveDuplicate,    // cmd_id already in flight — do not re-run
    CompletedDuplicate, // RETRY of a completed command — answer from the ring
    RejectBusy,         // a wanted resource is held by an in-flight command
    RejectNoSlots,      // all slots occupied by compatible commands
};

struct SlotInfo {
    uint8_t cmd_id;
    uint8_t cmd_type;
    uint8_t state;     // SlotState
    uint8_t progress;  // 0..100
    uint32_t claims;
};

class SlotManager {
public:
    static const size_t kNumSlots = 5;
    static const size_t kRingSize = 8;

    SlotManager();

    // Offer a command. On RejectBusy, *out_conflict_res is the blocking
    // resource id and *out_holder_cmd_id the cmd_id holding it. On
    // CompletedDuplicate, *out_ring_status/*out_ring_error carry the recorded
    // outcome. Any out pointer may be null. Never re-runs a duplicate.
    AcceptResult try_accept(uint8_t cmd_id, uint8_t cmd_type, uint32_t claims, bool retry,
                            uint8_t* out_conflict_res, uint8_t* out_holder_cmd_id,
                            uint8_t* out_ring_status, uint8_t* out_ring_error);

    // Free the slot for cmd_id and record {final_status, error_code} in the
    // ring (advancing head_seq). No-op if cmd_id is not active.
    void complete(uint8_t cmd_id, uint8_t final_status, uint8_t error_code);

    // Update an active command's progress (clamped to 0..100). No-op if absent.
    void set_progress(uint8_t cmd_id, uint8_t pct);

    // Active slot for cmd_id, or nullptr.
    const SlotInfo* find(uint8_t cmd_id) const;

    // Newest recorded outcome for cmd_id in the ring, or false if not present.
    bool ring_lookup(uint8_t cmd_id, uint8_t* out_status, uint8_t* out_error) const;

    uint32_t inflight_claims_union() const;

    // Monotonic completion counter (mod 256); the wire ring_head_seq. The host
    // watches this to detect new completions; the newest ring entry is at
    // physical index (ring_head_seq - 1) mod kRingSize.
    uint8_t ring_head_seq() const { return (uint8_t)completions_; }

    // Fill the slots + ring section of a StandardResponse.
    void fill_response(StandardResponse& r) const;

    // Clear all slots and the ring (protocol RESET / HELLO session start).
    void reset();

private:
    int find_slot(uint8_t cmd_id) const;  // active slot index or -1
    int find_free() const;                 // free slot index or -1

    SlotInfo slots_[kNumSlots];
    RingEntry ring_[kRingSize];
    uint32_t completions_;  // total completions; ring_head_seq is the low 8 bits
};

}  // namespace protocol

#endif  // PROTOCOL_SLOTS_H
