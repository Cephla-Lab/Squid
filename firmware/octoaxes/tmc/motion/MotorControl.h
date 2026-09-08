/*
 * MotorControl.h
 *
 * High-level motion control layer for TMC4361A + TMC2660/TMC2240.
 * Provides unified API for motor initialization, motion control,
 * and unit conversion.
 *
 * Created: 2026-01-21
 */

#ifndef TMC_MOTION_MOTOR_CONTROL_H_
#define TMC_MOTION_MOTOR_CONTROL_H_

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

// ============================================================================
// Driver Type Constants
// ============================================================================

#define DRIVER_TMC2660  0
#define DRIVER_TMC2240  1
#define DRIVER_AUTO     0xFF   // auto-detect the driver chip type during init

// ============================================================================
// Configuration Structures
// ============================================================================

/**
 * @brief TMC4361A motion configuration
 */
typedef struct {
    uint32_t clockFrequency;     // External clock frequency (Hz), typically 16MHz
    float    screwPitchMM;       // Lead screw pitch (mm per revolution)
    uint16_t fullStepsPerRev;    // Full steps per revolution (typically 200)
    uint16_t microsteps;         // Microstep resolution (1, 2, 4, ... 256)
    float    maxVelocityMM;      // Maximum velocity (mm/s)
    float    maxAccelerationMM;  // Maximum acceleration (mm/s²)
    float    maxDecelerationMM;  // Maximum deceleration (mm/s²), 0 = same as accel
    bool     useSShapedRamp;     // Use S-shaped ramp (bow parameters)
    float    astartMM;           // Initial acceleration (mm/s²), 0 = disabled
    float    dfinalMM;           // Final deceleration (mm/s²), 0 = same as astart
    uint32_t bow1;               // Bow parameter 1 (for S-shaped ramp)
    uint32_t bow2;               // Bow parameter 2
    uint32_t bow3;               // Bow parameter 3
    uint32_t bow4;               // Bow parameter 4
} MotionConfig;

/**
 * @brief Motor/driver configuration (supports TMC2660 and TMC2240)
 */
typedef struct {
    uint8_t  driverType;         // DRIVER_TMC2660 (default) or DRIVER_TMC2240
    float    rSense;             // Sense resistor value (Ohms)
    float    runCurrentMA;       // Peak run current (mA), NOT RMS
    float    holdCurrentRatio;   // Hold current as ratio of run (0.0-1.0)
    uint8_t  microstepRes;       // Microstep resolution (0=256, 1=128, ... 8=1)
    bool     interpolation;      // Enable 256 microstep interpolation
    // Chopper parameters (common to TMC2660 and TMC2240)
    uint8_t  toff;               // Chopper off time (1-15)
    uint8_t  hstrt;              // Hysteresis start (0-7)
    int8_t   hend;               // Hysteresis end (-3 to 12)
    uint8_t  tbl;                // Blanking time (0-3)
    int8_t   stallThreshold;     // StallGuard threshold (-64 to 63)
    bool     stallFilter;        // Enable StallGuard filter
    // TMC2240-specific parameters (ignored for TMC2660)
    bool     enableStealthChop;  // EN_PWM_MODE (StealthChop)
    uint8_t  globalScaler;       // GLOBAL_SCALER (0=256, 1-255), 0 means full scale
    uint8_t  iholdDelay;         // IHOLDDELAY (0-15)
    uint8_t  currentRange;       // DRV_CONF.CURRENT_RANGE: 0=1A, 1=2A, 2=3A, 3=3A
} MotorConfig;

/**
 * @brief Limit switch configuration
 */
typedef struct {
    bool     enableLeft;         // Enable left limit switch
    bool     enableRight;        // Enable right limit switch
    uint8_t  leftPolarity;       // Left switch polarity (0=active low, 1=active high)
    uint8_t  rightPolarity;      // Right switch polarity
    bool     leftFlipped;        // Swap left/right assignment
    bool     rightFlipped;
    uint8_t  homingSwitch;       // Which switch to use for homing (0=left, 1=right)
    float    homeSafetyMarginMM; // Safety margin after homing
} LimitConfig;

/**
 * @brief Combined axis configuration
 */
typedef struct {
    MotionConfig motion;
    MotorConfig  motor;
    LimitConfig  limits;
} AxisMotionConfig;

// ============================================================================
// Motion Parameter Cache (per IC)
// ============================================================================

#define MOTOR_IC_COUNT 7

