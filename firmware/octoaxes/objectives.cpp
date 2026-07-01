#include "objectives.h"
#include "build_opt.h"

Objectives::Objectives(uint8_t csPin, uint8_t axisIndex, const char* axisName, uint8_t objectivesCount) 
  : Axis(csPin, axisIndex, axisName), _objectivesCount(objectivesCount), _currentObjective(0) {
  _objectivePositions = new float[objectivesCount];

  // Initialize default positions: evenly spaced, assuming each objective is 90 degrees apart
  for (uint8_t i = 0; i < objectivesCount; i++) {
    _objectivePositions[i] = i * (360.0f / objectivesCount); // in degrees; must be converted to mm when actually used
  }
  
}

bool Objectives::begin(const AxisConfig& config) {
  // call the base-class init
  bool result = Axis::begin(config);
  
  if (result) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":Objectives with ");
    DEBUG_PRINT(_objectivesCount);
    DEBUG_PRINTLN(" Objectives initialized successfully");
  }
  
  return result;
}

void Objectives::update() {
  // call the base-class update first
  Axis::update();
  
}

bool Objectives::processCommand(const String& command) {
  if (command.startsWith("MOVE_TO_OBJECTIVE")) {
    return handleMoveToObjective(command);
  } else if (command.startsWith("GET_CURRENT_OBJECTIVE")) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":CURRENT_OBJECTIVE:");
    DEBUG_PRINTLN(_currentObjective);
    return true;
  } else if (command.startsWith("GET_OBJECTIVE_COUNT")) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":OBJECTIVE_COUNT:");
    DEBUG_PRINTLN(_objectivesCount);
    return true;
  } else {
    // hand other commands to the base class
    return Axis::processCommand(command);
  }
}

void Objectives::performHomingSequence() {
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
      switchToHomingMicrosteps();

      if (limit_state == _config.homingSwitch) {
        DEBUG_PRINT(_axisName);
        DEBUG_PRINTLN(":Already at home position, moving away first...");
        setState(STATE_LEAVING_HOME);
      } else {
        DEBUG_PRINT(_axisName);
        DEBUG_PRINTLN(":Starting homing process...");

        DEBUG_PRINTLN(_config.homingVelocityMM);
        int32_t speedInternal = motor_velocityMMToInternal(_icID, _config.homingVelocityMM);
        motor_setVelocityInternal(_icID, speedInternal);
        setState(STATE_HOMING_SEARCH);
      }
      break;

    case STATE_HOMING_SEARCH:
      if (limit_state == _config.homingSwitch) {
        DEBUG_PRINT(_axisName);
        DEBUG_PRINTLN(":Home limit switch triggered!");

        motor_setCurrentPositionMicrosteps(_icID, 0);

        _checkHomeReachTimeout = 0;

        setState(STATE_HOMING_SET_ZERO);
      }
      break;

    case STATE_HOMING_SET_ZERO:
      // wait for the move to the safe position to complete
      if (isMovementComplete() || _checkHomeReachTimeout >= 500 * 1000) {
        // restore normal microstepping
        restoreNormalMicrosteps();
        // set the current position to 0
        DEBUG_PRINT(_axisName);

        if (_checkHomeReachTimeout > 500 * 1000) {
          DEBUG_PRINTLN(":Homing Set Current Position to 0 position Timeout");
        }

        DEBUG_PRINTLN(":Homing completed! Current position set to 0");

        // after homing completes, restore soft limits and PID
        if (_softLimitsEnabled) {
          enableSoftLimits(true);
        }
        if (_pidState.enabled) {
          motor_enablePID(_icID);
          DEBUG_PRINT(_axisName);
          DEBUG_PRINTLN(":PID re-enabled after homing");
        }

        setState(STATE_IDLE);
      } else {
        // optional: add a progress display
        static unsigned long lastProgressTime = 0;
        if (millis() - lastProgressTime > 500) {
          DEBUG_PRINT(_axisName);
          DEBUG_PRINT(":Moving to safe position... Current :");
          DEBUG_PRINT(getCurrentPositionMicrosteps());
          DEBUG_PRINT(" microsteps, Target: ");
          DEBUG_PRINT(motor_getTargetMicrosteps(_icID));
          DEBUG_PRINTLN(" microsteps");
          lastProgressTime = millis();
        }
      }
      break;

    default:
      break;
  }
}

void Objectives::performLeavingHome() {
  if (checkTimeout(LEAVING_HOME_TIMEOUT_MS)) {
    handleError("Leaving home timeout");
    return;
  }

  uint8_t limit_state = readLimitSwitches();

  if (_currentState == STATE_LEAVING_HOME) {
    if (!(limit_state == _config.homingSwitch)) {
      DEBUG_PRINT(_axisName);
      DEBUG_PRINTLN(":Left home position, starting homing...");

      // start the actual homing search
      int32_t speedInternal = motor_velocityMMToInternal(_icID, _config.homingVelocityMM);
      motor_setVelocityInternal(_icID, speedInternal);
      setState(STATE_HOMING_SEARCH);
    } else {
      // keep moving to leave the home position
      // set the correct leaving direction based on the limit-switch type
      int32_t speedInternal;
      if (_config.homingSwitch == RGHT_SW) {
        speedInternal = motor_velocityMMToInternal(_icID, _config.homingVelocityMM); // move left to leave the right limit
      } else {
        speedInternal = -1 * motor_velocityMMToInternal(_icID, _config.homingVelocityMM); // move right to leave the left limit
      }
      motor_setVelocityInternal(_icID, speedInternal);
    }
  }
}

bool Objectives::handleSetLimits(const String& command) {
	return true;
}

bool Objectives::handleMoveToObjective(const String& command) {
  int space1 = command.indexOf(' ');
  if (space1 == -1) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":MOVE_TO_OBJECTIVE ERROR: Invalid format");
    return false;
  }
  
  String filterStr = command.substring(space1 + 1);
  [[maybe_unused]] uint8_t ObjectivePosition = (uint8_t)filterStr.toInt();
  
	/*
  if (!moveToFilter(ObjectivePosition)) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":MOVE_TO_OBJECTIVE ERROR: Movement failed");
    return false;
  }
	*/
  
  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":MOVE_TO_OBJECTIVE: Moving to filter ");
  DEBUG_PRINTLN(ObjectivePosition);
  return true;
}
