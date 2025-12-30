#ifndef CONSTANTS_H
#define CONSTANTS_H

#include "constants_protocol.h"

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
