#ifndef JOYSTICK_PANEL_H_
#define JOYSTICK_PANEL_H_

#include <stdint.h>
#include <stddef.h>

// The link to the joystick / focus-wheel panel (Teensy LC on Serial5), as the controller sees it.
// Pure logic, no Arduino dependencies, so it is host-tested.
//
// RECONNECTING THE PANEL CABLE MUST NOT MOVE THE STAGE. The panel is powered through that cable, so a
// reconnect reboots it, and its packets carry the focus wheel as an ABSOLUTE count that restarts at 0
// and the joystick as an offset from a zero it takes once at boot. Before this module, the controller
// took the wheel baseline only at its own boot and on INITIALIZE, so the first packet after a reconnect
// moved Z by minus the whole session's wheel travel; it never noticed the panel go away, so a stick
// held when the cable dropped kept X/Y running at that speed; and a packet has no checksum, so noise
// while the plug goes in can decode as a 10-byte packet with any speed and any wheel count.
//
// So: a gap of JP_TIMEOUT_MS between valid packets is a disconnect. It forgets the wheel baseline,
// and the next packet only sets a new one, never moves Z. It also disarms the joystick, which then
// reads as centred until the panel has reported the stick centred JP_CENTERED_PACKETS times in a row,
// so a stick held while plugging in, or a panel that took its zero off-centre, cannot drive X/Y. A
// packet whose last byte is not 0 (the panel always sends 0 there), or whose wheel count jumps more
// than any hand can turn it between packets, is dropped; the jump also forgets the baseline and
// disarms the joystick, but leaves the timeout running from the last good packet.

static const int JP_MSG_LENGTH = 10;                 // wheel int32, x int16, y int16, buttons, 0 (all big-endian)
static const uint32_t JP_TIMEOUT_MS = 200;           // the panel sends every ~3-5 ms
static const uint8_t JP_CENTERED_PACKETS = 5;        // consecutive centred packets that arm the joystick
static const int32_t JP_MAX_WHEEL_STEP = 65536;      // microsteps; a fast hand turn is ~1000 per packet

struct JoystickPanelLink {
    bool connected;          // a valid packet arrived within JP_TIMEOUT_MS
    uint32_t last_packet_ms;
    bool have_wheel_baseline;
    int32_t wheel_pos;       // the panel's wheel count in the last accepted packet
    bool xy_armed;
    uint8_t centered_count;
};

struct JoystickPanelInput {
    int32_t focus_delta;     // microsteps to add to the Z target
    int16_t delta_x;         // joystick, 0 while disarmed (panel sign; JOYSTICK_SIGN_* not applied)
    int16_t delta_y;
    uint8_t buttons;
};

// Disconnected, no wheel baseline, joystick disarmed.
void jp_reset(JoystickPanelLink *link);

// Forget the wheel baseline only, so the next packet does not move Z (INITIALIZE zeroes the Z target).
void jp_forget_wheel(JoystickPanelLink *link);

// One decoded packet. False if it is dropped (wrong size, nonzero last byte, implausible wheel jump);
// `in` is then untouched and must not be applied.
bool jp_on_packet(JoystickPanelLink *link, const uint8_t *buffer, size_t size, uint32_t now_ms, JoystickPanelInput *in);

// True exactly once when the link times out; the link is then reset. The caller stops joystick motion.
bool jp_check_timeout(JoystickPanelLink *link, uint32_t now_ms);

#endif /* JOYSTICK_PANEL_H_ */
