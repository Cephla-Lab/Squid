#include "stage_commands.h"

void callback_move_x()
{
    long relative_position = int32_t(uint32_t(buffer_rx[2]) << 24 | uint32_t(buffer_rx[3]) << 16 | uint32_t(buffer_rx[4]) << 8 | uint32_t(buffer_rx[5]));
    long current_position = tmc4361A_currentPosition(&tmc4361[x]);
    X_direction = sgn(relative_position);
    X_commanded_target_position = ( relative_position > 0 ? min(current_position + relative_position, X_POS_LIMIT) : max(current_position + relative_position, X_NEG_LIMIT) );
    if ( tmc4361A_moveTo(&tmc4361[x], X_commanded_target_position) == 0)
    {
        X_commanded_movement_in_progress = true;
        mcu_cmd_execution_in_progress = true;
    }
}

void callback_move_y()
{
    long relative_position = int32_t(uint32_t(buffer_rx[2]) << 24 | uint32_t(buffer_rx[3]) << 16 | uint32_t(buffer_rx[4]) << 8 | uint32_t(buffer_rx[5]));
    long current_position = tmc4361A_currentPosition(&tmc4361[y]);
    Y_direction = sgn(relative_position);
    Y_commanded_target_position = ( relative_position > 0 ? min(current_position + relative_position, Y_POS_LIMIT) : max(current_position + relative_position, Y_NEG_LIMIT) );
    if ( tmc4361A_moveTo(&tmc4361[y], Y_commanded_target_position) == 0)
    {
        Y_commanded_movement_in_progress = true;
        mcu_cmd_execution_in_progress = true;
    }
}

void callback_move_z()
{
    long relative_position = int32_t(uint32_t(buffer_rx[2]) << 24 | uint32_t(buffer_rx[3]) << 16 | uint32_t(buffer_rx[4]) << 8 | uint32_t(buffer_rx[5]));
    long current_position = tmc4361A_currentPosition(&tmc4361[z]);
    Z_direction = sgn(relative_position);
    Z_commanded_target_position = ( relative_position > 0 ? min(current_position + relative_position, Z_POS_LIMIT) : max(current_position + relative_position, Z_NEG_LIMIT) );
    focusPosition = Z_commanded_target_position;
    if ( tmc4361A_moveTo(&tmc4361[z], Z_commanded_target_position) == 0)
    {
        Z_commanded_movement_in_progress = true;
        mcu_cmd_execution_in_progress = true;
    }
}

void callback_move_w()
{
    if (enable_filterwheel == true) {
        long relative_position = int32_t(uint32_t(buffer_rx[2]) << 24 | uint32_t(buffer_rx[3]) << 16 | uint32_t(buffer_rx[4]) << 8 | uint32_t(buffer_rx[5]));
        long current_position = tmc4361A_currentPosition(&tmc4361[w]);
        W_direction = sgn(relative_position);
        W_commanded_target_position = current_position + relative_position;
        if ( tmc4361A_moveTo(&tmc4361[w], W_commanded_target_position) == 0)
        {
        W_commanded_movement_in_progress = true;
        mcu_cmd_execution_in_progress = true;
        }
    }
}

void callback_move_to_x()
{
    long absolute_position = int32_t(uint32_t(buffer_rx[2]) << 24 | uint32_t(buffer_rx[3]) << 16 | uint32_t(buffer_rx[4]) << 8 | uint32_t(buffer_rx[5]));
    X_direction = sgn(absolute_position - tmc4361A_currentPosition(&tmc4361[x]));
    X_commanded_target_position = absolute_position;
    if (tmc4361A_moveTo(&tmc4361[x], X_commanded_target_position) == 0)
    {
        X_commanded_movement_in_progress = true;
        mcu_cmd_execution_in_progress = true;
    }
}

void callback_move_to_y()
{
    long absolute_position = int32_t(uint32_t(buffer_rx[2]) << 24 | uint32_t(buffer_rx[3]) << 16 | uint32_t(buffer_rx[4]) << 8 | uint32_t(buffer_rx[5]));
    Y_direction = sgn(absolute_position - tmc4361A_currentPosition(&tmc4361[y]));
    Y_commanded_target_position = absolute_position;
    if (tmc4361A_moveTo(&tmc4361[y], Y_commanded_target_position) == 0)
    {
        Y_commanded_movement_in_progress = true;
        mcu_cmd_execution_in_progress = true;
    }
}

