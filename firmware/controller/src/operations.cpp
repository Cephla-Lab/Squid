#include "operations.h"

// TODO: move the movement direction sign from configuration.txt (python) to the firmware (with
// setPinsInverted() so that homing_direction_X, homing_direction_Y, homing_direction_Z will no
// longer be needed. This way the home switches can act as limit switches - right now because
// homing_direction_ needs be set by the computer, before they're set, the home switches cannot be
// used as limit switches. Alternatively, add homing_direction_set variables.

void prepare_homing_x()
{
  if (is_preparing_for_homing_X)
  {
    if (homing_direction_X == HOME_NEGATIVE) // use the left limit switch for homing
    {
      if (tmc4361A_readLimitSwitches(&tmc4361[x]) != LEFT_SW)
      {
        is_preparing_for_homing_X = false;
        is_homing_X = true;
        tmc4361A_readInt(&tmc4361[x], TMC4361A_EVENTS);
        tmc4361A_setSpeed(&tmc4361[x], tmc4361A_vmmToMicrosteps( &tmc4361[x], LEFT_DIR * HOMING_VELOCITY_X * MAX_VELOCITY_X_mm ));
      }
    }
    else // use the right limit switch for homing
    {
      if (tmc4361A_readLimitSwitches(&tmc4361[x]) != RGHT_SW)
      {
        is_preparing_for_homing_X = false;
        is_homing_X = true;
        tmc4361A_readInt(&tmc4361[x], TMC4361A_EVENTS);
        tmc4361A_setSpeed(&tmc4361[x], tmc4361A_vmmToMicrosteps( &tmc4361[x], RGHT_DIR * HOMING_VELOCITY_X * MAX_VELOCITY_X_mm ));
      }
    }
  }
}

void prepare_homing_y()
{
  if (is_preparing_for_homing_Y)
  {
    if (homing_direction_Y == HOME_NEGATIVE) // use the left limit switch for homing
    {
      if (tmc4361A_readLimitSwitches(&tmc4361[y]) != LEFT_SW)
      {
        is_preparing_for_homing_Y = false;
        is_homing_Y = true;
        tmc4361A_readInt(&tmc4361[y], TMC4361A_EVENTS);
        tmc4361A_setSpeed(&tmc4361[y], tmc4361A_vmmToMicrosteps( &tmc4361[y], LEFT_DIR * HOMING_VELOCITY_Y * MAX_VELOCITY_Y_mm ));
      }
    }
    else // use the right limit switch for homing
    {
      if (tmc4361A_readLimitSwitches(&tmc4361[y]) != RGHT_SW)
      {
        is_preparing_for_homing_Y = false;
        is_homing_Y = true;
        tmc4361A_readInt(&tmc4361[y], TMC4361A_EVENTS);
        tmc4361A_setSpeed(&tmc4361[y], tmc4361A_vmmToMicrosteps( &tmc4361[y], RGHT_DIR * HOMING_VELOCITY_Y * MAX_VELOCITY_Y_mm ));
      }
    }
  }
}

void prepare_homing_z()
{
  if (is_preparing_for_homing_Z)
  {
    if (homing_direction_Z == HOME_NEGATIVE) // use the left limit switch for homing
    {
      if (tmc4361A_readLimitSwitches(&tmc4361[z]) != LEFT_SW)
      {
        is_preparing_for_homing_Z = false;
        is_homing_Z = true;
        tmc4361A_readInt(&tmc4361[z], TMC4361A_EVENTS);
        tmc4361A_setSpeed(&tmc4361[z], tmc4361A_vmmToMicrosteps( &tmc4361[z], LEFT_DIR * HOMING_VELOCITY_Z * MAX_VELOCITY_Z_mm ));
      }
    }
    else // use the right limit switch for homing
    {
      if (tmc4361A_readLimitSwitches(&tmc4361[z]) != RGHT_SW)
      {
        is_preparing_for_homing_Z = false;
        is_homing_Z = true;
        tmc4361A_readInt(&tmc4361[z], TMC4361A_EVENTS);
        tmc4361A_setSpeed(&tmc4361[z], tmc4361A_vmmToMicrosteps( &tmc4361[z], RGHT_DIR * HOMING_VELOCITY_Z * MAX_VELOCITY_Z_mm ));
      }
    }
  }
}

