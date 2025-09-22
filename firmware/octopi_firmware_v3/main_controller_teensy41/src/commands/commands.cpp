#include "commands.h"

CommandCallback cmd_map[256] = {0};

void init_callbacks()
{
    cmd_map[MOVE_X] = &callback_move_x;
    cmd_map[MOVE_Y] = &callback_move_y;
    cmd_map[MOVE_Z] = &callback_move_z;
    cmd_map[MOVE_W] = &callback_move_w;
    cmd_map[MOVETO_X] = &callback_move_to_x;
    cmd_map[MOVETO_Y] = &callback_move_to_y;
    cmd_map[MOVETO_Z] = &callback_move_to_z;
    cmd_map[MOVETO_W] = &callback_move_to_w;
    cmd_map[SET_LIM] = &callback_set_lim;
    cmd_map[SET_LIM_SWITCH_POLARITY] = &callback_set_lim_switch_polarity;
    cmd_map[SET_HOME_SAFETY_MERGIN] = &callback_set_home_safety_margin;
    cmd_map[SET_PID_ARGUMENTS] = &callback_set_pid_arguments;
    cmd_map[CONFIGURE_STEPPER_DRIVER] = &callback_configure_stepper_driver;
    cmd_map[SET_MAX_VELOCITY_ACCELERATION] = &callback_set_max_velocity_acceleration;
    cmd_map[SET_LEAD_SCREW_PITCH] = &callback_set_lead_screw_pitch;
    cmd_map[HOME_OR_ZERO] = &callback_home_or_zero;
    cmd_map[SET_OFFSET_VELOCITY] = &callback_set_offset_velocity;
    cmd_map[TURN_ON_ILLUMINATION] = &callback_turn_on_illumination;
    cmd_map[TURN_OFF_ILLUMINATION] = &callback_turn_off_illumination;
    cmd_map[SET_ILLUMINATION] = &callback_set_illumination;
    cmd_map[SET_ILLUMINATION_LED_MATRIX] = &callback_set_illumination_led_matrix;
    cmd_map[ACK_JOYSTICK_BUTTON_PRESSED] = &callback_ack_joystick_button_pressed;
    cmd_map[ANALOG_WRITE_ONBOARD_DAC] = &callback_analog_write_onboard_dac;
    cmd_map[SET_DAC80508_REFDIV_GAIN] = &callback_set_dac80508_defdiv_gain;
    cmd_map[SET_ILLUMINATION_INTENSITY_FACTOR] = &callback_set_illumination_intensity_factor;
    cmd_map[SET_STROBE_DELAY] = &callback_set_strobe_delay;
    cmd_map[SEND_HARDWARE_TRIGGER] = &callback_send_hardware_trigger;
    cmd_map[SET_PIN_LEVEL] = &callback_set_pin_level;
    cmd_map[CONFIGURE_STAGE_PID] = &callback_configure_stage_pid;
    cmd_map[ENABLE_STAGE_PID] = &callback_enable_stage_pid;
    cmd_map[DISABLE_STAGE_PID] = &callback_disable_stage_pid;
    cmd_map[INITFILTERWHEEL] = &callback_initfilterwheel;
    cmd_map[SET_AXIS_DISABLE_ENABLE] = &callback_set_axis_disable_enable;

    cmd_map[INITIALIZE] = &callback_initialize;
    cmd_map[RESET] = &callback_reset;
}

void callback_default()
{
    // TODO: This is for future use, e.g., when we implement immediate error reporting back to the
    // host. For now, do nothing.
}

void callback_ack_joystick_button_pressed()
{
    joystick_button_pressed = false;
}

void callback_analog_write_onboard_dac()
{
    int dac = buffer_rx[2];
    uint16_t value = ( uint16_t(buffer_rx[3]) * 256 + uint16_t(buffer_rx[4]) );
    set_DAC8050x_output(dac, value);
}

void callback_set_dac80508_defdiv_gain()
{
    uint8_t div   = buffer_rx[2];
    uint8_t gains = buffer_rx[3];
    set_DAC8050x_gain(div, gains);
}

void callback_set_strobe_delay()
{
    strobe_delay[buffer_rx[2]] = uint32_t(buffer_rx[3]) << 24 | uint32_t(buffer_rx[4]) << 16 | uint32_t(buffer_rx[5]) << 8 | uint32_t(buffer_rx[6]);
}

