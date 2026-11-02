#include "joystick_panel.h"

void jp_reset(JoystickPanelLink *link) {
    link->connected = false;
    link->last_packet_ms = 0;
    link->have_wheel_baseline = false;
    link->wheel_pos = 0;
    link->xy_armed = false;
    link->centered_count = 0;
}

void jp_forget_wheel(JoystickPanelLink *link) {
    link->have_wheel_baseline = false;
}

bool jp_on_packet(JoystickPanelLink *link, const uint8_t *buffer, size_t size, uint32_t now_ms, JoystickPanelInput *in) {
    if (size != (size_t)JP_MSG_LENGTH || buffer[9] != 0)
        return false;

    int32_t wheel = (int32_t)((uint32_t)buffer[0] << 24 | (uint32_t)buffer[1] << 16 | (uint32_t)buffer[2] << 8 | (uint32_t)buffer[3]);
    int16_t x = (int16_t)((uint16_t)buffer[4] << 8 | buffer[5]);
    int16_t y = (int16_t)((uint16_t)buffer[6] << 8 | buffer[7]);

    int32_t focus_delta = 0;
    if (link->have_wheel_baseline) {
        // In 64 bits: two garbage counts can be 2^32 apart.
        int64_t step = (int64_t)wheel - (int64_t)link->wheel_pos;
        if (step > JP_MAX_WHEEL_STEP || step < -JP_MAX_WHEEL_STEP) {
            // Not jp_reset: `connected` and `last_packet_ms` stay, so that if the panel has really gone
            // the timeout still fires and stops a joystick move that was running.
            link->have_wheel_baseline = false;
            link->xy_armed = false;
            link->centered_count = 0;
            return false;
        }
        focus_delta = (int32_t)step;
    }
    link->wheel_pos = wheel;
    link->have_wheel_baseline = true;
    link->connected = true;
    link->last_packet_ms = now_ms;

    if (!link->xy_armed) {
        if (x == 0 && y == 0) {
            if (++link->centered_count >= JP_CENTERED_PACKETS)
                link->xy_armed = true;
        } else {
            link->centered_count = 0;
        }
    }

    in->focus_delta = focus_delta;
    in->delta_x = link->xy_armed ? x : 0;
    in->delta_y = link->xy_armed ? y : 0;
    in->buttons = buffer[8];
    return true;
}

bool jp_check_timeout(JoystickPanelLink *link, uint32_t now_ms) {
    if (!link->connected || now_ms - link->last_packet_ms <= JP_TIMEOUT_MS)
        return false;
    jp_reset(link);
    return true;
}
