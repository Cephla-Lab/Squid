/**
 * Protocol constants for firmware-software communication.
 *
 * This header contains protocol-related constants that are shared between
 * firmware and software. It has NO Arduino/hardware dependencies, making it
 * suitable for inclusion in native (host) unit tests.
 *
 * For hardware-specific constants (pins, timers, etc.), see constants.h.
 */

#ifndef CONSTANTS_PROTOCOL_H
#define CONSTANTS_PROTOCOL_H

/***************************************************************************************************/
/***************************************** Communications ******************************************/
/***************************************************************************************************/
// Command packet: 8 bytes
// byte[0]: command ID
// byte[1]: command code
// byte[2-5]: parameters (big-endian)
// byte[6]: reserved
// byte[7]: CRC-8

static const int CMD_LENGTH = 8;
static const int MSG_LENGTH = 24;

// Command codes
static const int MOVE_X = 0;
static const int MOVE_Y = 1;
static const int MOVE_Z = 2;
static const int MOVE_THETA = 3;
static const int MOVE_W = 4;
static const int HOME_OR_ZERO = 5;
static const int MOVETO_X = 6;
static const int MOVETO_Y = 7;
static const int MOVETO_Z = 8;
static const int SET_LIM = 9;
static const int TURN_ON_ILLUMINATION = 10;
static const int TURN_OFF_ILLUMINATION = 11;
static const int SET_ILLUMINATION = 12;
static const int SET_ILLUMINATION_LED_MATRIX = 13;
static const int ACK_JOYSTICK_BUTTON_PRESSED = 14;
static const int ANALOG_WRITE_ONBOARD_DAC = 15;
static const int SET_DAC80508_REFDIV_GAIN = 16;
static const int SET_ILLUMINATION_INTENSITY_FACTOR = 17;
static const int MOVETO_W = 18;
static const int SET_LIM_SWITCH_POLARITY = 20;
static const int CONFIGURE_STEPPER_DRIVER = 21;
static const int SET_MAX_VELOCITY_ACCELERATION = 22;
static const int SET_LEAD_SCREW_PITCH = 23;
static const int SET_OFFSET_VELOCITY = 24;
static const int CONFIGURE_STAGE_PID = 25;
static const int ENABLE_STAGE_PID = 26;
static const int DISABLE_STAGE_PID = 27;
// Note: "MERGIN" is intentionally misspelled to match legacy constant name
static const int SET_HOME_SAFETY_MERGIN = 28;
static const int SET_PID_ARGUMENTS = 29;
static const int SEND_HARDWARE_TRIGGER = 30;
static const int SET_STROBE_DELAY = 31;
static const int SET_AXIS_DISABLE_ENABLE = 32;
static const int SET_PIN_LEVEL = 41;
static const int INITFILTERWHEEL = 253;
static const int INITIALIZE = 254;
static const int RESET = 255;

// Command execution status
static const int COMPLETED_WITHOUT_ERRORS = 0;
static const int IN_PROGRESS = 1;
static const int CMD_CHECKSUM_ERROR = 2;
static const int CMD_INVALID = 3;
static const int CMD_EXECUTION_ERROR = 4;

// Home/zero command types
static const int HOME_NEGATIVE = 1;
static const int HOME_POSITIVE = 0;
static const int HOME_OR_ZERO_ZERO = 2;

// Axis identifiers
static const int AXIS_X = 0;
static const int AXIS_Y = 1;
static const int AXIS_Z = 2;
static const int AXIS_THETA = 3;
static const int AXES_XY = 4;
static const int AXIS_W = 5;

// Button/switch bit positions in response packet
static const int BIT_POS_JOYSTICK_BUTTON = 0;

// Limit switch codes (for SET_LIM command)
static const int LIM_CODE_X_POSITIVE = 0;
static const int LIM_CODE_X_NEGATIVE = 1;
static const int LIM_CODE_Y_POSITIVE = 2;
static const int LIM_CODE_Y_NEGATIVE = 3;
static const int LIM_CODE_Z_POSITIVE = 4;
static const int LIM_CODE_Z_NEGATIVE = 5;

// Limit switch polarity
static const int ACTIVE_LOW = 0;
static const int ACTIVE_HIGH = 1;
static const int DISABLED = 2;

/***************************************************************************************************/
/***************************************** Illumination ********************************************/
/***************************************************************************************************/
static const int ILLUMINATION_SOURCE_LED_ARRAY_FULL = 0;
static const int ILLUMINATION_SOURCE_LED_ARRAY_LEFT_HALF = 1;
static const int ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_HALF = 2;
static const int ILLUMINATION_SOURCE_LED_ARRAY_LEFTB_RIGHTR = 3;
static const int ILLUMINATION_SOURCE_LED_ARRAY_LOW_NA = 4;
static const int ILLUMINATION_SOURCE_LED_ARRAY_LEFT_DOT = 5;
static const int ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_DOT = 6;
static const int ILLUMINATION_SOURCE_LED_ARRAY_TOP_HALF = 7;
static const int ILLUMINATION_SOURCE_LED_ARRAY_BOTTOM_HALF = 8;
static const int ILLUMINATION_SOURCE_LED_EXTERNAL_FET = 20;
static const int ILLUMINATION_SOURCE_405NM = 11;
static const int ILLUMINATION_SOURCE_488NM = 12;
static const int ILLUMINATION_SOURCE_638NM = 13;
static const int ILLUMINATION_SOURCE_561NM = 14;
static const int ILLUMINATION_SOURCE_730NM = 15;

#endif // CONSTANTS_PROTOCOL_H
