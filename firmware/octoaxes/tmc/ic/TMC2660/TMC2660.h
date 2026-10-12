/*
 * TMC2660.h
 *
 * TMC2660 stepper driver for Octoaxes project.
 * Communicates through TMC4361A Cover interface.
 *
 * Created: 2026-01-21
 */

#ifndef TMC_IC_TMC2660_H_
#define TMC_IC_TMC2660_H_

#include "TMC2660_HW_Abstraction.h"
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

// ============================================================================
// API Configuration
// ============================================================================

#ifndef TMC2660_CACHE
#define TMC2660_CACHE  1
#endif

#ifndef TMC2660_ENABLE_TMC_CACHE
#define TMC2660_ENABLE_TMC_CACHE   1
#endif

// Number of ICs (same as TMC4361A, paired 1:1)
#ifndef TMC2660_IC_CACHE_COUNT
#define TMC2660_IC_CACHE_COUNT 7
#endif

// ============================================================================
// RegisterField Type Definition
// ============================================================================

typedef struct {
    uint32_t mask;
    uint8_t  shift;
    uint8_t  address;
    bool     isSigned;
} TMC2660RegisterField;

// ============================================================================
// Communication Mode
// ============================================================================

typedef enum {
    TMC2660_COMM_COVER,     // Through TMC4361A Cover interface (default)
    TMC2660_COMM_DIRECT_SPI // Direct SPI (reserved for future use)
} TMC2660CommMode;

// ============================================================================
// Core Register API
// ============================================================================

/**
 * @brief Write a register to TMC2660
 * @param icID  IC identifier (0 to TMC2660_IC_CACHE_COUNT-1)
 * @param address Register address
 * @param value Value to write (20-bit)
 */
void tmc2660_writeRegister(uint8_t icID, uint8_t address, uint32_t value);

/**
 * @brief Read a register from TMC2660
 * @param icID  IC identifier
 * @param address Register address
 * @return Register value
 */
uint32_t tmc2660_readRegister(uint8_t icID, uint8_t address);

/**
 * @brief Get status bits from last response
 * @param icID  IC identifier
 * @return Status bits (8-bit)
 */
uint8_t tmc2660_getStatusBits(uint8_t icID);

// ============================================================================
// Field-Level Operations
// ============================================================================

static inline uint32_t tmc2660_fieldExtract(uint32_t data, TMC2660RegisterField field)
{
    uint32_t value = (data & field.mask) >> field.shift;

    if (field.isSigned) {
        uint32_t baseMask = field.mask >> field.shift;
        uint32_t signMask = baseMask & (~baseMask >> 1);
        value = (value ^ signMask) - signMask;
    }

    return value;
}

static inline uint32_t tmc2660_fieldRead(uint8_t icID, TMC2660RegisterField field)
{
    uint32_t value = tmc2660_readRegister(icID, field.address);
    return tmc2660_fieldExtract(value, field);
}

static inline uint32_t tmc2660_fieldUpdate(uint32_t data, TMC2660RegisterField field, uint32_t value)
{
    return (data & (~field.mask)) | ((value << field.shift) & field.mask);
}

static inline void tmc2660_fieldWrite(uint8_t icID, TMC2660RegisterField field, uint32_t value)
{
    uint32_t regValue = tmc2660_readRegister(icID, field.address);
    regValue = tmc2660_fieldUpdate(regValue, field, value);
    tmc2660_writeRegister(icID, field.address, regValue);
}

// ============================================================================
// High-Level Configuration API
// ============================================================================

/**
 * @brief Initialize TMC2660 driver with default settings
 * @param icID  IC identifier
 */
void tmc2660_initDriver(uint8_t icID);

/**
 * @brief Set run current (0-31)
 * @param icID  IC identifier
 * @param current Current scale value (0-31, where 31 = max current)
 */
void tmc2660_setRunCurrent(uint8_t icID, uint8_t current);

/**
 * @brief Set microstep resolution
 * @param icID  IC identifier
 * @param mres Microstep resolution (0=256, 1=128, 2=64, ... 8=1)
 */
void tmc2660_setMicrostepResolution(uint8_t icID, uint8_t mres);

/**
 * @brief Enable/disable microstep interpolation
 * @param icID  IC identifier
 * @param enable true to enable 256 microstep interpolation
 */
void tmc2660_setInterpolation(uint8_t icID, bool enable);

/**
 * @brief Configure chopper parameters
 * @param icID  IC identifier
 * @param toff  Off time (1-15, 0=driver disabled)
 * @param hstrt Hysteresis start (0-7)
 * @param hend  Hysteresis end (-3 to 12, add 3 for register value)
 * @param tbl   Blanking time (0-3)
 */
void tmc2660_setChopperConfig(uint8_t icID, uint8_t toff, uint8_t hstrt, int8_t hend, uint8_t tbl);

/**
 * @brief Enable/disable driver output
 * @param icID  IC identifier
 * @param enable true to enable, false to disable
 */
void tmc2660_enableDriver(uint8_t icID, bool enable);

/**
 * @brief Set StallGuard threshold
 * @param icID  IC identifier
 * @param threshold Threshold value (-64 to 63)
 */
void tmc2660_setStallGuardThreshold(uint8_t icID, int8_t threshold);

