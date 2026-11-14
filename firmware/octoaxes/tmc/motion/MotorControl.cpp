/*
 * MotorControl.cpp
 *
 * High-level motion control layer implementation.
 *
 * Created: 2026-01-21
 */

#include "MotorControl.h"
#include "../ic/TMC4361A/TMC4361A.h"
#include "../ic/TMC2660/TMC2660.h"
#include "../ic/TMC2240/TMC2240.h"
#include "../hal/TMC_SPI.h"
#include <Arduino.h>
#include "../../build_opt.h"

// ============================================================================
// Debug Helper
// ============================================================================

extern "C" void motor_debugPrint(const char* msg, int32_t val)
{
    DEBUG_PRINT(msg);
    DEBUG_PRINT(":");
    DEBUG_PRINTLN(val);
}

// ============================================================================
// TMC2240 HAL Callbacks
// ============================================================================

// TMC2240 communicates through the TMC4361A 40-bit Cover interface
// tmc2240_readWriteSPI callback: a 5-byte SPI frame -> TMC4361A COVER_HIGH + COVER_LOW
extern "C" void tmc2240_readWriteSPI(uint16_t icID, uint8_t *data, size_t dataLength)
{
    // route to the TMC4361A Cover interface (5 bytes = 40-bit)
    tmc4361A_readWriteCover(icID, data, dataLength);
}

extern "C" TMC2240BusType tmc2240_getBusType(uint16_t icID)
{
    (void)icID;
    return TMC2240_BUS_SPI;
}

// ============================================================================
// Motor Parameters Cache
// ============================================================================

MotorParams motorParams[MOTOR_IC_COUNT] = {};

// ============================================================================
// Internal Helper Functions
// ============================================================================

// BOW parameter maximum (consistent with the old API BOWMAX)
#define BOWMAX ((1 << 24) - 1)

// automatically compute the BOW parameters (exactly the same as the old API tmc4361A_adjustBows)
// formula: BOW = AMAX^2 / VMAX
// purpose: minimize the time spent saturated at AMAX
static void motor_adjustBows(uint8_t icID)
{
    if (icID >= MOTOR_IC_COUNT || !motorParams[icID].initialized)
        return;

    // get AMAX and VMAX (internal units)
    int32_t vmax = motorParams[icID].vmax;
    uint32_t amax = motorParams[icID].amax;

    if (vmax == 0) {
        // avoid division by zero
        motorParams[icID].bow1 = 0;
        motorParams[icID].bow2 = 0;
        motorParams[icID].bow3 = 0;
        motorParams[icID].bow4 = 0;
        return;
    }

    // convert to mm units for the calculation (consistent with the old API)
    // VMAX internal units are 24.8 fixed-point, so divide by 256
    float vmax_mm = (float)vmax / 256.0f / motorParams[icID].stepsPerMM;

    // AMAX internal units are 22.2 fixed-point, so divide by 4
    float amax_mm = (float)amax / 4.0f / motorParams[icID].stepsPerMM;

    // compute the BOW value: AMAX^2 / VMAX
    float bowval_mm = (amax_mm * amax_mm) / vmax_mm;

    // convert back to internal units (microsteps)
    int32_t bow = (int32_t)(bowval_mm * motorParams[icID].stepsPerMM);
    if (bow < 0) bow = -bow;  // abs
    if (bow > BOWMAX) bow = BOWMAX;

    // set all 4 BOW parameters to the same value (consistent with the old API)
    motorParams[icID].bow1 = bow;
    motorParams[icID].bow2 = bow;
    motorParams[icID].bow3 = bow;
    motorParams[icID].bow4 = bow;

    DEBUG_PRINT("motor_adjustBows: icID=");
    DEBUG_PRINT(icID);
    DEBUG_PRINT(" AMAX=");
    DEBUG_PRINT(amax);
    DEBUG_PRINT(" VMAX=");
    DEBUG_PRINT(vmax);
    DEBUG_PRINT(" BOW=");
    DEBUG_PRINTLN(bow);
}

// Calculate current scale from peak current (mA) and sense resistor
// TMC2660 formula:
//   I_peak = (CS + 1) / 32 × V_FS / R_sense
//   I_rms  = I_peak / √2
// VSENSE=0 → V_FS = 0.310V; VSENSE=1 → V_FS = 0.165V
// this project's DRVCONF sets VSENSE=0 (high range)
// chip absolute max: 4A peak (2.8A RMS), CS range 0~31
static uint8_t calculateCurrentScale(float currentMA, float rSense)
{
    // 2026-05-11 fix: interpret currentMA as RMS (consistent with legacy Squid firmware)
    // going from legacy Squid software -> octoaxes firmware, interpreting it as PEAK would make the actual current ~30% low
    // -> step loss and noise on the Y-axis acceleration phase (see SESSION.md 2026-05-11 #6)
    //
    // TMC2660 datasheet: I_peak = (CS+1)/32 × V_FS/R_sense
    // derived from I_RMS = I_peak/sqrt(2): CS = I_RMS * R_sense * 32 * sqrt(2) / V_FS - 1
    // V_FS = 0.310 V (VSENSE=0, high range, consistent with the DRVCONF setting)
    static const float SQRT2 = 1.41421356f;
    float cs = (currentMA / 1000.0f) * rSense * 32.0f * SQRT2 / 0.310f - 1.0f;

    if (cs < 0) cs = 0;
    if (cs > 31) cs = 31;

    return (uint8_t)cs;
}

// Calculate microstep resolution register value (reserved for future use)
__attribute__((unused))
static uint8_t calculateMresValue(uint16_t microsteps)
{
    // MRES: 0=256, 1=128, 2=64, 3=32, 4=16, 5=8, 6=4, 7=2, 8=1
    switch (microsteps) {
        case 256: return 0;
        case 128: return 1;
        case 64:  return 2;
        case 32:  return 3;
        case 16:  return 4;
        case 8:   return 5;
        case 4:   return 6;
        case 2:   return 7;
        case 1:   return 8;
        default:  return 0;  // Default to 256
    }
}

// Get TMC2240 full-scale current (A) from CURRENT_RANGE setting
// TMC2240 uses integrated current sense (ICS), no external sense resistor
// I_FS is determined by DRV_CONF.CURRENT_RANGE (assuming RREF=default)
static float getTMC2240_IFS(uint8_t currentRange)
{
    switch (currentRange & 0x03) {
        case 0:  return 1.0f;   // CURRENT_RANGE=0: ~1.0A
        case 1:  return 2.0f;   // CURRENT_RANGE=1: ~2.0A
        default: return 3.0f;   // CURRENT_RANGE=2/3: ~3.0A
    }
}

// Calculate TMC2240 current scale (IRUN/IHOLD value 0-31)
// TMC2240 formula (datasheet section 3):
//   I_RMS = (CS_ACTUAL + 1) / 32 × (GLOBALSCALER / 256) × I_FS
// simplified (GLOBALSCALER=0, i.e. 256):
//   IRUN = round(I_peak / I_FS × 32) - 1
// note: currentMA is peak current (mA), I_FS is determined by CURRENT_RANGE
static uint8_t calculateCurrentScale_TMC2240(float currentMA, uint8_t currentRange, uint8_t globalScaler)
{
    float ifs = getTMC2240_IFS(currentRange);
    float gs = (globalScaler == 0) ? 1.0f : (float)globalScaler / 256.0f;

    // IRUN = (I_peak / I_FS / GLOBALSCALER_ratio) × 32 - 1
    float cs = (currentMA / 1000.0f) / ifs / gs * 32.0f - 1.0f;

    if (cs < 0) cs = 0;
    if (cs > 31) cs = 31;

    return (uint8_t)cs;
}

// Forward declaration
static bool motor_initDriver_TMC2660(uint8_t icID, const MotorConfig *config);
static bool motor_initDriver_TMC2240(uint8_t icID, const MotorConfig *config);

// ============================================================================
// Initialization
// ============================================================================

void motor_initSubsystem(void)
{
    // Initialize SPI HAL
    tmc_spi_init();

    // Initialize TMC4361A cache
    tmc4361A_initCache();

    // Initialize TMC2660 cache
    tmc2660_initCache();

    // Initialize TMC2240 cache
    tmc2240_initCache();

    // Clear motor parameters
    for (int i = 0; i < MOTOR_IC_COUNT; i++) {
        motorParams[i].initialized = false;
    }
}

