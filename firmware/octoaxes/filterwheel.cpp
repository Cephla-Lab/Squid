#include "filterwheel.h"
#include "build_opt.h"

FilterWheel::FilterWheel(uint8_t csPin, uint8_t axisIndex, const char* axisName, uint8_t filterCount)
  : Axis(csPin, axisIndex, axisName), _filterCount(filterCount), _currentFilter(0) {
  _filterPositions = new float[filterCount];

  // Initialize default positions: evenly spaced, assuming each filter is 60 degrees apart
  for (uint8_t i = 0; i < filterCount; i++) {
    _filterPositions[i] = i * (360.0f / filterCount); // in degrees; must be converted to mm when actually used
  }
}

bool FilterWheel::begin(const AxisConfig& config) {
  // call the base-class init
  bool result = Axis::begin(config);

  if (result) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":FilterWheel with ");
    DEBUG_PRINT(_filterCount);
    DEBUG_PRINTLN(" filters initialized successfully");
  }

  return result;
}

bool FilterWheel::moveToFilter(uint8_t filterPosition) {
  if (!isValidFilterPosition(filterPosition)) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":Invalid filter position: ");
    DEBUG_PRINTLN(filterPosition);
    return false;
  }

  if (_currentState != STATE_IDLE) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":Filter wheel is busy");
    return false;
  }

  float targetPosition = getFilterPosition(filterPosition);
  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":Moving to filter ");
  DEBUG_PRINT(filterPosition);
  DEBUG_PRINT(" at position ");
  DEBUG_PRINTLN(targetPosition);

  if (Axis::moveToPosition(targetPosition)) {
    _currentFilter = filterPosition;
    return true;
  }

  return false;
}

uint8_t FilterWheel::getCurrentFilter() const {
  return _currentFilter;
}

uint8_t FilterWheel::getFilterCount() const {
  return _filterCount;
}

void FilterWheel::update() {
  // call the base-class update first
  Axis::update();

  // filter-wheel-specific update logic can be added here
  // e.g. check whether the target filter position has been reached
}

bool FilterWheel::processCommand(const String& command) {
  if (command.startsWith("MOVE_TO_FILTER")) {
    return handleMoveToFilter(command);
  } else if (command.startsWith("GET_CURRENT_FILTER")) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":CURRENT_FILTER:");
    DEBUG_PRINTLN(_currentFilter);
    return true;
  } else if (command.startsWith("GET_FILTER_COUNT")) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":FILTER_COUNT:");
    DEBUG_PRINTLN(_filterCount);
    return true;
  } else {
    // hand other commands to the base class
    return Axis::processCommand(command);
  }
}

void FilterWheel::setFilterPositions(const float* positions, uint8_t count) {
  if (count > _filterCount) {
    count = _filterCount;
  }

  for (uint8_t i = 0; i < count; i++) {
    _filterPositions[i] = positions[i];
  }

  DEBUG_PRINT(_axisName);
  DEBUG_PRINTLN(":Filter positions updated");
}

bool FilterWheel::handleMoveToFilter(const String& command) {
  int space1 = command.indexOf(' ');
  if (space1 == -1) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":MOVE_TO_FILTER ERROR: Invalid format");
    return false;
  }

  String filterStr = command.substring(space1 + 1);
  uint8_t filterPosition = (uint8_t)filterStr.toInt();

  if (!moveToFilter(filterPosition)) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":MOVE_TO_FILTER ERROR: Movement failed");
    return false;
  }

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":MOVE_TO_FILTER: Moving to filter ");
  DEBUG_PRINTLN(filterPosition);
  return true;
}

float FilterWheel::getFilterPosition(uint8_t filterIndex) const {
  if (filterIndex < _filterCount) {
    return _filterPositions[filterIndex];
  }
  return 0.0f;
}

bool FilterWheel::isValidFilterPosition(uint8_t filterPosition) const {
  return (filterPosition < _filterCount);
}