void callback_send_hardware_trigger()
{
    // Some (all?) the arrays used by the trigger timer interrupt use data types that don't have
    // atomic writes, so we need to disable interrupts here to make sure the timer interrupt
    // doesn't get partially written values.
    noInterrupts();
    int camera_channel = buffer_rx[2] & 0x0f;
    control_strobe[camera_channel] = buffer_rx[2] >> 7;
    illumination_on_time[camera_channel] = uint32_t(buffer_rx[3]) << 24 | uint32_t(buffer_rx[4]) << 16 | uint32_t(buffer_rx[5]) << 8 | uint32_t(buffer_rx[6]);
    digitalWrite(camera_trigger_pins[camera_channel], LOW);
    timestamp_trigger_rising_edge[camera_channel] = micros();
    trigger_output_level[camera_channel] = LOW;
    interrupts();
}

void callback_set_pin_level()
{
    int pin = buffer_rx[2];
    bool level = buffer_rx[3];
    digitalWrite(pin, level);
}

void callback_configure_stage_pid()
{
    int axis = buffer_rx[2];
    int flip_direction = buffer_rx[3];
    int transitions_per_revolution = (buffer_rx[4] << 8) + buffer_rx[5];
    // Init encoder. transitions per revolution, velocity filter wait time (# of clock cycles), IIR filter exponent, vmean update frequency, invert direction (must increase as microsteps increases)
    tmc4361A_init_ABN_encoder(&tmc4361[axis], transitions_per_revolution, 32, 4, 512, flip_direction);
    // Init PID. target reach tolerance, position error tolerance, P, I, and D coefficients, max speed, winding limit, derivative update rate
    if (axis == x)
        tmc4361A_init_PID(&tmc4361[axis], 25, 25, axes_pid_arg[axis].p, axes_pid_arg[axis].i, axes_pid_arg[axis].d, tmc4361A_vmmToMicrosteps(&tmc4361[axis], MAX_VELOCITY_X_mm), 32767, 2);
    else if (axis == y)
        tmc4361A_init_PID(&tmc4361[axis], 25, 25, axes_pid_arg[axis].p, axes_pid_arg[axis].i, axes_pid_arg[axis].d, tmc4361A_vmmToMicrosteps(&tmc4361[axis], MAX_VELOCITY_Y_mm), 32767, 2);
    else if (axis == z)
        tmc4361A_init_PID(&tmc4361[axis], 25, 25, axes_pid_arg[axis].p, axes_pid_arg[axis].i, axes_pid_arg[axis].d, tmc4361A_vmmToMicrosteps(&tmc4361[axis], MAX_VELOCITY_Z_mm), 4096, 2);
    else if (axis == w) {
        if (enable_filterwheel == true)
        tmc4361A_init_PID(&tmc4361[axis], 2, 2, axes_pid_arg[axis].p, axes_pid_arg[axis].i, axes_pid_arg[axis].d, tmc4361A_vmmToMicrosteps(&tmc4361[axis], MAX_VELOCITY_W_mm), 4096, 2);
    }
}

void callback_enable_stage_pid()
{
    int axis = buffer_rx[2];
    tmc4361A_set_PID(&tmc4361[axis], PID_BPG0);
    stage_PID_enabled[axis] = 1;
}

void callback_disable_stage_pid()
{
    int axis = buffer_rx[2];
    tmc4361A_set_PID(&tmc4361[axis], PID_DISABLE);
    stage_PID_enabled[axis] = 0;
}

void callback_initfilterwheel()
{
    enable_filterwheel = true;

    tmc4361A_init(&tmc4361[w], pin_TMC4361_CS[w], &tmc4361_configs[w], tmc4361A_defaultRegisterResetState);
    pinMode(pin_TMC4361_CS[w], OUTPUT);
    digitalWrite(pin_TMC4361_CS[w], HIGH);

    tmc4361A_tmc2660_config(&tmc4361[w], (W_MOTOR_RMS_CURRENT_mA / 1000)*R_sense_w / 0.2298, W_MOTOR_I_HOLD, 1, 1, 1, SCREW_PITCH_W_MM, FULLSTEPS_PER_REV_W, MICROSTEPPING_W); // need to make current scaling on TMC2660 is > 16 (out of 31)
    tmc4361A_tmc2660_init(&tmc4361[w], clk_Hz_TMC4361);   // set up ICs with SPI control and other parameters
        tmc4361A_enableLimitSwitch(&tmc4361[w], lft_sw_pol[w], LEFT_SW, false);

    tmc4361A_setMaxSpeed(&tmc4361[w], max_velocity_usteps[w]);
    tmc4361A_setMaxAcceleration(&tmc4361[w], max_acceleration_usteps[w]);
    tmc4361[w].rampParam[ASTART_IDX] = 0;
    tmc4361[w].rampParam[DFINAL_IDX] = 0;
    tmc4361A_sRampInit(&tmc4361[w]);

    tmc4361A_set_PID(&tmc4361[w], PID_DISABLE);

    tmc4361A_enableHomingLimit(&tmc4361[w], rht_sw_pol[w], TMC4361_homing_sw[w], home_safety_margin[w]);
    tmc4361A_disableVirtualLimitSwitch(&tmc4361[w], -1);
    tmc4361A_disableVirtualLimitSwitch(&tmc4361[w], 1);
}

