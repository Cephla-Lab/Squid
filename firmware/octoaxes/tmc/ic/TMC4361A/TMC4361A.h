/*
 * TMC4361A.h
 *
 * TMC4361A motion controller driver based on official TMC-API.
 * Adapted for multi-IC support with icID-based addressing.
 *
 * Created: 2026-01-21
 */

#ifndef TMC_IC_TMC4361A_H_
#define TMC_IC_TMC4361A_H_

#include "TMC4361A_HW_Abstraction.h"
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

// ============================================================================
// API Configuration
// ============================================================================

// Enable cache mechanism
#ifndef TMC4361A_CACHE
#define TMC4361A_CACHE   1
#endif

// Use TMC-API built-in cache implementation
#ifndef TMC4361A_ENABLE_TMC_CACHE
#define TMC4361A_ENABLE_TMC_CACHE   1
#endif

// Number of ICs to support in cache (7 axes: X, Y, Z, W, E1, E3, E4)
#ifndef TMC4361A_IC_CACHE_COUNT
#define TMC4361A_IC_CACHE_COUNT 7
#endif

// ============================================================================
// RegisterField Type Definition (shared with TMC2240)
// ============================================================================

#ifndef REGISTER_FIELD_DEFINED
#define REGISTER_FIELD_DEFINED
typedef struct {
    uint32_t mask;
    uint8_t  shift;
    uint8_t  address;
    bool     isSigned;
} RegisterField;
#endif

// ============================================================================
// SPI Callback Declarations (implemented in HAL layer)
// ============================================================================

extern void tmc4361A_readWriteSPI(uint16_t icID, uint8_t *data, size_t dataLength);
extern void tmc4361A_setStatus(uint16_t icID, uint8_t *data);

// ============================================================================
// Register Read/Write API
// ============================================================================

/**
 * @brief Read a register from TMC4361A
 * @param icID  IC identifier (0 to TMC4361A_IC_CACHE_COUNT-1)
 * @param address Register address (0x00 to 0x7F)
 * @return Register value (32-bit)
 */
int32_t tmc4361A_readRegister(uint16_t icID, uint8_t address);

/**
 * @brief Write a register to TMC4361A
 * @param icID  IC identifier
 * @param address Register address
 * @param value Value to write (32-bit)
 */
void tmc4361A_writeRegister(uint16_t icID, uint8_t address, int32_t value);

/**
 * @brief Read/Write through Cover interface (for TMC2660 communication)
 * @param icID  IC identifier
 * @param data  Data buffer
 * @param length Data length
 */
void tmc4361A_readWriteCover(uint16_t icID, uint8_t *data, size_t length);

// ============================================================================
// Field-Level Operations
// ============================================================================

/**
 * @brief Extract field value from register data
 * @param data  Raw register data
 * @param field Field definition
 * @return Extracted field value
 */
static inline uint32_t tmc4361A_fieldExtract(uint32_t data, RegisterField field)
{
    uint32_t value = (data & field.mask) >> field.shift;

    if (field.isSigned)
    {
        // Apply signedness conversion
        uint32_t baseMask = field.mask >> field.shift;
        uint32_t signMask = baseMask & (~baseMask >> 1);
        value = (value ^ signMask) - signMask;
    }

    return value;
}

/**
 * @brief Read and extract a field from a register
 * @param icID  IC identifier
 * @param field Field definition
 * @return Field value
 */
static inline uint32_t tmc4361A_fieldRead(uint16_t icID, RegisterField field)
{
    uint32_t value = tmc4361A_readRegister(icID, field.address);
    return tmc4361A_fieldExtract(value, field);
}

/**
 * @brief Update a field in register data
 * @param data  Current register data
 * @param field Field definition
 * @param value New field value
 * @return Updated register data
 */
static inline uint32_t tmc4361A_fieldUpdate(uint32_t data, RegisterField field, uint32_t value)
{
    return (data & (~field.mask)) | ((value << field.shift) & field.mask);
}

