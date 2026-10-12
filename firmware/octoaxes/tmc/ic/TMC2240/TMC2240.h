/*
 * TMC2240.h
 *
 * TMC2240 stepper driver for Octoaxes project.
 * Communicates through TMC4361A Cover interface (40-bit SPI).
 *
 * Based on official TMC-API, adapted for multi-IC support.
 * Original: Copyright © 2017 TRINAMIC / 2024 Analog Devices Inc.
 *
 * Created: 2026-03-16
 */

#ifndef TMC_IC_TMC2240_H_
#define TMC_IC_TMC2240_H_

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include "TMC2240_HW_Abstraction.h"

#ifdef __cplusplus
extern "C" {
#endif

// ============================================================================
// API Configuration
// ============================================================================

#ifndef TMC2240_CACHE
#define TMC2240_CACHE  1
#endif

#ifndef TMC2240_ENABLE_TMC_CACHE
#define TMC2240_ENABLE_TMC_CACHE   1
#endif

// Number of ICs (same as TMC4361A, paired 1:1)
#ifndef TMC2240_IC_CACHE_COUNT
#define TMC2240_IC_CACHE_COUNT 7
#endif

// ============================================================================
// Bus Type
// ============================================================================

typedef enum {
    TMC2240_BUS_SPI,
    TMC2240_BUS_UART,
} TMC2240BusType;

// ============================================================================
// HAL Callbacks (implemented in MotorControl.cpp)
// ============================================================================

extern void tmc2240_readWriteSPI(uint16_t icID, uint8_t *data, size_t dataLength);
extern TMC2240BusType tmc2240_getBusType(uint16_t icID);

// ============================================================================
// Core Register API
// ============================================================================

int32_t tmc2240_readRegister(uint16_t icID, uint8_t address);
void tmc2240_writeRegister(uint16_t icID, uint8_t address, int32_t value);

// ============================================================================
// Field-Level Operations
// ============================================================================

// RegisterField type — shared with TMC4361A_HW_Abstraction.h macros
#ifndef REGISTER_FIELD_DEFINED
#define REGISTER_FIELD_DEFINED
typedef struct {
    uint32_t mask;
    uint8_t  shift;
    uint8_t  address;
    bool     isSigned;
} RegisterField;
#endif

static inline uint32_t tmc2240_fieldExtract(uint32_t data, RegisterField field)
{
    uint32_t value = (data & field.mask) >> field.shift;
    if (field.isSigned) {
        uint32_t baseMask = field.mask >> field.shift;
        uint32_t signMask = baseMask & (~baseMask >> 1);
        value = (value ^ signMask) - signMask;
    }
    return value;
}

static inline uint32_t tmc2240_fieldRead(uint16_t icID, RegisterField field)
{
    uint32_t value = tmc2240_readRegister(icID, field.address);
    return tmc2240_fieldExtract(value, field);
}

static inline uint32_t tmc2240_fieldUpdate(uint32_t data, RegisterField field, uint32_t value)
{
    return (data & (~field.mask)) | ((value << field.shift) & field.mask);
}

static inline void tmc2240_fieldWrite(uint16_t icID, RegisterField field, uint32_t value)
{
    uint32_t regValue = tmc2240_readRegister(icID, field.address);
    regValue = tmc2240_fieldUpdate(regValue, field, value);
    tmc2240_writeRegister(icID, field.address, regValue);
}

// ============================================================================
// High-Level Configuration API
// ============================================================================

/**
 * @brief Set run current (IRUN field, 0-31)
 */
void tmc2240_setRunCurrent(uint16_t icID, uint8_t irun);

/**
 * @brief Set hold current (IHOLD field, 0-31)
 */
void tmc2240_setHoldCurrent(uint16_t icID, uint8_t ihold);

/**
 * @brief Enable/disable driver (via CHOPCONF.TOFF)
 */
void tmc2240_enableDriver(uint16_t icID, bool enable);

// ============================================================================
// Cache Implementation
// ============================================================================

#if TMC2240_CACHE == 1
#if TMC2240_ENABLE_TMC_CACHE == 1

typedef enum {
    TMC2240_CACHE_READ,
    TMC2240_CACHE_WRITE,
    TMC2240_CACHE_FILL_DEFAULT,
} TMC2240CacheOp;

typedef struct {
    uint8_t  address;
    uint32_t value;
} TMC2240RegisterConstants;

#define TMC2240_ACCESS_DIRTY       0x08
#define TMC2240_ACCESS_READ        0x01
#define TMC2240_ACCESS_W_PRESET    0x42
#define TMC2240_IS_READABLE(x)     ((x) & TMC2240_ACCESS_READ)

#ifndef ARRAY_SIZE
#define ARRAY_SIZE(x)              (sizeof(x)/sizeof(x[0]))
#endif

#ifndef ____
#define ____ 0x00
#endif
#ifndef N_A
#define N_A  0
#endif

// Default register values
#define R2240_00 0x00002108  // GCONF
#define R2240_0A 0x00000020  // DRV_CONF
#define R2240_10 0x00070A03  // IHOLD_IRUN
#define R2240_11 0x0000000A  // TPOWERDOWN
#define R2240_6C 0x14410153  // CHOPCONF
#define R2240_70 ((int32_t)0xC44C001E)  // PWMCONF

// Register access permissions
static const uint8_t tmc2240_registerAccess[TMC2240_REGISTER_COUNT] =
{
    //  0     1     2     3     4     5     6     7     8     9     A     B     C     D     E     F
    0x03, 0x23, 0x01, 0x03, 0x03, ____, ____, ____, ____, ____, 0x03, 0x03, ____, ____, ____, ____, // 0x00
    0x03, 0x03, 0x01, 0x03, 0x03, 0x03, ____, ____, ____, ____, ____, ____, ____, ____, ____, ____, // 0x10
    ____, ____, ____, ____, ____, ____, ____, ____, ____, ____, ____, ____, ____, 0x03, ____, ____, // 0x20
    ____, ____, ____, ____, ____, ____, ____, ____, 0x03, 0x03, 0x03, 0x23, 0x01, ____, ____, ____, // 0x30
    ____, ____, ____, ____, ____, ____, ____, ____, ____, ____, ____, ____, ____, ____, ____, ____, // 0x40
    0x01, 0x01, 0x03, ____, ____, ____, ____, ____, ____, ____, ____, ____, ____, ____, ____, ____, // 0x50
    0x42, 0x42, 0x42, 0x42, 0x42, 0x42, 0x42, 0x42, 0x42, 0x42, 0x01, 0x01, 0x03, 0x03, ____, 0x01, // 0x60
    0x03, 0x01, 0x01, ____, 0x03, 0x01, 0x01, ____, ____, ____, ____, ____, ____, ____, ____, ____  // 0x70
};

static const int32_t tmc2240_sampleRegisterPreset[TMC2240_REGISTER_COUNT] =
{
    //  0,       1,   2,   3,   4,   5,   6,   7,   8,   9,   A,       B,   C,   D,   E,   F
    R2240_00, 0,   0,   0,   0,   0,   0,   0,   0,   0,   R2240_0A, 0,   0,   0,   0,   0, // 0x00
    R2240_10, R2240_11, 0, 0,  0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0, // 0x10
    0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0, // 0x20
    0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0, // 0x30
    0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0, // 0x40
    0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0, // 0x50
    N_A, N_A, N_A, N_A, N_A, N_A, N_A, N_A, N_A, N_A, 0,   0,   R2240_6C, 0, 0, 0, // 0x60
    R2240_70, 0, 0, 0,  0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0, // 0x70
};

static const TMC2240RegisterConstants tmc2240_RegisterConstants[] =
{
    { 0x60, 0xAAAAB554 }, // MSLUT[0]
    { 0x61, 0x4A9554AA }, // MSLUT[1]
    { 0x62, 0x24492929 }, // MSLUT[2]
    { 0x63, 0x10104222 }, // MSLUT[3]
    { 0x64, 0xFBFFFFFF }, // MSLUT[4]
    { 0x65, 0xB5BB777D }, // MSLUT[5]
    { 0x66, 0x49295556 }, // MSLUT[6]
    { 0x67, 0x00404222 }, // MSLUT[7]
    { 0x68, 0xFFFF8056 }, // MSLUTSEL
    { 0x69, 0x00F70000 }, // MSLUTSTART
};

extern uint8_t tmc2240_dirtyBits[TMC2240_IC_CACHE_COUNT][TMC2240_REGISTER_COUNT / 8];
extern int32_t tmc2240_shadowRegister[TMC2240_IC_CACHE_COUNT][TMC2240_REGISTER_COUNT];
bool tmc2240_cache(uint16_t icID, TMC2240CacheOp operation, uint8_t address, uint32_t *value);
void tmc2240_initCache(void);
void tmc2240_setDirtyBit(uint16_t icID, uint8_t index, bool value);
bool tmc2240_getDirtyBit(uint16_t icID, uint8_t index);

#endif
#endif

#ifdef __cplusplus
}
#endif

#endif /* TMC_IC_TMC2240_H_ */
