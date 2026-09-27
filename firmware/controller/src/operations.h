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
// Called by the stage move commands before the ramp starts: opens a rest-only loop (SET_PID_OPEN_ABOVE 0).
void pid_before_move(uint8_t axis);
// Shared with move-and-settle (move_settle.cpp): the bounded post-homing realignment (false = refused,
// fault latched), whether a homing owns the axis, and the fault / failed-move paths of the contract.
bool pid_realign_now(uint8_t axis);
bool pid_axis_is_homing(uint8_t axis);
void pid_raise_fault(uint8_t axis, uint8_t cause);
void pid_fail_move(uint8_t axis);
bool pid_move_in_progress(uint8_t axis);   // a commanded move of this axis has not been acknowledged yet

#endif // OPERATIONS_H
