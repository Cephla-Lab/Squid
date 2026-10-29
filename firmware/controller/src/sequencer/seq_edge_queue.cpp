#include "sequencer/seq_edge_queue.h"

namespace seq {

namespace {
// Signed distance from now to t: negative = overdue. Valid while the two are within 2^31 us,
// which the engine guarantees by bounding every duration to kMaxDurationUs.
inline int32_t until(uint32_t t_us, uint32_t now_us) { return (int32_t)(t_us - now_us); }

// True when a must run before b.
inline bool before(const Edge& a, const Edge& b, uint32_t now_us) {
    const int32_t da = until(a.t_us, now_us);
    const int32_t db = until(b.t_us, now_us);
    if (da != db) return da < db;
    return a.kind < b.kind;  // EdgeKind order is the tie-break order
}
}  // namespace

bool EdgeQueue::push(const Edge& e, uint32_t now_us) {
    if (n_ >= kCapacity) return false;
    uint8_t i = n_;
    while (i > 0 && before(e, edges_[i - 1], now_us)) {
        edges_[i] = edges_[i - 1];
        i--;
    }
    edges_[i] = e;
    n_++;
    return true;
}

bool EdgeQueue::push_plan(const ExposurePlan& p, uint32_t now_us) {
    const bool lit = p.illum_ttl_mask != 0;
    if ((uint8_t)(kCapacity - n_) < (lit ? 4 : 2)) return false;
    Edge e{};
    e.camera_id = p.camera_id;
    e.illum_ttl_mask = p.illum_ttl_mask;
    e.kind = (uint8_t)EdgeKind::TriggerAssert;
    e.t_us = p.t_assert_us;
    push(e, now_us);
    if (lit) {
        e.kind = (uint8_t)EdgeKind::IllumOn;
        e.t_us = p.t_illum_on_us;
        push(e, now_us);
        e.kind = (uint8_t)EdgeKind::IllumOff;
        e.t_us = p.t_illum_off_us;
        push(e, now_us);
    }
    e.kind = (uint8_t)EdgeKind::TriggerRelease;
    e.t_us = p.t_deassert_us;
    push(e, now_us);
    return true;
}

bool EdgeQueue::pop_due(uint32_t now_us, Edge* out) {
    if (n_ == 0 || until(edges_[0].t_us, now_us) > 0) return false;
    *out = edges_[0];
    n_--;
    for (uint8_t i = 0; i < n_; i++) edges_[i] = edges_[i + 1];
    return true;
}

bool EdgeQueue::next_delay(uint32_t now_us, uint32_t* delay_us) const {
    if (n_ == 0) return false;
    const int32_t d = until(edges_[0].t_us, now_us);
    *delay_us = (d > 0) ? (uint32_t)d : 0;
    return true;
}

}  // namespace seq
