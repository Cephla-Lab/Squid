#include "globals.h"

byte buffer_rx[512];
byte buffer_tx[MSG_LENGTH];

uint32_t max_velocity_usteps[TOTAL_AXES];
uint32_t max_acceleration_usteps[TOTAL_AXES];

ConfigurationTypeDef tmc4361_configs[TOTAL_AXES];
TMC4361ATypeDef tmc4361[TOTAL_AXES];

elapsedMicros us_since_x_home_found;
elapsedMicros us_since_y_home_found;
elapsedMicros us_since_z_home_found;
elapsedMicros us_since_w_home_found;
elapsedMicros us_since_w2_home_found;

long X_POS_LIMIT = X_POS_LIMIT_MM * steps_per_mm_X;
long X_NEG_LIMIT = X_NEG_LIMIT_MM * steps_per_mm_X;
long Y_POS_LIMIT = Y_POS_LIMIT_MM * steps_per_mm_Y;
long Y_NEG_LIMIT = Y_NEG_LIMIT_MM * steps_per_mm_Y;
long Z_POS_LIMIT = Z_POS_LIMIT_MM * steps_per_mm_Z;
long Z_NEG_LIMIT = Z_NEG_LIMIT_MM * steps_per_mm_Z;


// PID
bool stage_PID_enabled[TOTAL_AXES] = {0};
PID_ARGUMENTS axes_pid_arg[TOTAL_AXES] = {0};

// home safety margin
uint16_t home_safety_margin[TOTAL_AXES] = {4, 4, 4, 4, 4};

volatile int buffer_rx_ptr = 0;
byte cmd_id = 0;
bool mcu_cmd_execution_in_progress = false;
bool checksum_error = false;

// limit switch
bool is_homing_X = false;
bool is_homing_Y = false;
bool is_homing_Z = false;
bool is_homing_XY = false;
bool is_homing_W = false;
bool is_homing_W2 = false;
bool home_X_found = false;
bool home_Y_found = false;
bool home_Z_found = false;
bool home_W_found = false;
bool home_W2_found = false;
bool is_preparing_for_homing_X = false;
bool is_preparing_for_homing_Y = false;
bool is_preparing_for_homing_Z = false;
bool is_preparing_for_homing_W = false;
bool is_preparing_for_homing_W2 = false;
bool homing_direction_X = false;
bool homing_direction_Y = false;
bool homing_direction_Z = false;
bool homing_direction_W = false;
bool homing_direction_W2 = false;

long X_commanded_target_position = 0;
long Y_commanded_target_position = 0;
long Z_commanded_target_position = 0;
long W_commanded_target_position = 0;
long W2_commanded_target_position = 0;

bool X_commanded_movement_in_progress = false;
bool Y_commanded_movement_in_progress = false;
bool Z_commanded_movement_in_progress = false;
bool W_commanded_movement_in_progress = false;
bool W2_commanded_movement_in_progress = false;

int X_direction;
int Y_direction;
int Z_direction;
int W_direction;
int W2_direction;

int32_t focusPosition = 0;

long target_position;

int32_t X_pos = 0;
int32_t Y_pos = 0;
int32_t Z_pos = 0;
int32_t W_pos = 0;
int32_t W2_pos = 0;

float offset_velocity_x = 0;
float offset_velocity_y = 0;

bool closed_loop_position_control = false;

/***************************************************************************************************/
/******************************************** timing ***********************************************/
/***************************************************************************************************/
// IntervalTimer does not work on teensy with SPI, the below lines are to be removed
volatile int counter_send_pos_update = 0;
volatile bool flag_send_pos_update = false;
elapsedMicros us_since_last_pos_update = 5000;
elapsedMicros us_since_last_check_position = 3000;
elapsedMicros us_since_last_joystick_update = 3000;
elapsedMicros us_since_last_check_limit = 2000;

/***************************************************************************************************/
/******************************************* joystick **********************************************/
/***************************************************************************************************/
bool flag_read_joystick = false;

// joystick xy
int16_t joystick_delta_x = 0;
int16_t joystick_delta_y = 0;

// joystick button
bool joystick_button_pressed = false;
long joystick_button_pressed_timestamp = 0;

// focus
int32_t focuswheel_pos = 0;
bool first_packet_from_joystick_panel = true;

// btns
uint8_t btns;

// The flag indicates whether the filter wheel(s) are enabled or disabled.
bool enable_filterwheel = false;
bool enable_filterwheel_w2 = false;

/***************************************************************************************************/
/***************************************** illumination ********************************************/
/***************************************************************************************************/
int illumination_source = 0;
uint16_t illumination_intensity = 65535;
// Illumination intensity scaling factor - scales DAC output for different hardware:
//   0.6 = Squid LEDs (0-1.5V output range)
//   0.8 = Squid laser engine (0-2V output range)
//   1.0 = Full range (0-2.5V output, when DAC gain is 1 instead of 2)
// This factor is applied to ALL illumination commands (legacy and multi-port).
float illumination_intensity_factor = 0.6;
uint8_t led_matrix_r = 0;
uint8_t led_matrix_g = 0;
uint8_t led_matrix_b = 0;
bool illumination_is_on = false;

// Multi-port illumination control (all ports off and at zero intensity by default)
bool illumination_port_is_on[NUM_ILLUMINATION_PORTS] = {false};
uint16_t illumination_port_intensity[NUM_ILLUMINATION_PORTS] = {0};
