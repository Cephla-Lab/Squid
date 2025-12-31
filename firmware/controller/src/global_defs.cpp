#include "global_defs.h"

#include "def/def_v1.h"

float SCREW_PITCH_X_MM = 2.54;
float SCREW_PITCH_Y_MM = 2.54;
float SCREW_PITCH_Z_MM = 0.3;
float SCREW_PITCH_W_MM = 1;

int MICROSTEPPING_X = 256;
int MICROSTEPPING_Y = 256;
int MICROSTEPPING_Z = 256;
int MICROSTEPPING_W = 64;

long steps_per_mm_X = FULLSTEPS_PER_REV_X*MICROSTEPPING_X/SCREW_PITCH_X_MM;
long steps_per_mm_Y = FULLSTEPS_PER_REV_Y*MICROSTEPPING_Y/SCREW_PITCH_Y_MM;
long steps_per_mm_Z = FULLSTEPS_PER_REV_Z*MICROSTEPPING_Z/SCREW_PITCH_Z_MM;
long steps_per_mm_W = FULLSTEPS_PER_REV_W*MICROSTEPPING_W/SCREW_PITCH_W_MM;

float MAX_VELOCITY_X_mm = 50;
float MAX_VELOCITY_Y_mm = 50;
float MAX_VELOCITY_Z_mm = 2;
float MAX_VELOCITY_W_mm = 3.19 * SCREW_PITCH_W_MM;

float MAX_ACCELERATION_X_mm = 200;
float MAX_ACCELERATION_Y_mm = 200;
float MAX_ACCELERATION_Z_mm = 20;
float MAX_ACCELERATION_W_mm = 300 * SCREW_PITCH_W_MM;

// size 11 lead screw motors
float X_MOTOR_RMS_CURRENT_mA = 1000;
float Y_MOTOR_RMS_CURRENT_mA = 1000;
// Ding's motion size 8 linear actuator
float Z_MOTOR_RMS_CURRENT_mA = 500;
float W_MOTOR_RMS_CURRENT_mA = 1900;

float X_MOTOR_I_HOLD = 0.25;
float Y_MOTOR_I_HOLD = 0.25;
float Z_MOTOR_I_HOLD = 0.5;
float W_MOTOR_I_HOLD = 0.5;

// encoder
bool X_use_encoder = false;
bool Y_use_encoder = false;
bool Z_use_encoder = false;
bool W_use_encoder = false;

// signs
int MOVEMENT_SIGN_X = 1;    // not used for now
int MOVEMENT_SIGN_Y = 1;    // not used for now
int MOVEMENT_SIGN_Z = 1;    // not used for now
int ENCODER_SIGN_X = 1;     // not used for now
int ENCODER_SIGN_Y = 1;     // not used for now
int ENCODER_SIGN_Z = 1;     // not used for now
int JOYSTICK_SIGN_X = -1;
int JOYSTICK_SIGN_Y = 1;
int JOYSTICK_SIGN_Z = 1;

// limit switch polarity
bool LIM_SWITCH_X_ACTIVE_LOW = false;
bool LIM_SWITCH_Y_ACTIVE_LOW = false;
bool LIM_SWITCH_Z_ACTIVE_LOW = false;

// offset velocity enable/disable
bool enable_offset_velocity = false;