bool motor_init(uint8_t icID, const AxisMotionConfig *config)
{
    if (icID >= MOTOR_IC_COUNT || config == NULL)
        return false;

    // save the driver type early; motor_initMotionController needs it to choose SPI_OUT_CONF
    motorParams[icID].driverType = config->motor.driverType;

    // Initialize motion controller (TMC4361A)
    if (!motor_initMotionController(icID, &config->motion))
        return false;

    // Initialize motor driver (TMC2660 or TMC2240)
    if (!motor_initDriver(icID, &config->motor))
        return false;

    // Configure limit switches
    motor_configLimitSwitches(icID, &config->limits);

    return true;
}

// ============================================================================
// driver chip auto-detection
// ============================================================================

uint8_t motor_detectDriverType(uint8_t icID)
{
    // use the TMC2660 format (format=0x0A, 20-bit auto SPI) together with a manual 40-bit Cover
    // the 20-bit auto SPI does not overwrite the full 40-bit Cover response, solving the format=0x0D interference issue
    // COVER_DATA_LENGTH=40 ensures the Cover transfer uses 40-bit
    // SPI timing: block=4, high=4, low=4
    uint32_t spiOutConf_detect = 0x4445000A;  // CDL=40 + format=0x0A
    tmc4361A_writeRegister(icID, TMC4361A_SPI_OUT_CONF, spiOutConf_detect);
    delayMicroseconds(500);

    // read TMC2240 IOIN via the 40-bit Cover (address 0x04)
    // the TMC2240 VERSION field is at IOIN[31:24] = 0x40
    // a TMC2660 receiving 40-bit returns a 20-bit response + padding, VERSION != 0x40
    int32_t ioin = tmc2240_readRegister(icID, TMC2240_IOIN);
    uint8_t version = (ioin >> 24) & 0xFF;

    DEBUG_PRINT("IC");
    DEBUG_PRINT(icID);
    DEBUG_PRINT(":detect IOIN=0x");
    DEBUG_PRINTF(ioin, HEX);
    DEBUG_PRINT(" ver=0x");
    DEBUG_PRINTF(version, HEX);

    if (version == 0x40) {
        DEBUG_PRINTLN(" -> TMC2240");
        return DRIVER_TMC2240;
    } else {
        DEBUG_PRINTLN(" -> TMC2660");
        return DRIVER_TMC2660;
    }
}

bool motor_initMotionController(uint8_t icID, const MotionConfig *config)
{
    if (icID >= MOTOR_IC_COUNT || config == NULL)
        return false;

    // Store motion parameters for unit conversion
    motorParams[icID].clockFrequency = config->clockFrequency;
    motorParams[icID].screwPitchMM = config->screwPitchMM;
    motorParams[icID].fullStepsPerRev = config->fullStepsPerRev;
    motorParams[icID].microsteps = config->microsteps;
    motorParams[icID].stepsPerMM = (float)(config->fullStepsPerRev * config->microsteps) / config->screwPitchMM;
    motorParams[icID].initialized = true;
    motorParams[icID].velocity_mode = false;  // consistent with the old API tmc4361A_init

    // Reset TMC4361A (same as old API)
    tmc4361A_writeRegister(icID, TMC4361A_SW_RESET, 0x52535400);

    // Read VERSION_NO to verify communication
    int32_t version = tmc4361A_readRegister(icID, TMC4361A_VERSION_NO);
    if (version == 0 || version == -1) {
        return false;  // Communication failed
    }

    // set CLK_FREQ first; the Cover SPI clock depends on this configuration
    tmc4361A_writeRegister(icID, TMC4361A_CLK_FREQ, config->clockFrequency);

    // auto-detect the driver chip type
    if (motorParams[icID].driverType == DRIVER_AUTO) {
        motorParams[icID].driverType = motor_detectDriverType(icID);
    }

    // Configure GENERAL_CONF
    uint32_t generalConf = 0x00000000;
    if (config->astartMM > 0) {
        generalConf |= TMC4361A_USE_ASTART_AND_VSTART_MASK;  // enable ASTART/DFINAL
    }
    // under TMC2240 direct_mode, the direction is determined by the TMC4361A microstep-table phase sequence,
    // the TMC2240 SHAFT bit is ineffective (it only affects STEP/DIR mode).
    // invert the TMC4361A internal microstep-table direction to compensate for the phase-mapping difference between format 0x0D and 0x0A
    if (motorParams[icID].driverType == DRIVER_TMC2240) {
        generalConf |= TMC4361A_REVERSE_MOTOR_DIR_MASK;  // bit 28
    }
    tmc4361A_writeRegister(icID, TMC4361A_GENERAL_CONF, generalConf);

    // Configure SPI_OUT_CONF - choose the SPI output format based on the driver chip type
    uint32_t spiOutConf;
    if (motorParams[icID].driverType == DRIVER_TMC2240) {
        // TMC2240: SPI_OUTPUT_FORMAT=0x0D (TMC2130/TMC2240 SPI current-transfer mode, 40-bit)
        // equivalent to the TMC2660 SDOFF mode: the TMC4361A directly controls the coil current
        // SPI timing: block=4, high=4, low=4
        // COVER_DATA_LENGTH=40 (bits[19:13]), explicitly specifying a 40-bit Cover length
        spiOutConf = 0x4445000D;
    } else {
        // TMC2660: SPI_OUTPUT_FORMAT=0x0A (TMC26x 20-bit SPI mode)
        // 0x4440108A: SCALE_VAL_TRANSFER_EN=1, COVER_DATA_LENGTH for 20-bit
        spiOutConf = 0x4440108A;
    }
    tmc4361A_writeRegister(icID, TMC4361A_SPI_OUT_CONF, spiOutConf);

    // CLK_FREQ was already set before detection

    // Configure ramp mode
    // RAMPMODE: 0=hold, 1=trapezoid, 2=S-shaped, 4=position mode
    uint32_t rampMode = config->useSShapedRamp ? 6 : 5;  // Position mode + ramp type
    tmc4361A_writeRegister(icID, TMC4361A_RAMPMODE, rampMode);

    // ========================================================================
    // cache the ramp parameters (consistent with the old API rampParam[])
    // ========================================================================

    // compute and cache the velocity/acceleration parameters
    int32_t vmax = motor_velocityMMToInternal(icID, config->maxVelocityMM);
    uint32_t amax = motor_accelMMToInternal(icID, config->maxAccelerationMM);
    float decelMM = config->maxDecelerationMM > 0 ? config->maxDecelerationMM : config->maxAccelerationMM;
    uint32_t dmax = motor_accelMMToInternal(icID, decelMM);

    motorParams[icID].vmax = vmax;
    motorParams[icID].amax = amax;
    motorParams[icID].dmax = dmax;
    // ASTART / DFINAL: start acceleration and final deceleration
    uint32_t astart = config->astartMM > 0 ? motor_accelMMToInternal(icID, config->astartMM) : 0;
    float dfinalMM = config->dfinalMM > 0 ? config->dfinalMM : config->astartMM;
    uint32_t dfinal = dfinalMM > 0 ? motor_accelMMToInternal(icID, dfinalMM) : 0;
    motorParams[icID].astart = astart;
    motorParams[icID].dfinal = dfinal;

    // write to the hardware registers
    tmc4361A_writeRegister(icID, TMC4361A_VMAX, vmax);
    tmc4361A_writeRegister(icID, TMC4361A_AMAX, amax);
    tmc4361A_writeRegister(icID, TMC4361A_DMAX, dmax);

    // Configure S-shaped ramp if enabled
    if (config->useSShapedRamp) {
        // if the BOW parameters are 0, compute them automatically (consistent with the old API tmc4361A_adjustBows)
        if (config->bow1 == 0 && config->bow2 == 0 && config->bow3 == 0 && config->bow4 == 0) {
            motor_adjustBows(icID);
        } else {
            motorParams[icID].bow1 = config->bow1;
            motorParams[icID].bow2 = config->bow2;
            motorParams[icID].bow3 = config->bow3;
            motorParams[icID].bow4 = config->bow4;
        }

        // write the BOW registers
        tmc4361A_writeRegister(icID, TMC4361A_BOW1, motorParams[icID].bow1);
        tmc4361A_writeRegister(icID, TMC4361A_BOW2, motorParams[icID].bow2);
        tmc4361A_writeRegister(icID, TMC4361A_BOW3, motorParams[icID].bow3);
        tmc4361A_writeRegister(icID, TMC4361A_BOW4, motorParams[icID].bow4);
    } else {
        motorParams[icID].bow1 = 0;
        motorParams[icID].bow2 = 0;
        motorParams[icID].bow3 = 0;
        motorParams[icID].bow4 = 0;
    }

    // Set VSTART, VSTOP, ASTART, DFINAL
    tmc4361A_writeRegister(icID, TMC4361A_VSTART, 0);
    tmc4361A_writeRegister(icID, TMC4361A_VSTOP, 0);
    tmc4361A_writeRegister(icID, TMC4361A_ASTART, motorParams[icID].astart);
    tmc4361A_writeRegister(icID, TMC4361A_DFINAL, motorParams[icID].dfinal);

    // ========================================================================
    // key configuration: microstepping and steps per revolution (consistent with the old API tmc4361A_writeMicrosteps/writeSPR)
    // ========================================================================

    // compute the MSTEP_PER_FS value: 256->0, 128->1, ..., 1->8
    uint16_t mstep = config->microsteps;
    uint8_t mstepVal = 0;
    if (mstep > 0 && (mstep & (mstep - 1)) == 0 && mstep <= 256) {
        // compute log2(mstep) + 1, then 9 - result
        uint8_t bitsSet = 0;
        while (mstep > 0) {
            bitsSet++;
            mstep >>= 1;
        }
        mstepVal = 9 - bitsSet;
    }

    // combine STEP_CONF: MSTEP_PER_FS (bit 0-3) + FS_PER_REV (bit 4-15)
    uint32_t stepConf = (mstepVal & TMC4361A_MSTEP_PER_FS_MASK) |
                        ((uint32_t)config->fullStepsPerRev << TMC4361A_FS_PER_REV_SHIFT);
    tmc4361A_writeRegister(icID, TMC4361A_STEP_CONF, stepConf);

    // ========================================================================
    // key configuration: current scaling
    // the TMC4361A internally uses SCALE_VALUES to compute the coil current amplitude, applicable to all SPI output formats:
    // - TMC2660 (format 0x0A): the 20-bit SPI data contains the scaled current value
    // - TMC2240 (format 0x0D): 40-bit SPI writes the DIRECT_MODE register, which also needs scaling
    // not configuring SCALE_VALUES would send zero current -> the motor does not move
    // ========================================================================

    // SCALE_VALUES + CURRENT_CONF (consistent with the old API tmc4361A_cScaleInit)
    uint32_t scaleValues = (128 << TMC4361A_HOLD_SCALE_VAL_SHIFT) |   // hold = 50%
                           (255 << TMC4361A_DRV2_SCALE_VAL_SHIFT) |   // drv2 = 100%
                           (255 << TMC4361A_DRV1_SCALE_VAL_SHIFT) |   // drv1 = 100%
                           (255 << TMC4361A_BOOST_SCALE_VAL_SHIFT);   // boost = 100%
    tmc4361A_writeRegister(icID, TMC4361A_SCALE_VALUES, scaleValues);

    uint32_t currentConf = tmc4361A_readRegister(icID, TMC4361A_CURRENT_CONF);
    currentConf |= TMC4361A_DRIVE_CURRENT_SCALE_EN_MASK;       // bit 1
    currentConf |= TMC4361A_HOLD_CURRENT_SCALE_EN_MASK;        // bit 0
    currentConf |= TMC4361A_BOOST_CURRENT_ON_ACC_EN_MASK;      // bit 2
    currentConf |= TMC4361A_BOOST_CURRENT_AFTER_START_EN_MASK;  // bit 4
    tmc4361A_writeRegister(icID, TMC4361A_CURRENT_CONF, currentConf);

    return true;
}