void prepare_homing_w()
{
  if (is_preparing_for_homing_W)
  {
    if (homing_direction_W == HOME_NEGATIVE) // use the left limit switch for homing
    {
      if (tmc4361A_readLimitSwitches(&tmc4361[w]) != 0x00)
      {
        is_preparing_for_homing_W = false;
        is_homing_W = true;
        tmc4361A_readInt(&tmc4361[w], TMC4361A_EVENTS);
        tmc4361A_setSpeed(&tmc4361[w], tmc4361A_vmmToMicrosteps( &tmc4361[w], RGHT_DIR * HOMING_VELOCITY_W ));
      }
    }
    else // use the right limit switch for homing
    {
      if (tmc4361A_readLimitSwitches(&tmc4361[w]) != 0x00)
      {
        is_preparing_for_homing_W = false;
        is_homing_W = true;
        tmc4361A_readInt(&tmc4361[w], TMC4361A_EVENTS);
        tmc4361A_setSpeed(&tmc4361[w], tmc4361A_vmmToMicrosteps( &tmc4361[w], LEFT_DIR * HOMING_VELOCITY_W ));
      }
    }
  }
}

void check_homing_x()
{
  if (is_homing_X && !home_X_found)
  {
    if (homing_direction_X == HOME_NEGATIVE) // use the left limit switch for homing
    {
      if (tmc4361A_readSwitchEvent(&tmc4361[x]) == LEFT_SW || tmc4361A_readLimitSwitches(&tmc4361[x]) == LEFT_SW)
      {
        home_X_found = true;
        us_since_x_home_found = 0;
        tmc4361[x].xmin = tmc4361A_readInt(&tmc4361[x], TMC4361A_X_LATCH_RD);
        // tmc4361A_writeInt(&tmc4361[x], TMC4361A_X_TARGET, tmc4361[x].xmin);
        tmc4361A_moveTo(&tmc4361[x], tmc4361[x].xmin);
        X_commanded_movement_in_progress = true;
        X_commanded_target_position = tmc4361[x].xmin;
        // turn_on_LED_matrix_pattern(matrix,ILLUMINATION_SOURCE_LED_ARRAY_FULL,30,10,10); // debug
      }
    }
    else // use the right limit switch for homing
    {
      if (tmc4361A_readSwitchEvent(&tmc4361[x]) == RGHT_SW || tmc4361A_readLimitSwitches(&tmc4361[x]) == RGHT_SW)
      {
        home_X_found = true;
        us_since_x_home_found = 0;
        tmc4361[x].xmax = tmc4361A_readInt(&tmc4361[x], TMC4361A_X_LATCH_RD);
        // tmc4361A_writeInt(&tmc4361[x], TMC4361A_X_TARGET, tmc4361[x].xmax);
        tmc4361A_moveTo(&tmc4361[x], tmc4361[x].xmax);
        X_commanded_movement_in_progress = true;
        X_commanded_target_position = tmc4361[x].xmax;
        // turn_on_LED_matrix_pattern(matrix,ILLUMINATION_SOURCE_LED_ARRAY_FULL,30,10,10); // debug
      }
    }
  }
}

