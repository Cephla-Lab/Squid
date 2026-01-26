/**
 * TTL-Only Firmware - Constants and Pin Definitions
 *
 * Simplified firmware for controlling 5 TTL light sources only.
 * Compatible with existing Squid software protocol.
 */

#ifndef CONSTANTS_H
#define CONSTANTS_H

#include <Arduino.h>

/***************************************************************************************************/
/***************************************** Communications ******************************************/
/***************************************************************************************************/
// Command packet: 8 bytes (same as main firmware)
static const int CMD_LENGTH = 8;
static const int MSG_LENGTH = 24;

// Command codes (subset - only what we handle)
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
static const int SET_HOME_SAFETY_MERGIN = 28;
static const int SET_PID_ARGUMENTS = 29;
static const int SEND_HARDWARE_TRIGGER = 30;
static const int SET_STROBE_DELAY = 31;
static const int SET_AXIS_DISABLE_ENABLE = 32;
static const int SET_TRIGGER_MODE = 33;
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

// Illumination source codes (laser/TTL sources only)
static const int ILLUMINATION_SOURCE_LED_ARRAY_FULL = 0;  // Not supported - will be ignored
static const int ILLUMINATION_SOURCE_405NM = 11;
static const int ILLUMINATION_SOURCE_488NM = 12;
static const int ILLUMINATION_SOURCE_638NM = 13;
static const int ILLUMINATION_SOURCE_561NM = 14;
static const int ILLUMINATION_SOURCE_730NM = 15;

/***************************************************************************************************/
/**************************************** Pin Definitions ******************************************/
/***************************************************************************************************/
// TTL outputs for light sources (directly accent pins 1-5)
static const int LASER_405nm = 1;
static const int LASER_488nm = 2;
static const int LASER_561nm = 3;
static const int LASER_638nm = 4;
static const int LASER_730nm = 5;

// Laser interlock disabled - always returns OK
#define DISABLE_LASER_INTERLOCK
static inline bool INTERLOCK_OK() { return true; }

// DAC for intensity control
static const int DAC8050x_CS_pin = 33;
static const uint8_t DAC8050x_DAC_ADDR = 0x08;
static const uint8_t DAC8050x_GAIN_ADDR = 0x04;
static const uint8_t DAC8050x_CONFIG_ADDR = 0x03;

/***************************************************************************************************/
/******************************************** Timing ***********************************************/
/***************************************************************************************************/
static const int interval_send_pos_update = 10000; // in us (10ms)

/***************************************************************************************************/
/****************************************** Callbacks **********************************************/
/***************************************************************************************************/
typedef void (*CommandCallback)();

#endif // CONSTANTS_H