bool motor_initDriver(uint8_t icID, const MotorConfig *config)
{
    if (icID >= MOTOR_IC_COUNT || config == NULL)
        return false;

    // cache the driver type and rSense
    motorParams[icID].driverType = config->driverType;
    motorParams[icID].rSense = config->rSense;

    if (config->driverType == DRIVER_TMC2240) {
        return motor_initDriver_TMC2240(icID, config);
    } else {
        return motor_initDriver_TMC2660(icID, config);
    }
}

// ========================================================================
// TMC2660 driver initialization
// ========================================================================
static bool motor_initDriver_TMC2660(uint8_t icID, const MotorConfig *config)
{
    // cache toff for enableDriver to restore
    motorParams[icID].toff = config->toff;

    // Calculate current scale
    uint8_t cs = calculateCurrentScale(config->runCurrentMA, config->rSense);

    // TMC2660 initialization - same order as the old API
    // old API order: CHOPCONF -> SMARTEN -> SGCSCONF -> DRVCONF
    // note: in SPI mode (SDOFF=1), DRVCTRL is not sent

    // 1. Configure CHOPCONF (old API: 0x000900C3)
    uint8_t hend_reg = (uint8_t)(config->hend + 3);  // Offset by 3
    uint32_t chopconf = TMC2660_SET_TBL(config->tbl) |
                        TMC2660_SET_HEND(hend_reg) |
                        TMC2660_SET_HSTRT(config->hstrt) |
                        TMC2660_SET_TOFF(config->toff);
    tmc2660_writeRegister(icID, TMC2660_CHOPCONF, chopconf);

    // 2. Configure SMARTEN (old API: 0x000A0000, CoolStep disabled)
    tmc2660_writeRegister(icID, TMC2660_SMARTEN, 0);

    // 3. Configure SGCSCONF (old API: 0x000C000A)
    uint8_t sgt = (uint8_t)(config->stallThreshold & 0x7F);
    uint32_t sgcsconf = TMC2660_SET_CS(cs) |
                        TMC2660_SET_SGT(sgt) |
                        TMC2660_SET_SFILT(config->stallFilter ? 1 : 0);
    tmc2660_writeRegister(icID, TMC2660_SGCSCONF, sgcsconf);

    // 4. Configure DRVCONF (old API: 0x000E00A1)
    // SDOFF=1: SPI mode (motion control via SPI, not Step/Dir)
    // VSENSE=0: High sense resistor voltage range (V_fs=0.310V)
    // RDSEL=2: StallGuard2 value and CoolStep current level in response
    uint32_t drvconf = TMC2660_SET_RDSEL(2) | TMC2660_SET_VSENSE(0) | TMC2660_SET_SDOFF(1) | 0x01;
    tmc2660_writeRegister(icID, TMC2660_DRVCONF, drvconf);

    // note: in SPI mode (SDOFF=1), DRVCTRL is not sent
    // microstepping is controlled by the TMC4361A STEP_CONF register

    return true;
}

