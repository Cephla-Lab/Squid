#include "axis.h"
#include "build_opt.h"
#include "tmc/ic/TMC4361A/TMC4361A.h"
#include <SPI.h>

static inline int sgn(int val) {
  if (val < 0)
    return -1;
  if (val == 0)
    return 0;
  return 1;
}

// Constructor
Axis::Axis(uint8_t csPin, uint8_t axisIndex, const char *axisName)
    : _csPin(csPin), _axisIndex(axisIndex), _axisName(axisName) {

  _currentState = STATE_IDLE;
  _previousState = STATE_IDLE;
  _stateStartTime = 0;
  _homeFound = false;

  _maxVelocityMicrosteps = 0;
  _maxAccelerationMicrosteps = 0;

  // New architecture: use axisIndex as the IC identifier
  _icID = axisIndex;

  // Added: initialize state-change detection
  _lastReportedState = STATE_IDLE;
  _stateChanged = false;
  _lastStateReportTime = 0;

  // Added: initialize movement state
  _isMoving = false;
  _moveDirection = 0;
  _softLimitsEnabled = false;
  _needReenableLimits = false;

  // Initialize the config struct (value-init: equivalent to zeroing + respects the member default initializer polarityAffectsChip=false.
  // AxisConfig now has default member initializers making it non-trivial, so memset can no longer be used, otherwise a -Wclass-memaccess warning)
  _config = AxisConfig{};
}

// Initialization function
bool Axis::begin(const AxisConfig &config) {
  _config = config;

  // HOME timeout ms
  _homing_timeout_ms = _config.homing_timeout_ms;

  // Configure the CS pin
#ifndef USE_HC154_CS
  // octoaxes direct GPIO CS: _csPin is the Teensy physical pin number, configured as OUTPUT default HIGH (not selected)
  pinMode(_csPin, OUTPUT);
  digitalWrite(_csPin, HIGH);
#endif
  // USE_HC154_CS (octoaxesplus): _csPin is the HC154 channel number (0-15), not a GPIO pin number.
  // The physical chip-select is initialized by tmc_spi_init() and, at the transaction level by tmc4361A_readWriteSPI(),
  // switched via Pins::hc154_select(). Calling pinMode/digitalWrite(_csPin) here would
  // wrongly drive Teensy physical pins 8/9/10 (on squid++ these are CAMERA_TRIGGER_2 /
  // CAMERA_TRIGGER_1 / ILLUMINATION_D8), causing the camera and laser to be triggered by mistake during init.

  // ========== New-architecture initialization ==========
  // Set the driver type (when DRIVER_AUTO, auto-detected by motor_initMotionController)
  motorParams[_icID].driverType = _config.driverType;

  // Initialize the motion-parameter cache (used for unit conversion in the new API)
  MotionConfig motionConfig = {
      .clockFrequency = _config.clockFrequency,
      .screwPitchMM = _config.screwPitchMM,
      .fullStepsPerRev = (uint16_t)_config.fullStepsPerRev,
      .microsteps = (uint16_t)_config.microstepping,
      .maxVelocityMM = _config.maxVelocityMM,
      .maxAccelerationMM = _config.maxAccelerationMM,
      .maxDecelerationMM = _config.maxAccelerationMM,
      .useSShapedRamp = _config.useSShapedRamp,
      .astartMM = _config.astartMM,
      .dfinalMM = _config.dfinalMM,
      .bow1 = 0,
      .bow2 = 0,
      .bow3 = 0,
      .bow4 = 0};
  // motor_initMotionController returns false when TMC4361A SPI communication fails (after writing
  // SW_RESET, reading VERSION_NO returns 0 or -1). Check the return value and propagate the failure upward so
  // beginAll() can record which axis chip failed to come up, avoiding later operations on an uninitialized chip.
  if (!motor_initMotionController(_icID, &motionConfig)) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":BEGIN_FAIL motor_initMotionController (TMC4361A SPI no response)");
    return false;
  }

  // After auto-detection completes, write back the actual driver type
  if (_config.driverType == DRIVER_AUTO) {
    _config.driverType = motorParams[_icID].driverType;
  }

  // Initialize the driver configuration
  MotorConfig motorConfig = {
      .driverType = _config.driverType,
      .rSense = _config.r_sense,
      .runCurrentMA = _config.motorCurrentMA,
      .holdCurrentRatio = _config.holdCurrent,
      .microstepRes = 0,  // 256 microsteps
      .interpolation = true,
      .toff = 3,   // TOFF = 3
      .hstrt = 0,  // HSTRT = 0 (matches legacy Squid CHOPCONF=0x000900C3, zero-hysteresis quiet)
      .hend = 0,   // HEND register value = 3, actual value = 0 (matches legacy Squid)
      .tbl = 2,    // TBL = 2
      .stallThreshold = (int8_t)_config.stallSensitivity,
      .stallFilter = true,
      .enableStealthChop = false,
      .globalScaler = 0,   // full scale (256)
      .iholdDelay = 7,
      .currentRange = _config.currentRange};
  motor_initDriver(_icID, &motorConfig);

  // Configure the limit switches
  LimitConfig limitConfig = {
      .enableLeft = _config.enableLeftLimitSwitch,
      .enableRight = _config.enableRightLimitSwitch,
      .leftPolarity = _config.leftSwitchPolarity,
      .rightPolarity = _config.rightSwitchPolarity,
      .leftFlipped = _config.leftFlipped,
      .rightFlipped = _config.rightFlipped,
      .homingSwitch = _config.homingSwitch,
      .homeSafetyMarginMM = _config.homeSafetyMarginMM};
  motor_configLimitSwitches(_icID, &limitConfig);

  // Set the motion parameters
  setMotionParameters(_config.maxVelocityMM, _config.maxAccelerationMM);

  // Enable the homing limit
  motor_enableHomingLimit(_icID, _config.rightSwitchPolarity,
                          _config.homingSwitch,
                          mmToMicrosteps(_config.homeSafetyMarginMM));

  // Disable the virtual limit switches (initial state)
  enableSoftLimits(false);

  // Encoder initialization
  if (_config.enableEncoder && _config.encoderLinesPerRev > 0) {
    uint32_t transitions = (uint32_t)_config.encoderLinesPerRev;
    motor_initABNEncoder(_icID, transitions,
                          32,    // filter_wait_time
                          4,     // filter_exponent
                          512,   // filter_vmean
                          _config.invertEncoderDir);
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":ENCODER_INIT lines=");
    DEBUG_PRINT(_config.encoderLinesPerRev);
    DEBUG_PRINT(" transitions=");
    DEBUG_PRINTLN(transitions);
  }

  // Disable PID (using the new API)
  motor_disablePID(_icID);

  // Configure StallGuard (using the new API)
  // TMC2660 SG2: SGT=12 has been stable over long-term testing; normal motion does not false-trigger, a collision stops the motor.
  // TMC2240 SG4: the algorithm is incompatible with SG2; SGT=12 very easily false-triggers the ACTIVE_STALL_F latch
  // locking up the chip (diagnosed on-site 2026-05-12 with legacy Squid X stuck, STATUS bit11 latched;
  // once triggered, power must be cut and USB unplugged to reset). The existing VSTOP
  // recovery path of motor_moveToMicrosteps does not clear this latch (it only clears VSTOPL/R_ACTIVE_F bit9/10).
  // Temporary workaround: skip enabling stall on TMC2240; keep config.enableStallSensitivity /
  // stallSensitivity parameters for future SG4 tuning, to enable after the chip-level latch recovery is fixed.
  if (_config.enableStallSensitivity && _config.driverType != DRIVER_TMC2240)
    motor_configStallGuard(_icID, _config.stallSensitivity, true, true);

  // Enable the axis by default
  enableAxis();

  return true;
}

