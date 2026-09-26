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
void joystick_x_apply();     // apply the joystick's deltas to the axis if it may drive it (at rest: offset velocity or a stop)
void joystick_y_apply();
void do_focus_control();

void check_position();
void check_limits();

#endif // OPERATIONS_H