// ========================================================================
// TMC2240 driver initialization
// ========================================================================
static bool motor_initDriver_TMC2240(uint8_t icID, const MotorConfig *config)
{
    // cache the TMC2240-specific parameters (needed at runtime by setRunCurrent / enableDriver)
    motorParams[icID].currentRange = config->currentRange;
    motorParams[icID].toff = config->toff;

    // note: SPI_OUTPUT_FORMAT=0x0D stays active and must not be disabled (format=0 turns off the SPI output hardware)
    // Cover writes and the automatic SPI output are serialized by the TMC4361A hardware, so writes should be reliable
    // Cover reads may be disturbed by the automatic SPI (read-back values are unreliable), but this does not affect configuration writes

    // 1. DRV_CONF - set CURRENT_RANGE and SLOPE_CONTROL
    // CURRENT_RANGE: 0=1A, 1=2A, 2=3A, 3=3A
    // SLOPE_CONTROL: 1 = 200V/us (default)
    uint32_t drvConf = ((uint32_t)(config->currentRange & 0x03) << 0) |
                       (1 << 4);  // SLOPE_CONTROL=1
    tmc2240_writeRegister(icID, TMC2240_DRV_CONF, drvConf);

    // 2. GLOBAL_SCALER (0=256, i.e. full scale; 32-255 scaling)
    tmc2240_writeRegister(icID, TMC2240_GLOBAL_SCALER,
                          config->globalScaler == 0 ? 0 : config->globalScaler);

    // 3. compute the current -- I_FS based on CURRENT_RANGE
    uint8_t irun = calculateCurrentScale_TMC2240(config->runCurrentMA,
                                                  config->currentRange,
                                                  config->globalScaler);
    uint8_t ihold = (uint8_t)(irun * config->holdCurrentRatio);
    if (ihold > 31) ihold = 31;

    // 4. IHOLD_IRUN - current configuration
    uint32_t iholdIrun = ((uint32_t)ihold << TMC2240_IHOLD_SHIFT) |
                         ((uint32_t)irun << TMC2240_IRUN_SHIFT) |
                         ((uint32_t)(config->iholdDelay & 0x0F) << TMC2240_IHOLDDELAY_SHIFT);
    tmc2240_writeRegister(icID, TMC2240_IHOLD_IRUN, iholdIrun);

    // 5. TPOWERDOWN - hold-current delay (default 10)
    tmc2240_writeRegister(icID, TMC2240_TPOWERDOWN, 10);

    // 6. GCONF - global configuration
    uint32_t gconf = 0x00000000;
    // direct_mode (bit 16): the TMC4361A directly controls the coil current via SPI (DIRECT_MODE register)
    // must be enabled, otherwise the TMC2240 waits for Step/Dir signals and does not respond to SPI current commands
    gconf |= TMC2240_DIRECT_MODE_MASK;  // bit 16: direct coil current control via SPI
    // note: SHAFT (bit 4) is ineffective in direct_mode; the direction is controlled by the TMC4361A REVERSE_MOTOR_DIR
    if (config->enableStealthChop) {
        gconf |= TMC2240_EN_PWM_MODE_MASK;  // bit 2: StealthChop enable
    }
    tmc2240_writeRegister(icID, TMC2240_GCONF, gconf);

    // 7. CHOPCONF - Chopper configuration (includes the MRES microstepping setting)
    // MRES encoding: 0=256, 1=128, 2=64, ..., 8=full step (consistent with TMC4361A STEP_CONF)
    uint8_t mresVal = config->microstepRes;  // passed in by Axis::begin(), usually 0 (256 microsteps)

    uint32_t chopconf = ((uint32_t)(config->toff & 0x0F) << TMC2240_TOFF_SHIFT) |
                        ((uint32_t)(config->hstrt & 0x07) << TMC2240_HSTRT_TFD210_SHIFT) |
                        ((uint32_t)((config->hend + 3) & 0x0F) << TMC2240_HEND_OFFSET_SHIFT) |
                        ((uint32_t)(config->tbl & 0x03) << TMC2240_TBL_SHIFT) |
                        ((uint32_t)(mresVal & 0x0F) << TMC2240_MRES_SHIFT) |
                        (config->interpolation ? (1 << TMC2240_INTPOL_SHIFT) : 0);
    tmc2240_writeRegister(icID, TMC2240_CHOPCONF, chopconf);

    // 8. PWMCONF - StealthChop PWM configuration
    if (config->enableStealthChop) {
        // defaults: pwm_autoscale=1, pwm_autograd=1
        tmc2240_writeRegister(icID, TMC2240_PWMCONF, 0xC44C001E);
    }

    // clear the GSTAT reset flag
    tmc2240_writeRegister(icID, TMC2240_GSTAT, 0x07);
    // ---- END DEBUG ----

    return true;
}

void motor_configLimitSwitches(uint8_t icID, const LimitConfig *config)
{
    if (icID >= MOTOR_IC_COUNT || config == NULL)
        return;

    // Read current REFERENCE_CONF to preserve other bits (consistent with the old API setBits behavior)
    uint32_t refConf = tmc4361A_readRegister(icID, TMC4361A_REFERENCE_CONF);

    // Left switch configuration
    // 2026-06-06: the hard-stop enable (STOP_LEFT_EN) is decoupled from "polarity / position latch".
    // the polarity (POL_STOP_LEFT) and latch (LATCH_X_ON_ACTIVE_L) are always written per the config, independent of enable --
    // so after disabling the chip hard stop (enableLeft=false, using software stop), STATUS STOPL_ACTIVE_F still reflects the
    // switch level correctly per polarity (software poll needs it), and the X_LATCH used for homing retract-to-safe still works.
    // the old implementation gated both inside if(enableLeft) -> when enable=false the polarity was lost (read inverted) and the latch failed.
    // behavior for enable=true axes is completely unchanged (no regression).
    if (config->enableLeft)
        refConf |= TMC4361A_STOP_LEFT_EN_MASK;   // bit 0
    else
        refConf &= ~TMC4361A_STOP_LEFT_EN_MASK;
    if (config->leftPolarity)
        refConf |= TMC4361A_POL_STOP_LEFT_MASK;  // bit 2
    else
        refConf &= ~TMC4361A_POL_STOP_LEFT_MASK;
    refConf |= TMC4361A_LATCH_X_ON_ACTIVE_L_MASK;  // bit 11

    // Right switch configuration (same as above: decoupled)
    if (config->enableRight)
        refConf |= TMC4361A_STOP_RIGHT_EN_MASK;  // bit 1
    else
        refConf &= ~TMC4361A_STOP_RIGHT_EN_MASK;
    if (config->rightPolarity)
        refConf |= TMC4361A_POL_STOP_RIGHT_MASK;  // bit 3
    else
        refConf &= ~TMC4361A_POL_STOP_RIGHT_MASK;
    refConf |= TMC4361A_LATCH_X_ON_ACTIVE_R_MASK;  // bit 13

    // Invert stop direction: swap the logical meaning of the left/right limit switches
    // consistent with the master-branch old API (tmc4361A_enableLimitSwitch):
    //   if (flipped != 0) setBits(INVERT_STOP_DIRECTION_MASK)
    if (config->leftFlipped || config->rightFlipped) {
        refConf |= TMC4361A_INVERT_STOP_DIRECTION_MASK;  // bit 4
    } else {
        refConf &= ~TMC4361A_INVERT_STOP_DIRECTION_MASK;
    }

    // note: do not set SOFT_STOP_EN (bit 5)
    // SOFT_STOP_EN=1 makes the chip enter an internal soft-stop state machine when a limit triggers,
    // locking RAMPMODE/VMAX/XTARGET writes and causing homing stop to fail.
    // kept consistent with the master-branch old API (tmc4361A_enableLimitSwitch): hard stop.

    tmc4361A_writeRegister(icID, TMC4361A_REFERENCE_CONF, refConf);
}

void motor_setHardwareStopEnable(uint8_t icID, uint8_t side, bool enable)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    uint32_t refConf = tmc4361A_readRegister(icID, TMC4361A_REFERENCE_CONF);

    uint32_t mask = 0;
    if (side & 0x01)  // LEFT_SW
        mask |= TMC4361A_STOP_LEFT_EN_MASK;
    if (side & 0x02)  // RGHT_SW
        mask |= TMC4361A_STOP_RIGHT_EN_MASK;

    if (enable) {
        refConf |= mask;
    } else {
        refConf &= ~mask;
    }

    tmc4361A_writeRegister(icID, TMC4361A_REFERENCE_CONF, refConf);
}

// ============================================================================
// Motion Control
// ============================================================================

void motor_moveToPosition(uint8_t icID, float positionMM)
{
    int32_t microsteps = motor_mmToMicrosteps(icID, positionMM);
    motor_moveToMicrosteps(icID, microsteps);
}

void motor_moveByDistance(uint8_t icID, float distanceMM)
{
    int32_t current = motor_getPositionMicrosteps(icID);
    int32_t delta = motor_mmToMicrosteps(icID, distanceMM);
    motor_moveToMicrosteps(icID, current + delta);
}

