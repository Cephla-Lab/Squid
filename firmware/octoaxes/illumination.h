#ifndef ILLUMINATION_H
#define ILLUMINATION_H

#include <Arduino.h>
#include "config.h"

// =============================================================================
// Illumination state variables (extern declarations, defined in illumination.cpp)
// =============================================================================
extern int      illumination_source;
extern uint16_t illumination_intensity;
extern float    illumination_intensity_factor;
extern uint8_t  led_matrix_r;
extern uint8_t  led_matrix_g;
extern uint8_t  led_matrix_b;
extern bool     illumination_is_on;
extern bool     illumination_port_is_on[IlluminationConfig::NUM_PORTS];
extern uint16_t illumination_port_intensity[IlluminationConfig::NUM_PORTS];

// =============================================================================
// Initialization
// =============================================================================

// Initialize the illumination hardware: pins, LED matrix, DAC, interlock
void illumination_init();

// Only initialize and clear the LED matrix, idempotent. Should be called as early as possible in setup(),
// before time-consuming init such as initializePowerManagement (waiting for the PG signal),
// to extinguish the APA102 power-on default lit state and minimize the user-perceived "startup glow" window.
void illumination_init_matrix_early();

// =============================================================================
// Safety interlock
// =============================================================================

// Interlock check: pin 2 LOW means safe
// The compile option -DDISABLE_LASER_INTERLOCK can force a true return (for laser-free systems)
bool illumination_interlock_ok();

// =============================================================================
// DAC80508 driver
// =============================================================================

void set_DAC8050x_output(int channel, uint16_t value);
void set_DAC8050x_gain(uint8_t div, uint8_t gains);
void set_DAC8050x_config();
void set_DAC8050x_default_gain();

// =============================================================================
// LED matrix (APA102, 128 pixels)
// =============================================================================

void clear_matrix();
void turn_on_LED_matrix_pattern(int pattern, uint8_t r, uint8_t g, uint8_t b);

// =============================================================================
// Legacy illumination API (single light-source model)
// =============================================================================

// turn the light on/off using the current illumination_source
void turn_on_illumination();
void turn_off_illumination();

// set the light-source code and DAC intensity (may update the output immediately)
void set_illumination(int source, uint16_t intensity);

// set the LED matrix color/pattern (may update the output immediately)
void set_illumination_led_matrix(int source, uint8_t r, uint8_t g, uint8_t b);

// =============================================================================
// New multi-port API (v1.0+)
// =============================================================================

// turn the GPIO of a given port on/off (interlock check required)
void turn_on_port(int port_index);
void turn_off_port(int port_index);

// set the DAC intensity of a given port (written after scaling by illumination_intensity_factor)
void set_port_intensity(int port_index, uint16_t intensity);

// turn off all ports + the LED matrix
void turn_off_all_ports();

// =============================================================================
// Port-mapping helpers
// =============================================================================

// legacy light-source code -> port index (11->0, 12->1, 14->2, 13->3, 15->4; others return -1)
int illumination_source_to_port_index(int source);

// port index -> GPIO pin number (0->5, 1->4, 2->22, 3->3, 4->23; others return -1)
int port_index_to_pin(int port_index);

// port index -> DAC channel (0-4 mapped directly; others return -1)
int port_index_to_dac_channel(int port_index);

// =============================================================================
// Serial watchdog (automatically turns off illumination after a communication loss)
// =============================================================================

// Watchdog default/max timeout (ms)
static const uint32_t DEFAULT_WATCHDOG_TIMEOUT_MS = 5000;
static const uint32_t MAX_WATCHDOG_TIMEOUT_MS = 3600000;  // 1 hour

extern uint32_t last_serial_message_time;
extern uint32_t watchdog_timeout_ms;
extern bool     watchdog_enabled;

// reset the watchdog timer (called whenever a valid serial message is received)
void watchdog_reset_timer();

// set the watchdog timeout and enable it (timeout_ms=0 uses the default; values above the max are clamped)
void watchdog_set_timeout(uint32_t timeout_ms);

// called from the main loop: after timeout, turn off all illumination, single-shot
void watchdog_check();

#endif // ILLUMINATION_H
