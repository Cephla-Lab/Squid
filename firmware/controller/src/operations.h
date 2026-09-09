#ifndef OPERATIONS_H
#define OPERATIONS_H

#include "globals.h"
#include "functions.h"
#include "utils/crc8.h"

void prepare_homing_x();
void prepare_homing_y();
void prepare_homing_z();
void prepare_homing_w();
void prepare_homing_w2();

void check_homing_x();
void check_homing_y();
void check_homing_z();
void check_homing_w();
void check_homing_w2();

void finalize_homing_x();
void finalize_homing_y();
void finalize_homing_z();
void finalize_homing_w();
void finalize_homing_w2();
void finalize_homing_xy();

void do_camera_trigger();
void check_joystick();
void do_focus_control();

void check_position();
void check_limits();
void check_closed_loop();
// Open a requested closed loop for the duration of a move (it re-engages at rest); see check_closed_loop().
void pid_open_for_move(uint8_t axis);
// Called by the stage move commands before the ramp starts, with the commanded target: a move no longer
// than SET_PID_KEEP_CLOSED_BELOW keeps the loop engaged; otherwise a rest-only loop (SET_PID_OPEN_ABOVE 0) opens.
void pid_before_move(uint8_t axis, int32_t target);

#endif // OPERATIONS_H
