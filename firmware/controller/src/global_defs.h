#ifndef GLOBAL_DEFS_H
#define GLOBAL_DEFS_H

extern float SCREW_PITCH_X_MM;
extern float SCREW_PITCH_Y_MM;
extern float SCREW_PITCH_Z_MM;
extern float SCREW_PITCH_W_MM;

extern int MICROSTEPPING_X;
extern int MICROSTEPPING_Y;
extern int MICROSTEPPING_Z;
extern int MICROSTEPPING_W;

extern long steps_per_mm_X;
extern long steps_per_mm_Y;
extern long steps_per_mm_Z;
extern long steps_per_mm_W;

extern float MAX_VELOCITY_X_mm;
extern float MAX_VELOCITY_Y_mm;
extern float MAX_VELOCITY_Z_mm;
extern float MAX_VELOCITY_W_mm;

extern float MAX_ACCELERATION_X_mm;
extern float MAX_ACCELERATION_Y_mm;
extern float MAX_ACCELERATION_Z_mm;
extern float MAX_ACCELERATION_W_mm;

// size 11 lead screw motors
extern float X_MOTOR_RMS_CURRENT_mA;
extern float Y_MOTOR_RMS_CURRENT_mA;
// Ding's motion size 8 linear actuator
extern float Z_MOTOR_RMS_CURRENT_mA;
extern float W_MOTOR_RMS_CURRENT_mA;

extern float X_MOTOR_I_HOLD;
extern float Y_MOTOR_I_HOLD;
extern float Z_MOTOR_I_HOLD;
extern float W_MOTOR_I_HOLD;

// encoder
extern bool X_use_encoder;
extern bool Y_use_encoder;
extern bool Z_use_encoder;
extern bool W_use_encoder;

// signs
extern int MOVEMENT_SIGN_X;
extern int MOVEMENT_SIGN_Y;
extern int MOVEMENT_SIGN_Z;
extern int ENCODER_SIGN_X;
extern int ENCODER_SIGN_Y;
extern int ENCODER_SIGN_Z;
extern int JOYSTICK_SIGN_X;
extern int JOYSTICK_SIGN_Y;
extern int JOYSTICK_SIGN_Z;

// limit switch polarity
extern bool LIM_SWITCH_X_ACTIVE_LOW;
extern bool LIM_SWITCH_Y_ACTIVE_LOW;
extern bool LIM_SWITCH_Z_ACTIVE_LOW;

// offset velocity enable/disable
extern bool enable_offset_velocity;

#endif // GLOBAL_DEFS_H