void callback_move_to_z()
{
    long absolute_position = int32_t(uint32_t(buffer_rx[2]) << 24 | uint32_t(buffer_rx[3]) << 16 | uint32_t(buffer_rx[4]) << 8 | uint32_t(buffer_rx[5]));
    Z_direction = sgn(absolute_position - tmc4361A_currentPosition(&tmc4361[z]));
    Z_commanded_target_position = absolute_position;
    if (tmc4361A_moveTo(&tmc4361[z], Z_commanded_target_position) == 0)
    {
        focusPosition = absolute_position;
        Z_commanded_movement_in_progress = true;
        mcu_cmd_execution_in_progress = true;
    }
}

void callback_move_to_w()
{
    if (enable_filterwheel == true) {
        long absolute_position = int32_t(uint32_t(buffer_rx[2]) << 24 | uint32_t(buffer_rx[3]) << 16 | uint32_t(buffer_rx[4]) << 8 | uint32_t(buffer_rx[5]));
        W_direction = sgn(absolute_position - tmc4361A_currentPosition(&tmc4361[w]));
        W_commanded_target_position = absolute_position;
        if (tmc4361A_moveTo(&tmc4361[w], W_commanded_target_position) == 0)
        {
        W_commanded_movement_in_progress = true;
        mcu_cmd_execution_in_progress = true;
        }
    }
}

void callback_set_lim()
{
    switch (buffer_rx[2])
    {
        case LIM_CODE_X_POSITIVE:
        {
            X_POS_LIMIT = int32_t(uint32_t(buffer_rx[3]) << 24 | uint32_t(buffer_rx[4]) << 16 | uint32_t(buffer_rx[5]) << 8 | uint32_t(buffer_rx[6]));
            tmc4361A_setVirtualLimit(&tmc4361[x], 1, X_POS_LIMIT);
            tmc4361A_enableVirtualLimitSwitch(&tmc4361[x], 1);
            break;
        }
        case LIM_CODE_X_NEGATIVE:
        {
            X_NEG_LIMIT = int32_t(uint32_t(buffer_rx[3]) << 24 | uint32_t(buffer_rx[4]) << 16 | uint32_t(buffer_rx[5]) << 8 | uint32_t(buffer_rx[6]));
            tmc4361A_setVirtualLimit(&tmc4361[x], -1, X_NEG_LIMIT);
            tmc4361A_enableVirtualLimitSwitch(&tmc4361[x], -1);
            break;
        }
        case LIM_CODE_Y_POSITIVE:
        {
            Y_POS_LIMIT = int32_t(uint32_t(buffer_rx[3]) << 24 | uint32_t(buffer_rx[4]) << 16 | uint32_t(buffer_rx[5]) << 8 | uint32_t(buffer_rx[6]));
            tmc4361A_setVirtualLimit(&tmc4361[y], 1, Y_POS_LIMIT);
            tmc4361A_enableVirtualLimitSwitch(&tmc4361[y], 1);
            break;
        }
        case LIM_CODE_Y_NEGATIVE:
        {
            Y_NEG_LIMIT = int32_t(uint32_t(buffer_rx[3]) << 24 | uint32_t(buffer_rx[4]) << 16 | uint32_t(buffer_rx[5]) << 8 | uint32_t(buffer_rx[6]));
            tmc4361A_setVirtualLimit(&tmc4361[y], -1, Y_NEG_LIMIT);
            tmc4361A_enableVirtualLimitSwitch(&tmc4361[y], -1);
            break;
        }
        case LIM_CODE_Z_POSITIVE:
        {
            Z_POS_LIMIT = int32_t(uint32_t(buffer_rx[3]) << 24 | uint32_t(buffer_rx[4]) << 16 | uint32_t(buffer_rx[5]) << 8 | uint32_t(buffer_rx[6]));
            tmc4361A_setVirtualLimit(&tmc4361[z], 1, Z_POS_LIMIT);
            tmc4361A_enableVirtualLimitSwitch(&tmc4361[z], 1);
            break;
        }
        case LIM_CODE_Z_NEGATIVE:
        {
            Z_NEG_LIMIT = int32_t(uint32_t(buffer_rx[3]) << 24 | uint32_t(buffer_rx[4]) << 16 | uint32_t(buffer_rx[5]) << 8 | uint32_t(buffer_rx[6]));
            tmc4361A_setVirtualLimit(&tmc4361[z], -1, Z_NEG_LIMIT);
            tmc4361A_enableVirtualLimitSwitch(&tmc4361[z], -1);
            break;
        }
    }
}