void check_homing_y()
{
  if (is_homing_Y && !home_Y_found)
  {
    if (homing_direction_Y == HOME_NEGATIVE) // use the left limit switch for homing
    {
      if (tmc4361A_readSwitchEvent(&tmc4361[y]) == LEFT_SW || tmc4361A_readLimitSwitches(&tmc4361[y]) == LEFT_SW)
      {
        home_Y_found = true;
        us_since_y_home_found = 0;
        tmc4361[y].xmin = tmc4361A_readInt(&tmc4361[y], TMC4361A_X_LATCH_RD);
        // tmc4361A_writeInt(&tmc4361[y], TMC4361A_X_TARGET, tmc4361[y].xmin);
        tmc4361A_moveTo(&tmc4361[y], tmc4361[y].xmin);
        Y_commanded_movement_in_progress = true;
        Y_commanded_target_position = tmc4361[y].xmin;
        // turn_on_LED_matrix_pattern(matrix,ILLUMINATION_SOURCE_LED_ARRAY_FULL,30,10,10); // debug
      }
    }
    else // use the right limit switch for homing
    {
      if (tmc4361A_readSwitchEvent(&tmc4361[y]) == RGHT_SW || tmc4361A_readLimitSwitches(&tmc4361[y]) == RGHT_SW)
      {
        home_Y_found = true;
        us_since_y_home_found = 0;
        tmc4361[y].xmax = tmc4361A_readInt(&tmc4361[y], TMC4361A_X_LATCH_RD);
        // tmc4361A_writeInt(&tmc4361[y], TMC4361A_X_TARGET, tmc4361[y].xmax);
        tmc4361A_moveTo(&tmc4361[y], tmc4361[y].xmax);
        Y_commanded_movement_in_progress = true;
        Y_commanded_target_position = tmc4361[y].xmax;
        // turn_on_LED_matrix_pattern(matrix,ILLUMINATION_SOURCE_LED_ARRAY_FULL,30,10,10); // debug
      }
    }
  }
}

void check_homing_z()
{
  if (is_homing_Z && !home_Z_found)
  {
    if (homing_direction_Z == HOME_NEGATIVE) // use the left limit switch for homing
    {
      if (tmc4361A_readSwitchEvent(&tmc4361[z]) == LEFT_SW || tmc4361A_readLimitSwitches(&tmc4361[z]) == LEFT_SW)
      {
        home_Z_found = true;
        us_since_z_home_found = 0;
        tmc4361[z].xmin = tmc4361A_readInt(&tmc4361[z], TMC4361A_X_LATCH_RD);
        // tmc4361A_writeInt(&tmc4361[z], TMC4361A_X_TARGET, tmc4361[z].xmin);
        tmc4361A_moveTo(&tmc4361[z], tmc4361[z].xmin);
        Z_commanded_movement_in_progress = true;
        Z_commanded_target_position = tmc4361[z].xmin;
        // turn_on_LED_matrix_pattern(matrix,ILLUMINATION_SOURCE_LED_ARRAY_FULL,30,10,10); // debug
      }
    }
    else // use the right limit switch for homing
    {
      if (tmc4361A_readSwitchEvent(&tmc4361[z]) == RGHT_SW || tmc4361A_readLimitSwitches(&tmc4361[z]) == RGHT_SW)
      {
        home_Z_found = true;
        us_since_z_home_found = 0;
        tmc4361[z].xmax = tmc4361A_readInt(&tmc4361[z], TMC4361A_X_LATCH_RD);
        //tmc4361A_writeInt(&tmc4361[z], TMC4361A_X_TARGET, tmc4361[z].xmax);
        tmc4361A_moveTo(&tmc4361[z], tmc4361[z].xmax);
        Z_commanded_movement_in_progress = true;
        Z_commanded_target_position = tmc4361[z].xmax;
        // turn_on_LED_matrix_pattern(matrix,ILLUMINATION_SOURCE_LED_ARRAY_FULL,30,10,10); // debug
      }
    }
  }
}

