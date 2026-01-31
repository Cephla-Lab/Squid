#ifndef COMMANDS_H
#define COMMANDS_H

#include "../constants.h"
#include "./stage_commands.h"
#include "./light_commands.h"

extern CommandCallback cmd_map[256];

void init_callbacks();

void callback_default();

void callback_ack_joystick_button_pressed();
void callback_analog_write_onboard_dac();
void callback_set_dac80508_defdiv_gain();
void callback_set_strobe_delay();
void callback_send_hardware_trigger();
void callback_set_pin_level();
void callback_configure_stage_pid();
void callback_enable_stage_pid();
void callback_disable_stage_pid();
void callback_initfilterwheel();
void callback_initfilterwheel_w2();
void callback_set_axis_disable_enable();
void callback_set_trigger_mode();
void callback_initialize();
void callback_reset();

#endif // COMMANDS_H
