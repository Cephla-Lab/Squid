#include "operations.h"

#include "tmc/drivers/stepper_driver.h"   // DRIVER_UNKNOWN, tmc_driver_ready
#include "pid_policy.h"                  // pid_engage_pending

/*
  THE OPERATOR-DRIVEN MOTION PATHS ARE PART OF THE FAIL-SAFE.

  stage_commands.cpp rejects host move commands on an axis whose driver the
  probe could not identify. The three gates below close the other door: the
  joystick and the focus wheel command motion directly, from the panel's UART
  packets, without going through a command callback at all.

  Leaving them open would be worse than an oversight. Rejecting the host's
  moves means *_commanded_movement_in_progress never becomes true on that axis,
  which is one of the conditions the joystick blocks wait for — so the guards in
  stage_commands.cpp hold the joystick gate PERMANENTLY OPEN on exactly the axis
  they just locked. The realistic sequence: X probes DRIVER_UNKNOWN on a
  mis-populated board, the host's MOVE_X comes back CMD_EXECUTION_ERROR, the
  operator reads that as "X is inhibited" and reaches for the joystick to jog it
  by hand — and X moves at whatever current its power stage happens to be
  strapped to, because tmc_driver_init() has no DRIVER_UNKNOWN arm and never
  configured it.

  These gates are SILENT: they must not touch mcu_cmd_execution_status. The
  joystick and focus wheel are not host commands and have no command status of
  their own, so writing CMD_EXECUTION_ERROR here would attribute a hardware
  fault to whatever unrelated command the host last sent. That is the one
  behavioural difference from the axis_driver_ready helper in
  stage_commands.cpp, which exists to report exactly that status.
*/

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
        // Homing runs open-loop: it ends at the home stop, where the stage may
        // stop following the actuator and the encoder error can only grow. The
        // request survives (pid_requested) and check_closed_loop() re-engages the
        // loop once the axis is back outside the home zone.
        if (stage_PID_enabled[z])
        {
          tmc4361A_set_PID(&tmc4361[z], PID_DISABLE);
          stage_PID_enabled[z] = 0;
          pid_zone_hold[z] = true;
        }
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
        // Homing runs open-loop: it ends at the home stop, where the stage may
        // stop following the actuator and the encoder error can only grow. The
        // request survives (pid_requested) and check_closed_loop() re-engages the
        // loop once the axis is back outside the home zone.
        if (stage_PID_enabled[z])
        {
          tmc4361A_set_PID(&tmc4361[z], PID_DISABLE);
          stage_PID_enabled[z] = 0;
          pid_zone_hold[z] = true;
        }
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

