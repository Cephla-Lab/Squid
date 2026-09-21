#pragma once
#include <Arduino.h>

#include "controller_profile.h"

// Arduino-side view of the active controller profile. Trigger pins are never written with a
// literal LOW/HIGH anywhere else: polarity differs between controllers (see controller_profile.h).

static const int NUM_CAMERA_TRIGGERS = controller::kActive.n_triggers;
static const int TRIGGER_READY_PIN = controller::kActive.ready_pin;

inline void trigger_assert(int channel) {
    digitalWrite(controller::kActive.trigger_pins[channel], controller::kActive.trigger_assert_level ? HIGH : LOW);
}
inline void trigger_release(int channel) {
    digitalWrite(controller::kActive.trigger_pins[channel], controller::kActive.trigger_assert_level ? LOW : HIGH);
}
