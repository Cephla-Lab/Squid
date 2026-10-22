#include "focus_wheel.h"
#include "crc8.h"
#include "../constants_protocol.h"

// Wrapping arithmetic is done in uint32_t: signed overflow is undefined in C++.

int32_t fw_wheel_delta(int32_t wheel_now, int32_t wheel_before) {
    return (int32_t)((uint32_t)wheel_now - (uint32_t)wheel_before);
}

void fw_route_delta(uint8_t mode, int32_t delta, int32_t *focus_position, int32_t *host_count) {
    if (mode == FOCUS_WHEEL_TO_HOST)
        *host_count = (int32_t)((uint32_t)*host_count + (uint32_t)delta);
    else
        *focus_position = *focus_position + delta;
}

bool fw_mode_allowed(uint8_t mode, bool encoder_reporting_on) {
    if (mode == FOCUS_WHEEL_DRIVES_Z) return true;
    return mode == FOCUS_WHEEL_TO_HOST && !encoder_reporting_on;
}

bool fw_encoder_reporting_allowed(bool turning_on, uint8_t wheel_mode) {
    return !turning_on || wheel_mode != FOCUS_WHEEL_TO_HOST;
}

void fw_status_fields(int32_t host_count, uint8_t out[8]) {
    uint32_t u = (uint32_t)host_count;
    out[0] = (uint8_t)(u >> 24);
    out[1] = (uint8_t)(u >> 16);
    out[2] = (uint8_t)(u >> 8);
    out[3] = (uint8_t)u;
    // out[4] is byte 18 (buttons and limit inputs), owned by the caller
    out[5] = (uint8_t)(1 << FOCUS_WHEEL_FLAG_REPORTING);
    out[6] = 0;
    out[7] = 0;
}

void fw_record_pack(uint8_t mode, uint8_t out[FW_RECORD_LEN]) {
    out[0] = 'F';
    out[1] = 'W';
    out[2] = mode;
    out[3] = crc8ccitt(out, 3);
}

uint8_t fw_record_unpack(const uint8_t in[FW_RECORD_LEN]) {
    bool ok = in[0] == 'F' && in[1] == 'W' && in[3] == crc8ccitt(in, 3) &&
              (in[2] == FOCUS_WHEEL_DRIVES_Z || in[2] == FOCUS_WHEEL_TO_HOST);
    return ok ? in[2] : (uint8_t)FOCUS_WHEEL_DRIVES_Z;
}
