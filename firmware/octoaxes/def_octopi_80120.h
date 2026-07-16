#ifndef DEF_OCTOPI_80120_H
#define DEF_OCTOPI_80120_H

// LED matrix
#define DOTSTAR_NUM_LEDS 128

// Axis assignment
static const uint8_t x = 1;
static const uint8_t y = 0;
static const uint8_t z = 2;
static const uint8_t w = 3;

// limit switch
static const bool flip_limit_switch_x = true;
static const bool flip_limit_switch_y = true;

// Motorized stage
static const long X_NEG_LIMIT_MM = -130;
static const long X_POS_LIMIT_MM = 130;
static const long Y_NEG_LIMIT_MM = -130;
static const long Y_POS_LIMIT_MM = 130;
static const long Z_NEG_LIMIT_MM = -20;
static const long Z_POS_LIMIT_MM = 20;

// encoder
static const bool X_use_encoder = false;
static const bool Y_use_encoder = false;
static const bool Z_use_encoder = false;
static const bool W_use_encoder = false;

// signs
static const int MOVEMENT_SIGN_X = 1;    // not used for now
static const int MOVEMENT_SIGN_Y = 1;    // not used for now
static const int MOVEMENT_SIGN_Z = 1;    // not used for now
static const int ENCODER_SIGN_X = 1;     // not used for now
static const int ENCODER_SIGN_Y = 1;     // not used for now
static const int ENCODER_SIGN_Z = 1;     // not used for now
static const int JOYSTICK_SIGN_X = -1;
static const int JOYSTICK_SIGN_Y = 1;
static const int JOYSTICK_SIGN_Z = 1;
  
// limit switch polarity
static const bool LIM_SWITCH_X_ACTIVE_LOW = false;
static const bool LIM_SWITCH_Y_ACTIVE_LOW = false;
static const bool LIM_SWITCH_Z_ACTIVE_LOW = false;

// offset velocity enable/disable
static const bool enable_offset_velocity = false;
#endif
