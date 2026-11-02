#pragma once
#include <stdint.h>

// Controller pin + polarity profiles — pure C++11, NO Arduino deps (natively tested).
// The profile is chosen at firmware upload time by the PlatformIO env:
//   pio run -e teensy41 -t upload           previous controllers (default)
//   pio run -e teensy41_newctrl -t upload   new controller (-D SQUID_CONTROLLER_NEWCTRL)
// Arduino IDE: add `#define SQUID_CONTROLLER_NEWCTRL` above this include for the new controller.

namespace controller {

constexpr uint8_t kMaxTriggers = 4;

struct Profile {
    uint8_t trigger_pins[kMaxTriggers];
    uint8_t n_triggers;
    uint8_t trigger_assert_level;  // level written to the Teensy pin to ASSERT a trigger: 0 LOW, 1 HIGH
    int8_t ready_pin;              // camera trigger-ready input; -1 = not wired
    uint8_t ready_assert_level;    // level at the Teensy pin that means READY: 0 LOW, 1 HIGH. The board pulls
                                   // the input to the OTHER level, so an unplugged cable reads NOT ready.
};

// Previous controllers: GPIO -> inverting 2N3904 stage (collector pulled to 5 V) -> connector,
// so pin LOW is 5 V = asserted at the camera. No ready input.
constexpr Profile kLegacy = {{29, 30, 31, 32}, 4, 0, -1, 0};

// New controller: ONE trigger, wired directly to the GPIO (pin HIGH = 3.3 V = asserted at the
// camera, same active-high contract at the connector). Trigger-ready input on pin 18 (<= 3.3 V),
// pulled up on the board (~4.7 k to 3.3 V, the whole input circuit; measured through the pin's
// ADC): the camera signals READY by driving it LOW, and an unplugged cable reads NOT ready.
constexpr Profile kNewCtrl = {{19, 0, 0, 0}, 1, 1, 18, 0};

inline bool trigger_channel_valid(const Profile& p, uint8_t channel) { return channel < p.n_triggers; }

#if defined(SQUID_CONTROLLER_NEWCTRL)
constexpr Profile kActive = kNewCtrl;
#else
constexpr Profile kActive = kLegacy;
#endif

}  // namespace controller