// Cached motion parameters for unit conversion and state tracking
typedef struct {
    uint32_t clockFrequency;
    float    screwPitchMM;
    uint16_t fullStepsPerRev;
    uint16_t microsteps;
    float    stepsPerMM;         // Calculated: (fullStepsPerRev * microsteps) / screwPitchMM
    bool     initialized;

    // state tracking consistent with the old API velocity_mode
    bool     velocity_mode;      // true when in velocity mode, cleared on moveTo

    // ramp-parameter cache (consistent with the old API rampParam[])
    uint32_t bow1;               // BOW1 parameter
    uint32_t bow2;               // BOW2 parameter
    uint32_t bow3;               // BOW3 parameter
    uint32_t bow4;               // BOW4 parameter
    uint32_t amax;               // Maximum acceleration
    uint32_t dmax;               // Maximum deceleration
    uint32_t astart;             // Initial acceleration
    uint32_t dfinal;             // Final deceleration
    int32_t  vmax;               // Maximum velocity (internal units)
    uint8_t  driverType;         // this axis's driver chip type (DRIVER_TMC2660 / DRIVER_TMC2240)
    float    rSense;             // cached rSense value, used for TMC2660 runtime current calculation
    uint8_t  currentRange;       // cached TMC2240 CURRENT_RANGE, used for runtime current calculation
    uint8_t  toff;               // cached TOFF value, used by enableDriver to restore
} MotorParams;

extern MotorParams motorParams[MOTOR_IC_COUNT];

// ============================================================================
// Initialization API
// ============================================================================

/**
 * @brief Initialize motor control subsystem
 * Call once at startup before using any motor functions.
 */
void motor_initSubsystem(void);

/**
 * @brief Initialize a motor axis with full configuration
 * @param icID  IC identifier (0-6)
 * @param config Combined axis configuration
 * @return true if successful
 */
bool motor_init(uint8_t icID, const AxisMotionConfig *config);

/**
 * @brief Initialize TMC4361A with motion parameters
 * @param icID  IC identifier
 * @param config Motion configuration
 * @return true if successful
 */
bool motor_initMotionController(uint8_t icID, const MotionConfig *config);

/**
 * @brief Auto-detect the driver chip type (TMC2240 or TMC2660)
 * @param icID  IC identifier (TMC4361A must already be reset and communicating normally)
 * @return DRIVER_TMC2240 or DRIVER_TMC2660
 */
uint8_t motor_detectDriverType(uint8_t icID);

/**
 * @brief Initialize TMC2660 driver
 * @param icID  IC identifier
 * @param config Motor configuration
 * @return true if successful
 */
bool motor_initDriver(uint8_t icID, const MotorConfig *config);

/**
 * @brief Configure limit switches
 * @param icID  IC identifier
 * @param config Limit switch configuration
 */
void motor_configLimitSwitches(uint8_t icID, const LimitConfig *config);

/**
 * @brief Enable/disable hardware stop on a specific limit switch
 * @param icID   IC identifier
 * @param side   LEFT_SW (0x01) or RGHT_SW (0x02)
 * @param enable true = enable hardware stop, false = disable
 *
 * Used during homing to prevent TMC4361A hardware stop from locking out
 * subsequent motion commands. STATUS register STOPL/STOPR_ACTIVE_F bits
 * still reflect pin state regardless of this setting.
 */
void motor_setHardwareStopEnable(uint8_t icID, uint8_t side, bool enable);

// ============================================================================
// Motion Control API
// ============================================================================

/**
 * @brief Move to absolute position
 * @param icID  IC identifier
 * @param positionMM Target position in mm
 */
void motor_moveToPosition(uint8_t icID, float positionMM);

/**
 * @brief Move relative distance
 * @param icID  IC identifier
 * @param distanceMM Distance to move in mm
 */
void motor_moveByDistance(uint8_t icID, float distanceMM);

/**
 * @brief Move to absolute position in microsteps
 * @param icID  IC identifier
 * @param position Target position in microsteps
 * @return true if virtual stop (VSTOPL/VSTOPR) was active before the move;
 *         caller can use this to decide whether limits need re-enabling later.
 *         Single SPI STATUS read is done inside this function — callers do
 *         NOT need to read STATUS themselves.
 */
bool motor_moveToMicrosteps(uint8_t icID, int32_t position);

/**
 * @brief Start velocity mode rotation
 * @param icID  IC identifier
 * @param velocityMM Velocity in mm/s (negative for reverse)
 */
void motor_rotateVelocity(uint8_t icID, float velocityMM);

/**
 * @brief Stop motor (decelerate to stop)
 * @param icID  IC identifier
 */
void motor_stop(uint8_t icID);

/**
 * @brief Emergency stop (immediate)
 * @param icID  IC identifier
 */
void motor_emergencyStop(uint8_t icID);

// ============================================================================
// Status Query API
// ============================================================================

/**
 * @brief Check if target position is reached
 * @param icID  IC identifier
 * @return true if target reached
 */
bool motor_isTargetReached(uint8_t icID);

