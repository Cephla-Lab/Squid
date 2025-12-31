#ifndef DEF_OCTOPI_80120_H
#define DEF_OCTOPI_80120_H

#include "../global_defs.h"

#include <Arduino.h>

// LED matrix
#define DOTSTAR_NUM_LEDS 128

// Axis assignment
static const uint8_t x = 1;
static const uint8_t y = 0;
static const uint8_t z = 2;
static const uint8_t w = 3;

static const float R_sense_xy = 0.22;
static const float R_sense_z = 0.43;
static const float R_sense_w = 0.105;

// limit switch
static const bool flip_limit_switch_x = true;
static const bool flip_limit_switch_y = true;

// Motorized stage
static const int FULLSTEPS_PER_REV_X = 200;
static const int FULLSTEPS_PER_REV_Y = 200;
static const int FULLSTEPS_PER_REV_Z = 200;
static const int FULLSTEPS_PER_REV_W = 200;
static const int FULLSTEPS_PER_REV_THETA = 200;

static const float HOMING_VELOCITY_X = 0.8;
static const float HOMING_VELOCITY_Y = 0.8;
static const float HOMING_VELOCITY_Z = 0.5;
static const float HOMING_VELOCITY_W = 0.15 * SCREW_PITCH_W_MM;

static const long X_NEG_LIMIT_MM = -130;
static const long X_POS_LIMIT_MM = 130;
static const long Y_NEG_LIMIT_MM = -130;
static const long Y_POS_LIMIT_MM = 130;
static const long Z_NEG_LIMIT_MM = -20;
static const long Z_POS_LIMIT_MM = 20;

#endif // DEF_OCTOPI_80120_H
