#ifndef AXIS_H
#define AXIS_H

#include "tmc/motion/MotorControl.h"
#include <SPI.h>

// Limit switch and direction constants
#define LEFT_SW 0b01
#define RGHT_SW 0b10
#define LEFT_DIR -1
#define RGHT_DIR 1
#define OBSW_SW 0b01 // used by the Objectives class

// Driver chip type: DRIVER_TMC2660 / DRIVER_TMC2240 (defined in MotorControl.h)

// State definitions - using more explicit state names
enum AxisState {
  STATE_IDLE,
  STATE_HOMING_INIT,
  STATE_HOMING_SEARCH,
  STATE_HOMING_SET_ZERO,
  STATE_LEAVING_HOME,
  STATE_MOVING,
  STATE_ERROR
};

class Axis {

public:
  // Configuration parameters
  struct AxisConfig {
    uint32_t clockFrequency;
    uint8_t homingSwitch;
    uint8_t leftSwitchPolarity;
    uint8_t rightSwitchPolarity;
    // Whether the host cmd 20 (SET_LIM_SWITCH_POLARITY) is allowed to write the polarity into the chip REFERENCE_CONF.
    // Only Z=true (polarity changes with the old/new Z variant and must be sent by software at runtime); axes with
    // fixed hardware polarity such as X/Y=false: cmd 20 only updates the struct and does not touch the chip, matching
    // the legacy Squid firmware behavior (legacy Squid cmd 20 also only sets a software variable and never writes the
    // chip), which avoids the X/Y polarity (active-high) sent by legacy Squid wrongly flipping the octoaxes hardware (active-low).
    bool polarityAffectsChip = false;   // default false (axes that omit this field = do not write the chip); only Z_AXIS sets it true explicitly
    uint8_t leftIsInactive;
    uint8_t rightIsInactive;
    bool leftFlipped;
    bool rightFlipped;
    bool enableLeftLimitSwitch;
    bool enableRightLimitSwitch;
    float r_sense;
    float screwPitchMM;
    int fullStepsPerRev;
    int microstepping;
    int homingMicrostepping;     // microstepping used during homing, default 256
    float maxVelocityMM;
    float maxAccelerationMM;
    float homingVelocityMM;
    float motorCurrentMA;           // peak current (mA), I_rms = I_peak / √2
    float holdCurrent;
    float homeSafetyMarginMM;
    float homeSafetyPositionMM;
    bool enableStallSensitivity;
    int stallSensitivity;
    bool useSShapedRamp;         // true=S-shaped ramp, false=trapezoidal ramp
    float astartMM;              // start acceleration (mm/s²), 0=unused
    float dfinalMM;              // final deceleration (mm/s²), 0=same as astart
    uint32_t homing_timeout_ms;
    int8_t homing_direct;
    uint8_t driverType;          // driver chip model, default DRIVER_TMC2660
    uint8_t currentRange;        // TMC2240 CURRENT_RANGE: 0=1A, 1=2A, 2=3A (ignored for TMC2660)
    bool enableEncoder;          // whether to enable the ABN encoder, default false
    uint16_t encoderLinesPerRev; // encoder lines (per revolution), e.g. 4000. Used directly as transitions
    bool invertEncoderDir;       // reverse the encoder counting direction, default false
    bool invert_direction;       // 2026-05-25 hardware direction inversion: when true, all MOVE/HOMING commands
                                 // invert their payload at the firmware level, so mirror-assembled hardware (whose
                                 // home flag bit is opposite to the legacy Squid design) reaches the correct physical
                                 // position using the same host commands.
                                 // moveTo/moveRelative invert target/delta;
                                 // getCurrentPositionMicrosteps inverts the chip XACTUAL;
                                 // filterwheel.cpp homing search inverts the velocity direction.
                                 // default false (fully consistent with legacy Squid behavior).
  };

protected:
  // Protected member variables, accessible to derived classes
  uint8_t _csPin;
  uint8_t _axisIndex;
  const char *_axisName;

  // IC identifier
  uint8_t _icID;

  // Motion parameters
  uint32_t _maxVelocityMicrosteps;
  uint32_t _maxAccelerationMicrosteps;