void check_homing_w()
{
  if (is_homing_W && !home_W_found)
  {
    if (homing_direction_W == HOME_NEGATIVE) // use the left limit switch for homing
    {
      if (tmc4361A_readLimitSwitches(&tmc4361[w]) == 0x00)
      {
        home_W_found = true;
        us_since_w_home_found = 0;
        if (enable_filterwheel == true) {
          tmc4361[w].xmin = tmc4361A_readInt(&tmc4361[w], TMC4361A_X_LATCH_RD);
          tmc4361A_setCurrentPosition(&tmc4361[w], tmc4361[w].xmin);
        }
        W_commanded_movement_in_progress = true;
        W_commanded_target_position = tmc4361[w].xmin;
      }
    }
    else // use the right limit switch for homing
    {
      if (tmc4361A_readLimitSwitches(&tmc4361[w]) == 0x00)
      {
        home_W_found = true;
        us_since_w_home_found = 0;
        if (enable_filterwheel == true) {
          tmc4361[w].xmax = tmc4361A_readInt(&tmc4361[w], TMC4361A_X_LATCH_RD);
          tmc4361A_setCurrentPosition(&tmc4361[w], tmc4361[w].xmax);
        }
        W_commanded_movement_in_progress = true;
        W_commanded_target_position = tmc4361[w].xmax;
      }
    }
  }
}

void finalize_homing_x()
{
  if (is_homing_X && home_X_found && ( tmc4361A_currentPosition(&tmc4361[x]) == tmc4361A_targetPosition(&tmc4361[x]) || us_since_x_home_found > 500 * 1000 ) )
  {
    tmc4361A_setCurrentPosition(&tmc4361[x], 0);
    if (stage_PID_enabled[AXIS_X])
      tmc4361A_set_PID(&tmc4361[AXIS_X], PID_BPG0);
    X_pos = 0;
    is_homing_X = false;
    X_commanded_movement_in_progress = false;
    X_commanded_target_position = 0;
    if (is_homing_XY == false)
      mcu_cmd_execution_in_progress = false;
  }
}

void finalize_homing_y()
{
  if (is_homing_Y && home_Y_found && ( tmc4361A_currentPosition(&tmc4361[y]) == tmc4361A_targetPosition(&tmc4361[y]) || us_since_y_home_found > 500 * 1000 ) )
  {
    tmc4361A_setCurrentPosition(&tmc4361[y], 0);
    if (stage_PID_enabled[AXIS_Y])
      tmc4361A_set_PID(&tmc4361[AXIS_Y], PID_BPG0);
    Y_pos = 0;
    is_homing_Y = false;
    Y_commanded_movement_in_progress = false;
    Y_commanded_target_position = 0;
    if (is_homing_XY == false)
      mcu_cmd_execution_in_progress = false;
  }
}

void finalize_homing_z()
{
  if (is_homing_Z && home_Z_found && ( tmc4361A_currentPosition(&tmc4361[z]) == tmc4361A_targetPosition(&tmc4361[z]) || us_since_z_home_found > 500 * 1000 ) )
  {
    tmc4361A_setCurrentPosition(&tmc4361[z], 0);
    if (stage_PID_enabled[AXIS_Z])
      tmc4361A_set_PID(&tmc4361[AXIS_Z], PID_BPG0);
    Z_pos = 0;
    focusPosition = 0;
    is_homing_Z = false;
    Z_commanded_movement_in_progress = false;
    Z_commanded_target_position = 0;
    mcu_cmd_execution_in_progress = false;
  }
}

void finalize_homing_w()
{
  if (is_homing_W && home_W_found && ( tmc4361A_currentPosition(&tmc4361[w]) == tmc4361A_targetPosition(&tmc4361[w]) || us_since_w_home_found > 500 * 1000 ) )
  {
    if (enable_filterwheel == true) {
      tmc4361A_write_encoder(&tmc4361[w], 0);
      if (stage_PID_enabled[w])
        tmc4361A_set_PID(&tmc4361[w], PID_BPG0);
    }
    W_pos = 0;
    is_homing_W = false;
    W_commanded_movement_in_progress = false;
    W_commanded_target_position = 0;
    mcu_cmd_execution_in_progress = false;
  }
}

void finalize_homing_xy()
{
  if (is_homing_XY && home_X_found && !is_homing_X && home_Y_found && !is_homing_Y)
  {
    is_homing_XY = false;
    mcu_cmd_execution_in_progress = false;
  }
}

