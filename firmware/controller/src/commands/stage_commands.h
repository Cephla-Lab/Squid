#ifndef STAGE_COMMANDS_H
#define STAGE_COMMANDS_H

#include "../globals.h"
#include "../functions.h"

void callback_move_x();
void callback_move_y();
void callback_move_z();
void callback_move_w();
void callback_move_w2();
void callback_move_to_x();
void callback_move_to_y();
void callback_move_to_z();
void callback_move_to_w();
void callback_set_lim();
void callback_set_lim_switch_polarity();
void callback_set_home_safety_margin();
void callback_set_pid_arguments();
void callback_configure_stepper_driver();
void callback_set_max_velocity_acceleration();
void callback_set_lead_screw_pitch();
void callback_home_or_zero();
void callback_set_offset_velocity();

#endif // STAGE_COMMANDS_H