// Set motion parameters (using the new API)
void Axis::setMotionParameters(float maxVelocityMM, float maxAccelerationMM) {
  _maxVelocityMicrosteps = motor_velocityMMToInternal(_icID, maxVelocityMM);
  _maxAccelerationMicrosteps = motor_accelMMToInternal(_icID, maxAccelerationMM);

  motor_setMaxVelocity(_icID, maxVelocityMM);
  motor_setMaxAcceleration(_icID, maxAccelerationMM);
}

// State-machine update
void Axis::update() {
  // Save the old state for comparison
  AxisState oldState = _currentState;

  switch (_currentState) {
  case STATE_HOMING_INIT:
  case STATE_HOMING_SEARCH:
  case STATE_HOMING_SET_ZERO:
    performHomingSequence();
    break;

  case STATE_LEAVING_HOME:
    performLeavingHome();
    break;

  case STATE_MOVING: {
    checkMovementComplete();

    // Limit-state check: matches legacy Squid `check_limits` 10ms throttle (operations.cpp:533)
    // reduces SPI bus contention; the hard-limit completion check tolerates a 0-10ms delay (the chip has already physically stopped)
    // (#5, 2026-05-19)
    if (_limitCheckThrottle >= 10000) {
      _limitCheckThrottle = 0;
      checkLimitPosition();
    }

    // Delayed re-enable of the virtual limits after VSTOP recovery:
    // only re-enable the limits after the motor leaves the boundary (VSTOP flags cleared in STATUS),
    // to avoid immediately re-triggering VSTOP at the boundary.
    if (_needReenableLimits) {
      uint32_t st = motor_readStatus(_icID);
      bool vstopStillActive = (st & TMC4361A_VSTOPL_ACTIVE_F_MASK) ||
                              (st & TMC4361A_VSTOPR_ACTIVE_F_MASK);
      if (!vstopStillActive) {
        motor_enableSoftLimits(_icID, true, true);
        _needReenableLimits = false;
      }
    }

    // Timeout check while moving
    if (checkTimeout(MOVEMENT_TIMEOUT_MS)) {
      handleError("Movement timeout");
    }
  } break;

  case STATE_IDLE:
    // The idle state needs no special handling
    break;

  case STATE_ERROR:
    // The error state requires external intervention
    break;
  }

  // Check whether the state changed
  if (oldState != _currentState) {
    _stateChanged = true;
  }

  // Report the state change (if needed)
  reportStateIfChanged();
}

// Added: state-report function
void Axis::reportStateIfChanged(bool force) {
  // Check whether a report is needed
  bool shouldReport = false;

  if (force) {
    // Force a report
    shouldReport = true;
  } else if (_stateChanged) {
    // The state changed
    shouldReport = true;
  } else if (_currentState == STATE_MOVING) {
  } else if (_currentState == STATE_HOMING_INIT ||
             _currentState == STATE_HOMING_SEARCH ||
             _currentState == STATE_HOMING_SET_ZERO ||
             _currentState == STATE_LEAVING_HOME) {
  } else {
  }

  if (shouldReport) {
    handleEmergency();
    _stateChanged = false;
    _lastStateReportTime = millis();
    _lastReportedState = _currentState;
  }
}

// Limit-position handler
void Axis::checkLimitPosition() {
  uint32_t event = readAxisEvent();

  // Virtual limits (software limits): trust that the upper-layer isMoveAllowedByDirection() has guaranteed
  // the in-progress move goes toward the safer direction; VSTOP_ACTIVE during this time is a
  // sticky/residual state left by the chip after SET_LIM placed the motor in the forbidden zone, and should not be treated as a real out-of-bounds.
  // motor_moveToMicrosteps() has temporarily cleared VIRT_*_LIMIT_EN so the motor can move;
  // the completion check is handled by checkMovementComplete() (XACTUAL == XTARGET).
  uint32_t vstop_bits =
      event & (TMC4361A_VSTOPL_ACTIVE_MASK | TMC4361A_VSTOPR_ACTIVE_MASK);
  if (vstop_bits) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":VSTOP active during move (ignored, gate handled upstream): event=0x");
    DEBUG_PRINTLNF(event, HEX);
    // do not call completeMovement(); let checkMovementComplete() finish normally when XACTUAL reaches XTARGET
  }

  // Hardware limits (keep the direction check; hardware limits require a direction match)
  uint32_t hw_datagram = event & (TMC4361A_STOPL_EVENT_MASK | TMC4361A_STOPR_EVENT_MASK);
  hw_datagram >>= TMC4361A_STOPL_EVENT_SHIFT;
  uint8_t hw_result = hw_datagram & 0xff;

  if ((hw_result == RGHT_SW && _moveDirection == RGHT_DIR) ||
      (hw_result == LEFT_SW && _moveDirection == LEFT_DIR)) {
    DEBUG_PRINT("Hardware Limit Stop: ");
    DEBUG_PRINTLN(hw_result);
    completeMovement();
    return;
  }

  // Determine whether this is a stall state
  if (event & 0x20000000) {
    DEBUG_PRINTLN("Axis Is Stop for Stalling");
    DEBUG_PRINTLNF(event, HEX);
  } else {
    if (event != 0) {
      DEBUG_PRINT("Axis Event is not Zero: ");
      DEBUG_PRINTLNF(event, HEX);
    }
  }
}