bool motor_moveToMicrosteps(uint8_t icID, int32_t position)
{
    if (icID >= MOTOR_IC_COUNT)
        return false;

    // ========================================================================
    // an implementation exactly matching the old API tmc4361A_moveTo
    // ========================================================================

    // state restore: call sRampInit only when velocity_mode == true
    // as in the old API: if(tmc4361A->velocity_mode) { tmc4361A_sRampInit(); velocity_mode = false; }
    if (motorParams[icID].velocity_mode) {
        // ====================================================================
        // sRampInit-equivalent implementation (exactly the same as the old API tmc4361A_sRampInit)
        // ====================================================================

        // 1. RAMPMODE: use setBits to set position mode + S-shaped ramp
        // old API: tmc4361A_setBits(tmc4361A, TMC4361A_RAMPMODE, TMC4361A_RAMP_POSITION | TMC4361A_RAMP_SSHAPE);
        uint32_t rampMode = tmc4361A_readRegister(icID, TMC4361A_RAMPMODE);
        rampMode |= (TMC4361A_RAMP_POSITION | TMC4361A_RAMP_SSHAPE);
        tmc4361A_writeRegister(icID, TMC4361A_RAMPMODE, rampMode);

        // 2. restore the USE_ASTART_AND_VSTART setting (decided by the astart config)
        uint32_t generalConf = tmc4361A_readRegister(icID, TMC4361A_GENERAL_CONF);
        if (motorParams[icID].astart > 0) {
            generalConf |= TMC4361A_USE_ASTART_AND_VSTART_MASK;
        } else {
            generalConf &= ~TMC4361A_USE_ASTART_AND_VSTART_MASK;
        }
        tmc4361A_writeRegister(icID, TMC4361A_GENERAL_CONF, generalConf);

        // 3. rewrite all ramp parameters (consistent with the old API sRampInit)
        tmc4361A_writeRegister(icID, TMC4361A_BOW1, motorParams[icID].bow1);
        tmc4361A_writeRegister(icID, TMC4361A_BOW2, motorParams[icID].bow2);
        tmc4361A_writeRegister(icID, TMC4361A_BOW3, motorParams[icID].bow3);
        tmc4361A_writeRegister(icID, TMC4361A_BOW4, motorParams[icID].bow4);
        tmc4361A_writeRegister(icID, TMC4361A_AMAX, motorParams[icID].amax);
        tmc4361A_writeRegister(icID, TMC4361A_DMAX, motorParams[icID].dmax);
        tmc4361A_writeRegister(icID, TMC4361A_ASTART, motorParams[icID].astart);
        tmc4361A_writeRegister(icID, TMC4361A_DFINAL, motorParams[icID].dfinal);
        tmc4361A_writeRegister(icID, TMC4361A_VMAX, motorParams[icID].vmax);

        // 4. clear the velocity_mode flag
        motorParams[icID].velocity_mode = false;

    }

    // unconditionally write back VMAX (consistent with the old API tmc4361A_moveTo)
    // the old API writes VMAX on every moveTo, ensuring that even if it is zeroed by something external (e.g. motor_stop)
    // the correct speed is restored
    tmc4361A_writeRegister(icID, TMC4361A_VMAX, motorParams[icID].vmax);

    // ========================================================================
    // write the target position (consistent with the old API tmc4361A_moveTo)
    // ========================================================================

    // virtual-limit recovery (TMC4361A Programming Guide section 10.4):
    // "precondition: the stop switch is no longer active OR the stop switch is disabled. Then clear the events."
    //
    // strategy: disable the activated virtual_limit_en -> clear events -> write XTARGET.
    // do not restore the enable bit here: it can only be restored after the motor leaves the boundary (managed by the Axis layer).
    // if restored immediately, XACTUAL is still at the boundary -> VSTOP re-triggers immediately -> the motor cannot move.
    uint32_t status = tmc4361A_readRegister(icID, TMC4361A_STATUS);
    bool vstopL = status & TMC4361A_VSTOPL_ACTIVE_F_MASK;
    bool vstopR = status & TMC4361A_VSTOPR_ACTIVE_F_MASK;
    bool vstopWasActive = vstopL || vstopR;

    if (vstopL || vstopR) {
        uint32_t refConf = tmc4361A_readRegister(icID, TMC4361A_REFERENCE_CONF);

        // disable the activated virtual limit (to satisfy the recovery precondition)
        if (vstopL)
            refConf &= ~TMC4361A_VIRTUAL_LEFT_LIMIT_EN_MASK;
        if (vstopR)
            refConf &= ~TMC4361A_VIRTUAL_RIGHT_LIMIT_EN_MASK;

        tmc4361A_writeRegister(icID, TMC4361A_REFERENCE_CONF, refConf);
        tmc4361A_readRegister(icID, TMC4361A_EVENTS);  // clear events (recovery action)

        tmc4361A_writeRegister(icID, TMC4361A_XTARGET, position);
        tmc4361A_readRegister(icID, TMC4361A_EVENTS);  // clear any new events
    } else {
        // normal path (no virtual limit active)
        tmc4361A_readRegister(icID, TMC4361A_EVENTS);
        tmc4361A_writeRegister(icID, TMC4361A_XTARGET, position);
        tmc4361A_readRegister(icID, TMC4361A_EVENTS);
    }

    // Read X_ACTUAL to get it to refresh
    tmc4361A_readRegister(icID, TMC4361A_XACTUAL);

    // Return vstop state so axis layer can skip its own STATUS read
    // (saves ~10-20µs SPI per move; 2026-05-18 acquisition optimization #2.2)
    return vstopWasActive;
}

void motor_rotateVelocity(uint8_t icID, float velocityMM)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    // Switch to velocity mode
    uint32_t rampMode = tmc4361A_readRegister(icID, TMC4361A_RAMPMODE);
    rampMode &= ~TMC4361A_RAMP_POSITION;  // Clear position mode bit
    tmc4361A_writeRegister(icID, TMC4361A_RAMPMODE, rampMode);

    // Set velocity
    int32_t vel = motor_velocityMMToInternal(icID, velocityMM);
    tmc4361A_writeRegister(icID, TMC4361A_VMAX, vel >= 0 ? vel : -vel);

    // Direction is determined by sign of VMAX in velocity mode
    if (vel < 0) {
        // For negative velocity, we need to handle direction
        // This depends on the specific implementation
    }
}

void motor_stop(uint8_t icID)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    // Set VMAX to 0 for smooth stop
    tmc4361A_writeRegister(icID, TMC4361A_VMAX, 0);
}

void motor_emergencyStop(uint8_t icID)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    // Set target to current position for immediate stop
    int32_t current = tmc4361A_readRegister(icID, TMC4361A_XACTUAL);
    tmc4361A_writeRegister(icID, TMC4361A_XTARGET, current);
    tmc4361A_writeRegister(icID, TMC4361A_VMAX, 0);
}

// ============================================================================
// Status Query
// ============================================================================

bool motor_isTargetReached(uint8_t icID)
{
    if (icID >= MOTOR_IC_COUNT)
        return true;

    // equivalent to legacy Squid tmc4361A_isRunning (negated): target reached AND velocity zero AND ramp not changing
    // - TARGET_REACHED_F (bit 0): XACTUAL == XTARGET
    // - VEL_STATE_F (bits 3-4): 00 = velocity has reached zero (non-zero = +/- velocity)
    // - RAMP_STATE_F (bits 5-6): 00 = ramp idle (non-zero = acc/dec/const)
    // reads multiple bits in a single STATUS read at no extra SPI cost; prevents the edge case at the end of the chip ramp where "XACTUAL briefly == XTARGET
    // but the speed has not reached zero" is misjudged
    uint32_t status = tmc4361A_readRegister(icID, TMC4361A_STATUS);
    return (status & TMC4361A_TARGET_REACHED_F_MASK) &&
           !(status & (TMC4361A_VEL_STATE_F_MASK | TMC4361A_RAMP_STATE_F_MASK));
}

bool motor_isRunning(uint8_t icID)
{
    if (icID >= MOTOR_IC_COUNT)
        return false;

    int32_t velocity = tmc4361A_readRegister(icID, TMC4361A_VACTUAL);
    return velocity != 0;
}

float motor_getPositionMM(uint8_t icID)
{
    int32_t microsteps = motor_getPositionMicrosteps(icID);
    return motor_microstepsToMM(icID, microsteps);
}

int32_t motor_getPositionMicrosteps(uint8_t icID)
{
    if (icID >= MOTOR_IC_COUNT)
        return 0;

    return tmc4361A_readRegister(icID, TMC4361A_XACTUAL);
}