void callback_set_lim_switch_polarity()
{
    switch (buffer_rx[2])
    {
        case AXIS_X:
        {
            if (buffer_rx[3] != DISABLED)
            {
            LIM_SWITCH_X_ACTIVE_LOW = (buffer_rx[3] == ACTIVE_LOW);
            }
            break;
        }
        case AXIS_Y:
        {
            if (buffer_rx[3] != DISABLED)
            {
            LIM_SWITCH_Y_ACTIVE_LOW = (buffer_rx[3] == ACTIVE_LOW);
            }
            break;
        }
        case AXIS_Z:
        {
            if (buffer_rx[3] != DISABLED)
            {
            LIM_SWITCH_Z_ACTIVE_LOW = (buffer_rx[3] == ACTIVE_LOW);
            }
            break;
        }
    }
}

void callback_set_home_safety_margin()
{
    switch (buffer_rx[2])
    {
        case AXIS_X:
        {
            uint16_t margin = (uint16_t(buffer_rx[3]) << 8) + uint16_t(buffer_rx[4]);
            float home_safety_margin_mm = float(margin) / 1000.0;
            home_safety_margin[x] = tmc4361A_xmmToMicrosteps(&tmc4361[x], home_safety_margin_mm);
            tmc4361A_enableHomingLimit(&tmc4361[x], lft_sw_pol[x], TMC4361_homing_sw[x], home_safety_margin[x]);
            break;
        }
        case AXIS_Y:
        {
            uint16_t margin = (uint16_t(buffer_rx[3]) << 8) + uint16_t(buffer_rx[4]);
            float home_safety_margin_mm = float(margin) / 1000.0;
            home_safety_margin[y] = tmc4361A_xmmToMicrosteps(&tmc4361[y], home_safety_margin_mm);
            tmc4361A_enableHomingLimit(&tmc4361[y], lft_sw_pol[y], TMC4361_homing_sw[y], home_safety_margin[y]);
            break;
        }
        case AXIS_Z:
        {
            uint16_t margin = (uint16_t(buffer_rx[3]) << 8) + uint16_t(buffer_rx[4]);
            float home_safety_margin_mm = float(margin) / 1000.0;
            home_safety_margin[z] = tmc4361A_xmmToMicrosteps(&tmc4361[z], home_safety_margin_mm);
            tmc4361A_enableHomingLimit(&tmc4361[z], lft_sw_pol[z], TMC4361_homing_sw[z], home_safety_margin[z]);
            break;
        }
        case AXIS_W:
        {
            uint16_t margin = (uint16_t(buffer_rx[3]) << 8) + uint16_t(buffer_rx[4]);
            float home_safety_margin_mm = float(margin) / 1000.0;
            home_safety_margin[w] = tmc4361A_xmmToMicrosteps(&tmc4361[w], home_safety_margin_mm);
            tmc4361A_enableHomingLimit(&tmc4361[w], rht_sw_pol[w], TMC4361_homing_sw[w], home_safety_margin[w]);
            break;
        }
    }
}

void callback_set_pid_arguments()
{
    int axis = buffer_rx[2];
    uint16_t p = (uint16_t(buffer_rx[3]) << 8) + uint16_t(buffer_rx[4]);
    uint8_t  i = uint8_t(buffer_rx[5]);
    uint8_t  d = uint8_t(buffer_rx[6]);

    axes_pid_arg[axis].p = p; 
    axes_pid_arg[axis].i = i;
    axes_pid_arg[axis].d = d;
}