// Command processing
bool Axis::processCommand(const String &command) {
  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":CMD_RECV:");
  DEBUG_PRINTLN(command);  // debug point 0 - command received

  if (command.startsWith("GET_POSITION")) {
    return handleGetPosition();
  } else if (command.startsWith("SET_LIMITS")) {
    return handleSetLimits(command);
  } else if (command.startsWith("MOVE_AXIS")) {
    return handleMoveAxis(command);
  } else if (command.startsWith("MOVETO_AXIS")) {
    return handleMoveToAxis(command);
  } else if (command.startsWith("HOMING")) {
    return handleHoming();
  } else if (command.startsWith("GET_DATA")) {
    return handleGetData();
  } else if (command.startsWith("DISABLE")) {
    return handleAxisAbilityToggle(false);
  } else if (command.startsWith("ENABLE")) {
    return handleAxisAbilityToggle(true);
  } else if (command.startsWith("RESET")) {
    return handleReset();
  } else if (command.startsWith("DEBUG_REG")) {
    return handleDebugReg();
  } else {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":Unknown command: ");
    DEBUG_PRINTLN(command);
    return false;
  }
}

// Command-handling helper method
bool Axis::handleGetPosition() {
  int32_t microsteps = getCurrentPosition();
  [[maybe_unused]] float positionMM = microstepsToMM(microsteps);
  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":Current Position (mm):");
  DEBUG_PRINTLNF(positionMM, 3); // increase display precision
  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":Current Position (microsteps):");
  DEBUG_PRINTLN(microsteps);
  return true;
}

int32_t Axis::hexStringToInt32(String hex) {
  char *endptr;
  uint32_t value = strtoul(hex.c_str(), &endptr, 16);
  return (int32_t)value;
}

bool Axis::moveAxis(int32_t value) {
  _cmdRecvMicros = micros();
  _moveDirection = sgn(value);

  if (!moveRelativeMicrosteps(value)) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":MOVE_AXIS ERROR: Movement failed");
    return false;
  } else {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":MOVE_AXIS (usteps): ");
    DEBUG_PRINTLN(value);
  }
  return true;
}

bool Axis::handleMoveAxis(const String &command) {
  int space1 = command.indexOf(' ');
  int space2 = command.indexOf(' ', space1 + 1);

  if (space1 == -1 || space2 == -1) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":MOVE_AXIS ERROR: Invalid format");
    return false;
  }
  String dataType = command.substring(space1 + 1, space2);
  String hexData = command.substring(space2 + 1);

  int32_t value = hexStringToInt32(hexData);

  return moveAxis(value);
}

bool Axis::handleMoveToAxis(const String &command) {
  int space1 = command.indexOf(' ');
  int space2 = command.indexOf(' ', space1 + 1);

  if (space1 == -1 || space2 == -1) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":MOVETO_AXIS ERROR: Invalid format");
    return false;
  }
  String dataType = command.substring(space1 + 1, space2);
  String hexData = command.substring(space2 + 1);

  int32_t value = hexStringToInt32(hexData);
  _moveDirection = sgn(value);

  if (!moveToPositionMicrosteps(value)) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":MOVETO_AXIS ERROR: Movement failed");
    return false;
  }

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":MOVETO_AXIS (usteps): ");
  DEBUG_PRINTLN(value);
  return true;
}

// Added: movement-detection function (using the new API)
void Axis::checkMovementComplete() {
  if (!_isMoving)
    return;

  // Use the chip STATUS.TARGET_REACHED_F bit instead of read-XACTUAL + read-XTARGET-and-compare.
  // The chip updates this bit in real time after XTARGET is written (set to 1 when XACTUAL == XTARGET,
  // and EVENTS were cleared after motor_moveToMicrosteps to prevent sticky residue), so no waiting is needed.
  // This reduces the completion-check path from 2 SPI reads to 1, and is more reliable (the chip's authoritative signal).
  if (motor_isTargetReached(_icID)) {
    completeMovement();
  }
}

// Added: start movement (using the new API)
void Axis::startMovement() {
  _isMoving = true;
  _moveStartMicros = micros();
  setState(STATE_MOVING);
}

// Added: complete movement
void Axis::completeMovement() {
  _isMoving = false;
  setState(STATE_IDLE);

  // Restore the virtual limits when the move completes (if the recovery path disabled the limits and the update loop did not restore them in time)
  if (_needReenableLimits && _softLimitsEnabled) {
    motor_enableSoftLimits(_icID, true, true);
    _needReenableLimits = false;
  }

#ifdef ENABLE_DEBUG
  // DEBUG-only: during debugging, log motor / prep / total time and position vs target
  // Production builds (NDEBUG) do not read SPI, saving ~200us/move x 1000 ~= 200ms (#3, 2026-05-19)
  unsigned long now = micros();
  unsigned long motorTime = now - _moveStartMicros;
  unsigned long totalTime = now - _cmdRecvMicros;
  unsigned long prepTime = _moveStartMicros - _cmdRecvMicros;
  int32_t endPos = motor_getPositionMicrosteps(_icID);
  int32_t targetPos = motor_getTargetMicrosteps(_icID);

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":DONE: total=");
  DEBUG_PRINT(totalTime);
  DEBUG_PRINT("us prep=");
  DEBUG_PRINT(prepTime);
  DEBUG_PRINT("us motor=");
  DEBUG_PRINT(motorTime);
  DEBUG_PRINT("us pos=");
  DEBUG_PRINT(endPos);
  DEBUG_PRINT(" tgt=");
  DEBUG_PRINT(targetPos);
  DEBUG_PRINT(" err=");
  DEBUG_PRINTLN(endPos - targetPos);
#endif
}

bool Axis::handleHoming() {
  if (!startHoming()) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":HOMING ERROR: Already in progress or busy");
    return false;
  }

  DEBUG_PRINT(_axisName);
  DEBUG_PRINTLN(":Received HOME command, starting homing process...");
  return true;
}