int32_t motor_getTargetMicrosteps(uint8_t icID)
{
    if (icID >= MOTOR_IC_COUNT)
        return 0;

    return tmc4361A_readRegister(icID, TMC4361A_XTARGET);
}

float motor_getVelocityMM(uint8_t icID)
{
    int32_t velInternal = motor_getVelocityInternal(icID);
    return motor_velocityInternalToMM(icID, velInternal);
}

int32_t motor_getVelocityInternal(uint8_t icID)
{
    if (icID >= MOTOR_IC_COUNT)
        return 0;

    return tmc4361A_readRegister(icID, TMC4361A_VACTUAL);
}

uint8_t motor_readLimitSwitches(uint8_t icID)
{
    if (icID >= MOTOR_IC_COUNT)
        return 0;

    uint32_t status = tmc4361A_readRegister(icID, TMC4361A_STATUS);

    // STOPL_ACTIVE_F is bit 7 (0x80), STOPR_ACTIVE_F is bit 8 (0x100)
    // Mask and shift to get bits 0 and 1
    status &= (TMC4361A_STOPL_ACTIVE_F_MASK | TMC4361A_STOPR_ACTIVE_F_MASK);
    status >>= TMC4361A_STOPL_ACTIVE_F_SHIFT;

    return (uint8_t)(status & 0x03);
}

uint32_t motor_readStatus(uint8_t icID)
{
    if (icID >= MOTOR_IC_COUNT)
        return 0;

    return tmc4361A_readRegister(icID, TMC4361A_STATUS);
}

uint32_t motor_readEvents(uint8_t icID)
{
    if (icID >= MOTOR_IC_COUNT)
        return 0;

    return tmc4361A_readRegister(icID, TMC4361A_EVENTS);
}

// ============================================================================
// Parameter Setting
// ============================================================================

void motor_setMaxVelocity(uint8_t icID, float velocityMM)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    int32_t vel = motor_velocityMMToInternal(icID, velocityMM);
    motorParams[icID].vmax = vel;  // saved for position-mode restore (consistent with the old API rampParam[VMAX_IDX])

    // consistent with the old API tmc4361A_setMaxSpeed: automatically recompute the BOW parameters
    motor_adjustBows(icID);

    // write to hardware (sRampInit-equivalent)
    tmc4361A_writeRegister(icID, TMC4361A_VMAX, motorParams[icID].vmax);
    tmc4361A_writeRegister(icID, TMC4361A_BOW1, motorParams[icID].bow1);
    tmc4361A_writeRegister(icID, TMC4361A_BOW2, motorParams[icID].bow2);
    tmc4361A_writeRegister(icID, TMC4361A_BOW3, motorParams[icID].bow3);
    tmc4361A_writeRegister(icID, TMC4361A_BOW4, motorParams[icID].bow4);
}

void motor_resetRampMode(uint8_t icID)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    // read the current RAMPMODE
    [[maybe_unused]] uint32_t rampModeBefore = tmc4361A_readRegister(icID, TMC4361A_RAMPMODE);

    // reset RAMPMODE to position mode + S-shaped ramp (consistent with initialization)
    // this must be called after a RESET command or a hardware-limit trigger
    uint32_t rampMode = 0x06;  // S-shaped position mode
    tmc4361A_writeRegister(icID, TMC4361A_RAMPMODE, rampMode);

    // read the RAMPMODE after setting
    [[maybe_unused]] uint32_t rampModeAfter = tmc4361A_readRegister(icID, TMC4361A_RAMPMODE);

    // debug output
    DEBUG_PRINT("motor_resetRampMode: icID=");
    DEBUG_PRINT(icID);
    DEBUG_PRINT(" RAMPMODE: 0x");
    DEBUG_PRINTF(rampModeBefore, HEX);
    DEBUG_PRINT(" -> 0x");
    DEBUG_PRINTLNF(rampModeAfter, HEX);
}

void motor_setMaxAcceleration(uint8_t icID, float accelerationMM)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    uint32_t accel = motor_accelMMToInternal(icID, accelerationMM);
    motorParams[icID].amax = accel;  // cache (consistent with the old API rampParam[AMAX_IDX])
    motorParams[icID].dmax = accel;  // consistent with the old API: DMAX = AMAX

    // consistent with the old API tmc4361A_setMaxAcceleration: automatically recompute the BOW parameters
    motor_adjustBows(icID);

    // write to hardware (sRampInit-equivalent)
    tmc4361A_writeRegister(icID, TMC4361A_AMAX, motorParams[icID].amax);
    tmc4361A_writeRegister(icID, TMC4361A_DMAX, motorParams[icID].dmax);
    tmc4361A_writeRegister(icID, TMC4361A_BOW1, motorParams[icID].bow1);
    tmc4361A_writeRegister(icID, TMC4361A_BOW2, motorParams[icID].bow2);
    tmc4361A_writeRegister(icID, TMC4361A_BOW3, motorParams[icID].bow3);
    tmc4361A_writeRegister(icID, TMC4361A_BOW4, motorParams[icID].bow4);
}

void motor_setMaxDeceleration(uint8_t icID, float decelerationMM)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    uint32_t decel = motor_accelMMToInternal(icID, decelerationMM);
    motorParams[icID].dmax = decel;  // cache (consistent with the old API rampParam[DMAX_IDX])
    tmc4361A_writeRegister(icID, TMC4361A_DMAX, decel);
}

void motor_setCurrentPosition(uint8_t icID, float positionMM)
{
    int32_t microsteps = motor_mmToMicrosteps(icID, positionMM);
    motor_setCurrentPositionMicrosteps(icID, microsteps);
}

void motor_setCurrentPositionMicrosteps(uint8_t icID, int32_t position)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    // consistent with the old API tmc4361A_setCurrentPosition behavior:
    // 1. stop the motor first (set VMAX=0)
    // 2. set XACTUAL and XTARGET
    // 3. set velocity_mode=true; VMAX will be restored on the next moveToMicrosteps
    tmc4361A_writeRegister(icID, TMC4361A_VMAX, 0);
    tmc4361A_writeRegister(icID, TMC4361A_XACTUAL, position);
    tmc4361A_writeRegister(icID, TMC4361A_XTARGET, position);
    tmc4361A_writeRegister(icID, TMC4361A_ENC_POS, position);  // sync the encoder position
    motorParams[icID].velocity_mode = true;
}

void motor_setMicrosteps(uint8_t icID, uint16_t microsteps)
{
    if (icID >= MOTOR_IC_COUNT || !motorParams[icID].initialized)
        return;

    // update the cache
    motorParams[icID].microsteps = microsteps;
    motorParams[icID].stepsPerMM = (float)(motorParams[icID].fullStepsPerRev * microsteps) / motorParams[icID].screwPitchMM;

    // compute the MSTEP_PER_FS value: 256->0, 128->1, ..., 1->8
    uint16_t mstep = microsteps;
    uint8_t mstepVal = 0;
    if (mstep > 0 && (mstep & (mstep - 1)) == 0 && mstep <= 256) {
        uint8_t bitsSet = 0;
        while (mstep > 0) {
            bitsSet++;
            mstep >>= 1;
        }
        mstepVal = 9 - bitsSet;
    }

    // combine STEP_CONF: MSTEP_PER_FS (bit 0-3) + FS_PER_REV (bit 4-15)
    uint32_t stepConf = (mstepVal & TMC4361A_MSTEP_PER_FS_MASK) |
                        ((uint32_t)motorParams[icID].fullStepsPerRev << TMC4361A_FS_PER_REV_SHIFT);
    tmc4361A_writeRegister(icID, TMC4361A_STEP_CONF, stepConf);

    // TMC2240: also update CHOPCONF.MRES (the TMC2240's MRES must match the TMC4361A's STEP_CONF)
    // note: cannot use tmc2240_fieldWrite (read-modify-write), because SPI_OUTPUT_FORMAT=0x0D
    // the automatic SPI output disturbs Cover reads; an unreliable read-back would corrupt CHOPCONF (TOFF=0 -> driver off).
    // use the shadow register to get the last-written CHOPCONF value instead.
    if (motorParams[icID].driverType == DRIVER_TMC2240) {
        uint32_t chopconf = (uint32_t)tmc2240_shadowRegister[icID][TMC2240_CHOPCONF];
        chopconf = (chopconf & ~((uint32_t)0x0F << TMC2240_MRES_SHIFT)) |
                   ((uint32_t)(mstepVal & 0x0F) << TMC2240_MRES_SHIFT);
        tmc2240_writeRegister(icID, TMC2240_CHOPCONF, chopconf);
    }
}

