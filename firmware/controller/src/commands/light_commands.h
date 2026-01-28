#ifndef LIGHT_COMMANDS_H
#define LIGHT_COMMANDS_H

#include "../functions.h"

void callback_turn_on_illumination();
void callback_turn_off_illumination();
void callback_set_illumination();
void callback_set_illumination_led_matrix();
void callback_set_illumination_intensity_factor();

// Multi-port illumination commands (firmware v1.0+)
void callback_set_port_intensity();
void callback_turn_on_port();
void callback_turn_off_port();
void callback_set_port_illumination();
void callback_set_multi_port_mask();
void callback_turn_off_all_ports();

#endif // LIGHT_COMMANDS_H
