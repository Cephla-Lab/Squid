/**
 * TTL-Only Light Source Controller for Teensy 4.1
 *
 * Simplified firmware that only controls 5 TTL triggered light sources.
 * Compatible with existing Squid software - no software changes required.
 *
 * Features:
 * - 5 TTL outputs for laser/LED enable (pins 1, 2, 3, 4, 5)
 * - DAC-based intensity control via DAC80508
 * - Full protocol compatibility with Squid software
 *
 * Non-features (compared to main firmware):
 * - No XYZ stage control
 * - No LED matrix
 * - No filter wheel
 * - No joystick panel support
 *
 * All unsupported commands are ACK'd without execution, so software
 * continues to work without timeouts.
 */

#include "src/init.h"
#include "src/commands.h"
#include "src/illumination.h"
#include "src/serial_communication.h"

void setup()
{
    // Initialize USB serial (2Mbps, same as main firmware)
    init_serial();

    // Initialize TTL output pins
    init_laser_pins();

    // Initialize DAC for intensity control
    init_dac();

    // Register command callbacks
    init_callbacks();
}

void loop()
{
    // Process incoming serial commands
    process_serial_message();

    // Send periodic status response
    send_position_update();
}
