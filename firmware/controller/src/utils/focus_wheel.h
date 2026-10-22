#ifndef FOCUS_WHEEL_H_
#define FOCUS_WHEEL_H_

#include <stdint.h>

// Focus wheel routing (SET_FOCUS_WHEEL_MODE, firmware 1.8). FOCUS_WHEEL_DRIVES_Z adds each wheel delta to
// focusPosition as before; FOCUS_WHEEL_TO_HOST adds it to a counter the status packet reports instead, so the host
// can drive a different focus axis (e.g. a PI V-308) while Z is something else (e.g. a lift).
// Pure helpers, no Arduino or TMC4361A dependencies, so they are host-tested.

// Wheel count change between two joystick-panel packets (bytes 0-3), wrapping like the panel's counter.
int32_t fw_wheel_delta(int32_t wheel_now, int32_t wheel_before);

// Route one delta: to *focus_position in FOCUS_WHEEL_DRIVES_Z, to *host_count (wrapping) in FOCUS_WHEEL_TO_HOST.
void fw_route_delta(uint8_t mode, int32_t delta, int32_t *focus_position, int32_t *host_count);

// SET_FOCUS_WHEEL_MODE is refused (CMD_EXECUTION_ERROR, nothing changes) for an unknown mode, and for
// FOCUS_WHEEL_TO_HOST while encoder reporting is on: both use status bytes 14-17 and 19.
bool fw_mode_allowed(uint8_t mode, bool encoder_reporting_on);

// SET_ENCODER_REPORTING turning reporting on is refused while the wheel reports to the host, for the same reason.
bool fw_encoder_reporting_allowed(bool turning_on, uint8_t wheel_mode);

// Status bytes 14-21 while the wheel reports to the host: 14-17 the count (big-endian), 19 the
// FOCUS_WHEEL_FLAG_REPORTING flag, 18 untouched (out[4] is left as is), 20-21 zero.
void fw_status_fields(int32_t host_count, uint8_t out[8]);

#endif /* FOCUS_WHEEL_H_ */