bool Axis::handleReset() {
  _isMoving = false;

  // Restore microstepping (may have been changed during homing)
  restoreNormalMicrosteps();

  // Clear state
  readLimitSwitches();
  readSwitchEvent();

  // Reset RAMPMODE (may become HOLD mode after a hardware limit triggers)
  motor_resetRampMode(_icID);

  // Restore motion parameters (VMAX/AMAX may have been zeroed by stop)
  setMotionParameters(_config.maxVelocityMM, _config.maxAccelerationMM);

  setState(STATE_IDLE);

  // Report the state immediately
  reportStateIfChanged(true);

  DEBUG_PRINT(_axisName);
  DEBUG_PRINTLN(":Received RESET command, starting reset process...");
  return true;
}

// Move to an absolute position (microstep units, protocol-layer entry point)
bool Axis::moveToPositionMicrosteps(int32_t targetMicrosteps) {
  // 2026-05-25 hardware direction inversion: mirror-assembled hardware needs the chip to move in the opposite direction
  // the host protocol layer is unchanged (still sends commands per the "standard Squid design"); the firmware layer inverts the target
  if (_config.invert_direction) {
    targetMicrosteps = -targetMicrosteps;
  }

  // Auto-recover from the error state (non-hardware faults such as a virtual-limit timeout)
  if (_currentState == STATE_ERROR) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":Auto-recovery from error state");
    handleReset();
  }

  // STATE_IDLE: normal path
  // STATE_MOVING: mimics legacy Squid (main_controller_teensy41.ino:900 MOVETO_X handler has no busy check)
  // -- overwrites the chip XTARGET; the chip ramp generator smoothly switches the target.
  // STATE_HOMING_*/LEAVING_HOME: during homing the chip is in velocity mode, so overwriting would break homing,
  // so still reject (this is an explicit rejection; the caller should wait until homing completes before sending).
  if (_currentState != STATE_IDLE && _currentState != STATE_MOVING) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":Movement rejected: Axis is homing, current state: ");
    DEBUG_PRINTLN(_currentState);
    return false;
  }

  if (!isWithinSoftLimits(targetMicrosteps)) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":Movement rejected: Outside soft limits");
    return false;
  }

  // Direction-aware clamp: a target toward the forbidden zone is clamped to the boundary, so the motor stops at the boundary
  // (compatible with legacy Squid behavior: the legacy Squid host cannot be changed, so the firmware must handle out-of-bounds targets as a fallback)
  targetMicrosteps = clampTargetByDirection(targetMicrosteps);

  // No-op short-circuit: when after clamping target == current position the motor need not move,
  // skip motor_moveToMicrosteps + startMovement and return directly,
  // to avoid _isMoving being set wrongly, which would make the host receive IN_PROGRESS and wait 5 seconds until timeout.
  // Typical case: the motor is already stuck at the limit boundary and the host keeps sending out-of-bounds MOVE commands.
  int32_t currentPos = motor_getPositionMicrosteps(_icID);
  if (targetMicrosteps == currentPos) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":Move no-op (clamped to current position), skipping motor command");
    return true;
  }

  // motor_moveToMicrosteps already reads STATUS to check VSTOP internally; reuse its return value
  // to avoid a redundant SPI read (2026-05-18 acquisition optimization #2.2, saves ~10-20us/move)
  bool vstopWasActive = motor_moveToMicrosteps(_icID, targetMicrosteps);
  startMovement(); // set the movement state

  if (vstopWasActive && _softLimitsEnabled) {
    _needReenableLimits = true;
  }

  return true;
}

// Move to an absolute position (mm units, thin wrapper)
bool Axis::moveToPosition(float positionMM) {
  if (!isValidPosition(positionMM)) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":Movement rejected: Invalid position");
    DEBUG_PRINTLN(positionMM);
    return false;
  }
  return moveToPositionMicrosteps(motor_mmToMicrosteps(_icID, positionMM));
}

// Relative move (microstep units, protocol-layer entry point)
bool Axis::moveRelativeMicrosteps(int32_t deltaMicrosteps) {
  // 2026-05-25 hardware direction inversion: invert delta so the chip moves in the opposite physical direction
  if (_config.invert_direction) {
    deltaMicrosteps = -deltaMicrosteps;
  }

  // Auto-recover from the error state
  if (_currentState == STATE_ERROR) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":Auto-recovery from error state");
    handleReset();
  }

  // STATE_IDLE: normal path
  // STATE_MOVING: mimics legacy Squid (main_controller_teensy41.ino:845 MOVE_X handler has no busy check)
  // -- recompute the target from the chip's current position and overwrite XTARGET. Semantics match legacy Squid:
  // delta is relative to "the chip current position when the command arrives", not "the target of the previous command".
  // STATE_HOMING_*/LEAVING_HOME: reject (same as moveToPositionMicrosteps).
  if (_currentState != STATE_IDLE && _currentState != STATE_MOVING) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":Move rejected: Axis is homing, current state: ");
    DEBUG_PRINTLN(_currentState);
    return false;
  }

  int32_t currentPos = motor_getPositionMicrosteps(_icID);
  int32_t targetPos = currentPos + deltaMicrosteps;

  if (!isWithinSoftLimits(targetPos)) {
    return false;
  }

  // Direction-aware clamp: a target toward the forbidden zone is clamped to the boundary, so the motor stops at the boundary
  // (compatible with legacy Squid behavior: the legacy Squid host cannot be changed, so the firmware must handle out-of-bounds targets as a fallback)
  targetPos = clampTargetByDirection(targetPos);

  // No-op short-circuit: when after clamping target == current position, skip motor + startMovement,
  // to avoid _isMoving being set wrongly, making the host wait a full 5 seconds for timeout (see the corresponding comment in moveToPositionMicrosteps)
  if (targetPos == currentPos) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":Move no-op (clamped to current position), skipping motor command");
    return true;
  }

  // motor_moveToMicrosteps already reads STATUS to check VSTOP internally; reuse its return value
  // to avoid a redundant SPI read (2026-05-18 acquisition optimization #2.2, saves ~10-20us/move)
  bool vstopWasActive = motor_moveToMicrosteps(_icID, targetPos);
  startMovement(); // set the movement state

  if (vstopWasActive && _softLimitsEnabled) {
    _needReenableLimits = true;
  }

  return true;
}

// Relative move (mm units, thin wrapper)
bool Axis::moveRelative(float distanceMM) {
  return moveRelativeMicrosteps(motor_mmToMicrosteps(_icID, distanceMM));
}

// Set speed
void Axis::setSpeed(float speedMM) {
  motor_setMaxVelocity(_icID, speedMM);
}

// Smooth stop
void Axis::smoothStop() {
  motor_stop(_icID);
  completeMovement(); // clear the movement state
}