void callback_configure_stepper_driver()
{
    switch (buffer_rx[2])
    {
        case AXIS_X:
        {
            int microstepping_setting = buffer_rx[3];
            if (microstepping_setting > 128)
            microstepping_setting = 256;
            MICROSTEPPING_X = microstepping_setting == 0 ? 1 : microstepping_setting;
            steps_per_mm_X = FULLSTEPS_PER_REV_X * MICROSTEPPING_X / SCREW_PITCH_X_MM;
            X_MOTOR_RMS_CURRENT_mA = uint16_t(buffer_rx[4]) * 256 + uint16_t(buffer_rx[5]);
            X_MOTOR_I_HOLD = float(buffer_rx[6]) / 255;
            tmc4361A_tmc2660_config(&tmc4361[x], (X_MOTOR_RMS_CURRENT_mA / 1000.0)*R_sense_xy / 0.2298, X_MOTOR_I_HOLD, 1, 1, 1, SCREW_PITCH_X_MM, FULLSTEPS_PER_REV_X, MICROSTEPPING_X);
            tmc4361A_tmc2660_update(&tmc4361[x]);
            break;
        }
        case AXIS_Y:
        {
            int microstepping_setting = buffer_rx[3];
            if (microstepping_setting > 128)
            microstepping_setting = 256;
            MICROSTEPPING_Y = microstepping_setting == 0 ? 1 : microstepping_setting;
            steps_per_mm_Y = FULLSTEPS_PER_REV_Y * MICROSTEPPING_Y / SCREW_PITCH_Y_MM;
            Y_MOTOR_RMS_CURRENT_mA = uint16_t(buffer_rx[4]) * 256 + uint16_t(buffer_rx[5]);
            Y_MOTOR_I_HOLD = float(buffer_rx[6]) / 255;
            tmc4361A_tmc2660_config(&tmc4361[y], (Y_MOTOR_RMS_CURRENT_mA / 1000.0)*R_sense_xy / 0.2298, Y_MOTOR_I_HOLD, 1, 1, 1, SCREW_PITCH_Y_MM, FULLSTEPS_PER_REV_Y, MICROSTEPPING_Y);
            tmc4361A_tmc2660_update(&tmc4361[y]);
            break;
        }
        case AXIS_Z:
        {
            int microstepping_setting = buffer_rx[3];
            if (microstepping_setting > 128)
            microstepping_setting = 256;
            MICROSTEPPING_Z = microstepping_setting == 0 ? 1 : microstepping_setting;
            steps_per_mm_Z = FULLSTEPS_PER_REV_Z * MICROSTEPPING_Z / SCREW_PITCH_Z_MM;
            Z_MOTOR_RMS_CURRENT_mA = uint16_t(buffer_rx[4]) * 256 + uint16_t(buffer_rx[5]);
            Z_MOTOR_I_HOLD = float(buffer_rx[6]) / 255;
            tmc4361A_tmc2660_config(&tmc4361[z], (Z_MOTOR_RMS_CURRENT_mA / 1000.0)*R_sense_z / 0.2298, Z_MOTOR_I_HOLD, 1, 1, 1, SCREW_PITCH_Z_MM, FULLSTEPS_PER_REV_Z, MICROSTEPPING_Z);
            tmc4361A_tmc2660_update(&tmc4361[z]);
            break;
        }
        case AXIS_W:
        {
            if (enable_filterwheel == true) {
            int microstepping_setting = buffer_rx[3];
            if (microstepping_setting > 128)
                microstepping_setting = 256;
            MICROSTEPPING_W = microstepping_setting == 0 ? 1 : microstepping_setting;
            steps_per_mm_W = FULLSTEPS_PER_REV_W * MICROSTEPPING_W / SCREW_PITCH_W_MM;
            W_MOTOR_RMS_CURRENT_mA = uint16_t(buffer_rx[4]) * 256 + uint16_t(buffer_rx[5]);
            W_MOTOR_I_HOLD = float(buffer_rx[6]) / 255;
            tmc4361A_tmc2660_config(&tmc4361[w], (W_MOTOR_RMS_CURRENT_mA / 1000.0)*R_sense_w / 0.2298, W_MOTOR_I_HOLD, 1, 1, 1, SCREW_PITCH_W_MM, FULLSTEPS_PER_REV_W, MICROSTEPPING_W);
            tmc4361A_tmc2660_update(&tmc4361[w]);
            }
            break;
        }
    }
}

void callback_set_max_velocity_acceleration()
{
    switch (buffer_rx[2])
    {
        case AXIS_X:
        {
            MAX_VELOCITY_X_mm = float(uint16_t(buffer_rx[3]) * 256 + uint16_t(buffer_rx[4])) / 100;
            MAX_ACCELERATION_X_mm = float(uint16_t(buffer_rx[5]) * 256 + uint16_t(buffer_rx[6])) / 10;
            tmc4361A_setMaxSpeed(&tmc4361[x], tmc4361A_vmmToMicrosteps( &tmc4361[x], MAX_VELOCITY_X_mm) );
            tmc4361A_setMaxAcceleration(&tmc4361[x], tmc4361A_ammToMicrosteps( &tmc4361[x], MAX_ACCELERATION_X_mm) );
            break;
        }
        case AXIS_Y:
        {
            MAX_VELOCITY_Y_mm = float(uint16_t(buffer_rx[3]) * 256 + uint16_t(buffer_rx[4])) / 100;
            MAX_ACCELERATION_Y_mm = float(uint16_t(buffer_rx[5]) * 256 + uint16_t(buffer_rx[6])) / 10;
            tmc4361A_setMaxSpeed(&tmc4361[y], tmc4361A_vmmToMicrosteps( &tmc4361[y], MAX_VELOCITY_Y_mm) );
            tmc4361A_setMaxAcceleration(&tmc4361[y], tmc4361A_ammToMicrosteps( &tmc4361[y], MAX_ACCELERATION_Y_mm) );
            break;
        }
        case AXIS_Z:
        {
            MAX_VELOCITY_Z_mm = float(uint16_t(buffer_rx[3]) * 256 + uint16_t(buffer_rx[4])) / 100;
            MAX_ACCELERATION_Z_mm = float(uint16_t(buffer_rx[5]) * 256 + uint16_t(buffer_rx[6])) / 10;
            tmc4361A_setMaxSpeed(&tmc4361[z], tmc4361A_vmmToMicrosteps( &tmc4361[z], MAX_VELOCITY_Z_mm) );
            tmc4361A_setMaxAcceleration(&tmc4361[z], tmc4361A_ammToMicrosteps( &tmc4361[z], MAX_ACCELERATION_Z_mm) );
            break;
        }
        case AXIS_W:
        {
            if (enable_filterwheel == true) {
            MAX_VELOCITY_W_mm = float(uint16_t(buffer_rx[3]) * 256 + uint16_t(buffer_rx[4])) / 100;
            MAX_ACCELERATION_W_mm = float(uint16_t(buffer_rx[5]) * 256 + uint16_t(buffer_rx[6])) / 10;
            tmc4361A_setMaxSpeed(&tmc4361[w], tmc4361A_vmmToMicrosteps( &tmc4361[w], MAX_VELOCITY_W_mm) );
            tmc4361A_setMaxAcceleration(&tmc4361[w], tmc4361A_ammToMicrosteps( &tmc4361[w], MAX_ACCELERATION_W_mm) );
            }
            break;
        }
    }
}