void prepare_homing_w2()
{
  if (is_preparing_for_homing_W2)
  {
    if (homing_direction_W2 == HOME_NEGATIVE) // use the left limit switch for homing
    {
      if (tmc4361A_readLimitSwitches(&tmc4361[w2]) != 0x00)
      {
        is_preparing_for_homing_W2 = false;
        is_homing_W2 = true;
        tmc4361A_readInt(&tmc4361[w2], TMC4361A_EVENTS);
        tmc4361A_setSpeed(&tmc4361[w2], tmc4361A_vmmToMicrosteps( &tmc4361[w2], RGHT_DIR * HOMING_VELOCITY_W ));
      }
    }
    else // use the right limit switch for homing
    {
      if (tmc4361A_readLimitSwitches(&tmc4361[w2]) != 0x00)
      {
        is_preparing_for_homing_W2 = false;
        is_homing_W2 = true;
        tmc4361A_readInt(&tmc4361[w2], TMC4361A_EVENTS);
        tmc4361A_setSpeed(&tmc4361[w2], tmc4361A_vmmToMicrosteps( &tmc4361[w2], LEFT_DIR * HOMING_VELOCITY_W ));
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

void check_homing_w2()
{
  if (is_homing_W2 && !home_W2_found)
  {
    if (homing_direction_W2 == HOME_NEGATIVE) // use the left limit switch for homing
    {
      if (tmc4361A_readLimitSwitches(&tmc4361[w2]) == 0x00)
      {
        home_W2_found = true;
        us_since_w2_home_found = 0;
        if (enable_filterwheel_w2 == true) {
          tmc4361[w2].xmin = tmc4361A_readInt(&tmc4361[w2], TMC4361A_X_LATCH_RD);
          tmc4361A_setCurrentPosition(&tmc4361[w2], tmc4361[w2].xmin);
        }
        W2_commanded_movement_in_progress = true;
        W2_commanded_target_position = tmc4361[w2].xmin;
      }
    }
    else // use the right limit switch for homing
    {
      if (tmc4361A_readLimitSwitches(&tmc4361[w2]) == 0x00)
      {
        home_W2_found = true;
        us_since_w2_home_found = 0;
        if (enable_filterwheel_w2 == true) {
          tmc4361[w2].xmax = tmc4361A_readInt(&tmc4361[w2], TMC4361A_X_LATCH_RD);
          tmc4361A_setCurrentPosition(&tmc4361[w2], tmc4361[w2].xmax);
        }
        W2_commanded_movement_in_progress = true;
        W2_commanded_target_position = tmc4361[w2].xmax;
      }
    }
  }
}

void finalize_homing_x()
{
  if (is_homing_X && home_X_found && ( tmc4361A_currentPosition(&tmc4361[x]) == tmc4361A_targetPosition(&tmc4361[x]) || us_since_x_home_found > 500 * 1000 ) )
  {
    tmc4361A_setCurrentPosition(&tmc4361[x], 0);
    // Keep the encoder frame aligned with XACTUAL: the closed loop nulls
    // XACTUAL - ENC_POS in absolute terms, and a TMC4361A reset (INITIALIZE)
    // zeroes ENC_POS wherever the axis happened to be. Same as the W homing path.
    tmc4361A_write_encoder(&tmc4361[x], 0);
    // Internal index x, not the protocol id AXIS_X: the two orders differ
    // (internal y=0, x=1), so the old AXIS_X subscript re-armed the wrong axis.
    if (stage_PID_enabled[x])
      tmc4361A_set_PID(&tmc4361[x], PID_BPG0);
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
    // Keep the encoder frame aligned with XACTUAL: the closed loop nulls
    // XACTUAL - ENC_POS in absolute terms, and a TMC4361A reset (INITIALIZE)
    // zeroes ENC_POS wherever the axis happened to be. Same as the W homing path.
    tmc4361A_write_encoder(&tmc4361[y], 0);
    // Internal index y, not the protocol id AXIS_Y: the two orders differ
    // (internal y=0, x=1), so the old AXIS_Y subscript re-armed the wrong axis.
    if (stage_PID_enabled[y])
      tmc4361A_set_PID(&tmc4361[y], PID_BPG0);
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
    // Keep the encoder frame aligned with XACTUAL: the closed loop nulls
    // XACTUAL - ENC_POS in absolute terms, and a TMC4361A reset (INITIALIZE)
    // zeroes ENC_POS wherever the axis happened to be. Same as the W homing path.
    tmc4361A_write_encoder(&tmc4361[z], 0);
    // No re-arm here (master re-armed with the wrong index anyway): the loop was
    // opened by prepare_homing_z and check_closed_loop() re-engages it once the
    // axis leaves the home zone with a small error - and never at the stop itself,
    // where the encoder may not be following the actuator.
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
      // Anchor the driver coordinate to 0 at the home reference so the
      // host can target absolute slot positions via MOVETO_W.
      tmc4361A_setCurrentPosition(&tmc4361[w], 0);
      tmc4361A_write_encoder(&tmc4361[w], 0);
      if (stage_PID_enabled[w])
        tmc4361A_set_PID(&tmc4361[w], PID_BPG0);
      // The filter wheel is ROTARY (no travel end-stop) -- the linear-stage
      // [xmin,xmax] range gate in tmc4361A_moveTo does not apply to it. Homing
      // leaves xmin = L - C (L = press-latch position, C = release-detect
      // position); a stale/mis-ordered latch makes xmin exceed the small absolute
      // post-home offset target, so MOVETO_W is wrongly rejected with
      // CMD_EXECUTION_ERROR. Reset to full range so valid rotary moves are never
      // range-rejected. Safe: W xmin/xmax are written only in check_homing_w and
      // consumed only by the moveTo range check (check_limits acts on X/Y/Z only,
      // stopping them on a limit-switch event, and never touches W/W2).
      tmc4361[w].xmin = INT32_MIN;
      tmc4361[w].xmax = INT32_MAX;
    }
    W_pos = 0;
    is_homing_W = false;
    W_commanded_movement_in_progress = false;
    W_commanded_target_position = 0;
    mcu_cmd_execution_in_progress = false;
  }
}

void finalize_homing_w2()
{
  if (is_homing_W2 && home_W2_found && ( tmc4361A_currentPosition(&tmc4361[w2]) == tmc4361A_targetPosition(&tmc4361[w2]) || us_since_w2_home_found > 500 * 1000 ) )
  {
    if (enable_filterwheel_w2 == true) {
      tmc4361A_setCurrentPosition(&tmc4361[w2], 0);
      tmc4361A_write_encoder(&tmc4361[w2], 0);
      if (stage_PID_enabled[w2])
        tmc4361A_set_PID(&tmc4361[w2], PID_BPG0);
      // Rotary wheel: no travel end-stop -> full range (see finalize_homing_w).
      tmc4361[w2].xmin = INT32_MIN;
      tmc4361[w2].xmax = INT32_MAX;
    }
    W2_pos = 0;
    is_homing_W2 = false;
    W2_commanded_movement_in_progress = false;
    W2_commanded_target_position = 0;
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
    for (int camera_channel = 0; camera_channel < 4; camera_channel++)
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
    for (int camera_channel = 0; camera_channel < 4; camera_channel++)
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
	  // tmc_driver_ready gates the whole block, not just the two setSpeed calls:
	  // an axis that is never commanded to move has nothing for the else-branch
	  // stop to halt, and a stop is itself a write to an unconfigured driver.
	  if (tmc_driver_ready(&tmc4361[x]) && !X_commanded_movement_in_progress && !is_homing_X && !is_preparing_for_homing_X) //if(stepper_X.distanceToGo()==0) // only read joystick when computer commanded travel has finished - doens't work
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
	  if (tmc_driver_ready(&tmc4361[y]) && !Y_commanded_movement_in_progress && !is_homing_Y && !is_preparing_for_homing_Y)
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
  // The clamp above still runs on a rejected axis: focusPosition is written by
  // the focus wheel (functions.cpp, onJoystickPacketReceived) whether or not Z
  // can be driven, and letting it drift outside the limits would hand Z a wild
  // target the moment the axis is recovered. Only the move is gated.
  if (tmc_driver_ready(&tmc4361[z]) && is_homing_Z == false && is_preparing_for_homing_Z == false)
    tmc4361A_moveTo(&tmc4361[z], focusPosition);
}

// Defined with check_closed_loop() below, next to the rest of the closed-loop policy;
// check_position() needs it to tell a held-open loop apart from a homing move.
static bool axis_is_homing(uint8_t i);

// SET_COMPLETION_WINDOW: a commanded move counts as complete once XACTUAL is within the
// axis's window of the target, while the ramp is still finishing. Off (0) for every axis
// unless the host sets it; the filter wheels use it so an exposure can start while the
// last degrees are travelled. With the closed loop engaged the encoder error must be
// inside the window as well: the counter reaching the target says nothing about where
// the stage is until the correction has run (measured 2026-09-08: a 0.3 um window on a
// closed-loop Z acknowledged 13 ms before the encoder had settled).
static inline bool within_completion_window(uint8_t axis, int32_t target, int32_t pos)
{
  int32_t win = completion_window_usteps[axis];
  if (win <= 0) return false;
  int32_t d = pos - target;
  if ((d < 0 ? -d : d) > win) return false;
  // A requested loop is opened for the move in rest-only mode and re-engages at rest, so the
  // encoder condition has to hold whenever the loop is REQUESTED, not only while it is engaged.
  if (!pid_requested[axis] && !stage_PID_enabled[axis]) return true;
  // ENC_POS_DEV (register ENC_POS_DEV_RD 0x52) is ENC_POS - XACTUAL as the chip reports it,
  // i.e. already (encoder - counter): positive means the encoder is AHEAD of the step counter.
  // Bench-verified on this branch - see the same statement in serial_communication.cpp, and the
  // self-test's "frame offset after homing", which is negative on the gap stage whose encoder
  // lags the counter by the 0.64 mm gap. So `dev` is passed through unnegated.
  // The bound is on the ENCODER's distance to the target, not on the counter's and the
  // encoder's separately: the latter accepted anything up to 2*win of real error.
  int32_t dev = tmc4361A_read_deviation(&tmc4361[axis]);
  return encoder_within_window(d, dev, win);
}

void check_position()
{
  if(us_since_last_check_position > interval_check_position) {
    us_since_last_check_position = 0;
    // check if commanded position has been reached. XACTUAL is read once per axis
    // and reused by both completion tests and by pid_engage_pending(), so the
    // three of them cannot disagree about where the axis is.
    //
    // pid_engage_pending(): a rest-only loop is opened for the move and re-engaged
    // by check_closed_loop(), which runs just before this function but reads STATUS
    // separately. If the ramp stops between the two reads the axis looks finished
    // here while the loop is still held open, and COMPLETED would go out before the
    // encoder correction had run at all. X, Y and Z only - the filter wheels have no
    // encoder loop.
    if (X_commanded_movement_in_progress && !is_homing_X) // homing is handled separately
    {
      int32_t pos = tmc4361A_currentPosition(&tmc4361[x]);
      if (((pos == X_commanded_target_position && !tmc4361A_isRunning(&tmc4361[x], stage_PID_enabled[x])) || within_completion_window(x, X_commanded_target_position, pos))
          && !pid_engage_pending(pid_requested[x], pid_zone_hold[x], axis_is_homing(x), pid_home_zone_usteps[x], pos))
      {
        X_commanded_movement_in_progress = false;
        mcu_cmd_execution_in_progress = false || Y_commanded_movement_in_progress || Z_commanded_movement_in_progress || W_commanded_movement_in_progress || W2_commanded_movement_in_progress;
      }
    }
    if (Y_commanded_movement_in_progress && !is_homing_Y)
    {
      int32_t pos = tmc4361A_currentPosition(&tmc4361[y]);
      if (((pos == Y_commanded_target_position && !tmc4361A_isRunning(&tmc4361[y], stage_PID_enabled[y])) || within_completion_window(y, Y_commanded_target_position, pos))
          && !pid_engage_pending(pid_requested[y], pid_zone_hold[y], axis_is_homing(y), pid_home_zone_usteps[y], pos))
      {
        Y_commanded_movement_in_progress = false;
        mcu_cmd_execution_in_progress = false || X_commanded_movement_in_progress || Z_commanded_movement_in_progress || W_commanded_movement_in_progress || W2_commanded_movement_in_progress;
      }
    }
    if (Z_commanded_movement_in_progress && !is_homing_Z)
    {
      int32_t pos = tmc4361A_currentPosition(&tmc4361[z]);
      if (((pos == Z_commanded_target_position && !tmc4361A_isRunning(&tmc4361[z], stage_PID_enabled[z])) || within_completion_window(z, Z_commanded_target_position, pos))
          && !pid_engage_pending(pid_requested[z], pid_zone_hold[z], axis_is_homing(z), pid_home_zone_usteps[z], pos))
      {
        Z_commanded_movement_in_progress = false;
        mcu_cmd_execution_in_progress = false || X_commanded_movement_in_progress || Y_commanded_movement_in_progress || W_commanded_movement_in_progress || W2_commanded_movement_in_progress;
      }
    }
    if (enable_filterwheel == true) {
      if (W_commanded_movement_in_progress && !is_homing_W)
      {
        int32_t pos = tmc4361A_currentPosition(&tmc4361[w]);
        if ((pos == W_commanded_target_position && !tmc4361A_isRunning(&tmc4361[w], stage_PID_enabled[w])) || within_completion_window(w, W_commanded_target_position, pos))
        {
          W_commanded_movement_in_progress = false;
          mcu_cmd_execution_in_progress = false || X_commanded_movement_in_progress || Y_commanded_movement_in_progress || Z_commanded_movement_in_progress || W2_commanded_movement_in_progress;
        }
      }
    }
    if (enable_filterwheel_w2 == true) {
      if (W2_commanded_movement_in_progress && !is_homing_W2)
      {
        int32_t pos = tmc4361A_currentPosition(&tmc4361[w2]);
        if ((pos == W2_commanded_target_position && !tmc4361A_isRunning(&tmc4361[w2], stage_PID_enabled[w2])) || within_completion_window(w2, W2_commanded_target_position, pos))
        {
          W2_commanded_movement_in_progress = false;
          mcu_cmd_execution_in_progress = false || X_commanded_movement_in_progress || Y_commanded_movement_in_progress || Z_commanded_movement_in_progress || W_commanded_movement_in_progress;
        }
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
        mcu_cmd_execution_in_progress = false || Y_commanded_movement_in_progress || Z_commanded_movement_in_progress || W_commanded_movement_in_progress || W2_commanded_movement_in_progress;
      }
    }
    if (Y_commanded_movement_in_progress && !is_homing_Y) // homing is handled separately
    {
      uint8_t event = tmc4361A_readSwitchEvent(&tmc4361[y]);
      //if( tmc4361A_readLimitSwitches(&tmc4361[y])==LEFT_SW || tmc4361A_readLimitSwitches(&tmc4361[y])==RGHT_SW )
      if ( ( Y_direction == LEFT_DIR && event == LEFT_SW ) || ( Y_direction == RGHT_DIR && event == RGHT_SW ) )
      {
        Y_commanded_movement_in_progress = false;
        mcu_cmd_execution_in_progress = false || X_commanded_movement_in_progress || Z_commanded_movement_in_progress || W_commanded_movement_in_progress || W2_commanded_movement_in_progress;
      }
    }
    if (Z_commanded_movement_in_progress && !is_homing_Z) // homing is handled separately
    {
      uint8_t event = tmc4361A_readSwitchEvent(&tmc4361[z]);
      // if( tmc4361A_readLimitSwitches(&tmc4361[z])==LEFT_SW || tmc4361A_readLimitSwitches(&tmc4361[z])==RGHT_SW )
      if ( ( Z_direction == LEFT_DIR && event == LEFT_SW ) || ( Z_direction == RGHT_DIR && event == RGHT_SW ) )
      {
        Z_commanded_movement_in_progress = false;
        mcu_cmd_execution_in_progress = false || X_commanded_movement_in_progress || Y_commanded_movement_in_progress || W_commanded_movement_in_progress || W2_commanded_movement_in_progress;
      }
    }
  }
}

/*
  Closed-loop deviation watchdog (firmware 1.6).

  When ENABLE_STAGE_PID hands an axis to the TMC4361A's encoder loop, the chip
  drives the motor on its own to null XACTUAL - ENC_POS, limited only by
  PID_DV_CLIP. A wrong encoder sign, a bad gain, or a dropped encoder signal
  turns that into a run-away that no host command interrupts fast enough over
  a 10 ms packet link - and on Z a run-away ends in a stall the operator has
  called non-recoverable. So the firmware watches the loop error itself: if
  |ENC_POS_DEV| exceeds pid_max_dev_usteps for the axis, the loop is switched
  off (the ramp generator keeps the axis at its open-loop target), the fault is
  latched for the status packet, and the axis stays open-loop until the host
  enables the loop again. The limit is per axis, set by SET_PID_LIMITS and
  defaulted at CONFIGURE_STAGE_PID; 0 disables the watchdog for that axis.

  One TMC4361A register read per enabled axis per loop iteration; nothing is
  read for axes whose loop is off, so the shipping path is unaffected.
*/
// True while a homing sequence owns the axis. Homing must run open-loop from
// its first move to its last, so check_closed_loop() neither re-engages a held
// loop during it nor leaves an engaged one running.
static bool axis_is_homing(uint8_t i)
{
  switch (i)
  {
    case x:  return is_homing_X || is_preparing_for_homing_X;
    case y:  return is_homing_Y || is_preparing_for_homing_Y;
    case z:  return is_homing_Z || is_preparing_for_homing_Z;
    case w:  return is_homing_W || is_preparing_for_homing_W;
    case w2: return is_homing_W2 || is_preparing_for_homing_W2;
    default: return false;
  }
}

// Closed loop open above a ramp velocity (SET_PID_OPEN_ABOVE). The TMC4361A loop adds
// a correction velocity while the ramp runs; at the gains that give a fast, tight
// settle (P 65535 at 16 usteps/FS) the in-flight error at cruise speed saturates the
// correction, which then switches at the loop rate and shakes the motor at a few
// hundred hertz. On the second bench Z (2026-09-07) that limit cycle was present at
// every cruise speed (velocity ripple 0.5 mm/s vs 0.05 open loop, +/-16 um error
// swings, 12 dB louder) and stalled the motor at 2.5 mm/s and above, while open loop
// ran clean to 4 mm/s. Focus steps never reach such speeds (1 um at 300 mm/s2 peaks
// at 0.55 mm/s), and they are the moves where every millisecond of settling counts,
// so the loop is kept while the ramp is slow and opened above pid_open_above_pps:
// 0 (default) = rest-only, opened for every move and re-engaged when the ramp stops
// (+11 ms on a 1 um step, measured 2026-09-08; the recommended mode - the in-flight
// loop limit-cycled and stalled the motor on both bench stages); a threshold between
// keeps the loop while the ramp is slower than it; >= VMAX = engaged throughout.
// check_position reports COMPLETED once the encoder error is inside the tolerance.
void pid_open_for_move(uint8_t axis)
{
  if (!pid_requested[axis] || !stage_PID_enabled[axis])
    return;
  tmc4361A_set_PID(&tmc4361[axis], PID_DISABLE);
  stage_PID_enabled[axis] = 0;
  pid_zone_hold[axis] = true;
}

void pid_before_move(uint8_t axis)
{
  // Rest-only: open before the ramp starts rather than 1 ms into it. With a velocity
  // threshold the loop stays on until the ramp actually exceeds it (small steps never do).
  if (pid_open_above_pps[axis] == 0)
    pid_open_for_move(axis);
}

// A closed-loop fault on `axis` (internal index) fails the command that is moving that
// axis, if any: the encoder says the stage is not where the counter says, so a completion
// on the counter would be a lie. Mirrors check_position's bookkeeping: clear this axis's
// in-progress flag, keep mcu_cmd_execution_in_progress true while any other axis still
// moves, and set the status the host reads on the next packet.
static void fail_commanded_move(uint8_t axis)
{
  bool *flag = axis == x ? &X_commanded_movement_in_progress
             : axis == y ? &Y_commanded_movement_in_progress
             : axis == z ? &Z_commanded_movement_in_progress
             : axis == w ? &W_commanded_movement_in_progress
             : axis == w2 ? &W2_commanded_movement_in_progress : (bool *)0;
  if (flag == (bool *)0 || !*flag) return;
  *flag = false;
  mcu_cmd_execution_status = CMD_EXECUTION_ERROR;
  mcu_cmd_execution_in_progress = X_commanded_movement_in_progress || Y_commanded_movement_in_progress
                               || Z_commanded_movement_in_progress || W_commanded_movement_in_progress
                               || W2_commanded_movement_in_progress;
}

// Bounded-correction watch state, one per axis (pid_policy.h). Reset whenever the loop is
// not engaged at rest, so a correction is only ever judged against its own timeline.
static PidCorrectionWatch pid_corr_watch[TOTAL_AXES];

// The loop on `axis` has proven unsafe to leave engaged: open it, drop the REQUEST (so the
// axis stays open-loop until the host explicitly enables again), latch the fault the status
// packet carries, and fail the move in flight if there is one. Every fault path uses this.
static void pid_trip_fault(uint8_t axis, uint8_t cause)
{
  tmc4361A_set_PID(&tmc4361[axis], PID_DISABLE);
  stage_PID_enabled[axis] = 0;
  pid_requested[axis] = false;
  pid_zone_hold[axis] = false;
  pid_fault[axis] = true;
  pid_fault_cause[axis] = cause;   // status bytes 19-21 while reporting is off
  pid_correction_watch_reset(&pid_corr_watch[axis]);
  fail_commanded_move(axis);
}

// Deadband the chip is regulating to, in usteps: the configured/derived tolerance held in the
// struct by CONFIGURE_STAGE_PID / SET_PID_TOLERANCE, else the legacy 25 usteps.
static inline int32_t pid_tolerance_eff(uint8_t axis)
{
  return tmc4361[axis].pid_tolerance > 0 ? tmc4361[axis].pid_tolerance : 25;
}

void check_closed_loop()
{
  for (uint8_t i = 0; i < TOTAL_AXES; i++)
  {
    // Nothing is read for axes the host never asked a loop for: the shipping
    // path costs nothing here. A loop that is not engaged is not correcting:
    // its watch starts fresh when it next engages.
    if (!pid_requested[i] || !stage_PID_enabled[i])
      pid_correction_watch_reset(&pid_corr_watch[i]);
    if (!pid_requested[i])
      continue;

    int32_t zone = pid_home_zone_usteps[i];
    int32_t pos = tmc4361A_currentPosition(&tmc4361[i]);
    bool in_zone = (zone > 0) && (pos > -zone) && (pos < zone);
    bool homing = axis_is_homing(i);
    // Homing re-zeroes both frames at the switch. On a stage whose actuator homes
    // below the stage's stop (0.64 mm gap on the second bench Z) the encoder then
    // stands still for the first part of the travel out, so when the axis reaches
    // a coupled position the two frames differ by the gap - a mechanical offset,
    // not a loop error. Remember that a homing happened; the next engage aligns.
    if (homing)
      pid_realign_pending[i] = true;

    int32_t v_abs = tmc4361A_speed(&tmc4361[i]);   // VACTUAL, pps
    if (v_abs < 0) v_abs = -v_abs;


    if (stage_PID_enabled[i])
    {
      // Home zone: the stage may be resting on its stop while the actuator
      // keeps moving, so the encoder error is meaningless there and the loop
      // would drive the actuator into its end. Drop to open loop; the ramp
      // generator finishes the commanded move on its own. Same during homing,
      // which ends at that very stop.
      if (in_zone || homing)
      {
        pid_open_for_move(i);
        continue;
      }
      // Open above the velocity threshold (see pid_open_for_move); with the threshold
      // at 0 any motion opens the loop - a move that did not come through a stage
      // command (joystick, focus wheel) is caught here, within 1 ms.
      int32_t v_open = pid_open_above_pps[i];
      if (v_open == 0 ? tmc4361A_isRunning(&tmc4361[i], 0) : (v_abs > v_open))
      {
        pid_open_for_move(i);
        continue;
      }
      // Deviation watchdog: a fault drops the REQUEST as well, so a decoupled
      // or runaway axis stays open-loop until the host explicitly enables again.
      // It also fails the move in flight on this axis - the counter will still
      // reach the target, but the encoder says the stage did not, so reporting
      // COMPLETED would hand the host a position it does not have. A fault with
      // no move in flight reaches the host through the status packet's fault bits
      // (BIT_POS_PID_FAULT_*), which are set in every packet.
      int32_t dev = tmc4361A_read_deviation(&tmc4361[i]);
      if (pid_max_dev_usteps[i] > 0 && (dev > pid_max_dev_usteps[i] || dev < -pid_max_dev_usteps[i]))
      {
        pid_trip_fault(i, PID_FAULT_WATCHDOG);
        continue;
      }
      // Bounded correction (pid_policy.h): the deviation watchdog cannot see a frozen encoder
      // - the error it reports never grows - nor a stage resting on its stop while the actuator
      // retracts, and the correction moves the motor without moving XACTUAL, so the travel
      // limits see nothing either. Judged only with the ramp idle: while the ramp moves the
      // target in threshold mode the deviation changes for legitimate reasons.
      if (v_abs == 0)
      {
        uint32_t progress_us, total_us;
        pid_correction_windows((int32_t)axes_pid_arg[i].p, pid_max_dev_usteps[i], pid_dv_clip_eff[i], &progress_us, &total_us);
        uint8_t verdict = pid_correction_watch_step(&pid_corr_watch[i], dev < 0 ? -dev : dev, pid_tolerance_eff(i),
                                                    micros(), progress_us, total_us);
        if (verdict == PID_CORRECTION_NO_PROGRESS)
          pid_trip_fault(i, PID_FAULT_NO_PROGRESS);
        else if (verdict == PID_CORRECTION_TIMEOUT)
          pid_trip_fault(i, PID_FAULT_TIMEOUT);
      }
      else
        pid_correction_watch_reset(&pid_corr_watch[i]);
    }
    else if (pid_zone_hold[i] && !in_zone && !homing && encoder_configured[i])
    {
      // Requested, held open by the zone, by homing or by a fast move, now allowed:
      // re-engage, but only from a small error - the loop slews by the error it
      // starts with - and only once the ramp is slow enough. Engaging while the ramp
      // runs fast adds the correction velocity (up to PID_DV_CLIP) on top of VMAX and
      // steps the velocity output; on the second bench Z (2026-09-07) that stalled
      // the motor the instant the loop came in at 3 mm/s on leaving the home zone,
      // and the ramp then ran on with the stage standing still. Rest-only (threshold
      // 0) waits for the axis to stop; a threshold re-engages below 7/8 of it, so a
      // long move closes its loop during the deceleration and a cruise exactly at
      // the threshold does not toggle the loop every millisecond.
      int32_t v_open = pid_open_above_pps[i];
      bool running = tmc4361A_isRunning(&tmc4361[i], 0);
      if (v_open == 0 ? running : (v_abs > v_open - v_open / 8))
        continue;
      if (pid_realign_pending[i])
      {
        // First engage after a homing: take the counter's frame as the encoder's.
        // The loop then corrects only deviations that arise from here on, which is
        // the same position semantics open loop has always had on such a stage.
        // Only at rest: the two frames must be compared with nothing in motion.
        if (running)
          continue;
        // The absorbed offset is bounded by what the configuration declares (home zone +
        // watchdog): a larger one is lost motion during the first departure, or an encoder
        // that never started following - a fault, not a gap (pid_policy.h).
        int32_t frame_offset = tmc4361A_read_deviation(&tmc4361[i]);
        pid_realign_pending[i] = false;
        if (!pid_realign_allowed(frame_offset, pid_home_zone_usteps[i], pid_max_dev_usteps[i]))
        {
          pid_trip_fault(i, PID_FAULT_REALIGN_REFUSED);
          continue;
        }
        tmc4361A_write_encoder(&tmc4361[i], tmc4361A_currentPosition(&tmc4361[i]));
      }
      int32_t dev = tmc4361A_read_deviation(&tmc4361[i]);
      int32_t lim = pid_max_dev_usteps[i] > 0 ? pid_max_dev_usteps[i] : 0x7FFFFFFF;
      if (dev <= lim && dev >= -lim)
      {
        tmc4361A_set_PID(&tmc4361[i], PID_BPG0);
        stage_PID_enabled[i] = 1;
        pid_zone_hold[i] = false;
      }
      else
      {
        // At rest, outside the zone, frames aligned, and still beyond the watchdog limit: the
        // encoder stopped following during the open-loop move (lost encoder, stuck stage). Say
        // so, the same way the engaged watchdog does, instead of silently never re-engaging.
        // The move in flight on this axis fails with it: the ramp finished on the counter but
        // the encoder is beyond the limit, so COMPLETED would be a lie. A fault with no move
        // in flight reaches the host through the status packet's fault bits, set in every packet.
        pid_trip_fault(i, PID_FAULT_REENGAGE_REFUSED);
      }
    }
  }
}