void do_camera_trigger()
{
  if (trigger_mode == 0) {
    for (int camera_channel = 0; camera_channel < 6; camera_channel++)
    {
      // end the trigger pulse
      if (trigger_output_level[camera_channel] == LOW && (micros() - timestamp_trigger_rising_edge[camera_channel]) >= TRIGGER_PULSE_LENGTH_us )
      {
        digitalWrite(camera_trigger_pins[camera_channel], HIGH);
        trigger_output_level[camera_channel] = HIGH;
      }
    }
  }
  else {
    // for level trigger logic
    for (int camera_channel = 0; camera_channel < 6; camera_channel++)
    {
      // end the trigger pulse after strobe_delay + illumination_on_time
      // so illumination is fully contained within the trigger pulse
      if (trigger_output_level[camera_channel] == LOW && (micros() - timestamp_trigger_rising_edge[camera_channel]) >= strobe_delay[camera_channel] + illumination_on_time[camera_channel])
      {
        digitalWrite(camera_trigger_pins[camera_channel], HIGH);
        trigger_output_level[camera_channel] = HIGH;
      }
    }
  }
}

void check_joystick()
{
  if (flag_read_joystick)
  {
	if (us_since_last_joystick_update > interval_send_joystick_update)
	{
	  us_since_last_joystick_update = 0;

	  // read x joystick
	  if (!X_commanded_movement_in_progress && !is_homing_X && !is_preparing_for_homing_X) //if(stepper_X.distanceToGo()==0) // only read joystick when computer commanded travel has finished - doens't work
	  {
	    // joystick at motion position
	    if (abs(joystick_delta_x) > 0)
	  	  tmc4361A_setSpeed( &tmc4361[x], tmc4361A_vmmToMicrosteps( &tmc4361[x], offset_velocity_x + (joystick_delta_x / 32768.0)*MAX_VELOCITY_X_mm ) );
	    // joystick at rest position
	    else
	    {
	  	  if (enable_offset_velocity)
	  	    tmc4361A_setSpeed( &tmc4361[x], tmc4361A_vmmToMicrosteps( &tmc4361[x], offset_velocity_x ) );
	  	  else
		    tmc4361A_stop(&tmc4361[x]); // tmc4361A_setSpeed( &tmc4361[x], 0 ) causes problems for zeroing
	      }
	  }

	  // read y joystick
	  if (!Y_commanded_movement_in_progress && !is_homing_Y && !is_preparing_for_homing_Y)
	  {
	    // joystick at motion position
	    if (abs(joystick_delta_y) > 0)
	  	  tmc4361A_setSpeed( &tmc4361[y], tmc4361A_vmmToMicrosteps( &tmc4361[y], offset_velocity_y + (joystick_delta_y / 32768.0)*MAX_VELOCITY_Y_mm ) );
	    // joystick at rest position
	    else
	    {
	  	  if (enable_offset_velocity)
	  	    tmc4361A_setSpeed( &tmc4361[y], tmc4361A_vmmToMicrosteps( &tmc4361[y], offset_velocity_y ) );
	  	  else
	  	    tmc4361A_stop(&tmc4361[y]); // tmc4361A_setSpeed( &tmc4361[y], 0 ) causes problems for zeroing
	    }
	  }
	}

    // set the read joystick flag to false
    flag_read_joystick = false;
  }
}

void do_focus_control()
{
  if (focusPosition > Z_POS_LIMIT)
    focusPosition = Z_POS_LIMIT;
  if (focusPosition < Z_NEG_LIMIT)
    focusPosition = Z_NEG_LIMIT;
  if (is_homing_Z == false && is_preparing_for_homing_Z == false)
    tmc4361A_moveTo(&tmc4361[z], focusPosition);
}