/**
 * @brief Read-modify-write a field in a register
 * @param icID  IC identifier
 * @param field Field definition
 * @param value New field value
 */
static inline void tmc4361A_fieldWrite(uint16_t icID, RegisterField field, uint32_t value)
{
    uint32_t regValue = tmc4361A_readRegister(icID, field.address);
    regValue = tmc4361A_fieldUpdate(regValue, field, value);
    tmc4361A_writeRegister(icID, field.address, regValue);
}

// ============================================================================
// Cache Implementation
// ============================================================================

#if TMC4361A_CACHE == 1
#if TMC4361A_ENABLE_TMC_CACHE == 1

typedef enum {
    TMC4361A_CACHE_READ,
    TMC4361A_CACHE_WRITE,
    // Fill cache without marking dirty (for hardware defaults)
    TMC4361A_CACHE_FILL_DEFAULT
} TMC4361ACacheOp;

typedef struct {
    uint8_t  address;
    uint32_t value;
} TMC4361ARegisterConstant;

// Access permission flags
#define TMC4361A_ACCESS_DIRTY       0x08
#define TMC4361A_ACCESS_READ        0x01
#define TMC_ACCESS_WRITE            0x02
#define TMC4361A_ACCESS_W_PRESET    0x42
#define TMC_IS_RESETTABLE(x)        (((x) & (TMC4361A_ACCESS_W_PRESET)) == TMC_ACCESS_WRITE)
#define TMC4361A_IS_READABLE(x)     ((x) & TMC4361A_ACCESS_READ)
#define TMC_IS_WRITABLE(x)          ((x) & TMC_ACCESS_WRITE)
#define ARRAY_SIZE(x)               (sizeof(x)/sizeof(x[0]))

// Default register values
#define R10 0x00040001  // STP_LENGTH_ADD
#define R20 0x00000001  // RAMPMODE
#define R28 0x00013880  // AMAX
#define R29 0x00013880  // DMAX
#define R2D 0x000003E8  // BOW1
#define R2E 0x000003E8  // BOW2
#define R2F 0x000003E8  // BOW3
#define R30 0x000003E8  // BOW4
#define R54 0x00009C40  // ENC_IN_RES
#define ____ 0x00
#ifndef N_A
#define N_A 0x00
#endif

// Sample register preset values
static const int32_t tmc4361A_sampleRegisterPreset[TMC4361A_REGISTER_COUNT] =
{
//  0,   1,   2,   3,   4,   5,   6,   7,   8,   9,   A,   B,   C,   D,   E,   F
    0,   0,   0,   0,   0,   0,   N_A, N_A, 0,   0,   N_A, N_A, 0,   0,   0,   0,   // 0x00 - 0x0F
    R10, 0,   N_A, 0,   0,   0,   0,   0,   0,   0,   0,   0,   N_A, 0,   0,   N_A, // 0x10 - 0x1F
    R20, 0,   0,   0,   0,   0,   0,   0,   R28, R29, 0,   0,   0,   R2D, R2E, R2F, // 0x20 - 0x2F
    R30, N_A, 0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   // 0x30 - 0x3F
    0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   // 0x40 - 0x4F
    0,   0,   0,   N_A, R54, 0,   N_A, N_A, N_A, 0,   0,   0,   0,   0,   0,   0,   // 0x50 - 0x5F
    0,   0,   N_A, 0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   // 0x60 - 0x6F
    N_A, N_A, N_A, N_A, N_A, N_A, N_A, N_A, N_A, 0,   0,   N_A, N_A, 0,   N_A, 0    // 0x70 - 0x7F
};

#undef R10
#undef R20
#undef R28
#undef R29
#undef R2D
#undef R2E
#undef R2F
#undef R30
#undef R54