  // Added: state-change detection
  AxisState _lastReportedState;       // last reported state
  bool _stateChanged;                 // flag for whether the state changed
  unsigned long _lastStateReportTime; // time of the last state report

  // State variables
  AxisState _currentState;
  AxisState _previousState;
  unsigned long _stateStartTime;
  bool _homeFound;

  // Added: movement state flags
  bool _isMoving;
  int32_t _moveDirection;
  unsigned long _cmdRecvMicros;   // command-received time (micros)
  unsigned long _moveStartMicros; // movement-start time (micros)

  // Added: axis enable state
  bool _isEnabled;

  // Soft-limit state tracking (for automatic restore after homing)
  bool _softLimitsEnabled;

  // Shadow state for the direction-aware soft-limit gate:
  // After a one-sided SET_LIM, record the host's intent, decoupled from the chip registers.
  // Even if motor_moveToMicrosteps recovery temporarily clears the chip's EN bit,
  // this still preserves the "was this side ever set" semantics, used by isMoveAllowedByDirection().
  struct SoftLimitShadow {
    bool leftEnabled;        // whether X-/Y-/Z- was set by SET_LIM
    bool rightEnabled;       // whether X+/Y+/Z+ was set by SET_LIM
    int32_t leftValue;       // latest set value of VIRT_STOP_LEFT (microsteps)
    int32_t rightValue;      // latest set value of VIRT_STOP_RIGHT (microsteps)
  };
  SoftLimitShadow _softLimits = {false, false, INT32_MIN, INT32_MAX};

  // Flag for delayed re-enable after virtual-limit recovery
  // motor_moveToMicrosteps() disables limits during VSTOP recovery,
  // so they can only be re-enabled after the motor leaves the boundary (VSTOP flags cleared in STATUS)
  bool _needReenableLimits;

  // PID state (independent per axis)
  struct PIDState {
    bool enabled;         // whether PID is currently active
    uint16_t p;           // cached P parameter
    uint8_t  i;           // cached I parameter
    uint8_t  d;           // cached D parameter
  } _pidState = {false, 0, 0, 0};

  AxisConfig _config;

  // Timeout settings
  static const unsigned long LEAVING_HOME_TIMEOUT_MS = 5000;
  static const unsigned long MOVEMENT_TIMEOUT_MS = 5000;

  elapsedMicros _checkHomeReachTimeout;

  // Throttle for STATE_MOVING checkLimitPosition (matches legacy Squid check_limits 10ms throttle,
  // reduces SPI bus contention; the hard-limit completion check tolerates a 0-10ms delay since the
  // chip has already physically stopped internally)
  // (#5, 2026-05-19)
  elapsedMicros _limitCheckThrottle;

  uint32_t _homing_timeout_ms;

public:
  // Constructor
  Axis(uint8_t csPin, uint8_t axisIndex, const char *axisName);

  // Virtual destructor
  virtual ~Axis() = default;

  // Initialization function
  virtual bool begin(const AxisConfig &config);

  // State-machine update - declared virtual so derived classes can override
  virtual void update();

  // Limit-position check
  virtual void checkLimitPosition();

  // Added: movement-complete detection called inside the ISR
  virtual void checkMovementComplete();

  // Command processing - returns the result
  virtual bool processCommand(const String &command);

  // Added: state-report control
  virtual void reportStateIfChanged(bool force = false);
  virtual void setStateChangeFlag() { _stateChanged = true; }

  // Motion control
  virtual bool moveToPosition(float positionMM);
  virtual bool moveRelative(float distanceMM);
  virtual bool moveToPositionMicrosteps(int32_t targetMicrosteps);
  virtual bool moveRelativeMicrosteps(int32_t deltaMicrosteps);
  virtual void setSpeed(float speedMM);
  virtual void smoothStop();

  void disableAxis();
  void enableAxis();

  // Position control
  virtual void setCurrentPosition(float positionMM);
  virtual float getCurrentPositionMM() const;
  virtual int32_t getCurrentPosition() const;
  virtual int32_t getCurrentPositionMicrosteps() const;
  virtual int32_t getEncoderPositionMicrosteps() const;
  virtual void setMotionParameters(float maxVelocityMM,
                                   float maxAccelerationMM);