void callback_set_lead_screw_pitch()
{
    switch (buffer_rx[2])
    {
        case AXIS_X:
        {
            SCREW_PITCH_X_MM = float(uint16_t(buffer_rx[3]) * 256 + uint16_t(buffer_rx[4])) / 1000;
            steps_per_mm_X = FULLSTEPS_PER_REV_X * MICROSTEPPING_X / SCREW_PITCH_X_MM;
            break;
        }
        case AXIS_Y:
        {
            SCREW_PITCH_Y_MM = float(uint16_t(buffer_rx[3]) * 256 + uint16_t(buffer_rx[4])) / 1000;
            steps_per_mm_Y = FULLSTEPS_PER_REV_Y * MICROSTEPPING_Y / SCREW_PITCH_Y_MM;
            break;
        }
        case AXIS_Z:
        {
            SCREW_PITCH_Z_MM = float(uint16_t(buffer_rx[3]) * 256 + uint16_t(buffer_rx[4])) / 1000;
            steps_per_mm_Z = FULLSTEPS_PER_REV_Z * MICROSTEPPING_Z / SCREW_PITCH_Z_MM;
            break;
        }
        case AXIS_W:
        {
            if (enable_filterwheel == true) {
            SCREW_PITCH_W_MM = float(uint16_t(buffer_rx[3]) * 256 + uint16_t(buffer_rx[4])) / 1000;
            steps_per_mm_W = FULLSTEPS_PER_REV_W * MICROSTEPPING_W / SCREW_PITCH_W_MM;
            }
            break;
        }
    }
}

