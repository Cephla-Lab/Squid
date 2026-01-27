#ifndef GLOBALS_H
#define GLOBALS_H

#include "constants.h"

#include <Arduino.h>

extern byte buffer_rx[512];
extern byte buffer_tx[MSG_LENGTH];

extern uint32_t max_velocity_usteps[TOTAL_AXES];
extern uint32_t max_acceleration_usteps[TOTAL_AXES];

extern ConfigurationTypeDef tmc4361_configs[TOTAL_AXES];
extern TMC4361ATypeDef tmc4361[TOTAL_AXES];

extern elapsedMicros us_since_x_home_found;
extern elapsedMicros us_since_y_home_found;
extern elapsedMicros us_since_z_home_found;
extern elapsedMicros us_since_w_home_found;
extern elapsedMicros us_since_w2_home_found;

extern long X_POS_LIMIT;
extern long X_NEG_LIMIT;
extern long Y_POS_LIMIT;
extern long Y_NEG_LIMIT;
extern long Z_POS_LIMIT;
extern long Z_NEG_LIMIT;

// PID
extern bool stage_PID_enabled[TOTAL_AXES];
extern PID_ARGUMENTS axes_pid_arg[TOTAL_AXES];

// home safety margin
extern uint16_t home_safety_margin[TOTAL_AXES];

extern volatile int buffer_rx_ptr;
extern byte cmd_id;
extern bool mcu_cmd_execution_in_progress;
extern bool checksum_error;

// limit switch
extern bool is_homing_X;
extern bool is_homing_Y;
extern bool is_homing_Z;
extern bool is_homing_XY;
extern bool is_homing_W;
extern bool is_homing_W2;
extern bool home_X_found;
extern bool home_Y_found;
extern bool home_Z_found;
extern bool home_W_found;
extern bool home_W2_found;
extern bool is_preparing_for_homing_X;
extern bool is_preparing_for_homing_Y;
extern bool is_preparing_for_homing_Z;
extern bool is_preparing_for_homing_W;
extern bool is_preparing_for_homing_W2;
extern bool homing_direction_X;
extern bool homing_direction_Y;
extern bool homing_direction_Z;
extern bool homing_direction_W;
extern bool homing_direction_W2;

extern long X_commanded_target_position;
extern long Y_commanded_target_position;
extern long Z_commanded_target_position;
extern long W_commanded_target_position;
extern long W2_commanded_target_position;

extern bool X_commanded_movement_in_progress;
extern bool Y_commanded_movement_in_progress;
extern bool Z_commanded_movement_in_progress;
extern bool W_commanded_movement_in_progress;
extern bool W2_commanded_movement_in_progress;

extern int X_direction;
extern int Y_direction;
extern int Z_direction;
extern int W_direction;
extern int W2_direction;

extern int32_t focusPosition;

extern long target_position;

extern int32_t X_pos;
extern int32_t Y_pos;
extern int32_t Z_pos;
extern int32_t W_pos;
extern int32_t W2_pos;

extern float offset_velocity_x;
extern float offset_velocity_y;

extern bool closed_loop_position_control;

/***************************************************************************************************/
/******************************************** timing ***********************************************/
/***************************************************************************************************/
extern volatile int counter_send_pos_update;
extern volatile bool flag_send_pos_update;
extern elapsedMicros us_since_last_pos_update;
extern elapsedMicros us_since_last_check_position;
extern elapsedMicros us_since_last_joystick_update;
extern elapsedMicros us_since_last_check_limit;

/***************************************************************************************************/
/******************************************* joystick **********************************************/
/***************************************************************************************************/
extern bool flag_read_joystick;

// joystick xy
extern int16_t joystick_delta_x;
extern int16_t joystick_delta_y;

// joystick button
extern bool joystick_button_pressed;
extern long joystick_button_pressed_timestamp;

// focus
extern int32_t focuswheel_pos;
extern bool first_packet_from_joystick_panel;

// btns
extern uint8_t btns;

// The flag indicates whether the filter wheel(s) are enabled or disabled.
extern bool enable_filterwheel;
extern bool enable_filterwheel_w2;

/***************************************************************************************************/
/***************************************** illumination ********************************************/
/***************************************************************************************************/
extern int illumination_source;
extern uint16_t illumination_intensity;
extern float illumination_intensity_factor;
extern uint8_t led_matrix_r;
extern uint8_t led_matrix_g;
extern uint8_t led_matrix_b;
extern bool illumination_is_on;

#endif // GLOBALS_H