void check_position()
{
  if(us_since_last_check_position > interval_check_position) {
    us_since_last_check_position = 0;
    // check if commanded position has been reached
    if (X_commanded_movement_in_progress && tmc4361A_currentPosition(&tmc4361[x]) == X_commanded_target_position && !is_homing_X && !tmc4361A_isRunning(&tmc4361[x], stage_PID_enabled[x])) // homing is handled separately
    {
      X_commanded_movement_in_progress = false;
      mcu_cmd_execution_in_progress = false || Y_commanded_movement_in_progress || Z_commanded_movement_in_progress || W_commanded_movement_in_progress;
    }
    if (Y_commanded_movement_in_progress && tmc4361A_currentPosition(&tmc4361[y]) == Y_commanded_target_position && !is_homing_Y && !tmc4361A_isRunning(&tmc4361[y], stage_PID_enabled[y]))
    {
      Y_commanded_movement_in_progress = false;
      mcu_cmd_execution_in_progress = false || X_commanded_movement_in_progress || Z_commanded_movement_in_progress || W_commanded_movement_in_progress;
    }
    if (Z_commanded_movement_in_progress && tmc4361A_currentPosition(&tmc4361[z]) == Z_commanded_target_position && !is_homing_Z && !tmc4361A_isRunning(&tmc4361[z], stage_PID_enabled[z]))
    {
      Z_commanded_movement_in_progress = false;
      mcu_cmd_execution_in_progress = false || X_commanded_movement_in_progress || Y_commanded_movement_in_progress || W_commanded_movement_in_progress;
    }
    if (enable_filterwheel == true) {
      if (W_commanded_movement_in_progress && tmc4361A_currentPosition(&tmc4361[w]) == W_commanded_target_position && !is_homing_W && !tmc4361A_isRunning(&tmc4361[w], stage_PID_enabled[w]))
      {
        W_commanded_movement_in_progress = false;
        mcu_cmd_execution_in_progress = false || X_commanded_movement_in_progress || Y_commanded_movement_in_progress || Z_commanded_movement_in_progress;
      }
    }
  }
}

void check_limits()
{
  if (us_since_last_check_limit > interval_check_limit) {
    us_since_last_check_limit = 0;

  	// at limit
    if (X_commanded_movement_in_progress && !is_homing_X) // homing is handled separately
    {
      uint8_t event = tmc4361A_readSwitchEvent(&tmc4361[x]);
      // if( tmc4361A_readLimitSwitches(&tmc4361[x])==LEFT_SW || tmc4361A_readLimitSwitches(&tmc4361[x])==RGHT_SW )
      if ( ( X_direction == LEFT_DIR && event == LEFT_SW ) || ( X_direction == RGHT_DIR && event == RGHT_SW ) )
      {
        X_commanded_movement_in_progress = false;
        mcu_cmd_execution_in_progress = false || Y_commanded_movement_in_progress || Z_commanded_movement_in_progress || W_commanded_movement_in_progress;
      }
    }
    if (Y_commanded_movement_in_progress && !is_homing_Y) // homing is handled separately
    {
      uint8_t event = tmc4361A_readSwitchEvent(&tmc4361[y]);
      //if( tmc4361A_readLimitSwitches(&tmc4361[y])==LEFT_SW || tmc4361A_readLimitSwitches(&tmc4361[y])==RGHT_SW )
      if ( ( Y_direction == LEFT_DIR && event == LEFT_SW ) || ( Y_direction == RGHT_DIR && event == RGHT_SW ) )
      {
        Y_commanded_movement_in_progress = false;
        mcu_cmd_execution_in_progress = false || X_commanded_movement_in_progress || Z_commanded_movement_in_progress || W_commanded_movement_in_progress;
      }
    }
    if (Z_commanded_movement_in_progress && !is_homing_Z) // homing is handled separately
    {
      uint8_t event = tmc4361A_readSwitchEvent(&tmc4361[z]);
      // if( tmc4361A_readLimitSwitches(&tmc4361[z])==LEFT_SW || tmc4361A_readLimitSwitches(&tmc4361[z])==RGHT_SW )
      if ( ( Z_direction == LEFT_DIR && event == LEFT_SW ) || ( Z_direction == RGHT_DIR && event == RGHT_SW ) )
      {
        Z_commanded_movement_in_progress = false;
        mcu_cmd_execution_in_progress = false || X_commanded_movement_in_progress || Y_commanded_movement_in_progress || W_commanded_movement_in_progress;
      }
    }
  }
}
