#ifndef CONSTANTS_H
#define CONSTANTS_H

#include "constants_protocol.h"

#include "def/def_v1.h"

#include "tmc/TMC4361A_TMC2660_Utils.h"

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
static const int interval_check_position = 10000; // in us
static const int interval_send_joystick_update = 30000; // in us
static const int interval_check_limit = 20000; // in us

/***************************************************************************************************/
/******************************************* joystick **********************************************/
/***************************************************************************************************/
static const int JOYSTICK_MSG_LENGTH = 10;

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

#endif // CONSTANTS_H
