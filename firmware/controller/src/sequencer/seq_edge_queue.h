#pragma once
#include <stdint.h>

#include "sequencer/seq_hal.h"
#include "sequencer/seq_types.h"

// Time-sorted queue of exposure edges — pure C++11, NO Arduino deps (natively tested).
//
// The engine hands the HAL a fully timestamped ExposurePlan; the Teensy binding expands it
// into edges here and a one-shot timer ISR executes them. The ISR only writes GPIO, so
// everything that needs judgment — ordering, tie-breaks, the micros() wrap — lives in this
// class where it can be tested against a virtual clock.
//
// Concurrency contract (enforced by the binding, not here): the loop context pushes with
// interrupts disabled; the ISR pops. There is one producer and one consumer.

namespace seq {

// The numeric order is the tie-break order for edges that share a timestamp:
//  - light OFF before the trigger releases: with LEVEL trigger + global reset the two share
//    a timestamp, and rows keep integrating until read out, so the light must never outlive
//    the exposure;
//  - the trigger asserts before the light comes ON (strobe delay 0).
enum class EdgeKind : uint8_t { IllumOff = 0, TriggerRelease = 1, TriggerAssert = 2, IllumOn = 3 };

struct Edge {
    uint32_t t_us;           // micros() timestamp, latched when the exposure is scheduled
    uint8_t kind;            // EdgeKind
    uint8_t camera_id;       // TriggerAssert / TriggerRelease
    uint8_t illum_ttl_mask;  // IllumOn / IllumOff: bit i = TTL port D(i+1)
};

class EdgeQueue {
   public:
    static constexpr uint8_t kCapacity = 4 * kMaxCameras;  // 4 edges per camera exposure

    void clear() { n_ = 0; }
    bool empty() const { return n_ == 0; }
    uint8_t size() const { return n_; }

    // Insert keeping time order. now_us anchors the comparison so ordering is correct
    // across the 32-bit micros() wrap. False when full (nothing inserted).
    bool push(const Edge& e, uint32_t now_us);
    // Expand a plan into its edges (4, or 2 when no TTL port is driven). All-or-nothing: a
    // half-scheduled exposure could leave the light on.
    bool push_plan(const ExposurePlan& p, uint32_t now_us);
    // Pop the earliest edge if its time has come.
    bool pop_due(uint32_t now_us, Edge* out);
    // Time until the earliest edge; 0 when it is due or overdue. False when empty.
    bool next_delay(uint32_t now_us, uint32_t* delay_us) const;

   private:
    Edge edges_[kCapacity];
    uint8_t n_ = 0;
};

}  // namespace seq
