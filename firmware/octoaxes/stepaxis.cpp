#include "stepaxis.h"
#include "build_opt.h"
#include "tmc/ic/TMC4361A/TMC4361A.h"

StepAxis::StepAxis(uint8_t csPin, uint8_t axisIndex, const char* axisName) 
  : Axis(csPin, axisIndex, axisName) {
  _backlashMM = 0.0f;
  _backlashCompensationEnabled = false;
}

bool StepAxis::begin(const AxisConfig& config) {
  // call the base-class init
  bool result = Axis::begin(config);
  
  if (result) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":StepAxis initialized successfully");
  }
  
  return result;
}

void StepAxis::setBacklashCompensation(float backlashMM) {
  _backlashMM = backlashMM;
  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":Backlash compensation set to ");
  DEBUG_PRINTF(backlashMM, 3);
  DEBUG_PRINTLN("mm");
}

void StepAxis::enableBacklashCompensation(bool enable) {
  _backlashCompensationEnabled = enable;
  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":Backlash compensation ");
  DEBUG_PRINTLN(enable ? "enabled" : "disabled");
}

bool StepAxis::moveToPosition(float positionMM) {
  // in the stepper axis, backlash-compensation logic can be added
  if (_backlashCompensationEnabled && _backlashMM > 0) {
    // compute the movement direction
    float currentPos = getCurrentPositionMM();
    int32_t direction = (positionMM > currentPos) ? 1 : -1;
    
    // apply backlash compensation
    applyBacklashCompensation(direction);
  }
  
  // call the base-class move function
  return Axis::moveToPosition(positionMM);
}

bool StepAxis::moveRelative(float distanceMM) {
  // in the stepper axis, backlash-compensation logic can be added
  if (_backlashCompensationEnabled && _backlashMM > 0) {
    int32_t direction = (distanceMM > 0) ? 1 : -1;
    applyBacklashCompensation(direction);
  }
  
  // call the base-class move function
  return Axis::moveRelative(distanceMM);
}

void StepAxis::applyBacklashCompensation(int32_t direction) {
  // a simple backlash-compensation implementation
  // real applications may need more complex logic
  if (_backlashMM > 0) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":Applying backlash compensation: ");
    DEBUG_PRINTF(_backlashMM, 3);
    DEBUG_PRINTLN("mm");
    
    // first move in the opposite direction to take up the backlash, then move toward the target
    float compensationDistance = direction * _backlashMM;
    Axis::moveRelative(compensationDistance);
    
    // wait for the compensation move to complete
    while (isMoving()) {
      delay(10);
    }
  }
}

bool StepAxis::handleSetLimits(const String& command) {
  int space1 = command.indexOf(' ');
  int space2 = command.indexOf(' ', space1 + 1);
  int space3 = command.indexOf(' ', space2 + 1);
  
  if (space1 == -1 || space2 == -1 || space3 == -1) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":SET_LIMITS ERROR: Invalid format");
    return false;
  }
  
  String s_down_limit = command.substring(space2 + 1, space3);
  String s_up_limit = command.substring(space3 + 1);

  int32_t down_limit = hexStringToInt32(s_down_limit);
  int32_t up_limit = hexStringToInt32(s_up_limit);

  float lowerLimitMM = down_limit / 1000.0;
  float upperLimitMM = up_limit / 1000.0;

	DEBUG_PRINT("1.LowLimit: ");
	DEBUG_PRINTLN(lowerLimitMM);

	DEBUG_PRINT("2.UpperLimit: ");
	DEBUG_PRINTLN(upperLimitMM);
  
  setSoftLimits(lowerLimitMM, upperLimitMM);
  DEBUG_PRINT(_axisName);
  DEBUG_PRINTLN(":SET_LIMITS OK");
  return true;
}