// Motion-control functions
void Axis::disableAxis() {
  motor_enableDriver(_icID, false);
  _isEnabled = false; // update the enable state
}

void Axis::enableAxis() {
  motor_enableDriver(_icID, true);
  _isEnabled = true; // update the enable state
}

// Set the current position
void Axis::setCurrentPosition(float positionMM) {
  motor_setCurrentPosition(_icID, positionMM);
}

// Get the current position in microsteps (using the new API)
int32_t Axis::getCurrentPosition() const {
  return motor_getPositionMicrosteps(_icID);
}

// Get the current position (mm) (using the new API)
float Axis::getCurrentPositionMM() const {
  return motor_getPositionMM(_icID);
}

// Get the current position (microsteps)
// When the encoder is enabled, return ENC_POS (converted via ENC_CONST, same units as microsteps)
// When disabled, return XACTUAL (open-loop position)
// 2026-05-25 hardware direction inversion: invert the value reported to the host so it sees a direction consistent with the protocol layer
int32_t Axis::getCurrentPositionMicrosteps() const {
  int32_t raw;
  if (_config.enableEncoder) {
    raw = (int32_t)tmc4361A_readRegister(_icID, TMC4361A_ENC_POS);
  } else {
    raw = motor_getPositionMicrosteps(_icID);
  }
  return _config.invert_direction ? -raw : raw;
}

// Get the encoder position (microstep units, converted via ENC_CONST)
// When the encoder is not enabled, return XACTUAL
int32_t Axis::getEncoderPositionMicrosteps() const {
  int32_t raw;
  if (_config.enableEncoder) {
    raw = (int32_t)tmc4361A_readRegister(_icID, TMC4361A_ENC_POS);
  } else {
    raw = motor_getPositionMicrosteps(_icID);
  }
  return _config.invert_direction ? -raw : raw;
}

// Homing microstepping switch
void Axis::switchToHomingMicrosteps() {
  if (_config.homingMicrostepping != _config.microstepping) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":Switch microsteps for homing: ");
    DEBUG_PRINT(_config.microstepping);
    DEBUG_PRINT(" -> ");
    DEBUG_PRINTLN(_config.homingMicrostepping);
    motor_setMicrosteps(_icID, _config.homingMicrostepping);
    setMotionParameters(_config.maxVelocityMM, _config.maxAccelerationMM);
  }
}

void Axis::restoreNormalMicrosteps() {
  if (_config.homingMicrostepping != _config.microstepping) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":Restore microsteps after homing: ");
    DEBUG_PRINT(_config.homingMicrostepping);
    DEBUG_PRINT(" -> ");
    DEBUG_PRINTLN(_config.microstepping);
    motor_setMicrosteps(_icID, _config.microstepping);
    setMotionParameters(_config.maxVelocityMM, _config.maxAccelerationMM);
  }
}

// Start homing
bool Axis::startHoming() {
  // Auto-recover from the error state
  if (_currentState == STATE_ERROR) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":Auto-recovery from error state for homing");
    handleReset();
  }

  if (_currentState != STATE_IDLE) {
    return false;
  }

  setState(STATE_HOMING_INIT);
  return true;
}

// Check whether homing is in progress
bool Axis::isHomingInProgress() const {
  return _currentState == STATE_HOMING_INIT ||
         _currentState == STATE_HOMING_SEARCH ||
         _currentState == STATE_HOMING_SET_ZERO ||
         _currentState == STATE_LEAVING_HOME;
}

// Check whether the movement is complete (using the new API)
// Dual condition: position reached the target + VACTUAL is zero (the motor has actually stopped)
// prevents reporting completion too early when, during the S-ramp deceleration phase, XACTUAL briefly equals XTARGET but the speed has not reached zero
bool Axis::isMovementComplete() const {
  return motor_getPositionMicrosteps(_icID) == motor_getTargetMicrosteps(_icID)
         && !motor_isRunning(_icID);
}

// Set soft limits (using the new API)
void Axis::setSoftLimits(float lowerLimitMM, float upperLimitMM) {
  int32_t lowerMicrosteps = motor_mmToMicrosteps(_icID, lowerLimitMM);
  int32_t upperMicrosteps = motor_mmToMicrosteps(_icID, upperLimitMM);

  motor_setSoftLimits(_icID, lowerMicrosteps, upperMicrosteps);

  // sync the direction-gate shadow (both sides)
  _softLimits.leftEnabled = true;
  _softLimits.leftValue = lowerMicrosteps;
  _softLimits.rightEnabled = true;
  _softLimits.rightValue = upperMicrosteps;

  enableSoftLimits(true);
}

// Enable/disable soft limits (using the new API)
void Axis::enableSoftLimits(bool enable) {
  motor_enableSoftLimits(_icID, enable, enable);
  _softLimitsEnabled = enable;
  if (!enable) {
    // when explicitly disabling soft limits, clear the direction-gate shadow to allow movement in any direction
    _softLimits.leftEnabled = false;
    _softLimits.rightEnabled = false;
  }
}

// Set one-sided soft limit (direction: +1=upper/right, -1=lower/left)
void Axis::setOneSoftLimit(int direction, int32_t valueMicrosteps) {
  // first set XTARGET to the current position to prevent the motor from auto-resuming motion after the limit is loosened
  int32_t xactual = tmc4361A_readRegister(_icID, TMC4361A_XACTUAL);
  tmc4361A_writeRegister(_icID, TMC4361A_XTARGET, xactual);

  uint32_t refConf = tmc4361A_readRegister(_icID, TMC4361A_REFERENCE_CONF);
  if (direction > 0) {
    tmc4361A_writeRegister(_icID, TMC4361A_VIRT_STOP_RIGHT, valueMicrosteps);
    refConf |= TMC4361A_VIRTUAL_RIGHT_LIMIT_EN_MASK;
    refConf |= (1 << TMC4361A_VIRT_STOP_MODE_SHIFT);
    _softLimits.rightEnabled = true;
    _softLimits.rightValue = valueMicrosteps;
  } else {
    tmc4361A_writeRegister(_icID, TMC4361A_VIRT_STOP_LEFT, valueMicrosteps);
    refConf |= TMC4361A_VIRTUAL_LEFT_LIMIT_EN_MASK;
    refConf |= (1 << TMC4361A_VIRT_STOP_MODE_SHIFT);
    _softLimits.leftEnabled = true;
    _softLimits.leftValue = valueMicrosteps;
  }
  tmc4361A_writeRegister(_icID, TMC4361A_REFERENCE_CONF, refConf);
  _softLimitsEnabled = true;
}