void callback_set_axis_disable_enable()
{
    int axis = buffer_rx[2];
    int status = buffer_rx[3];
    if (status == 0) {
        tmc4361A_tmc2660_disable_driver(&tmc4361[axis]);
    }
    else {
        tmc4361A_tmc2660_enable_driver(&tmc4361[axis]);
    }
}

void callback_initialize()
{
    // reset z target position so that z does not move when "current position" for z is set to 0
    focusPosition = 0;
    first_packet_from_joystick_panel = true;
    // initilize TMC4361 and TMC2660
    for (int i = 0; i < STAGE_AXES; i++)
        tmc4361A_tmc2660_init(&tmc4361[i], clk_Hz_TMC4361); // set up ICs with SPI control and other parameters

    // enable limit switch reading
    tmc4361A_enableLimitSwitch(&tmc4361[x], lft_sw_pol[x], LEFT_SW, flip_limit_switch_x);
    tmc4361A_enableLimitSwitch(&tmc4361[x], rht_sw_pol[x], RGHT_SW, flip_limit_switch_x);
    tmc4361A_enableLimitSwitch(&tmc4361[y], lft_sw_pol[y], LEFT_SW, flip_limit_switch_y);
    tmc4361A_enableLimitSwitch(&tmc4361[y], rht_sw_pol[y], RGHT_SW, flip_limit_switch_y);
    tmc4361A_enableLimitSwitch(&tmc4361[z], rht_sw_pol[z], RGHT_SW, false);
    tmc4361A_enableLimitSwitch(&tmc4361[z], lft_sw_pol[z], LEFT_SW, false);

    // motion profile
    uint32_t max_velocity_usteps[STAGE_AXES];
    uint32_t max_acceleration_usteps[STAGE_AXES];
    max_acceleration_usteps[x] = tmc4361A_ammToMicrosteps(&tmc4361[x], MAX_ACCELERATION_X_mm);
    max_acceleration_usteps[y] = tmc4361A_ammToMicrosteps(&tmc4361[y], MAX_ACCELERATION_Y_mm);
    max_acceleration_usteps[z] = tmc4361A_ammToMicrosteps(&tmc4361[z], MAX_ACCELERATION_Z_mm);

    max_velocity_usteps[x] = tmc4361A_vmmToMicrosteps(&tmc4361[x], MAX_VELOCITY_X_mm);
    max_velocity_usteps[y] = tmc4361A_vmmToMicrosteps(&tmc4361[y], MAX_VELOCITY_Y_mm);
    max_velocity_usteps[z] = tmc4361A_vmmToMicrosteps(&tmc4361[z], MAX_VELOCITY_Z_mm);

    for (int i = 0; i < STAGE_AXES; i++) {
        // initialize ramp with default values
        tmc4361A_setMaxSpeed(&tmc4361[i], max_velocity_usteps[i]);
        tmc4361A_setMaxAcceleration(&tmc4361[i], max_acceleration_usteps[i]);
        tmc4361[i].rampParam[ASTART_IDX] = 0;
        tmc4361[i].rampParam[DFINAL_IDX] = 0;
        tmc4361A_sRampInit(&tmc4361[i]);
    }

    // homing switch settings
    tmc4361A_enableHomingLimit(&tmc4361[x], lft_sw_pol[x], TMC4361_homing_sw[x], home_safety_margin[x]);
    tmc4361A_enableHomingLimit(&tmc4361[y], lft_sw_pol[y], TMC4361_homing_sw[y], home_safety_margin[y]);
    tmc4361A_enableHomingLimit(&tmc4361[z], rht_sw_pol[z], TMC4361_homing_sw[z], home_safety_margin[z]);

    // DAC init
    set_DAC8050x_config();
    set_DAC8050x_default_gain();
}

void callback_reset()
{
    mcu_cmd_execution_in_progress = false;
    X_commanded_movement_in_progress = false;
    Y_commanded_movement_in_progress = false;
    Z_commanded_movement_in_progress = false;
    W_commanded_movement_in_progress = false;
    is_homing_X = false;
    is_homing_Y = false;
    is_homing_Z = false;
    is_homing_W = false;
    is_homing_XY = false;
    home_X_found = false;
    home_Y_found = false;
    home_Z_found = false;
    home_W_found = false;
    is_preparing_for_homing_X = false;
    is_preparing_for_homing_Y = false;
    is_preparing_for_homing_Z = false;
    is_preparing_for_homing_W = false;
    cmd_id = 0;
}