void StepAxis::performHomingSequence() {
  if (checkTimeout(_homing_timeout_ms)) {
    restoreNormalMicrosteps();
    handleError("Homing timeout");
    return;
  }

  uint8_t limit_state = readLimitSwitches();

  switch (_currentState) {
    case STATE_HOMING_INIT:
      // directly disable the virtual limits in hardware without changing the _softLimitsEnabled flag
      motor_enableSoftLimits(_icID, false, false);

      // unlock the hard-stop latch: reuse the full, already-verified
      // VSTOP recovery path of motor_moveToMicrosteps (disable EN -> clear EVENTS -> write XTARGET -> clear EVENTS again).
      //
      // scenario: after a firmware reset XACTUAL=0, SET_LIM x_neg=5mm immediately triggers a VSTOPL_ACTIVE_F
      // hard-stop, locking the chip ramp generator. A subsequent motor_setVelocityInternal
      // only writes VMAX and cannot release the hard-stop latch, so the motor does not move.
      //
      // writing XTARGET=XACTUAL causes no movement, only triggers the chip to re-evaluate the ramp state,
      // resetting the hard-stop latch. This is the VSTOP recovery path of motor_moveToMicrosteps,
      // already verified effective in the 2026-02-27 commit.
      motor_moveToMicrosteps(_icID, motor_getPositionMicrosteps(_icID));

      switchToHomingMicrosteps();

      if (limit_state & _config.homingSwitch) {
        DEBUG_PRINT(_axisName);
        DEBUG_PRINTLN(":Already at home position, moving away first...");
        setState(STATE_LEAVING_HOME);
      } else {
        DEBUG_PRINT(_axisName);
        DEBUG_PRINTLN(":Starting homing process...");
        int32_t speedInternal = _config.homing_direct * motor_velocityMMToInternal(_icID, _config.homingVelocityMM);
        motor_setVelocityInternal(_icID, speedInternal);
        setState(STATE_HOMING_SEARCH);
      }
      break;

    case STATE_HOMING_SEARCH:
      if (limit_state & _config.homingSwitch) {
        DEBUG_PRINT(_axisName);
        DEBUG_PRINTLN(":Home limit switch triggered!");

        motor_setVelocityInternal(_icID, 0); // stop
        delay(100);                          // wait for a full stop

        int32_t latchedPosition = motor_readLatchPosition(_icID);

        // compute the safe position (away from the limit switch)
        int32_t safePosition = latchedPosition;
        int32_t margin = motor_mmToMicrosteps(_icID, _config.homeSafetyPositionMM);
        // retract direction = opposite of the search direction (homing_direct), always leaving the limit just hit.
        // more robust than "homingSwitch +/- margin": when homingSwitch and the search direction do not follow the usual convention
        // (e.g. new Z: LEFT_SW but homing_direct=+1 toward physical left, the left limit is at the firmware positive-direction end),
        // the old logic would retract deeper into the limit -> unable to leave the sensing zone. Equivalent for regular X/Y/Z (no regression).
        safePosition -= _config.homing_direct * margin;

        DEBUG_PRINT(_axisName);
        DEBUG_PRINT(":Moving to safe position: ");
        DEBUG_PRINTLN(safePosition);

        motor_moveToMicrosteps(_icID, safePosition);
        _checkHomeReachTimeout = 0;

        setState(STATE_HOMING_SET_ZERO);
      }
      break;

    case STATE_HOMING_SET_ZERO:
      // wait for the move to the safe position to complete (timeout 5 seconds = 5,000,000 microseconds)
      if (isMovementComplete() || _checkHomeReachTimeout >= 5000000) {
        // restore normal microstepping
        restoreNormalMicrosteps();
        // set the current position to 0
        motor_setCurrentPositionMicrosteps(_icID, 0);
        DEBUG_PRINT(_axisName);
        DEBUG_PRINTLN(":Homing completed! Current position set to 0");
        if (_checkHomeReachTimeout >= 5000000) {
          DEBUG_PRINT(_axisName);
          DEBUG_PRINTLN(":Homing Set Current Position to safe position Timeout");
        }
        // restore the pre-homing soft-limit state (the host set the VIRT_STOP values during initialization)
        if (_softLimitsEnabled) {
          enableSoftLimits(true);
        }

        // automatically restore PID after homing completes (consistent with the old architecture)
        if (_pidState.enabled) {
          motor_enablePID(_icID);
          DEBUG_PRINT(_axisName);
          DEBUG_PRINTLN(":PID re-enabled after homing");
        }

        setState(STATE_IDLE);
      }
      break;

    default:
      break;
  }
}

void StepAxis::performLeavingHome() {
  if (checkTimeout(LEAVING_HOME_TIMEOUT_MS)) {
    handleError("Leaving home timeout");
    return;
  }

  uint8_t limit_state = readLimitSwitches();

  if (_currentState == STATE_LEAVING_HOME) {
    if (!(limit_state & _config.homingSwitch)) {
      DEBUG_PRINT(_axisName);
      DEBUG_PRINTLN(":Left home position, starting homing...");
      motor_setVelocityInternal(_icID, 0); // stop

      // wait for a full stop
      delay(100);

      // start the actual homing search
      int32_t speedInternal = _config.homing_direct * motor_velocityMMToInternal(_icID, _config.maxVelocityMM);
      motor_setVelocityInternal(_icID, speedInternal);
      setState(STATE_HOMING_SEARCH);
    } else {
      // keep moving to leave the home position
      // set the correct leaving direction based on the limit-switch type
      int32_t speedInternal = -1 * _config.homing_direct * motor_velocityMMToInternal(_icID, _config.maxVelocityMM);
      motor_setVelocityInternal(_icID, speedInternal);
    }
  }
}