// Direction-aware clamp: see the comment in axis.h
//
// Boundary margin (BOUNDARY_MARGIN) prevents the chip ramp generator's insufficient deceleration precision from causing a hard-stop latch:
// Measured case (main_hcs.log 2026-05-09 10:31:57, cmd 37 MOVETO_X usteps=6300 = L+1):
// host target=L+1 (5mm = 6300, exactly 1 microstep against the X_NEG_LIMIT=6299 boundary),
// the chip writes XTARGET and starts ramp deceleration; sub-microstep precision lets the ramp briefly cross L -> triggering
// VSTOPL_ACTIVE and entering a hard-stop latch, **so all subsequent MOVE_X in any direction
// cannot start a ramp** (the chip's internal latch does not release; clearing EVENTS alone cannot unlock it).
// in the safe zone, force the target at least N microsteps away from the boundary to avoid this quirk.
static constexpr int32_t BOUNDARY_MARGIN_MICROSTEPS = 100;

int32_t Axis::clampTargetByDirection(int32_t target) const {
  int32_t C = motor_getPositionMicrosteps(_icID);
  int32_t original = target;
  if (_softLimits.leftEnabled) {
    int32_t L = _softLimits.leftValue;
    // when out of bounds, lower bound = C (forbid going further down); in the safe zone, lower bound = L + margin (prevents the ramp from crossing)
    int32_t effective_lower = (C <= L) ? C : (L + BOUNDARY_MARGIN_MICROSTEPS);
    if (target < effective_lower) target = effective_lower;
  }
  if (_softLimits.rightEnabled) {
    int32_t R = _softLimits.rightValue;
    int32_t effective_upper = (C >= R) ? C : (R - BOUNDARY_MARGIN_MICROSTEPS);
    if (target > effective_upper) target = effective_upper;
  }
  if (target != original) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":Move clamped (soft limit): target=");
    DEBUG_PRINT(original);
    DEBUG_PRINT(" → ");
    DEBUG_PRINT(target);
    DEBUG_PRINT(" (C=");
    DEBUG_PRINT(C);
    DEBUG_PRINT(" L=");
    DEBUG_PRINT(_softLimits.leftEnabled ? _softLimits.leftValue : 0);
    DEBUG_PRINT(" R=");
    DEBUG_PRINT(_softLimits.rightEnabled ? _softLimits.rightValue : 0);
    DEBUG_PRINTLN(")");
  }
  return target;
}

// PID control
void Axis::configureStagePID(bool flip_direction, uint16_t transitions_per_rev) {
  // enable the encoder at runtime (takes effect after the host sends it; getCurrentPositionMicrosteps will then read ENC_POS)
  _config.enableEncoder = true;

  // ENC-2 tripwire: the runtime flip is the authoritative value and should match the config.h boot default invertEncoderDir.
  // A mismatch means constants.py and config.h have decoupled encoder directions (only one side changed) -> warn.
  // Then sync _config.invertEncoderDir to the actually-effective value so this field always reflects the true hardware direction.
  if (flip_direction != _config.invertEncoderDir) {
    DEBUG_PRINT(_axisName);
    DEBUG_PRINT(":WARN encoder flip mismatch boot=");
    DEBUG_PRINT(_config.invertEncoderDir);
    DEBUG_PRINT(" runtime=");
    DEBUG_PRINTLN(flip_direction);
  }
  _config.invertEncoderDir = flip_direction;

  // ABN encoder initialization (hardcoded parameters match the old architecture)
  motor_initABNEncoder(_icID, transitions_per_rev,
                        32,    // filter_wait_time
                        4,     // filter_exponent
                        512,   // filter_vmean
                        flip_direction);

  // PID parameter initialization (differentiated by axis type)
  // pid_dclip = VMAX (internal units), already cached in motorParams
  uint32_t vmax_usteps = (uint32_t)motorParams[_icID].vmax;
  uint32_t target_tolerance, pid_tolerance, pid_iclip;

  // differentiate parameters by axis name
  if (strcmp(_axisName, "W") == 0 || strcmp(_axisName, "W2") == 0) {
    // 2026-05-26 speed optimization: target_tolerance / pid_tolerance 2->20 let the chip finish the end-of-move settling earlier,
    // while suppressing PID hunting (position accuracy +/-1.8deg, visually imperceptible on the 45deg/slot filter wheel).
    // 2026-05-27: tried tightening pid_tolerance=5 but never flashed/tested; the final hardware-verified config is ms=8 + P=8192 + tol=20.
    target_tolerance = 20;
    pid_tolerance = 20;
    pid_iclip = 4096;
  } else if (strcmp(_axisName, "Z") == 0) {
    target_tolerance = 25;
    pid_tolerance = 25;
    pid_iclip = 4096;
  } else {
    // X, Y and others
    target_tolerance = 25;
    pid_tolerance = 25;
    pid_iclip = 32767;
  }

  motor_initPID(_icID, target_tolerance, pid_tolerance,
                _pidState.p, _pidState.i, _pidState.d,
                vmax_usteps, pid_iclip, 2);  // pid_d_clkdiv = 2

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":CONFIGURE_STAGE_PID flip=");
  DEBUG_PRINT(flip_direction);
  DEBUG_PRINT(" tpr=");
  DEBUG_PRINT(transitions_per_rev);
  DEBUG_PRINT(" P=");
  DEBUG_PRINT(_pidState.p);
  DEBUG_PRINT(" I=");
  DEBUG_PRINT(_pidState.i);
  DEBUG_PRINT(" D=");
  DEBUG_PRINTLN(_pidState.d);
}

void Axis::enableStagePID() {
  _pidState.enabled = true;
  motor_enablePID(_icID);
  DEBUG_PRINT(_axisName);
  DEBUG_PRINTLN(":ENABLE_STAGE_PID");
}

void Axis::disableStagePID() {
  _pidState.enabled = false;
  motor_disablePID(_icID);
  DEBUG_PRINT(_axisName);
  DEBUG_PRINTLN(":DISABLE_STAGE_PID");
}

void Axis::setPIDArguments(uint16_t p, uint8_t i, uint8_t d) {
  _pidState.p = p;
  _pidState.i = i;
  _pidState.d = d;
  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":SET_PID_ARGUMENTS P=");
  DEBUG_PRINT(p);
  DEBUG_PRINT(" I=");
  DEBUG_PRINT(i);
  DEBUG_PRINT(" D=");
  DEBUG_PRINTLN(d);
}