void motor_setRunCurrent(uint8_t icID, float currentMA)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    if (motorParams[icID].driverType == DRIVER_TMC2240) {
        uint8_t irun = calculateCurrentScale_TMC2240(currentMA,
                                                      motorParams[icID].currentRange, 0);
        tmc2240_setRunCurrent(icID, irun);
    } else {
        float rSense = motorParams[icID].rSense > 0 ? motorParams[icID].rSense : 0.22f;
        uint8_t cs = calculateCurrentScale(currentMA, rSense);
        tmc2660_setRunCurrent(icID, cs);
    }
}

void motor_enableDriver(uint8_t icID, bool enable)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    if (motorParams[icID].driverType == DRIVER_TMC2240) {
        if (enable) {
            // use the cached TOFF to restore the driver (rather than a hardcoded default)
            uint32_t chopconf = tmc2240_readRegister(icID, TMC2240_CHOPCONF);
            uint8_t currentToff = (chopconf & TMC2240_TOFF_MASK) >> TMC2240_TOFF_SHIFT;
            if (currentToff == 0) {
                uint8_t toff = motorParams[icID].toff > 0 ? motorParams[icID].toff : 3;
                tmc2240_fieldWrite(icID, TMC2240_TOFF_FIELD, toff);
            }
        } else {
            tmc2240_fieldWrite(icID, TMC2240_TOFF_FIELD, 0);
        }
    } else {
        tmc2660_enableDriver(icID, enable);
    }
}

// ============================================================================
// Unit Conversion
// ============================================================================

int32_t motor_mmToMicrosteps(uint8_t icID, float mm)
{
    if (icID >= MOTOR_IC_COUNT || !motorParams[icID].initialized)
        return 0;

    return (int32_t)(mm * motorParams[icID].stepsPerMM);
}

float motor_microstepsToMM(uint8_t icID, int32_t microsteps)
{
    if (icID >= MOTOR_IC_COUNT || !motorParams[icID].initialized)
        return 0.0f;

    return (float)microsteps / motorParams[icID].stepsPerMM;
}

int32_t motor_velocityMMToInternal(uint8_t icID, float velocityMM)
{
    if (icID >= MOTOR_IC_COUNT || !motorParams[icID].initialized)
        return 0;

    // TMC4361A velocity format: multiply by 2^8 (256) to account for 8 decimal places
    // Formula matches old API: (1 << 8) * mm * stepsPerMM
    int32_t velocity = (int32_t)((1 << 8) * velocityMM * motorParams[icID].stepsPerMM);

    return velocity;
}

float motor_velocityInternalToMM(uint8_t icID, int32_t velocityInternal)
{
    if (icID >= MOTOR_IC_COUNT || !motorParams[icID].initialized)
        return 0.0f;

    // Reverse of above
    float velocityPPS = (float)velocityInternal * (float)motorParams[icID].clockFrequency / 65536.0f;
    return velocityPPS / motorParams[icID].stepsPerMM;
}

uint32_t motor_accelMMToInternal(uint8_t icID, float accelMM)
{
    if (icID >= MOTOR_IC_COUNT || !motorParams[icID].initialized)
        return 0;

    // TMC4361A acceleration format: multiply by 2^2 (4) to account for 2 decimal places
    // Formula matches old API: (1 << 2) * mm * stepsPerMM
    uint32_t accel = (uint32_t)((1 << 2) * accelMM * motorParams[icID].stepsPerMM);

    return accel;
}

// ============================================================================
// Homing
// ============================================================================

void motor_startHoming(uint8_t icID, int8_t direction, float velocityMM)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    // Configure for velocity mode toward limit switch
    motor_rotateVelocity(icID, direction > 0 ? velocityMM : -velocityMM);
}

void motor_setHomePosition(uint8_t icID, float positionMM)
{
    motor_setCurrentPosition(icID, positionMM);
}

void motor_enableHomingLimit(uint8_t icID, uint8_t polarity, uint8_t whichSwitch,
                              int32_t safetyMarginMicrosteps)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    // Read current REFERENCE_CONF
    uint32_t refConf = tmc4361A_readRegister(icID, TMC4361A_REFERENCE_CONF);

    // Configure HOME_EVENT and home switch (consistent with the old API tmc4361A_enableHomingLimit)
    if (whichSwitch == 0x01) {  // Left switch (LEFT_SW)
        if (polarity != 0) {
            // Active high: HOME_REF = 0 indicates positive direction
            refConf |= (0b1100 << TMC4361A_HOME_EVENT_SHIFT);
        } else {
            // Active low: HOME_REF = 0 indicates negative direction
            refConf |= (0b0011 << TMC4361A_HOME_EVENT_SHIFT);
        }
        // Use stop left as home
        refConf |= TMC4361A_STOP_LEFT_IS_HOME_MASK;
    } else {  // Right switch (RGHT_SW)
        if (polarity != 0) {
            // Active high
            refConf |= (0b0011 << TMC4361A_HOME_EVENT_SHIFT);
        } else {
            // Active low
            refConf |= (0b1100 << TMC4361A_HOME_EVENT_SHIFT);
        }
        // Use stop right as home (bit 15)
        refConf |= (1 << 15);  // TMC4361A_STOP_RIGHT_IS_HOME
    }

    tmc4361A_writeRegister(icID, TMC4361A_REFERENCE_CONF, refConf);

    // Set HOME_SAFETY_MARGIN
    tmc4361A_writeRegister(icID, TMC4361A_HOME_SAFETY_MARGIN, safetyMarginMicrosteps);
}

// ============================================================================
// Soft Limit Implementation
// ============================================================================

void motor_setSoftLimits(uint8_t icID, int32_t lowerLimitMicrosteps, int32_t upperLimitMicrosteps)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    // Set virtual stop positions
    tmc4361A_writeRegister(icID, TMC4361A_VIRT_STOP_LEFT, lowerLimitMicrosteps);
    tmc4361A_writeRegister(icID, TMC4361A_VIRT_STOP_RIGHT, upperLimitMicrosteps);
}

void motor_enableSoftLimits(uint8_t icID, bool enableLower, bool enableUpper)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    // Read current REFERENCE_CONF
    uint32_t refConf = tmc4361A_readRegister(icID, TMC4361A_REFERENCE_CONF);

    // Configure virtual stop enables (using the official macro definitions)
    if (enableLower) {
        refConf |= TMC4361A_VIRTUAL_LEFT_LIMIT_EN_MASK;   // bit 6
        // Set VIRT_STOP_MODE = 1 for hard stop (consistent with the old API)
        refConf |= (1 << TMC4361A_VIRT_STOP_MODE_SHIFT);  // bit 8
    } else {
        refConf &= ~TMC4361A_VIRTUAL_LEFT_LIMIT_EN_MASK;
    }

    if (enableUpper) {
        refConf |= TMC4361A_VIRTUAL_RIGHT_LIMIT_EN_MASK;  // bit 7
        // Set VIRT_STOP_MODE = 1 for hard stop (consistent with the old API)
        refConf |= (1 << TMC4361A_VIRT_STOP_MODE_SHIFT);  // bit 8
    } else {
        refConf &= ~TMC4361A_VIRTUAL_RIGHT_LIMIT_EN_MASK;
    }

    tmc4361A_writeRegister(icID, TMC4361A_REFERENCE_CONF, refConf);
}

// ============================================================================
// Advanced Configuration Implementation
// ============================================================================

