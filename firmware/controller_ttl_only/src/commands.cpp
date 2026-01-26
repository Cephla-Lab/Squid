/**
 * TTL-Only Firmware - Command Handlers Implementation
 */

#include "commands.h"

void init_callbacks()
{
    // Illumination commands - these actually do something
    cmd_map[TURN_ON_ILLUMINATION] = &callback_turn_on_illumination;
    cmd_map[TURN_OFF_ILLUMINATION] = &callback_turn_off_illumination;
    cmd_map[SET_ILLUMINATION] = &callback_set_illumination;
    cmd_map[SET_ILLUMINATION_INTENSITY_FACTOR] = &callback_set_illumination_intensity_factor;
    cmd_map[SET_DAC80508_REFDIV_GAIN] = &callback_set_dac_gain;
    cmd_map[ANALOG_WRITE_ONBOARD_DAC] = &callback_analog_write_dac;

    // System commands
    cmd_map[INITIALIZE] = &callback_initialize;
    cmd_map[RESET] = &callback_reset;

    // All other commands (stage movement, homing, PID, etc.) are NOT registered.
    // They will call callback_default() which ACKs without execution.
    // This ensures software doesn't timeout waiting for a response.
}

void callback_default()
{
    // No-op: Command is acknowledged via send_position_update()
    // with COMPLETED_WITHOUT_ERRORS status.
    // This allows the software to continue without timeout.
}

/***************************************************************************************************/
/************************************** Illumination Callbacks *************************************/
/***************************************************************************************************/

void callback_turn_on_illumination()
{
    turn_on_illumination();
}

void callback_turn_off_illumination()
{
    turn_off_illumination();
}

void callback_set_illumination()
{
    int source = buffer_rx[2];
    uint16_t intensity = (uint16_t(buffer_rx[3]) << 8) + uint16_t(buffer_rx[4]);
    set_illumination(source, intensity);
}

void callback_set_illumination_intensity_factor()
{
    uint8_t factor = uint8_t(buffer_rx[2]);
    if (factor > 100)
        factor = 100;
    illumination_intensity_factor = float(factor) / 100.0f;
}

void callback_set_dac_gain()
{
    uint8_t div = buffer_rx[2];
    uint8_t gains = buffer_rx[3];
    set_dac_gain(div, gains);
}

void callback_analog_write_dac()
{
    int channel = buffer_rx[2];
    uint16_t value = (uint16_t(buffer_rx[3]) << 8) + uint16_t(buffer_rx[4]);
    set_dac_output(channel, value);
}

/***************************************************************************************************/
/**************************************** System Callbacks *****************************************/
/***************************************************************************************************/

void callback_initialize()
{
    // Reset illumination state
    illumination_source = 0;
    illumination_intensity = 0;
    illumination_is_on = false;

    // Ensure all lasers are off
    turn_off_all_lasers();

    // Re-initialize DAC
    init_dac();
}

void callback_reset()
{
    // Reset command tracking
    cmd_id = 0;

    // Turn off all lasers
    turn_off_all_lasers();

    // Reset illumination state
    illumination_source = 0;
    illumination_intensity = 0;
    illumination_is_on = false;
}