// Update the lead-screw pitch at runtime
void Axis::setLeadScrewPitch(float pitchMM) {
  _config.screwPitchMM = pitchMM;
  motorParams[_icID].screwPitchMM = pitchMM;
  motorParams[_icID].stepsPerMM =
      (float)(motorParams[_icID].fullStepsPerRev * motorParams[_icID].microsteps) /
      pitchMM;
}

// Reconfigure the stepper driver at runtime (microstepping + current)
void Axis::configureDriver(uint16_t microstepping, float currentMA,
                            float holdCurrentRatio) {
  _config.microstepping = microstepping;
  // Note: do not sync homingMicrostepping -- on Y, 256 microsteps + 30 mm/s measured as quietest,
  // so even if legacy Squid software sends 32 microsteps for running, homing still switches to 256 microsteps.
  _config.motorCurrentMA = currentMA;
  _config.holdCurrent = holdCurrentRatio;

  // update the TMC4361A controller-side microstepping + stepsPerMM cache
  motor_setMicrosteps(_icID, microstepping);

  // reinitialize the driver (current + chopper parameters)
  MotorConfig motorConfig = {
      .driverType = _config.driverType,
      .rSense = _config.r_sense,
      .runCurrentMA = currentMA,
      .holdCurrentRatio = holdCurrentRatio,
      .microstepRes = 0,
      .interpolation = true,
      .toff = 3,
      .hstrt = 0,  // match legacy Squid zero-hysteresis (see the begin() comment)
      .hend = 0,
      .tbl = 2,
      .stallThreshold = (int8_t)_config.stallSensitivity,
      .stallFilter = true,
      .enableStealthChop = false,
      .globalScaler = 0,
      .iholdDelay = 7,
      .currentRange = _config.currentRange};
  motor_initDriver(_icID, &motorConfig);

  // a microstepping change alters stepsPerMM, so recompute the motion parameters
  setMotionParameters(_config.maxVelocityMM, _config.maxAccelerationMM);
}

// Update the homing safety margin at runtime
void Axis::setHomeSafetyMargin(float marginMM) {
  _config.homeSafetyMarginMM = marginMM;
  motor_enableHomingLimit(_icID, _config.rightSwitchPolarity,
                          _config.homingSwitch,
                          mmToMicrosteps(marginMM));
}

// Re-write the _config limit configuration into the chip at runtime (polarity is now sent by the host via cmd 20 and must be reapplied to take effect)
void Axis::reapplyLimitSwitches() {
  LimitConfig limitConfig = {
      .enableLeft = _config.enableLeftLimitSwitch,
      .enableRight = _config.enableRightLimitSwitch,
      .leftPolarity = _config.leftSwitchPolarity,
      .rightPolarity = _config.rightSwitchPolarity,
      .leftFlipped = _config.leftFlipped,
      .rightFlipped = _config.rightFlipped,
      .homingSwitch = _config.homingSwitch,
      .homeSafetyMarginMM = _config.homeSafetyMarginMM};
  motor_configLimitSwitches(_icID, &limitConfig);
  motor_enableHomingLimit(_icID, _config.rightSwitchPolarity,
                          _config.homingSwitch,
                          mmToMicrosteps(_config.homeSafetyMarginMM));
}

// Get the current state
AxisState Axis::getCurrentState() const { return _currentState; }

// Get the axis name
const char *Axis::getAxisName() const { return _axisName; }

// Check whether in the error state
bool Axis::isInErrorState() const { return _currentState == STATE_ERROR; }

// Read the electronic limit-switch state (using the new API)
uint8_t Axis::readLimitSwitches() const {
  return motor_readLimitSwitches(_icID);
}

// Read switch events (using the new API)
uint8_t Axis::readSwitchEvent() const {
  return motor_readSwitchEvent(_icID);
}

// Read axis events (using the new API)
uint32_t Axis::readAxisEvent() const {
  return motor_readEvents(_icID);
}

// Private method implementations
void Axis::setState(AxisState newState) {
  if (_currentState != newState) {
    _previousState = _currentState;
    _currentState = newState;
    _stateStartTime = millis();
    _stateChanged = true; // mark the state as changed
  }
}

void Axis::handleError(const char *errorMsg) {
  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":Axis Error: ");
  DEBUG_PRINTLN(errorMsg);
  smoothStop();
  setState(STATE_ERROR);
}

bool Axis::checkTimeout(unsigned long timeoutMs) const {
  return (millis() - _stateStartTime) > timeoutMs;
}


// Unit-conversion functions (using the new API)
int32_t Axis::mmToMicrosteps(float mm) const {
  return motor_mmToMicrosteps(_icID, mm);
}

float Axis::microstepsToMM(int32_t microsteps) const {
  return motor_microstepsToMM(_icID, microsteps);
}

uint32_t Axis::velocityMMToMicrosteps(float velocityMM) const {
  return motor_velocityMMToInternal(_icID, velocityMM);
}

uint32_t Axis::accelerationMMToMicrosteps(float accelerationMM) const {
  return motor_accelMMToInternal(_icID, accelerationMM);
}

bool Axis::isValidPosition(float positionMM) const {
  // check whether the position is within a reasonable range
  return (positionMM >= -1000.0f && positionMM <= 1000.0f); // adjust to actual conditions
}

bool Axis::isWithinSoftLimits(int32_t microsteps) const {
  // this should check against the actual soft-limit settings
  // temporarily returns true; needs to be completed per the concrete implementation
  return true;
}

bool Axis::handleEmergency() {
  // send the axis state
  [[maybe_unused]] const char *stateStr = "UNKNOWN";
  switch (_currentState) {
  case STATE_IDLE:
    stateStr = "IDLE";
    break;
  case STATE_HOMING_INIT:
    stateStr = "HOMING_INIT";
    break;
  case STATE_HOMING_SEARCH:
    stateStr = "HOMING_SEARCH";
    break;
  case STATE_HOMING_SET_ZERO:
    stateStr = "HOMING_SET_ZERO";
    break;
  case STATE_LEAVING_HOME:
    stateStr = "LEAVING_HOME";
    break;
  case STATE_MOVING:
    stateStr = "MOVING";
    break;
  case STATE_ERROR:
    stateStr = "ERROR";
    break;
  }

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":EMERGENCY:");
  DEBUG_PRINTLN(stateStr);

  return true;
}