/**
 * @brief Enable/disable StallGuard filter
 * @param icID  IC identifier
 * @param enable true to enable filtering
 */
void tmc2660_setStallGuardFilter(uint8_t icID, bool enable);

// ============================================================================
// Status Detection API
// ============================================================================

/**
 * @brief Check if motor is stalled (StallGuard flag)
 */
bool tmc2660_isStalled(uint8_t icID);

/**
 * @brief Check for overtemperature shutdown
 */
bool tmc2660_isOvertemperature(uint8_t icID);

/**
 * @brief Check for overtemperature warning
 */
bool tmc2660_isOvertemperatureWarning(uint8_t icID);

/**
 * @brief Check for short to ground on phase A
 */
bool tmc2660_isShortToGroundA(uint8_t icID);

/**
 * @brief Check for short to ground on phase B
 */
bool tmc2660_isShortToGroundB(uint8_t icID);

/**
 * @brief Check for open load on phase A
 */
bool tmc2660_isOpenLoadA(uint8_t icID);

/**
 * @brief Check for open load on phase B
 */
bool tmc2660_isOpenLoadB(uint8_t icID);

/**
 * @brief Check if motor is at standstill
 */
bool tmc2660_isStandstill(uint8_t icID);

// ============================================================================
// Diagnostic API
// ============================================================================

/**
 * @brief Get StallGuard value (load indicator)
 * @param icID  IC identifier
 * @return StallGuard value (0-1023, higher = lower load)
 */
uint16_t tmc2660_getStallGuardValue(uint8_t icID);

/**
 * @brief Get current microstep position
 * @param icID  IC identifier
 * @return Microstep position (0-1023)
 */
uint16_t tmc2660_getMicrostepPosition(uint8_t icID);

/**
 * @brief Get actual current scale (from CoolStep)
 * @param icID  IC identifier
 * @return Current scale value
 */
uint8_t tmc2660_getActualCurrentScale(uint8_t icID);

// ============================================================================
// Cache Implementation
// ============================================================================

#if TMC2660_CACHE == 1
#if TMC2660_ENABLE_TMC_CACHE == 1

typedef enum {
    TMC2660_CACHE_READ,
    TMC2660_CACHE_WRITE,
    TMC2660_CACHE_FILL_DEFAULT
} TMC2660CacheOp;

#define TMC_ACCESS_READ        0x01
#define TMC_ACCESS_WRITE       0x02
#define TMC_ACCESS_NONE        0x00
#define TMC2660_IS_READABLE(x) ((x) & TMC_ACCESS_READ)

static const uint8_t tmc2660_registerAccess[TMC2660_REGISTER_COUNT] =
{
    TMC_ACCESS_READ,   // 0: RESPONSE 0
    TMC_ACCESS_READ,   // 1: RESPONSE 1
    TMC_ACCESS_READ,   // 2: RESPONSE 2
    TMC_ACCESS_READ,   // 3: RESPONSE_LATEST
    TMC_ACCESS_NONE,   // 4: UNUSED
    TMC_ACCESS_NONE,   // 5: UNUSED
    TMC_ACCESS_NONE,   // 6: UNUSED
    TMC_ACCESS_NONE,   // 7: UNUSED
    TMC_ACCESS_WRITE,  // 8: DRVCTRL
    TMC_ACCESS_NONE,   // 9: UNUSED
    TMC_ACCESS_NONE,   // A: UNUSED
    TMC_ACCESS_NONE,   // B: UNUSED
    TMC_ACCESS_WRITE,  // C: CHOPCONF
    TMC_ACCESS_WRITE,  // D: SMARTEN
    TMC_ACCESS_WRITE,  // E: SGCSCONF
    TMC_ACCESS_WRITE   // F: DRVCONF
};

static const int32_t tmc2660_sampleRegisterPreset[TMC2660_REGISTER_COUNT] =
{
    0x00000000,  // 0: RESPONSE0
    0x00000000,  // 1: RESPONSE1
    0x00000000,  // 2: RESPONSE2
    0x00000000,  // 3: RESPONSE_LATEST
    0x00000000,  // 4: UNUSED
    0x00000000,  // 5: UNUSED
    0x00000000,  // 6: UNUSED
    0x00000000,  // 7: UNUSED
    0x00000000,  // 8: DRVCTRL (microstep mode, INTPOL=0, MRES=0)
    0x00000000,  // 9: UNUSED
    0x00000000,  // A: UNUSED
    0x00000000,  // B: UNUSED
    0x00091935,  // C: CHOPCONF (TBL=2, HEND=3, HSTRT=4, TOFF=5)
    0x000A0000,  // D: SMARTEN (disabled)
    0x000D0505,  // E: SGCSCONF (CS=5, SGT=5)
    0x000EF040   // F: DRVCONF (RDSEL=0, VSENSE=1)
};

extern int32_t tmc2660_shadowRegister[TMC2660_IC_CACHE_COUNT][TMC2660_REGISTER_COUNT];

bool tmc2660_cache(uint16_t icID, TMC2660CacheOp operation, uint8_t address, uint32_t *value);
void tmc2660_initCache(void);

#endif
#endif

#ifdef __cplusplus
}
#endif

#endif /* TMC_IC_TMC2660_H_ */