void motor_initABNEncoder(uint8_t icID, uint32_t transitions_per_rev,
                           uint8_t filter_wait_time, uint8_t filter_exponent,
                           uint16_t filter_vmean, bool invert_dir)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    // Set encoder resolution
    tmc4361A_writeRegister(icID, TMC4361A_ENC_IN_RES, transitions_per_rev);

    // 2026-05-25 reverted the always-on debug print: legacy Squid software has no mixed ASCII/binary
    // parsing and would treat the "ENC_INIT ..." text as response-packet bytes, causing checksum errors + misaligned acks for subsequent commands
    // -> cmd 7 (HOME_OR_ZERO) timeout abort. Reverted to DEBUG_PRINT (compiled out under NDEBUG).
    DEBUG_PRINT("ENC_INIT icID=");
    DEBUG_PRINT(icID);
    DEBUG_PRINT(" wrote_ENC_IN_RES=");
    DEBUG_PRINT(transitions_per_rev);
    DEBUG_PRINT(" readback_ENC_CONST=");
    DEBUG_PRINTLN((uint32_t)tmc4361A_readRegister(icID, TMC4361A_ENC_IN_RES));

    // Set encoder velocity mean filter:
    // ENC_VMEAN_FILTER = wait_time | (filter_exp << 8) | (vmean_int << 16)
    uint32_t filterVal = (uint32_t)filter_wait_time
                       | ((uint32_t)filter_exponent << 8)
                       | ((uint32_t)filter_vmean << 16);
    tmc4361A_writeRegister(icID, TMC4361A_ENC_VMEAN_FILTER, filterVal);

    // disable differential encoder input (single-ended ABN encoder)
    uint32_t gen_conf = tmc4361A_readRegister(icID, TMC4361A_GENERAL_CONF);
    gen_conf |= TMC4361A_DIFF_ENC_IN_DISABLE_MASK;  // bit 12 = 1
    tmc4361A_writeRegister(icID, TMC4361A_GENERAL_CONF, gen_conf);

    // Set or clear INVERT_ENC_DIR bit (bit 29 of ENC_IN_CONF)
    uint32_t enc_conf = tmc4361A_readRegister(icID, TMC4361A_ENC_IN_CONF);
    if (invert_dir) {
        enc_conf |= TMC4361A_INVERT_ENC_DIR_MASK;
    } else {
        enc_conf &= ~TMC4361A_INVERT_ENC_DIR_MASK;
    }
    tmc4361A_writeRegister(icID, TMC4361A_ENC_IN_CONF, enc_conf);
}

void motor_initPID(uint8_t icID, uint32_t target_tolerance, uint32_t pid_tolerance,
                    uint32_t pid_p, uint32_t pid_i, uint32_t pid_d,
                    uint32_t pid_dclip, uint32_t pid_iclip, uint8_t pid_d_clkdiv)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    // Closed-loop target tolerance
    tmc4361A_writeRegister(icID, TMC4361A_CL_TR_TOLERANCE, target_tolerance);
    // PID tolerance
    tmc4361A_writeRegister(icID, TMC4361A_PID_TOLERANCE, pid_tolerance);
    // PID gains (24-bit each)
    tmc4361A_writeRegister(icID, TMC4361A_PID_P, pid_p & 0xFFFFFF);
    tmc4361A_writeRegister(icID, TMC4361A_PID_I, pid_i & 0xFFFFFF);
    tmc4361A_writeRegister(icID, TMC4361A_PID_D, pid_d & 0xFFFFFF);
    // PID velocity clip
    tmc4361A_writeRegister(icID, TMC4361A_PID_DV_CLIP, pid_dclip);
    // PID integral clip + derivative clock divider
    // PID_I_CLIP_WR (0x5D) = iclip | (d_clkdiv << 16)
    tmc4361A_writeRegister(icID, TMC4361A_PID_I_CLIP,
                           pid_iclip | ((uint32_t)pid_d_clkdiv << 16));
}

void motor_enablePID(uint8_t icID)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    // Set REGULATION_MODUS bits (22-23) of ENC_IN_CONF to 0b10 (PID via BPG0)
    uint32_t enc_conf = tmc4361A_readRegister(icID, TMC4361A_ENC_IN_CONF);
    enc_conf &= ~TMC4361A_REGULATION_MODUS_MASK;
    enc_conf |= (0x02 << TMC4361A_REGULATION_MODUS_SHIFT);
    tmc4361A_writeRegister(icID, TMC4361A_ENC_IN_CONF, enc_conf);
}

void motor_disablePID(uint8_t icID)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    // Clear REGULATION_MODUS bits (22-23) of ENC_IN_CONF to disable PID
    uint32_t enc_conf = tmc4361A_readRegister(icID, TMC4361A_ENC_IN_CONF);
    enc_conf &= ~TMC4361A_REGULATION_MODUS_MASK;
    tmc4361A_writeRegister(icID, TMC4361A_ENC_IN_CONF, enc_conf);
}

void motor_configStallGuard(uint8_t icID, int8_t threshold, bool filterEnable, bool stopOnStall)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    if (motorParams[icID].driverType == DRIVER_TMC2240) {
        // TMC2240: StallGuard4
        // SGT is in bits [22:16] of COOLCONF (0x6D)
        tmc2240_fieldWrite(icID, TMC2240_SGT_FIELD, (uint32_t)(threshold & 0x7F));
        // SG4_THRS is in bits [7:0] of SG4_THRS (0x74)
        tmc2240_fieldWrite(icID, TMC2240_SG4_FILT_EN_FIELD, filterEnable ? 1 : 0);
    } else {
        // TMC2660: StallGuard2
        tmc2660_setStallGuardThreshold(icID, threshold);
        tmc2660_setStallGuardFilter(icID, filterEnable);
    }

    // Configure TMC4361A to react to stall event (consistent with the old API)
    if (stopOnStall) {
        // Set VSTALL_LIMIT (consistent with the old API)
        // 0 = react at any velocity > 0
        tmc4361A_writeRegister(icID, TMC4361A_VSTALL_LIMIT, 0);

        // Enable stop on stall in REFERENCE_CONF (bit 26)
        uint32_t refConf = tmc4361A_readRegister(icID, TMC4361A_REFERENCE_CONF);
        refConf |= TMC4361A_STOP_ON_STALL_MASK;      // Enable stop on stall
        refConf &= ~TMC4361A_DRV_AFTER_STALL_MASK;   // Disable drive after stall
        tmc4361A_writeRegister(icID, TMC4361A_REFERENCE_CONF, refConf);
    }
}

uint8_t motor_readSwitchEvent(uint8_t icID)
{
    if (icID >= MOTOR_IC_COUNT)
        return 0;

    // Read EVENTS register and extract switch events
    // STOPL_EVENT is bit 11 (0x0800), STOPR_EVENT is bit 12 (0x1000)
    uint32_t events = tmc4361A_readRegister(icID, TMC4361A_EVENTS);

    // Mask and shift to get bits 0 and 1
    events &= (TMC4361A_STOPL_EVENT_MASK | TMC4361A_STOPR_EVENT_MASK);
    events >>= TMC4361A_STOPL_EVENT_SHIFT;

    return (uint8_t)(events & 0x03);
}

void motor_setVelocityInternal(uint8_t icID, int32_t velocityInternal)
{
    if (icID >= MOTOR_IC_COUNT)
        return;

    // ========================================================================
    // an implementation exactly matching the old API tmc4361A_setSpeed
    // ========================================================================

    // 1. set the velocity_mode flag (as in the old API: tmc4361A->velocity_mode = true)
    motorParams[icID].velocity_mode = true;

    // 2. Clear EVENTS register (reading clears it)
    tmc4361A_readRegister(icID, TMC4361A_EVENTS);

    // 3. clear the POSITION and HOLD bits, keep the S-shaped bit
    // old API: tmc4361A_rstBits(tmc4361A, TMC4361A_RAMPMODE, TMC4361A_RAMP_POSITION | TMC4361A_RAMP_HOLD);
    // if it was originally 0x06 (S-shaped position mode), the result is 0x02 (S-shaped velocity mode)
    uint32_t rampModeBefore = tmc4361A_readRegister(icID, TMC4361A_RAMPMODE);
    uint32_t rampMode = rampModeBefore & ~(TMC4361A_RAMP_POSITION | TMC4361A_RAMP_HOLD);
    tmc4361A_writeRegister(icID, TMC4361A_RAMPMODE, rampMode);

    // 4. Set velocity directly to VMAX (signed value determines direction)
    tmc4361A_writeRegister(icID, TMC4361A_VMAX, velocityInternal);

}

int32_t motor_readLatchPosition(uint8_t icID)
{
    if (icID >= MOTOR_IC_COUNT)
        return 0;

    return tmc4361A_readRegister(icID, TMC4361A_X_LATCH);
}