bool Axis::handleGetData() {
  DEBUG_PRINT(_axisName);
  DEBUG_PRINTLN(":GET_DATA:START");  // debug point 1

  // send the axis state
  [[maybe_unused]] const char *stateStr = "UNKNOWN";
  switch (_currentState) {
  case STATE_IDLE:
    stateStr = "IDLE";
    break;
  case STATE_HOMING_INIT:
    stateStr = "HOMING_INIT";
    break;
  case STATE_HOMING_SEARCH:
    stateStr = "HOMING_SEARCH";
    break;
  case STATE_HOMING_SET_ZERO:
    stateStr = "HOMING_SET_ZERO";
    break;
  case STATE_LEAVING_HOME:
    stateStr = "LEAVING_HOME";
    break;
  case STATE_MOVING:
    stateStr = "MOVING";
    break;
  case STATE_ERROR:
    stateStr = "ERROR";
    break;
  }

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":STATE:");
  DEBUG_PRINTLN(stateStr);

  DEBUG_PRINT(_axisName);
  DEBUG_PRINTLN(":GET_DATA:BEFORE_GET_POS");  // debug point 2

  // send the current position
  int32_t microsteps = getCurrentPosition();

  DEBUG_PRINT(_axisName);
  DEBUG_PRINTLN(":GET_DATA:AFTER_GET_POS");  // debug point 3
  [[maybe_unused]] float positionMM = microstepsToMM(microsteps);
  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":Current Position (mm):");
  DEBUG_PRINTLNF(positionMM, 3);

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":Current Position (microsteps):");
  DEBUG_PRINTLN(microsteps);

  DEBUG_PRINT(_axisName);
  DEBUG_PRINTLN(":GET_DATA:BEFORE_READ_LIMIT");  // debug point 4

  // send the limit-switch state
  [[maybe_unused]] uint8_t limitState = readLimitSwitches();

  DEBUG_PRINT(_axisName);
  DEBUG_PRINTLN(":GET_DATA:AFTER_READ_LIMIT");  // debug point 5

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":LIMIT_SWITCHES:0x");
  DEBUG_PRINTLNF(limitState, HEX);

  // Added: send the movement state
  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":IS_MOVING:");
  DEBUG_PRINTLN(_isMoving ? "YES" : "NO");

  // Added: send the enable state
  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":IS_ENABLED:");
  DEBUG_PRINTLN(_isEnabled ? "YES" : "NO");

  // send aggregate status info (for label display)
  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":AXIS_STATUS:");
  DEBUG_PRINT(stateStr);
  DEBUG_PRINT(" | Pos:");
  DEBUG_PRINTF(positionMM, 3);
  DEBUG_PRINT("mm | Moving:");
  DEBUG_PRINT(_isMoving ? "YES" : "NO");
  DEBUG_PRINT(" | Enabled:");
  DEBUG_PRINT(_isEnabled ? "YES" : "NO");
  DEBUG_PRINT(" | Limits:0x");
  DEBUG_PRINTLNF(limitState, HEX);

  return true;
}

bool Axis::handleAxisAbilityToggle(bool action) {
  if (action == true) {
    enableAxis();
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":AXIS Enable");
  } else {
    disableAxis();
    DEBUG_PRINT(_axisName);
    DEBUG_PRINTLN(":AXIS Disable");
  }
  return true;
}

bool Axis::handleDebugReg() {
  DEBUG_PRINT(_axisName);
  DEBUG_PRINTLN(":DEBUG_REG:START");

  // read the key TMC4361A registers
  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":REG:GENERAL_CONF(0x00)=0x");
  DEBUG_PRINTLNF(tmc4361A_readRegister(_icID, TMC4361A_GENERAL_CONF), HEX);

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":REG:REFERENCE_CONF(0x01)=0x");
  DEBUG_PRINTLNF(tmc4361A_readRegister(_icID, TMC4361A_REFERENCE_CONF), HEX);

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":REG:SPI_OUT_CONF(0x05)=0x");
  DEBUG_PRINTLNF(tmc4361A_readRegister(_icID, TMC4361A_SPI_OUT_CONF), HEX);

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":REG:STATUS(0x0E)=0x");
  DEBUG_PRINTLNF(tmc4361A_readRegister(_icID, TMC4361A_STATUS), HEX);

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":REG:EVENTS(0x0F)=0x");
  DEBUG_PRINTLNF(tmc4361A_readRegister(_icID, TMC4361A_EVENTS), HEX);

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":REG:CLK_FREQ(0x1F)=");
  DEBUG_PRINTLN(tmc4361A_readRegister(_icID, TMC4361A_CLK_FREQ));

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":REG:RAMPMODE(0x20)=0x");
  DEBUG_PRINTLNF(tmc4361A_readRegister(_icID, TMC4361A_RAMPMODE), HEX);

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":REG:XACTUAL(0x21)=");
  DEBUG_PRINTLN(tmc4361A_readRegister(_icID, TMC4361A_XACTUAL));

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":REG:VACTUAL(0x22)=");
  DEBUG_PRINTLN(tmc4361A_readRegister(_icID, TMC4361A_VACTUAL));

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":REG:XTARGET(0x2D)=");
  DEBUG_PRINTLN(tmc4361A_readRegister(_icID, TMC4361A_XTARGET));

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":REG:VMAX(0x24)=");
  DEBUG_PRINTLN(tmc4361A_readRegister(_icID, TMC4361A_VMAX));

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":REG:AMAX(0x28)=");
  DEBUG_PRINTLN(tmc4361A_readRegister(_icID, TMC4361A_AMAX));

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":REG:DMAX(0x29)=");
  DEBUG_PRINTLN(tmc4361A_readRegister(_icID, TMC4361A_DMAX));

  // Added: key configuration registers
  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":REG:STEP_CONF(0x0A)=0x");
  DEBUG_PRINTLNF(tmc4361A_readRegister(_icID, TMC4361A_STEP_CONF), HEX);

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":REG:CURRENT_CONF(0x05)=0x");
  DEBUG_PRINTLNF(tmc4361A_readRegister(_icID, TMC4361A_CURRENT_CONF), HEX);

  DEBUG_PRINT(_axisName);
  DEBUG_PRINT(":REG:SCALE_VALUES(0x06)=0x");
  DEBUG_PRINTLNF(tmc4361A_readRegister(_icID, TMC4361A_SCALE_VALUES), HEX);

  DEBUG_PRINT(_axisName);
  DEBUG_PRINTLN(":DEBUG_REG:END");

  return true;
}
