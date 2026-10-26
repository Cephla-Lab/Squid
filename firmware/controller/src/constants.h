#ifndef CONSTANTS_H
#define CONSTANTS_H

#include "constants_protocol.h"

/***************************************************************************************************/
/**************************************** Firmware Version *****************************************/
/***************************************************************************************************/
// Version is sent in response byte 22 as nibble-encoded: high nibble = major, low nibble = minor
// Version 1.0 = first version with multi-port illumination support
// Version 1.1 = serial watchdog for illumination auto-shutoff
// Version 1.2 = CMD_EXECUTION_ERROR reported on failed moves + MOVETO_W2 command
// Version 1.3 = strobe ISR latches illumination source at start (race fix when
//               channel switched during live HW-triggered acquisition)
// Version 1.4 = filter-wheel (W/W2) home resets xmin/xmax to full range (rotary
//               axis has no travel end-stop); fixes intermittent post-home
//               MOVETO_W CMD_EXECUTION_ERROR (home left xmin = L - C > offset)
// Version 1.5 = TMC2240 stepper driver support: each axis's power stage is
//               identified at init (TMC2660 or TMC2240) behind a driver seam in
//               src/tmc/drivers/, so one image serves both board populations.
//               An axis whose driver could not be identified is left
//               unconfigured and rejects moves. No protocol change: command
//               codes, packet layout and the host contract are untouched
// Version 1.6 = filter-wheel speed and diagnostics. SET_RAMP_PROFILE (47) selects a
//               trapezoidal or S-shaped ramp per axis (default S-shape, as before).
//               SET_COMPLETION_WINDOW (49) lets an axis report a move complete once
//               within a set distance of the target while the ramp finishes (filter
//               wheels: start exposing while the last degrees travel; default 0 = off).
//               SET_ENCODER_REPORTING (44) puts one axis's encoder into the status
//               packet's unused theta field (the wheels have no position field, so
//               this is the only way to see where a wheel is; default off: packet
//               byte-identical to 1.5). For every axis: a status packet is sent the
//               moment a command completes, and completion is checked every 1 ms
//               (was 10 ms), so the host learns of completion up to ~20 ms sooner.
// Version 1.7 = configurable limit switches. SET_LIMIT_SWITCH_CONFIG (64) sets one
//               axis's (X/Y/Z) limit inputs - enable and polarity per side, the
//               LIMIT_SWITCH_* nibble - applies it at once and saves it in EEPROM
//               bytes 0-6 ('L', 'S', record version 1, X, Y, Z nibbles, CRC-8;
//               written only when it changes). Boot, INITIALIZE and the home safety
//               margin program X/Y/Z from that record (blank or invalid: both inputs
//               enabled, active-low, as before), so the settings hold from power-up,
//               before a host connects. Status byte 18 bits 2-3 report Z's left and
//               right limit inputs as triggered (always 0 before 1.7). Homing and
//               the move-completion check ignore a disabled input (it still reads
//               active in STATUS), except the input being homed toward.
//               SET_LIM_SWITCH_POLARITY (20) is retired: no handler, so it answers
//               success and does nothing like any unknown command; never reuse 20.
//               TMC2240: CURRENT_RANGE_XY/Z/W became per-axis floors, stepped up
//               only when a current needs it, so a current 1.6 refused (e.g. Z
//               1000 mA) is now accepted; every current 1.6 accepted keeps its range.
//               The number may move to the next free minor at merge: the hardware
//               sequencer (#655/#656) also claims 1.7.
// Version 1.8 = focus wheel to host. SET_FOCUS_WHEEL_MODE (65) FOCUS_WHEEL_TO_HOST
//               stops the control panel's focus wheel from moving Z and counts
//               its ticks instead: status bytes 14-17 carry the count (int32,
//               wrapping, 0 when enabled) and byte 19 bit 7 marks it, so the host
//               can drive another focus axis (a PI V-308 while Z is a lift).
//               Refused while encoder reporting is on (same bytes) and the other
//               way round. FOCUS_WHEEL_DRIVES_Z is the 1.7 behaviour, packet
//               unchanged. With FOCUS_WHEEL_SAVE_AS_POWER_UP ([3]) the mode is
//               also saved (EEPROM 8-11, written only on change) and used at
//               power-up and after RESET and INITIALIZE; unsaved, that is
//               DRIVES_Z. A lift system saves TO_HOST: with no host running,
//               the wheel moves nothing instead of the lift.
//               SET_PANEL_LOCK (66) locks the control panel: joystick X/Y reads
//               centred and wheel turns are dropped (a lift system at its
//               loading position). Not saved; RESET and INITIALIZE unlock.
// Version 1.9 = startup state, so a relaunched host can skip startup homing when
//               the stage is still homed (utils/homing_validity.h). Per X/Y/Z
//               homed flag: set when a home started and finished with power good
//               and the driver enabled; cleared by zeroing the axis, disabling its
//               driver, power good low, INITIALIZE and power-up. RESET keeps it.
//               STORE_CONFIG_SIGNATURE (67) keeps one signature of the host's
//               configuration; a covered X/Y/Z setting (pitch, driver,
//               velocity/acceleration, home margin, limit switches) that arrives
//               with different bytes empties it. Storing a different signature,
//               or any signature after it was emptied, clears every flag.
//               QUERY_STARTUP_STATE (68) answers in the packet status byte (byte 1):
//               STARTUP_VERDICT_STATUS_BASE (64) + STARTUP_VERDICT_*, until the next
//               command, even while a command is still IN_PROGRESS. Every other
//               packet is unchanged, and the verdict does not touch byte 18.
//               The number may move to the next free minor at merge: the piezo
//               focus wheel design also claims 1.9.
#define FIRMWARE_VERSION_MAJOR 1
#define FIRMWARE_VERSION_MINOR 9