/**
 * @brief Check if motor is running
 * @param icID  IC identifier
 * @return true if motor is moving
 */
bool motor_isRunning(uint8_t icID);

/**
 * @brief Get current position in mm
 * @param icID  IC identifier
 * @return Current position
 */
float motor_getPositionMM(uint8_t icID);

/**
 * @brief Get current position in microsteps
 * @param icID  IC identifier
 * @return Current position
 */
int32_t motor_getPositionMicrosteps(uint8_t icID);

/**
 * @brief Get target position in microsteps
 * @param icID  IC identifier
 * @return Target position
 */
int32_t motor_getTargetMicrosteps(uint8_t icID);

/**
 * @brief Get current velocity in mm/s
 * @param icID  IC identifier
 * @return Current velocity
 */
float motor_getVelocityMM(uint8_t icID);

/**
 * @brief Get current velocity in internal units
 * @param icID  IC identifier
 * @return Current velocity (24.8 fixed point)
 */
int32_t motor_getVelocityInternal(uint8_t icID);

/**
 * @brief Read limit switch status
 * @param icID  IC identifier
 * @return Bit 0 = left, Bit 1 = right
 */
uint8_t motor_readLimitSwitches(uint8_t icID);

/**
 * @brief Read TMC4361A status/event register
 * @param icID  IC identifier
 * @return Status bits
 */
uint32_t motor_readStatus(uint8_t icID);

/**
 * @brief Read TMC4361A event register
 * @param icID  IC identifier
 * @return Event bits
 */
uint32_t motor_readEvents(uint8_t icID);

// ============================================================================
// Parameter Setting API
// ============================================================================

/**
 * @brief Set maximum velocity
 * @param icID  IC identifier
 * @param velocityMM Maximum velocity in mm/s
 */
void motor_setMaxVelocity(uint8_t icID, float velocityMM);

/**
 * @brief Reset RAMPMODE to position mode (S-shaped)
 * Call after RESET command or hardware limit trigger to restore normal operation
 * @param icID  IC identifier
 */
void motor_resetRampMode(uint8_t icID);

/**
 * @brief Set maximum acceleration
 * @param icID  IC identifier
 * @param accelerationMM Maximum acceleration in mm/s²
 */
void motor_setMaxAcceleration(uint8_t icID, float accelerationMM);

/**
 * @brief Set maximum deceleration
 * @param icID  IC identifier
 * @param decelerationMM Maximum deceleration in mm/s²
 */
void motor_setMaxDeceleration(uint8_t icID, float decelerationMM);

/**
 * @brief Set current position (without moving)
 * @param icID  IC identifier
 * @param positionMM Position to set in mm
 */
void motor_setCurrentPosition(uint8_t icID, float positionMM);

/**
 * @brief Set current position in microsteps
 * @param icID  IC identifier
 * @param position Position in microsteps
 */
void motor_setCurrentPositionMicrosteps(uint8_t icID, int32_t position);

/**
 * @brief Set microstep resolution at runtime
 * Updates STEP_CONF register and cached stepsPerMM.
 * Caller should recalculate motion parameters (VMAX/AMAX/BOW) after calling this.
 * @param icID  IC identifier
 * @param microsteps New microstep resolution (1, 2, 4, ... 256)
 */
void motor_setMicrosteps(uint8_t icID, uint16_t microsteps);

/**
 * @brief Set motor run current
 * @param icID  IC identifier
 * @param currentMA Current in mA
 */
void motor_setRunCurrent(uint8_t icID, float currentMA);

/**
 * @brief Enable/disable motor driver
 * @param icID  IC identifier
 * @param enable true to enable
 */
void motor_enableDriver(uint8_t icID, bool enable);

// ============================================================================
// Unit Conversion API
// ============================================================================

/**
 * @brief Convert mm to microsteps
 * @param icID  IC identifier
 * @param mm Distance in mm
 * @return Distance in microsteps
 */
int32_t motor_mmToMicrosteps(uint8_t icID, float mm);

/**
 * @brief Convert microsteps to mm
 * @param icID  IC identifier
 * @param microsteps Distance in microsteps
 * @return Distance in mm
 */
float motor_microstepsToMM(uint8_t icID, int32_t microsteps);

/**
 * @brief Convert velocity from mm/s to internal units
 * @param icID  IC identifier
 * @param velocityMM Velocity in mm/s
 * @return Velocity in internal units (24.8 fixed point)
 */
int32_t motor_velocityMMToInternal(uint8_t icID, float velocityMM);

/**
 * @brief Convert velocity from internal units to mm/s
 * @param icID  IC identifier
 * @param velocityInternal Velocity in internal units
 * @return Velocity in mm/s
 */
float motor_velocityInternalToMM(uint8_t icID, int32_t velocityInternal);

