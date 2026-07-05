/**
 * Five-slot command manager + completion ring. See slots.h for the contract.
 */

#include "protocol/slots.h"

#include "protocol/claims.h"  // claims_conflict

namespace protocol {

SlotManager::SlotManager() { reset(); }

void SlotManager::reset() {
    for (size_t i = 0; i < kNumSlots; ++i) {
        slots_[i].cmd_id = 0;
        slots_[i].cmd_type = 0;
        slots_[i].state = SLOT_EMPTY;
        slots_[i].progress = 0;
        slots_[i].claims = 0;
    }
    for (size_t i = 0; i < kRingSize; ++i) {
        ring_[i].cmd_id = 0;
        ring_[i].cmd_type = 0;
        ring_[i].final_status = 0;
        ring_[i].error_code = 0;
    }
    completions_ = 0;
}

int SlotManager::find_slot(uint8_t cmd_id) const {
    for (size_t i = 0; i < kNumSlots; ++i) {
        if (slots_[i].state != SLOT_EMPTY && slots_[i].cmd_id == cmd_id) {
            return (int)i;
        }
    }
    return -1;
}

int SlotManager::find_free() const {
    for (size_t i = 0; i < kNumSlots; ++i) {
        if (slots_[i].state == SLOT_EMPTY) {
            return (int)i;
        }
    }
    return -1;
}

const SlotInfo* SlotManager::find(uint8_t cmd_id) const {
    int i = find_slot(cmd_id);
    return (i >= 0) ? &slots_[i] : nullptr;
}

uint32_t SlotManager::inflight_claims_union() const {
    uint32_t u = 0;
    for (size_t i = 0; i < kNumSlots; ++i) {
        if (slots_[i].state != SLOT_EMPTY) {
            u |= slots_[i].claims;
        }
    }
    return u;
}

bool SlotManager::ring_lookup(uint8_t cmd_id, uint8_t* out_status, uint8_t* out_error) const {
    uint32_t count = (completions_ < kRingSize) ? completions_ : (uint32_t)kRingSize;
    // Newest first, so a recycled cmd_id resolves to its latest outcome.
    for (uint32_t k = 0; k < count; ++k) {
        uint32_t idx = (completions_ - 1 - k) % (uint32_t)kRingSize;
        if (ring_[idx].cmd_id == cmd_id) {
            if (out_status) *out_status = ring_[idx].final_status;
            if (out_error) *out_error = ring_[idx].error_code;
            return true;
        }
    }
    return false;
}

AcceptResult SlotManager::try_accept(uint8_t cmd_id, uint8_t cmd_type, uint32_t claims, bool retry,
                                     uint8_t* out_conflict_res, uint8_t* out_holder_cmd_id,
                                     uint8_t* out_ring_status, uint8_t* out_ring_error) {
    // 1. Already in flight? Never double-accept (dedup, retry or not).
    if (find_slot(cmd_id) >= 0) {
        return AcceptResult::ActiveDuplicate;
    }

    // 2. RETRY of a completed command: replay the recorded outcome, no re-run.
    if (retry) {
        uint8_t st = 0, er = 0;
        if (ring_lookup(cmd_id, &st, &er)) {
            if (out_ring_status) *out_ring_status = st;
            if (out_ring_error) *out_ring_error = er;
            return AcceptResult::CompletedDuplicate;
        }
        // RETRY of an unknown command falls through and is treated as new.
    }

    // 3. Resource conflict against the in-flight union.
    uint8_t conflict = claims_conflict(claims, inflight_claims_union());
    if (conflict != 0) {
        uint8_t res = (uint8_t)(conflict - 1);
        if (out_conflict_res) *out_conflict_res = res;
        if (out_holder_cmd_id) {
            *out_holder_cmd_id = 0;
            for (size_t i = 0; i < kNumSlots; ++i) {
                if (slots_[i].state != SLOT_EMPTY &&
                    (slots_[i].claims & (uint32_t(1) << res))) {
                    *out_holder_cmd_id = slots_[i].cmd_id;
                    break;
                }
            }
        }
        return AcceptResult::RejectBusy;
    }

    // 4. Reserve a slot.
    int free = find_free();
    if (free < 0) {
        return AcceptResult::RejectNoSlots;
    }
    slots_[free].state = SLOT_ACTIVE;
    slots_[free].cmd_id = cmd_id;
    slots_[free].cmd_type = cmd_type;
    slots_[free].progress = 0;
    slots_[free].claims = claims;
    return AcceptResult::NewCommand;
}

void SlotManager::complete(uint8_t cmd_id, uint8_t final_status, uint8_t error_code) {
    int i = find_slot(cmd_id);
    if (i < 0) {
        return;  // not active — nothing to complete or record
    }
    uint8_t cmd_type = slots_[i].cmd_type;

    // Free the slot.
    slots_[i].state = SLOT_EMPTY;
    slots_[i].cmd_id = 0;
    slots_[i].cmd_type = 0;
    slots_[i].progress = 0;
    slots_[i].claims = 0;

    // Record the outcome in the ring, advancing head_seq.
    uint32_t idx = completions_ % (uint32_t)kRingSize;
    ring_[idx].cmd_id = cmd_id;
    ring_[idx].cmd_type = cmd_type;
    ring_[idx].final_status = final_status;
    ring_[idx].error_code = error_code;
    completions_++;
}

void SlotManager::set_progress(uint8_t cmd_id, uint8_t pct) {
    int i = find_slot(cmd_id);
    if (i < 0) {
        return;
    }
    slots_[i].progress = (pct > 100) ? 100 : pct;
}

void SlotManager::fill_response(StandardResponse& r) const {
    for (size_t i = 0; i < kNumSlots; ++i) {
        r.slots[i].cmd_id = slots_[i].cmd_id;
        r.slots[i].cmd_type = slots_[i].cmd_type;
        r.slots[i].state = slots_[i].state;
        r.slots[i].progress = slots_[i].progress;
    }
    r.ring_head_seq = (uint8_t)completions_;
    for (size_t i = 0; i < kRingSize; ++i) {
        r.ring[i] = ring_[i];
    }
}

}  // namespace protocol