  // Homing control
  virtual bool startHoming();
  virtual bool handleReset();
  virtual bool handleDebugReg();
  virtual bool isHomingInProgress() const;
  virtual bool isMovementComplete() const;

  // Added: movement-state query
  virtual bool isMoving() const { return _isMoving; }

  // Added: enable-state query
  virtual bool isEnabled() const { return _isEnabled; }

  // Soft-limit state query
  bool isSoftLimitsEnabled() const { return _softLimitsEnabled; }

  // Limit configuration
  virtual void setSoftLimits(float lowerLimitMM, float upperLimitMM);
  virtual void enableSoftLimits(bool enable);
  void setOneSoftLimit(int direction, int32_t valueMicrosteps);

  // Direction-aware clamp: clamp the target to the range allowed by the "move toward the safer direction" principle
  // With current position C, target T, and _softLimits leftValue=L / rightValue=R:
  //   effective_lower = (C ≤ L) ? C : L  // when past the lower limit, forbid going further down; in the safe zone, lower bound = L
  //   effective_upper = (C ≥ R) ? C : R  // symmetric
  //   returns clamp(T, effective_lower, effective_upper)
  // The side that is not enabled does not participate in clamping. After clamping to the boundary the motor stops at
  // the boundary, compatible with legacy Squid (legacy Squid also does min/max clamp in firmware callback_move_x/y/z)
  int32_t clampTargetByDirection(int32_t targetMicrosteps) const;

  // PID control
  void configureStagePID(bool flip_direction, uint16_t transitions_per_rev);
  void enableStagePID();
  void disableStagePID();
  void setPIDArguments(uint16_t p, uint8_t i, uint8_t d);
  bool isPIDEnabled() const { return _pidState.enabled; }

  // Runtime configuration updates
  void setLeadScrewPitch(float pitchMM);
  void configureDriver(uint16_t microstepping, float currentMA,
                        float holdCurrentRatio);
  void setHomeSafetyMargin(float marginMM);
  // Re-write the limit polarity/flip/enable/homingSwitch from _config into the chip REFERENCE_CONF at runtime.
  // begin() only configures once at boot; after cmd 20 (SET_LIM_SWITCH_POLARITY) updates the struct, this method must be called for it to actually take effect.
  void reapplyLimitSwitches();

  // Configuration access
  uint8_t getIcID() const { return _icID; }
  const AxisConfig &getConfig() const { return _config; }
  AxisConfig &getMutableConfig() { return _config; }

  // State query
  virtual AxisState getCurrentState() const;
  virtual const char *getAxisName() const;
  uint8_t getDriverType() const { return _config.driverType; }
  virtual bool isInErrorState() const;
  virtual uint32_t readAxisEvent() const;

  // Limit-switch state
  virtual uint8_t readLimitSwitches() const;
  virtual uint8_t readSwitchEvent() const;

  // Axis-move interface
  bool moveAxis(int32_t value);

protected:
  // Protected member methods, accessible to derived classes
  virtual void performHomingSequence() = 0;
  virtual void performLeavingHome() = 0;

  // Homing microstepping switch
  void switchToHomingMicrosteps();
  void restoreNormalMicrosteps();

  virtual void setState(AxisState newState);
  virtual void handleError(const char *errorMsg);
  virtual bool checkTimeout(unsigned long timeoutMs) const;
  virtual int32_t hexStringToInt32(String hex);

  // Command-handling helper methods
  virtual bool handleGetPosition();
  virtual bool handleSetLimits(const String &command) = 0;
  virtual bool handleMoveAxis(const String &command);
  virtual bool handleMoveToAxis(const String &command);
  virtual bool handleHoming();
  virtual bool handleGetData();
  virtual bool handleEmergency();
  virtual bool handleAxisAbilityToggle(bool);

  // Unit conversion
  virtual int32_t mmToMicrosteps(float mm) const;
  virtual float microstepsToMM(int32_t microsteps) const;
  virtual uint32_t velocityMMToMicrosteps(float velocityMM) const;
  virtual uint32_t accelerationMMToMicrosteps(float accelerationMM) const;

  // Motion checks
  virtual bool isValidPosition(float positionMM) const;
  virtual bool isWithinSoftLimits(int32_t microsteps) const;

  // Added: movement-state management
  virtual void startMovement();
  virtual void completeMovement();
};

#endif
