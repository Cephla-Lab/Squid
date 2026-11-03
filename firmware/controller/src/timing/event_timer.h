#pragma once
#include "sequencer/seq_hal.h"

// One-shot executor of exposure edges for the hardware sequencer (design S9).
//
// The ISR ONLY pops due edges and writes GPIO — no SPI, no FastLED, no waits. It is separate
// from the v1 strobe path on purpose: ISR_strobeTimer busy-waits delayMicroseconds() for
// exposures <= 30 ms and models one delay + one on-time + one global source. While a
// sequence runs the host's trigger commands are rejected, so control_strobe[] stays false
// and the v1 ISR idles; the two never drive a pin at the same time.
//
// All deadlines are latched when the exposure is scheduled — never recomputed from mutable
// state (the strobe-ISR follow-ups lesson).

// Schedule one camera exposure. Loop context only. False = queue full, nothing scheduled.
bool event_timer_schedule(const seq::ExposurePlan& plan);

// Drop every pending edge, release all triggers, all TTL illumination ports LOW. Loop context.
void event_timer_cancel_all();

// True once an illumination-ON edge found the laser interlock open and skipped the turn-on.
// Latched until the next event_timer_cancel_all(). The caller must abort the run: the frame
// being exposed is dark.
bool event_timer_interlock_tripped();
