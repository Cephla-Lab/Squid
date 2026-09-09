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

// Encoder reporting and closed-loop safety (firmware 1.6, see constants_protocol.h)
extern uint8_t encoder_report_axis;             // INTERNAL axis index being reported, 0xFF = off
extern uint8_t encoder_report_mode;             // ENCODER_REPORT_*
extern bool encoder_configured[TOTAL_AXES];     // CONFIGURE_STAGE_PID has initialised the encoder since the last chip reset
extern bool pid_fault[TOTAL_AXES];              // deviation watchdog disabled the loop; cleared by ENABLE_STAGE_PID / RESET
extern int32_t pid_max_dev_usteps[TOTAL_AXES];  // watchdog limit in usteps; 0 = unset (default applied at configure)
extern uint32_t pid_dv_clip_usteps[TOTAL_AXES]; // PID_DV_CLIP override in usteps/s; 0 = firmware default
extern bool pid_requested[TOTAL_AXES];          // host asked for the loop (ENABLE_STAGE_PID); cleared by DISABLE, fault, RESET, INITIALIZE
extern bool pid_zone_hold[TOTAL_AXES];          // loop requested but held open by the home zone / homing; re-engages outside
extern int32_t pid_home_zone_usteps[TOTAL_AXES];// home exclusion zone half-width in usteps; 0 = none
extern uint32_t pid_tolerance_usteps[TOTAL_AXES];    // loop deadband override; 0 = legacy 25 usteps (2 on wheels)
extern bool pid_realign_pending[TOTAL_AXES];    // a homing ran while the loop was requested: align ENC_POS to XACTUAL at the next engage
extern int32_t completion_window_usteps[TOTAL_AXES];  // SET_COMPLETION_WINDOW: early COMPLETED when within this of the target; 0 = off
extern int32_t pid_open_above_pps[TOTAL_AXES];  // SET_PID_OPEN_ABOVE: loop opened above this ramp velocity (pps); 0 = rest-only
extern int32_t pid_keep_closed_usteps[TOTAL_AXES]; // SET_PID_KEEP_CLOSED_BELOW: commanded moves up to this length stay closed-loop; 0 = off
extern bool pid_short_move[TOTAL_AXES];         // the move in progress is such a short move: check_closed_loop leaves the loop engaged
extern int32_t pid_precomp_usteps[TOTAL_AXES][2]; // SET_PID_PRECOMP: open-loop residual (ENC - XACTUAL) after [0] counter-decreasing, [1] counter-increasing moves
extern int32_t pid_true_target[TOTAL_AXES];     // true target of a pre-compensated move; the counter is rewritten to it at rest
extern bool pid_true_target_pending[TOTAL_AXES];
extern uint32_t pid_tr_tolerance_usteps[TOTAL_AXES]; // target-reached tolerance override; 0 = legacy

// home safety margin
extern uint16_t home_safety_margin[TOTAL_AXES];

extern volatile int buffer_rx_ptr;
extern byte cmd_id;
extern bool mcu_cmd_execution_in_progress;
extern byte mcu_cmd_execution_status;
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
// volatile: read/written from both ISR_strobeTimer() and main-loop command callbacks
extern volatile int illumination_source;
extern uint16_t illumination_intensity;
extern float illumination_intensity_factor;
extern uint8_t led_matrix_r;
extern uint8_t led_matrix_g;
extern uint8_t led_matrix_b;
// volatile: cleared at strobe end inside ISR, read by set_illumination() in main loop
extern volatile bool illumination_is_on;

// Multi-port illumination control (supports up to 16 ports D1-D16)
#define NUM_ILLUMINATION_PORTS 16
extern bool illumination_port_is_on[NUM_ILLUMINATION_PORTS];
extern uint16_t illumination_port_intensity[NUM_ILLUMINATION_PORTS];

// Serial watchdog (illumination auto-shutoff safety)
extern uint32_t last_serial_message_time;
extern uint32_t watchdog_timeout_ms;
extern bool watchdog_enabled;

#endif // GLOBALS_H
