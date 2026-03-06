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

/***************************************************************************************************/
/*************************** Multi-port illumination commands (v1.0+) ******************************/
/***************************************************************************************************/

// Command byte layout: [cmd_id, 34, port, intensity_hi, intensity_lo, 0, 0, crc]
void callback_set_port_intensity()
{
    int port_index = buffer_rx[2];
    uint16_t intensity = (uint16_t(buffer_rx[3]) << 8) | uint16_t(buffer_rx[4]);
    set_port_intensity(port_index, intensity);
}

// Command byte layout: [cmd_id, 35, port, 0, 0, 0, 0, crc]
void callback_turn_on_port()
{
    int port_index = buffer_rx[2];
    turn_on_port(port_index);
}

// Command byte layout: [cmd_id, 36, port, 0, 0, 0, 0, crc]
void callback_turn_off_port()
{
    int port_index = buffer_rx[2];
    turn_off_port(port_index);
}

// Command byte layout: [cmd_id, 37, port, intensity_hi, intensity_lo, on_flag, 0, crc]
void callback_set_port_illumination()
{
    int port_index = buffer_rx[2];
    uint16_t intensity = (uint16_t(buffer_rx[3]) << 8) | uint16_t(buffer_rx[4]);
    bool turn_on = buffer_rx[5] != 0;

    set_port_intensity(port_index, intensity);
    if (turn_on)
        turn_on_port(port_index);
    else
        turn_off_port(port_index);
}

// Command byte layout: [cmd_id, 38, mask_hi, mask_lo, on_hi, on_lo, 0, crc]
// port_mask: which ports to update (bit 0 = D1, bit 15 = D16)
// on_mask: for selected ports, which to turn ON (1) vs OFF (0)
void callback_set_multi_port_mask()
{
    uint16_t port_mask = (uint16_t(buffer_rx[2]) << 8) | uint16_t(buffer_rx[3]);
    uint16_t on_mask = (uint16_t(buffer_rx[4]) << 8) | uint16_t(buffer_rx[5]);

    for (int i = 0; i < NUM_ILLUMINATION_PORTS; i++)
    {
        if (port_mask & (1 << i))  // If this port is selected
        {
            if (on_mask & (1 << i))
                turn_on_port(i);
            else
                turn_off_port(i);
        }
        // Ports not in port_mask are left unchanged
    }
}

// Command byte layout: [cmd_id, 39, 0, 0, 0, 0, 0, crc]
void callback_turn_off_all_ports()
{
    turn_off_all_ports();
}