/**
 * @brief Convert acceleration from mm/s² to internal units
 * @param icID  IC identifier
 * @param accelMM Acceleration in mm/s²
 * @return Acceleration in internal units
 */
uint32_t motor_accelMMToInternal(uint8_t icID, float accelMM);

// ============================================================================
// Homing API
// ============================================================================

/**
 * @brief Start homing sequence
 * @param icID  IC identifier
 * @param direction Homing direction (-1 or +1)
 * @param velocityMM Homing velocity in mm/s
 */
void motor_startHoming(uint8_t icID, int8_t direction, float velocityMM);

/**
 * @brief Set home position (current position becomes reference)
 * @param icID  IC identifier
 * @param positionMM Position value to set as home
 */
void motor_setHomePosition(uint8_t icID, float positionMM);

/**
 * @brief Configure homing limit switch
 * @param icID  IC identifier
 * @param polarity Switch polarity (0=active low, 1=active high)
 * @param whichSwitch Which switch (0x01=left, 0x02=right)
 * @param safetyMarginMicrosteps Safety margin after homing
 */
void motor_enableHomingLimit(uint8_t icID, uint8_t polarity, uint8_t whichSwitch,
                              int32_t safetyMarginMicrosteps);

// ============================================================================
// Soft Limit API
// ============================================================================

/**
 * @brief Set soft (virtual) limit positions
 * @param icID  IC identifier
 * @param lowerLimitMicrosteps Lower limit position
 * @param upperLimitMicrosteps Upper limit position
 */
void motor_setSoftLimits(uint8_t icID, int32_t lowerLimitMicrosteps, int32_t upperLimitMicrosteps);

/**
 * @brief Enable/disable soft limits
 * @param icID  IC identifier
 * @param enableLower Enable lower limit
 * @param enableUpper Enable upper limit
 */
void motor_enableSoftLimits(uint8_t icID, bool enableLower, bool enableUpper);

// ============================================================================
// Advanced Configuration API
// ============================================================================

/**
 * @brief Initialize ABN encoder interface
 * @param icID  IC identifier
 * @param transitions_per_rev  Encoder transitions per revolution
 * @param filter_wait_time  Filter wait time (0-255)
 * @param filter_exponent  Filter exponent (0-15)
 * @param filter_vmean  Filter vmean integration (0-65535)
 * @param invert_dir  Invert encoder direction
 */
void motor_initABNEncoder(uint8_t icID, uint32_t transitions_per_rev,
                           uint8_t filter_wait_time, uint8_t filter_exponent,
                           uint16_t filter_vmean, bool invert_dir);

/**
 * @brief Initialize PID parameters (write to TMC4361A registers)
 * @param icID  IC identifier
 * @param target_tolerance  Closed-loop target tolerance
 * @param pid_tolerance  PID tolerance
 * @param pid_p  Proportional gain
 * @param pid_i  Integral gain
 * @param pid_d  Derivative gain
 * @param pid_dclip  PID velocity clip
 * @param pid_iclip  PID integral clip
 * @param pid_d_clkdiv  PID derivative clock divider
 */
void motor_initPID(uint8_t icID, uint32_t target_tolerance, uint32_t pid_tolerance,
                    uint32_t pid_p, uint32_t pid_i, uint32_t pid_d,
                    uint32_t pid_dclip, uint32_t pid_iclip, uint8_t pid_d_clkdiv);

/**
 * @brief Enable PID control mode
 * @param icID  IC identifier
 */
void motor_enablePID(uint8_t icID);

/**
 * @brief Disable PID control mode
 * @param icID  IC identifier
 */
void motor_disablePID(uint8_t icID);

/**
 * @brief Configure StallGuard parameters
 * @param icID  IC identifier
 * @param threshold StallGuard threshold (-64 to 63)
 * @param filterEnable Enable StallGuard filter
 * @param stopOnStall Stop motor when stall detected
 */
void motor_configStallGuard(uint8_t icID, int8_t threshold, bool filterEnable, bool stopOnStall);

/**
 * @brief Read switch event register (clears on read)
 * @param icID  IC identifier
 * @return Switch event bits
 */
uint8_t motor_readSwitchEvent(uint8_t icID);

/**
 * @brief Set velocity directly in internal units (for homing)
 * @param icID  IC identifier
 * @param velocityInternal Velocity in internal units (signed, direction included)
 */
void motor_setVelocityInternal(uint8_t icID, int32_t velocityInternal);

/**
 * @brief Read latched position (captured on limit switch event)
 * @param icID  IC identifier
 * @return Latched position in microsteps
 */
int32_t motor_readLatchPosition(uint8_t icID);

#ifdef __cplusplus
}
#endif

#endif /* TMC_MOTION_MOTOR_CONTROL_H_ */