void FilterWheel::performHomingSequence() {
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
      _slowApproach = false;
      switchToHomingMicrosteps();

      if (limit_state == 0x00) {
        // already in the sensing zone, move out first
        DEBUG_PRINT(_axisName);
        DEBUG_PRINTLN(":Already at home, moving away first...");
        setState(STATE_LEAVING_HOME);
      } else {
        // not in the sensing zone, fast search
        // 2026-05-25 reverted commit 2b5dce4's "direction bug fix", restoring behavior consistent with the legacy Squid W section:
        // hardcoded + directional search (legacy Squid stage_commands.cpp:621-636: when W HOME_NEGATIVE is not in the
        // sensing zone it moves toward RGHT_DIR, which is W-section-specific behavior, opposite to X/Y/Z).
        // hardware direction inversion is handled via _config.invert_direction (set true for mirror-assembled hardware).
        DEBUG_PRINT(_axisName);
        DEBUG_PRINTLN(":Fast search...");
        int32_t speedInternal = motor_velocityMMToInternal(_icID, _config.homingVelocityMM);
        if (_config.invert_direction) speedInternal = -speedInternal;
        motor_setVelocityInternal(_icID, speedInternal);
        setState(STATE_HOMING_SEARCH);
      }
      break;

    case STATE_HOMING_SEARCH:
      if (limit_state == 0x00) {
        // reached the sensing zone
        motor_setVelocityInternal(_icID, 0);  // stop
        delay(100);

        if (!_slowApproach) {
          // phase one (fast): after finding the sensing zone, move out then approach slowly
          DEBUG_PRINT(_axisName);
          DEBUG_PRINTLN(":Sensor found (fast), moving away for slow approach...");
          _slowApproach = true;
          setState(STATE_LEAVING_HOME);
        } else {
          // phase two (slow): precise positioning done
          DEBUG_PRINT(_axisName);
          DEBUG_PRINTLN(":Sensor found (slow), homing position locked.");

          // stop and zero first, then switch back to position mode, finally restore microstepping and motion parameters
          motor_setCurrentPositionMicrosteps(_icID, 0);  // VMAX=0 stop, set zero, velocity_mode=true
          motor_moveToMicrosteps(_icID, 0);              // trigger sRampInit to switch back to position mode (target=0=current, no movement)
          restoreNormalMicrosteps();                      // safely restore microstepping and VMAX/AMAX
          DEBUG_PRINT(_axisName);
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
        }
      }
      break;

    default:
      break;
  }
}

void FilterWheel::performLeavingHome() {
  if (checkTimeout(LEAVING_HOME_TIMEOUT_MS)) {
    handleError("Leaving home timeout");
    return;
  }

  uint8_t limit_state = readLimitSwitches();

  if (_currentState == STATE_LEAVING_HOME) {
    if (!(limit_state == 0x00)) {
      // has left the sensing zone
      DEBUG_PRINT(_axisName);

      // 2026-05-25 reverted commit 2b5dce4: restored hardcoded + directional search (consistent with the legacy Squid W section).
      // leave direction = -search direction (original logic choosing one of two based on homingSwitch).
      // hardware inversion is handled uniformly by _config.invert_direction.
      if (_slowApproach) {
        // stop first to ensure a consistent slow-approach start point
        motor_setVelocityInternal(_icID, 0);
        delay(100);
        DEBUG_PRINTLN(":Left sensor, slow approach...");
        int32_t speedInternal = motor_velocityMMToInternal(_icID, _config.homingVelocityMM / 5.0);
        if (_config.invert_direction) speedInternal = -speedInternal;
        motor_setVelocityInternal(_icID, speedInternal);
      } else {
        // fast search for the sensing zone
        DEBUG_PRINTLN(":Left sensor, fast search...");
        int32_t speedInternal = motor_velocityMMToInternal(_icID, _config.homingVelocityMM);
        if (_config.invert_direction) speedInternal = -speedInternal;
        motor_setVelocityInternal(_icID, speedInternal);
      }
      setState(STATE_HOMING_SEARCH);
    } else {
      // still in the sensing zone, keep moving out (original legacy Squid logic: choose one of two based on homingSwitch)
      float leaveSpeed = _slowApproach
        ? _config.homingVelocityMM / 5.0   // move out slowly to reduce overshoot
        : _config.homingVelocityMM;          // move out at full speed
      int32_t speedInternal;
      if (_config.homingSwitch == RGHT_SW) {
        speedInternal = motor_velocityMMToInternal(_icID, leaveSpeed);
      } else {
        speedInternal = -1 * motor_velocityMMToInternal(_icID, leaveSpeed);
      }
      if (_config.invert_direction) speedInternal = -speedInternal;
      motor_setVelocityInternal(_icID, speedInternal);
    }
  }
}

bool FilterWheel::handleSetLimits(const String& command) {
	return true;
}
