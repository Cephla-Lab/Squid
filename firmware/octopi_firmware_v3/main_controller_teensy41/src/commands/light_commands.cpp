#include "light_commands.h"

void callback_turn_on_illumination()
{
    // mcu_cmd_execution_in_progress = true;
    turn_on_illumination();
    // mcu_cmd_execution_in_progress = false;
    // these are atomic operations - do not change the mcu_cmd_execution_in_progress flag
}

void callback_turn_off_illumination()
{
    turn_off_illumination();
}

void callback_set_illumination()
{
    set_illumination(buffer_rx[2], (uint16_t(buffer_rx[3]) << 8) + uint16_t(buffer_rx[4])); //important to have "<<8" with in "()"
}

void callback_set_illumination_led_matrix()
{
    set_illumination_led_matrix(buffer_rx[2], buffer_rx[3], buffer_rx[4], buffer_rx[5]);
}

void callback_set_illumination_intensity_factor()
{
    uint8_t factor   = uint8_t(buffer_rx[2]);
    if (factor > 100) factor = 100;
    illumination_intensity_factor = float(factor) / 100;
}
