#ifndef FUNCTIONS_H
#define FUNCTIONS_H

#include "constants.h"
#include "globals.h"
#include "utils/illumination_mapping.h"

#include <Arduino.h>
#include <SPI.h>
#include <FastLED.h>
#include <PacketSerial.h>

// No library may print on the protocol serial port: FastLED 3.10.5 and later log to that same USB
// port and the host loses packet sync. Check the log level FastLED itself RESOLVED, not our flag:
// since 3.10.8 that is FL_LOG_LEVEL (a -DFL_LOG_LEVEL overrides FASTLED_LOG_VERBOSITY, and with
// neither set errors still print); 3.10.5 resolves FASTLED_LOG_VERBOSITY. A FastLED that defines
// neither predates that logging and passes: Teensyduino's bundled 3.4.0, and 3.10.3, whose debug
// output CI checks in the linked image instead. The flag has to reach EVERY compile, FastLED's own
// sources included - platformio.ini sets -D FASTLED_LOG_VERBOSITY=0; a #define here would not.
#if defined(FL_LOG_LEVEL)
#if FL_LOG_LEVEL != 0
#error "FastLED logging is compiled in (FL_LOG_LEVEL != 0) - it would print on the protocol serial port; see firmware/README.md"
#endif
#elif defined(FASTLED_LOG_VERBOSITY)
#if FASTLED_LOG_VERBOSITY != 0
#error "FastLED logging is compiled in (FASTLED_LOG_VERBOSITY != 0) - it would print on the protocol serial port; see firmware/README.md"
#endif
#endif

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
extern uint32_t illumination_on_time[6];
extern long timestamp_trigger_rising_edge[6];

// 0: normal trigger mode
// 1: level trigger mode
extern volatile uint8_t trigger_mode;
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

// Multi-port illumination control
// illumination_source_to_port_index() is provided by utils/illumination_mapping.h
// Gets GPIO pin for port index, returns -1 for invalid port
int port_index_to_pin(int port_index);
// Per-port control functions (interlock checked for turn_on)
void turn_on_port(int port_index);
void turn_off_port(int port_index);
void set_port_intensity(int port_index, uint16_t intensity);
void turn_off_all_ports();

/***************************************************************************************************/
/******************************************* joystick **********************************************/
/***************************************************************************************************/
extern PacketSerial joystick_packetSerial;

bool panel_locked_out();   // a commanded move or a homing is in progress: the panel's input is dropped
void onJoystickPacketReceived(const uint8_t* buffer, size_t size);

/***************************************************************************************************/
/*********************************************  utils  *********************************************/
/***************************************************************************************************/
long signed2NBytesUnsigned(long signedLong, int N);
int sgn(int val);

#endif // FUNCTIONS_H
