#include "timing/event_timer.h"

#include <Arduino.h>

#include "constants.h"
#include "sequencer/seq_edge_queue.h"
#include "trigger_pins.h"

namespace {

IntervalTimer timer;
seq::EdgeQueue queue;  // loop context pushes with interrupts disabled; the ISR pops
volatile bool interlock_tripped = false;

// TTL mask bit i drives illumination port D(i+1).
const uint8_t kTtlPins[] = {PIN_ILLUMINATION_D1, PIN_ILLUMINATION_D2, PIN_ILLUMINATION_D3,
                            PIN_ILLUMINATION_D4, PIN_ILLUMINATION_D5};
const uint8_t kNumTtlPins = sizeof(kTtlPins) / sizeof(kTtlPins[0]);

// Shortest interval the PIT is armed for. An edge closer than this runs up to kMinArmUs late
// rather than being busy-waited for inside the ISR.
const uint32_t kMinArmUs = 2;
// Longest interval the PIT is armed for. The Teensy core refuses periods above about 179 s
// (IntervalTimer::MAX_PERIOD) and begin() then arms nothing, which would leave the pending
// edges (a trigger release, an illumination OFF) unexecuted for good. A longer gap is covered
// by several wake-ups: each finds nothing due yet and re-arms for the remainder.
const uint32_t kMaxArmUs = 60u * 1000000u;
// begin() refused to arm (no free PIT channel): the edges still queued will never run and the
// outputs stay where they are. Latched for seq_tick() to abort the run on, like the interlock.
volatile bool arm_failed = false;

inline void write_ttl(uint8_t mask, uint8_t level) {
    for (uint8_t i = 0; i < kNumTtlPins; i++)
        if ((mask >> i) & 1) digitalWriteFast(kTtlPins[i], level);
}

inline void apply(const seq::Edge& e) {
    switch ((seq::EdgeKind)e.kind) {
        case seq::EdgeKind::TriggerAssert:
            trigger_assert(e.camera_id);
            break;
        case seq::EdgeKind::TriggerRelease:
            trigger_release(e.camera_id);
            break;
        case seq::EdgeKind::IllumOn:
            // The laser interlock is enforced where the light is turned on, not only in
            // loop(): a TTL port must never go HIGH with the interlock open.
            if (INTERLOCK_OK())
                write_ttl(e.illum_ttl_mask, HIGH);
            else
                interlock_tripped = true;
            break;
        case seq::EdgeKind::IllumOff:
            write_ttl(e.illum_ttl_mask, LOW);
            break;
    }
}

void on_timer();

// Run every due edge, then arm the timer for the next one. The caller has interrupts
// disabled (it is the ISR, or a noInterrupts() section in loop context).
void service_and_arm() {
    timer.end();
    seq::Edge e;
    while (queue.pop_due(micros(), &e)) apply(e);
    uint32_t delay_us;
    if (!queue.next_delay(micros(), &delay_us)) return;  // nothing pending: stay disarmed
    if (delay_us < kMinArmUs) delay_us = kMinArmUs;
    if (delay_us > kMaxArmUs) delay_us = kMaxArmUs;
    if (!timer.begin(on_timer, delay_us)) arm_failed = true;
}

void on_timer() { service_and_arm(); }

}  // namespace

bool event_timer_schedule(const seq::ExposurePlan& plan) {
    noInterrupts();
    const bool ok = queue.push_plan(plan, micros());
    // The trigger-assert edge is due now: run it here instead of paying a timer round trip.
    if (ok) service_and_arm();
    interrupts();
    return ok;
}

void event_timer_cancel_all() {
    noInterrupts();
    timer.end();
    queue.clear();
    for (int ch = 0; ch < NUM_CAMERA_TRIGGERS; ch++) trigger_release(ch);
    write_ttl(0xFF, LOW);
    interlock_tripped = false;
    arm_failed = false;
    interrupts();
}

bool event_timer_interlock_tripped() { return interlock_tripped; }

bool event_timer_arm_failed() { return arm_failed; }
