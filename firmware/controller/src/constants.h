#ifndef CONSTANTS_H
#define CONSTANTS_H

#include "def/def_v1.h"

#include "tmc/TMC4361A_TMC2660_Utils.h"

// default axes, such as X,Y,Z
#define STAGE_AXES 3
#define DEBUG_MODE false

// PID arguments
typedef struct pid_arguments {
	uint16_t 	p;
	uint8_t 	i; 
	uint8_t 	d; 
} PID_ARGUMENTS;

/***************************************************************************************************/
/***************************************** Communications ******************************************/
/***************************************************************************************************/
// byte[0]: which motor to move: 0 x, 1 y, 2 z, 3 LED, 4 Laser
// byte[1]: what direction: 1 forward, 0 backward
// byte[2]: how many micro steps - upper 8 bits
// byte[3]: how many micro steps - lower 8 bits

static const int CMD_LENGTH = 8;
static const int MSG_LENGTH = 24;

// command sets
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
static const int SET_PIN_LEVEL = 41;
static const int INITFILTERWHEEL = 253;
static const int INITIALIZE = 254;
static const int RESET = 255;

static const int COMPLETED_WITHOUT_ERRORS = 0;
static const int IN_PROGRESS = 1;
static const int CMD_CHECKSUM_ERROR = 2;
static const int CMD_INVALID = 3;
static const int CMD_EXECUTION_ERROR = 4;

static const int HOME_NEGATIVE = 1;
static const int HOME_POSITIVE = 0;
static const int HOME_OR_ZERO_ZERO = 2;

static const int AXIS_X = 0;
static const int AXIS_Y = 1;
static const int AXIS_Z = 2;
static const int AXIS_THETA = 3;
static const int AXES_XY = 4;
static const int AXIS_W = 5;

static const int BIT_POS_JOYSTICK_BUTTON = 0;

static const int LIM_CODE_X_POSITIVE = 0;
static const int LIM_CODE_X_NEGATIVE = 1;
static const int LIM_CODE_Y_POSITIVE = 2;
static const int LIM_CODE_Y_NEGATIVE = 3;
static const int LIM_CODE_Z_POSITIVE = 4;
static const int LIM_CODE_Z_NEGATIVE = 5;

static const int ACTIVE_LOW = 0;
static const int ACTIVE_HIGH = 1;
static const int DISABLED = 2;

/***************************************************************************************************/
/**************************************** Pin definations ******************************************/
/***************************************************************************************************/
// Teensy4.1 board v1 def

// illumination
static const int LASER_405nm = 5;   // to rename
static const int LASER_488nm = 4;   // to rename
static const int LASER_561nm = 22;   // to rename
static const int LASER_638nm = 3;  // to rename
static const int LASER_730nm = 23;  // to rename
static const int LASER_INTERLOCK = 1;
// PWM6 2
// PWM7 1
// PWM8 0

// output pins
//static const int digitial_output_pins = {2,1,6,7,8,9,10,15,24,25} // PWM 6-7, 9-16
//static const int num_digital_pins = 10;
// pin 7,8 (PWM 10, 11) may be used for UART, pin 24,25 (PWM 15, 16) may be used for UART
static const int num_digital_pins = 6;
static const int digitial_output_pins[num_digital_pins] = {2, 1, 6, 9, 10, 15}; // PWM 6-7, 9, 12-14

// camera trigger
static const int camera_trigger_pins[] = {29, 30, 31, 32, 16, 28}; // trigger 1-6

// motors
const uint8_t pin_TMC4361_CS[4] = {41, 36, 35, 34};
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
static const int ILLUMINATION_SOURCE_LED_ARRAY_FULL = 0;
static const int ILLUMINATION_SOURCE_LED_ARRAY_LEFT_HALF = 1;
static const int ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_HALF = 2;
static const int ILLUMINATION_SOURCE_LED_ARRAY_LEFTB_RIGHTR = 3;
static const int ILLUMINATION_SOURCE_LED_ARRAY_LOW_NA = 4;
static const int ILLUMINATION_SOURCE_LED_EXTERNAL_FET = 20;
static const int ILLUMINATION_SOURCE_LED_ARRAY_LEFT_DOT = 5;
static const int ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_DOT = 6;
static const int ILLUMINATION_SOURCE_LED_ARRAY_TOP_HALF = 7;
static const int ILLUMINATION_SOURCE_LED_ARRAY_BOTTOM_HALF = 8;
static const int ILLUMINATION_SOURCE_405NM = 11;
static const int ILLUMINATION_SOURCE_488NM = 12;
static const int ILLUMINATION_SOURCE_638NM = 13;
static const int ILLUMINATION_SOURCE_561NM = 14;
static const int ILLUMINATION_SOURCE_730NM = 15;
#define NUM_LEDS 128 // DOTSTAR_NUM_LEDS
#define LED_MATRIX_DATA_PIN 26
#define LED_MATRIX_CLOCK_PIN 27

/***************************************************************************************************/
/******************************************* steppers **********************************************/
/***************************************************************************************************/
const uint32_t clk_Hz_TMC4361 = 16000000;
const uint8_t lft_sw_pol[4] = {0, 0, 0, 0};
const uint8_t rht_sw_pol[4] = {0, 0, 0, 0};
const uint8_t TMC4361_homing_sw[4] = {LEFT_SW, LEFT_SW, RGHT_SW, LEFT_SW};
const int32_t vslow = 0x04FFFC00;

typedef void (*CommandCallback)();

#endif // CONSTANTS_H