#include "def/def_v1.h"

#include "tmc/TMC4361A_Utils.h"

// default axes, such as X,Y,Z
#define STAGE_AXES 3
// total axes including filter wheels (X, Y, Z, W, W2)
#define TOTAL_AXES 5
#define DEBUG_MODE false

// PID arguments
typedef struct pid_arguments {
	uint16_t 	p;
	uint8_t 	i;
	uint8_t 	d;
} PID_ARGUMENTS;

/***************************************************************************************************/
/**************************************** Pin definations ******************************************/
/***************************************************************************************************/
// Teensy4.1 board v1 def

// Illumination Control TTL Ports - GPIO pin assignments
// Pin numbers are based on PCB layout, not sequential
static const int PIN_ILLUMINATION_D1 = 5;
static const int PIN_ILLUMINATION_D2 = 4;
static const int PIN_ILLUMINATION_D3 = 22;
static const int PIN_ILLUMINATION_D4 = 3;
static const int PIN_ILLUMINATION_D5 = 23;
static const int PIN_ILLUMINATION_INTERLOCK = 2;

// Legacy aliases (deprecated, kept for compatibility)
static const int LASER_405nm = PIN_ILLUMINATION_D1;
static const int LASER_488nm = PIN_ILLUMINATION_D2;
static const int LASER_561nm = PIN_ILLUMINATION_D3;
static const int LASER_638nm = PIN_ILLUMINATION_D4;
static const int LASER_730nm = PIN_ILLUMINATION_D5;
static const int LASER_INTERLOCK = PIN_ILLUMINATION_INTERLOCK;

// Laser safety interlock check
#ifdef DISABLE_LASER_INTERLOCK
static inline bool INTERLOCK_OK() { return true; }
#else
static inline bool INTERLOCK_OK() { return digitalRead(PIN_ILLUMINATION_INTERLOCK) == LOW; }
#endif

// PWM6 2
// PWM7 1
// PWM8 0

// output pins
//static const int digitial_output_pins = {2,1,6,7,8,9,10,15,24,25} // PWM 6-7, 9-16
//static const int num_digital_pins = 10;
// pin 7,8 (PWM 10, 11) may be used for UART, pin 24,25 (PWM 15, 16) may be used for UART
static const int num_digital_pins = 4;
static const int digitial_output_pins[num_digital_pins] = {6, 9, 10, 15}; // PWM 9, 12-14

// camera trigger
static const int camera_trigger_pins[] = {29, 30, 31, 32}; // trigger 1-4 (pin 16 used for W2 CS, pin 28 used for W2 CLK)

// W2 clock pin
const uint8_t pin_TMC4361_CLK_W2 = 28;

// motors
// Chip select pins: [0]=X, [1]=Y, [2]=Z, [3]=W (filter wheel 1), [4]=W2 (filter wheel 2)
const uint8_t pin_TMC4361_CS[TOTAL_AXES] = {41, 36, 35, 34, 16};
const uint8_t pin_TMC4361_CLK = 37;

// DAC
const int DAC8050x_CS_pin = 33;

// LED driver
const int pin_LT3932_SYNC = 25;

