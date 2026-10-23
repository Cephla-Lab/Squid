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

// The power-up mode record in EEPROM, after the limit-switch record (bytes 0-6): 'F', 'W', mode, crc8ccitt of the
// first three bytes. A blank or invalid record, or an unknown mode, gives FOCUS_WHEEL_DRIVES_Z (the pre-1.8 wheel).
static const uint8_t FW_RECORD_ADDR = 8;
static const uint8_t FW_RECORD_LEN = 4;
void fw_record_pack(uint8_t mode, uint8_t out[FW_RECORD_LEN]);
uint8_t fw_record_unpack(const uint8_t in[FW_RECORD_LEN]);

#endif /* FOCUS_WHEEL_H_ */
