#ifndef DEF_OCTOPI_80120_H
#define DEF_OCTOPI_80120_H

#include "../global_defs.h"

#include <Arduino.h>

// LED matrix
#define DOTSTAR_NUM_LEDS 128

// Internal axis indices for array access (tmc4361[], axes_pid_arg[], etc.).
// IMPORTANT: These are INTERNAL indices, NOT protocol constants!
// The protocol uses different values (see constants_protocol.h):
//   Protocol: AXIS_X=0, AXIS_Y=1, AXIS_Z=2, AXIS_W=5, AXIS_W2=6
//   Internal: x=1, y=0, z=2, w=3, w2=4
// Use protocol_axis_to_internal() to convert protocol values to these indices.
static const uint8_t x = 1;
static const uint8_t y = 0;
static const uint8_t z = 2;
static const uint8_t w = 3;   // First filter wheel
static const uint8_t w2 = 4;  // Second filter wheel

static const float R_sense_xy = 0.22;
static const float R_sense_z = 0.43;
static const float R_sense_w = 0.105;  // Used by both W and W2 (identical hardware)

// limit switch
static const bool flip_limit_switch_x = true;
static const bool flip_limit_switch_y = true;

// Motorized stage
static const int FULLSTEPS_PER_REV_X = 200;
static const int FULLSTEPS_PER_REV_Y = 200;
static const int FULLSTEPS_PER_REV_Z = 200;
static const int FULLSTEPS_PER_REV_W = 200;   // Used by both W and W2
static const int FULLSTEPS_PER_REV_W2 = 200;  // Kept for documentation (W2 uses W settings)
static const int FULLSTEPS_PER_REV_THETA = 200;

static const float HOMING_VELOCITY_X = 0.8;
static const float HOMING_VELOCITY_Y = 0.8;
static const float HOMING_VELOCITY_Z = 0.5;
static const float HOMING_VELOCITY_W = 0.15 * SCREW_PITCH_W_MM;   // Used by both W and W2
static const float HOMING_VELOCITY_W2 = 0.15 * SCREW_PITCH_W_MM;  // Kept for documentation (W2 uses W settings)

static const long X_NEG_LIMIT_MM = -130;
static const long X_POS_LIMIT_MM = 130;
static const long Y_NEG_LIMIT_MM = -130;
static const long Y_POS_LIMIT_MM = 130;
static const long Z_NEG_LIMIT_MM = -20;
static const long Z_POS_LIMIT_MM = 20;

#endif // DEF_OCTOPI_80120_H
