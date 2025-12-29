#ifndef FUNCTIONS_H
#define FUNCTIONS_H

#include "constants.h"
#include "globals.h"

#include <Arduino.h>
#include <SPI.h>
#include <FastLED.h>
#include <PacketSerial.h>

void set_DAC8050x_gain(uint8_t div, uint8_t gains);
void set_DAC8050x_default_gain();
void set_DAC8050x_config();
void set_DAC8050x_output(int channel, uint16_t value);

/***************************************************************************************************/
/*******************************************  LED Array  *******************************************/
/***************************************************************************************************/
void set_all(CRGB * matrix, uint8_t r, uint8_t g, uint8_t b);
void set_left(CRGB * matrix, uint8_t r, uint8_t g, uint8_t b);
void set_right(CRGB * matrix, uint8_t r, uint8_t g, uint8_t b);
void set_top(CRGB * matrix, uint8_t r, uint8_t g, uint8_t b);
void set_bottom(CRGB * matrix, uint8_t r, uint8_t g, uint8_t b);
void set_low_na(CRGB * matrix, uint8_t r, uint8_t g, uint8_t b);
void set_left_dot(CRGB * matrix, uint8_t r, uint8_t g, uint8_t b);
void set_right_dot(CRGB * matrix, uint8_t r, uint8_t g, uint8_t b);
void clear_matrix(CRGB * matrix);
void turn_on_LED_matrix_pattern(CRGB * matrix, int pattern, uint8_t led_matrix_r, uint8_t led_matrix_g, uint8_t led_matrix_b);

/***************************************************************************************************/
/************************************ camera trigger and strobe ************************************/
/***************************************************************************************************/
extern bool trigger_output_level[6];
extern bool control_strobe[6];
// bool strobe_output_level[6] = {LOW, LOW, LOW, LOW, LOW, LOW};
// bool strobe_on[6] = {false, false, false, false, false, false};
extern unsigned long strobe_delay[6];
extern long illumination_on_time[6];
extern long timestamp_trigger_rising_edge[6];
extern IntervalTimer strobeTimer;

/***************************************************************************************************/
/***************************************** illumination ********************************************/
/***************************************************************************************************/
extern CRGB matrix[NUM_LEDS];
void turn_on_illumination();
void turn_off_illumination();
void set_illumination(int source, uint16_t intensity);
void set_illumination_led_matrix(int source, uint8_t r, uint8_t g, uint8_t b);
void ISR_strobeTimer();

/***************************************************************************************************/
/******************************************* joystick **********************************************/
/***************************************************************************************************/
extern PacketSerial joystick_packetSerial;

void onJoystickPacketReceived(const uint8_t* buffer, size_t size);

/***************************************************************************************************/
/*********************************************  utils  *********************************************/
/***************************************************************************************************/
long signed2NBytesUnsigned(long signedLong, int N);
int sgn(int val);

#endif // FUNCTIONS_H
