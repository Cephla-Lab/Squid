#ifndef STAGE_COMMANDS_H
#define STAGE_COMMANDS_H

#include "../globals.h"
#include "../functions.h"

/*
  Fail-safe gate for every host command that can put an axis in motion. Returns
  true when the axis's power stage was identified by the probe; otherwise sets
  mcu_cmd_execution_status = CMD_EXECUTION_ERROR and returns false. Defined in
  stage_commands.cpp, where the full rationale lives.

  Exposed here for callback_enable_stage_pid in commands.cpp, which is an
  actuator path outside this file. The joystick and focus-wheel gates in
  operations.cpp deliberately do NOT use it: they are not host commands and must
  reject silently.
*/
// Surface a failed motion command from an early-return path that never claimed
// mcu_cmd_execution_in_progress for this command. Leaves in_progress untouched
// so an unrelated motion already in flight on another axis keeps its "still
// working" state. Header-inline: used by stage_commands.cpp and by
// callback_enable_stage_pid in commands.cpp (the closed loop is a motion path).
static inline void report_move_error()
{
    mcu_cmd_execution_status = CMD_EXECUTION_ERROR;
}

bool axis_driver_ready(uint8_t axis);

void callback_move_x();
void callback_move_y();
void callback_move_z();
void callback_move_w();
void callback_move_w2();
void callback_move_to_x();
void callback_move_to_y();
void callback_move_to_z();
void callback_move_to_w();
void callback_move_to_w2();
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