// Register access permissions
static const uint8_t tmc4361A_registerAccess[TMC4361A_REGISTER_COUNT] =
{
//  0     1     2     3     4     5     6     7     8     9     A     B     C     D     E     F
    0x43, 0x03, 0x03, 0x03, 0x03, 0x03, 0x43, 0x43, 0x03, 0x03, 0x43, 0x43, 0x03, 0x03, 0x23, 0x01, // 0x00 - 0x0F
    0x03, 0x03, 0x43, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x43, 0x03, 0x03, 0x43, // 0x10 - 0x1F
    0x03, 0x03, 0x01, 0x01, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, // 0x20 - 0x2F
    0x03, 0x43, 0x03, 0x03, 0x03, 0x03, 0x13, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, // 0x30 - 0x3F
    0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, // 0x40 - 0x4F
    0x03, 0x13, 0x13, 0x42, 0x13, 0x02, 0x42, 0x42, 0x42, 0x03, 0x13, 0x13, 0x02, 0x13, 0x02, 0x02, // 0x50 - 0x5F
    0x02, 0x02, 0x42, 0x02, ____, 0x01, 0x01, 0x02, 0x02, 0x02, 0x01, 0x01, 0x13, 0x13, 0x01, 0x01, // 0x60 - 0x6F
    0x42, 0x42, 0x42, 0x42, 0x42, 0x42, 0x42, 0x42, 0x42, 0x13, 0x01, 0x13, 0x13, 0x02, 0x42, 0x01  // 0x70 - 0x7F
};

#undef ____

// Register constants for preset write-only registers
static const TMC4361ARegisterConstant tmc4361A_RegisterConstants[] =
{
    { 0x53, 0xFFFFFFFF }, // ENC_POS_DEV_TOL
    { 0x56, 0x00A000A0 }, // SER_CLK_IN_HIGH, SER_CLK_IN_LOW
    { 0x57, 0x00F00000 }, // SSI_IN_CLK_DELAY, SSI_IN_WTIME
    { 0x58, 0x00000190 }, // SER_PTIME
    { 0x62, 0x00FFFFFF }, // ENC_VEL_ZERO
    { 0x70, 0xAAAAB554 }, // MSLUT[0]
    { 0x71, 0x4A9554AA }, // MSLUT[1]
    { 0x72, 0x24492929 }, // MSLUT[2]
    { 0x73, 0x10104222 }, // MSLUT[3]
    { 0x74, 0xFBFFFFFF }, // MSLUT[4]
    { 0x75, 0xB5BB777D }, // MSLUT[5]
    { 0x76, 0x49295556 }, // MSLUT[6]
    { 0x77, 0x00404222 }, // MSLUT[7]
    { 0x78, 0xFFFF8056 }, // MSLUTSEL
    { 0x7E, 0x00F70000 }, // START_SIN, START_SIN_90_120, DAC_OFFSET
};

// Cache storage (extern, defined in .cpp)
extern uint8_t tmc4361A_dirtyBits[TMC4361A_IC_CACHE_COUNT][TMC4361A_REGISTER_COUNT/8];
extern int32_t tmc4361A_shadowRegister[TMC4361A_IC_CACHE_COUNT][TMC4361A_REGISTER_COUNT];

/**
 * @brief Set dirty bit for a register
 */
void tmc4361A_setDirtyBit(uint16_t icID, uint8_t index, bool value);

/**
 * @brief Get dirty bit for a register
 */
bool tmc4361A_getDirtyBit(uint16_t icID, uint8_t index);

/**
 * @brief Cache operation function
 */
bool tmc4361A_cache(uint16_t icID, TMC4361ACacheOp operation, uint8_t address, uint32_t *value);

/**
 * @brief Initialize cache with default values
 */
void tmc4361A_initCache(void);

#endif // TMC4361A_ENABLE_TMC_CACHE
#endif // TMC4361A_CACHE

#ifdef __cplusplus
}
#endif

#endif /* TMC_IC_TMC4361A_H_ */