void callback_home_or_zero()
{
    // zeroing
    if (buffer_rx[3] == HOME_OR_ZERO_ZERO)
    {
        switch (buffer_rx[2])
        {
        case AXIS_X:
            tmc4361A_setCurrentPosition(&tmc4361[x], 0);
            X_pos = 0;
            break;
        case AXIS_Y:
            tmc4361A_setCurrentPosition(&tmc4361[y], 0);
            Y_pos = 0;
            break;
        case AXIS_Z:
            tmc4361A_setCurrentPosition(&tmc4361[z], 0);
            Z_pos = 0;
            focusPosition = 0;
            break;
        case AXIS_W:
            if (enable_filterwheel == true) {
            tmc4361A_setCurrentPosition(&tmc4361[w], 0);
            W_pos = 0;
            }
            break;
        }
    }
    // atomic operation, no need to change mcu_cmd_execution_in_progress flag
    // homing
    else if (buffer_rx[3] == HOME_NEGATIVE || buffer_rx[3] == HOME_POSITIVE)
    {
        switch (buffer_rx[2])
        {
        case AXIS_X:
            if (stage_PID_enabled[AXIS_X] == 1)
            tmc4361A_set_PID(&tmc4361[AXIS_X], PID_DISABLE);
            tmc4361A_disableVirtualLimitSwitch(&tmc4361[x], -1);
            tmc4361A_disableVirtualLimitSwitch(&tmc4361[x], 1);
            homing_direction_X = buffer_rx[3];
            home_X_found = false;
            if (homing_direction_X == HOME_NEGATIVE) // use the left limit switch for homing
            {
            if ( tmc4361A_readLimitSwitches(&tmc4361[x]) == LEFT_SW )
            {
                // get out of the hysteresis zone
                is_preparing_for_homing_X = true;
                tmc4361A_readInt(&tmc4361[x], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[x], tmc4361A_vmmToMicrosteps( &tmc4361[x], RGHT_DIR * HOMING_VELOCITY_X * MAX_VELOCITY_X_mm ));
            }
            else
            {
                is_homing_X = true;
                tmc4361A_readInt(&tmc4361[x], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[x], tmc4361A_vmmToMicrosteps( &tmc4361[x], LEFT_DIR * HOMING_VELOCITY_X * MAX_VELOCITY_X_mm ));
            }
            }
            else // use the right limit switch for homing
            {
            if ( tmc4361A_readLimitSwitches(&tmc4361[x]) == RGHT_SW )
            {
                // get out of the hysteresis zone
                is_preparing_for_homing_X = true;
                tmc4361A_readInt(&tmc4361[x], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[x], tmc4361A_vmmToMicrosteps( &tmc4361[x], LEFT_DIR * HOMING_VELOCITY_X * MAX_VELOCITY_X_mm ));
            }
            else
            {
                is_homing_X = true;
                tmc4361A_readInt(&tmc4361[x], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[x], tmc4361A_vmmToMicrosteps( &tmc4361[x], RGHT_DIR * HOMING_VELOCITY_X * MAX_VELOCITY_X_mm ));
            }
            }
            /*
            if(digitalRead(X_LIM)==(LIM_SWITCH_X_ACTIVE_LOW?HIGH:LOW))
            {
            is_homing_X = true;
            if(homing_direction_X==HOME_NEGATIVE)
                stepper_X.setSpeed(-HOMING_VELOCITY_X*MAX_VELOCITY_X_mm*steps_per_mm_X);
            else
                stepper_X.setSpeed(HOMING_VELOCITY_X*MAX_VELOCITY_X_mm*steps_per_mm_X);
            }
            else
            {
            // get out of the hysteresis zone
            is_preparing_for_homing_X = true;
            if(homing_direction_X==HOME_NEGATIVE)
                stepper_X.setSpeed(HOMING_VELOCITY_X*MAX_VELOCITY_X_mm*steps_per_mm_X);
            else
                stepper_X.setSpeed(-HOMING_VELOCITY_X*MAX_VELOCITY_X_mm*steps_per_mm_X);
            }
            */
            break;
        case AXIS_Y:
            if (stage_PID_enabled[AXIS_Y] == 1)
            tmc4361A_set_PID(&tmc4361[AXIS_Y], PID_DISABLE);
            tmc4361A_disableVirtualLimitSwitch(&tmc4361[y], -1);
            tmc4361A_disableVirtualLimitSwitch(&tmc4361[y], 1);
            homing_direction_Y = buffer_rx[3];
            home_Y_found = false;
            if (homing_direction_Y == HOME_NEGATIVE) // use the left limit switch for homing
            {
            if ( tmc4361A_readLimitSwitches(&tmc4361[y]) == LEFT_SW )
            {
                // get out of the hysteresis zone
                is_preparing_for_homing_Y = true;
                tmc4361A_readInt(&tmc4361[y], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[y], tmc4361A_vmmToMicrosteps( &tmc4361[y], RGHT_DIR * HOMING_VELOCITY_Y * MAX_VELOCITY_Y_mm ));
            }
            else
            {
                is_homing_Y = true;
                tmc4361A_readInt(&tmc4361[y], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[y], tmc4361A_vmmToMicrosteps( &tmc4361[y], LEFT_DIR * HOMING_VELOCITY_Y * MAX_VELOCITY_Y_mm ));
            }
            }
            else // use the right limit switch for homing
            {
            if ( tmc4361A_readLimitSwitches(&tmc4361[y]) == RGHT_SW )
            {
                // get out of the hysteresis zone
                is_preparing_for_homing_Y = true;
                tmc4361A_readInt(&tmc4361[y], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[y], tmc4361A_vmmToMicrosteps( &tmc4361[y], LEFT_DIR * HOMING_VELOCITY_Y * MAX_VELOCITY_Y_mm ));
            }
            else
            {
                is_homing_Y = true;
                tmc4361A_readInt(&tmc4361[y], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[y], tmc4361A_vmmToMicrosteps( &tmc4361[y], RGHT_DIR * HOMING_VELOCITY_Y * MAX_VELOCITY_Y_mm ));
            }
            }
            break;
        case AXIS_Z:
            if (stage_PID_enabled[AXIS_Z] == 1)
            tmc4361A_set_PID(&tmc4361[AXIS_Z], PID_DISABLE);
            tmc4361A_disableVirtualLimitSwitch(&tmc4361[z], -1);
            tmc4361A_disableVirtualLimitSwitch(&tmc4361[z], 1);
            homing_direction_Z = buffer_rx[3];
            home_Z_found = false;
            if (homing_direction_Z == HOME_NEGATIVE) // use the left limit switch for homing
            {
            if ( tmc4361A_readLimitSwitches(&tmc4361[z]) == LEFT_SW )
            {
                // get out of the hysteresis zone
                is_preparing_for_homing_Z = true;
                tmc4361A_readInt(&tmc4361[z], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[z], tmc4361A_vmmToMicrosteps( &tmc4361[z], RGHT_DIR * HOMING_VELOCITY_Z * MAX_VELOCITY_Z_mm ));
            }
            else
            {
                is_homing_Z = true;
                tmc4361A_readInt(&tmc4361[z], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[z], tmc4361A_vmmToMicrosteps( &tmc4361[z], LEFT_DIR * HOMING_VELOCITY_Z * MAX_VELOCITY_Z_mm ));
            }
            }
            else // use the right limit switch for homing
            {
            if ( tmc4361A_readLimitSwitches(&tmc4361[z]) == RGHT_SW )
            {
                // get out of the hysteresis zone
                is_preparing_for_homing_Z = true;
                tmc4361A_readInt(&tmc4361[z], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[z], tmc4361A_vmmToMicrosteps( &tmc4361[z], LEFT_DIR * HOMING_VELOCITY_Z * MAX_VELOCITY_Z_mm ));
            }
            else
            {
                is_homing_Z = true;
                tmc4361A_readInt(&tmc4361[z], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[z], tmc4361A_vmmToMicrosteps( &tmc4361[z], RGHT_DIR * HOMING_VELOCITY_Z * MAX_VELOCITY_Z_mm ));
            }
            }
            break;
        case AXIS_W:
            if (enable_filterwheel == true) {
            if (stage_PID_enabled[w] == 1)
                tmc4361A_set_PID(&tmc4361[w], PID_DISABLE);
            tmc4361A_disableVirtualLimitSwitch(&tmc4361[w], -1);
            tmc4361A_disableVirtualLimitSwitch(&tmc4361[w], 1);
            homing_direction_W = buffer_rx[3];
            home_W_found = false;
            if (homing_direction_W == HOME_NEGATIVE) // use the left limit switch for homing
            {
                if (tmc4361A_readLimitSwitches(&tmc4361[w]) == 0x00)
                {
                // get out of the hysteresis zone
                is_preparing_for_homing_W = true;
                tmc4361A_readInt(&tmc4361[w], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[w], tmc4361A_vmmToMicrosteps( &tmc4361[w], LEFT_DIR * HOMING_VELOCITY_W ));
                }
                else
                {
                is_homing_W = true;
                tmc4361A_readLimitSwitches(&tmc4361[w]);
                tmc4361A_readInt(&tmc4361[w], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[w], tmc4361A_vmmToMicrosteps( &tmc4361[w], RGHT_DIR * HOMING_VELOCITY_W ));
                }
            }
            else // use the right limit switch for homing
            {
                if (tmc4361A_readLimitSwitches(&tmc4361[w]) == 0x00)
                {
                // get out of the hysteresis zone
                is_preparing_for_homing_W = true;
                tmc4361A_readInt(&tmc4361[w], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[w], tmc4361A_vmmToMicrosteps( &tmc4361[w], RGHT_DIR * HOMING_VELOCITY_W ));
                }
                else
                {
                is_homing_W = true;
                tmc4361A_readLimitSwitches(&tmc4361[w]);
                tmc4361A_readInt(&tmc4361[w], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[w], tmc4361A_vmmToMicrosteps( &tmc4361[w], LEFT_DIR * HOMING_VELOCITY_W ));
                }
            }
            }
            break;
        case AXES_XY:
            if (stage_PID_enabled[AXIS_X] == 1)
            tmc4361A_set_PID(&tmc4361[AXIS_X], PID_DISABLE);
            if (stage_PID_enabled[AXIS_Y] == 1)
            tmc4361A_set_PID(&tmc4361[AXIS_Y], PID_DISABLE);
            tmc4361A_disableVirtualLimitSwitch(&tmc4361[x], -1);
            tmc4361A_disableVirtualLimitSwitch(&tmc4361[x], 1);
            tmc4361A_disableVirtualLimitSwitch(&tmc4361[y], -1);
            tmc4361A_disableVirtualLimitSwitch(&tmc4361[y], 1);
            is_homing_XY = true;
            home_X_found = false;
            home_Y_found = false;
            // homing x
            homing_direction_X = buffer_rx[3];
            home_X_found = false;
            if (homing_direction_X == HOME_NEGATIVE) // use the left limit switch for homing
            {
            if ( tmc4361A_readLimitSwitches(&tmc4361[x]) == LEFT_SW )
            {
                // get out of the hysteresis zone
                is_preparing_for_homing_X = true;
                tmc4361A_readInt(&tmc4361[x], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[x], tmc4361A_vmmToMicrosteps( &tmc4361[x], RGHT_DIR * HOMING_VELOCITY_X * MAX_VELOCITY_X_mm ));
            }
            else
            {
                is_homing_X = true;
                tmc4361A_readInt(&tmc4361[x], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[x], tmc4361A_vmmToMicrosteps( &tmc4361[x], LEFT_DIR * HOMING_VELOCITY_X * MAX_VELOCITY_X_mm ));
            }
            }
            else // use the right limit switch for homing
            {
            if ( tmc4361A_readLimitSwitches(&tmc4361[x]) == RGHT_DIR )
            {
                // get out of the hysteresis zone
                is_preparing_for_homing_X = true;
                tmc4361A_readInt(&tmc4361[x], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[x], tmc4361A_vmmToMicrosteps( &tmc4361[x], LEFT_DIR * HOMING_VELOCITY_X * MAX_VELOCITY_X_mm ));
            }
            else
            {
                is_homing_X = true;
                tmc4361A_readInt(&tmc4361[x], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[x], tmc4361A_vmmToMicrosteps( &tmc4361[x], RGHT_DIR * HOMING_VELOCITY_X * MAX_VELOCITY_X_mm ));
            }
            }
            // homing y
            homing_direction_Y = buffer_rx[4];
            home_Y_found = false;
            if (homing_direction_Y == HOME_NEGATIVE) // use the left limit switch for homing
            {
            if ( tmc4361A_readLimitSwitches(&tmc4361[y]) == LEFT_SW )
            {
                // get out of the hysteresis zone
                is_preparing_for_homing_Y = true;
                tmc4361A_readInt(&tmc4361[y], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[y], tmc4361A_vmmToMicrosteps( &tmc4361[y], RGHT_DIR * HOMING_VELOCITY_Y * MAX_VELOCITY_Y_mm ));
            }
            else
            {
                is_homing_Y = true;
                tmc4361A_readInt(&tmc4361[y], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[y], tmc4361A_vmmToMicrosteps( &tmc4361[y], LEFT_DIR * HOMING_VELOCITY_Y * MAX_VELOCITY_Y_mm ));
            }
            }
            else // use the right limit switch for homing
            {
            if ( tmc4361A_readLimitSwitches(&tmc4361[y]) == RGHT_DIR )
            {
                // get out of the hysteresis zone
                is_preparing_for_homing_Y = true;
                tmc4361A_readInt(&tmc4361[y], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[y], tmc4361A_vmmToMicrosteps( &tmc4361[y], LEFT_DIR * HOMING_VELOCITY_Y * MAX_VELOCITY_Y_mm ));
            }
            else
            {
                is_homing_Y = true;
                tmc4361A_readInt(&tmc4361[y], TMC4361A_EVENTS);
                tmc4361A_setSpeed(&tmc4361[y], tmc4361A_vmmToMicrosteps( &tmc4361[y], RGHT_DIR * HOMING_VELOCITY_Y * MAX_VELOCITY_Y_mm ));
            }
            }
            break;
        }
        mcu_cmd_execution_in_progress = true;
    }
}

void callback_set_offset_velocity()
{
    if (enable_offset_velocity)
    {
        switch (buffer_rx[2])
        {
        case AXIS_X:
            offset_velocity_x = float( int32_t(uint32_t(buffer_rx[3]) << 24 | uint32_t(buffer_rx[4]) << 16 | uint32_t(buffer_rx[5]) << 8 | uint32_t(buffer_rx[6])) ) / 1000000;
            break;
        case AXIS_Y:
            offset_velocity_y = float( int32_t(uint32_t(buffer_rx[3]) << 24 | uint32_t(buffer_rx[4]) << 16 | uint32_t(buffer_rx[5]) << 8 | uint32_t(buffer_rx[6])) ) / 1000000;
            break;
        }
        // TODO: Where does this "break" statement belong? - It was part of the squid controller
        // code before refactoring.
        // break;
    }
}