// power good
const int pin_PG = 0;

/***************************************************************************************************/
/************************************ camera trigger and strobe ************************************/
/***************************************************************************************************/
static const int TRIGGER_PULSE_LENGTH_us = 50;
static const int strobeTimer_interval_us = 100;

/***************************************************************************************************/
/******************************************* DAC80508 **********************************************/
/***************************************************************************************************/
const uint8_t DAC8050x_DAC_ADDR = 0x08;
const uint8_t DAC8050x_GAIN_ADDR = 0x04;
const uint8_t DAC8050x_CONFIG_ADDR = 0x03;

/***************************************************************************************************/
/******************************************** timing ***********************************************/
/***************************************************************************************************/
// IntervalTimer does not work on teensy with SPI, the below lines are to be removed
static const int TIMER_PERIOD = 500; // in us
static const int interval_send_pos_update = 10000; // in us
static const int interval_check_position = 1000;  // in us (was 10000: completion was detected up to 10 ms late)
static const int interval_send_joystick_update = 30000; // in us
static const int interval_check_limit = 20000; // in us

/***************************************************************************************************/
/******************************************* joystick **********************************************/
/***************************************************************************************************/
// packet layout and length: JP_MSG_LENGTH in utils/joystick_panel.h

/***************************************************************************************************/
/***************************************** illumination ********************************************/
/***************************************************************************************************/
static const int LED_MATRIX_MAX_INTENSITY = 100;
static const float GREEN_ADJUSTMENT_FACTOR = 1;
static const float RED_ADJUSTMENT_FACTOR = 1;
static const float BLUE_ADJUSTMENT_FACTOR = 1;
#define NUM_LEDS 128 // DOTSTAR_NUM_LEDS
#define LED_MATRIX_DATA_PIN 26
#define LED_MATRIX_CLOCK_PIN 27

// Serial watchdog (illumination auto-shutoff safety)
// If no serial message is received within timeout, firmware turns off all illumination
#define DEFAULT_WATCHDOG_TIMEOUT_MS 5000
#define MAX_WATCHDOG_TIMEOUT_MS 3600000

/***************************************************************************************************/
/******************************************* steppers **********************************************/
/***************************************************************************************************/
const uint32_t clk_Hz_TMC4361 = 16000000;
const uint8_t lft_sw_pol[TOTAL_AXES] = {0, 0, 0, 0, 0};
const uint8_t rht_sw_pol[TOTAL_AXES] = {0, 0, 0, 0, 0};
const uint8_t TMC4361_homing_sw[TOTAL_AXES] = {LEFT_SW, LEFT_SW, RGHT_SW, LEFT_SW, LEFT_SW};
const int32_t vslow = 0x04FFFC00;

typedef void (*CommandCallback)();

/***************************************************************************************************/
/*************************************** axis mapping **********************************************/
/***************************************************************************************************/
// Maps protocol axis constants to internal array indices for safe array access.
//
// Why this is needed:
// - Protocol constants (constants_protocol.h): AXIS_X=0, AXIS_Y=1, AXIS_Z=2, AXIS_W=5, AXIS_W2=6
// - Internal indices (def/def_v1.h): x=1, y=0, z=2, w=3, w2=4
//
// The mismatch exists because:
// - Protocol values are for software-firmware communication (historical API)
// - Internal indices match hardware wiring (x/y swapped, filter wheels at indices 3-4)
//
// Always use this function when buffer_rx[2] (axis from command) is used for array access.
// Returns 0xFF for invalid/unsupported axis values.
inline uint8_t protocol_axis_to_internal(int protocol_axis)
{
    switch (protocol_axis)
    {
        case AXIS_X:  return x;
        case AXIS_Y:  return y;
        case AXIS_Z:  return z;
        case AXIS_W:  return w;
        case AXIS_W2: return w2;
        default:      return 0xFF;  // Invalid axis
    }
}

// Inverse of protocol_axis_to_internal(): internal array index -> protocol axis id.
// Used by the status packet's encoder-reporting flags. Returns 0xFF for an
// index that is not a controlled axis.
inline uint8_t internal_axis_to_protocol(uint8_t internal_axis)
{
    switch (internal_axis)
    {
        case x:  return AXIS_X;
        case y:  return AXIS_Y;
        case z:  return AXIS_Z;
        case w:  return AXIS_W;
        case w2: return AXIS_W2;
        default: return 0xFF;
    }
}

#endif // CONSTANTS_H
