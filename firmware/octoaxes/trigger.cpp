#include "trigger.h"
#include "build_opt.h"
#include "illumination.h"

// =============================================================================
// State-variable definitions
// =============================================================================

bool          trigger_output_level[NUM_TRIGGER_CHANNELS];
bool          control_strobe[NUM_TRIGGER_CHANNELS];
bool          strobe_output_level[NUM_TRIGGER_CHANNELS];
bool          strobe_on[NUM_TRIGGER_CHANNELS];
unsigned long strobe_delay_us[NUM_TRIGGER_CHANNELS];
uint32_t      illumination_on_time_us[NUM_TRIGGER_CHANNELS];
unsigned long timestamp_trigger_rising_edge[NUM_TRIGGER_CHANNELS];
volatile uint8_t trigger_mode = TRIGGER_MODE_NORMAL;

// Joystick state
bool          joystick_button_pressed = false;
unsigned long joystick_button_pressed_timestamp = 0;

// Strobe timer
static IntervalTimer strobeTimer;

// =============================================================================
// Initialization
// =============================================================================

void trigger_init()
{
    // initialize the trigger pins: OUTPUT + HIGH (idle is high, negative-pulse triggered)
    for (int i = 0; i < NUM_TRIGGER_CHANNELS; i++) {
        pinMode(camera_trigger_pins[i], OUTPUT);
        digitalWrite(camera_trigger_pins[i], HIGH);
    }

    // initialize the state arrays
    for (int i = 0; i < NUM_TRIGGER_CHANNELS; i++) {
        trigger_output_level[i] = HIGH;
        control_strobe[i] = false;
        strobe_output_level[i] = LOW;
        strobe_on[i] = false;
        strobe_delay_us[i] = 0;
        illumination_on_time_us[i] = 0;
        timestamp_trigger_rising_edge[i] = 0;
    }

    trigger_mode = TRIGGER_MODE_NORMAL;

    // start the strobe timer (100us interval)
    strobeTimer.begin(ISR_strobeTimer, STROBE_TIMER_INTERVAL_us);

    DEBUG_PRINTLN("Trigger system initialized");
}

// =============================================================================
// main-loop update: manage trigger-pulse recovery
// =============================================================================

void trigger_update()
{
    unsigned long now = micros();

    for (int i = 0; i < NUM_TRIGGER_CHANNELS; i++) {
        // only process channels that have been triggered (LOW)
        if (trigger_output_level[i] == LOW) {
            if (trigger_mode == TRIGGER_MODE_NORMAL) {
                // mode 0: restore HIGH after a fixed 50us pulse width
                if (now - timestamp_trigger_rising_edge[i] >= TRIGGER_PULSE_LENGTH_us) {
                    digitalWrite(camera_trigger_pins[i], HIGH);
                    trigger_output_level[i] = HIGH;
                }
            } else {
                // mode 1: pulse width = strobe_delay + illumination_on_time
                unsigned long pulse_duration = strobe_delay_us[i] + illumination_on_time_us[i];
                if (now - timestamp_trigger_rising_edge[i] >= pulse_duration) {
                    digitalWrite(camera_trigger_pins[i], HIGH);
                    trigger_output_level[i] = HIGH;
                }
            }
        }
    }
}

// =============================================================================
// strobe timer ISR (100us interval)
// =============================================================================

void ISR_strobeTimer()
{
    unsigned long now = micros();

    for (int i = 0; i < NUM_TRIGGER_CHANNELS; i++) {
        // only process triggered channels that have strobe control enabled
        if (!control_strobe[i] || trigger_output_level[i] == HIGH)
            continue;

        unsigned long elapsed = now - timestamp_trigger_rising_edge[i];

        if (illumination_on_time_us[i] <= 30000) {
            // short exposure (<= 30ms): synchronous mode
            // wait strobe_delay then turn on the light, keep it on for illumination_on_time, then turn off
            if (!strobe_on[i] && elapsed >= strobe_delay_us[i]) {
                turn_on_illumination();
                strobe_on[i] = true;
                // short exposure uses delayMicroseconds for precise control
                delayMicroseconds(illumination_on_time_us[i]);
                turn_off_illumination();
                strobe_on[i] = false;
                control_strobe[i] = false;  // one strobe done, clear the flag
            }
        } else {
            // long exposure (> 30ms): asynchronous mode, split into two steps
            if (!strobe_on[i] && elapsed >= strobe_delay_us[i]) {
                // step 1: turn on the light
                turn_on_illumination();
                strobe_on[i] = true;
            } else if (strobe_on[i] &&
                       elapsed >= strobe_delay_us[i] + illumination_on_time_us[i]) {
                // step 2: turn off the light
                turn_off_illumination();
                strobe_on[i] = false;
                control_strobe[i] = false;  // one strobe done, clear the flag
            }
        }
    }
}
