/*
 * TMC2240.cpp
 *
 * TMC2240 stepper driver implementation for Octoaxes project.
 * SPI communication only (through TMC4361A Cover interface).
 *
 * Based on official TMC-API, adapted for multi-IC support.
 * Original: Copyright © 2017 TRINAMIC / 2024 Analog Devices Inc.
 *
 * Created: 2026-03-16
 */

#include "TMC2240.h"

// ============================================================================
// Cache Implementation
// ============================================================================

#if TMC2240_CACHE == 0
static inline bool tmc2240_cache(uint16_t icID, TMC2240CacheOp operation, uint8_t address, uint32_t *value)
{
    (void)icID;
    (void)address;
    (void)operation;
    (void)value;
    return false;
}
#else
#if TMC2240_ENABLE_TMC_CACHE == 1

uint8_t tmc2240_dirtyBits[TMC2240_IC_CACHE_COUNT][TMC2240_REGISTER_COUNT / 8] = {0};
int32_t tmc2240_shadowRegister[TMC2240_IC_CACHE_COUNT][TMC2240_REGISTER_COUNT];

void tmc2240_setDirtyBit(uint16_t icID, uint8_t index, bool value)
{
    if (index >= TMC2240_REGISTER_COUNT || icID >= TMC2240_IC_CACHE_COUNT)
        return;

    uint8_t *tmp  = &tmc2240_dirtyBits[icID][index / 8];
    uint8_t shift = (index % 8);
    uint8_t mask  = 1 << shift;
    *tmp = (((*tmp) & (~mask)) | ((value ? 1 : 0) << shift));
}

bool tmc2240_getDirtyBit(uint16_t icID, uint8_t index)
{
    if (index >= TMC2240_REGISTER_COUNT || icID >= TMC2240_IC_CACHE_COUNT)
        return false;

    uint8_t *tmp  = &tmc2240_dirtyBits[icID][index / 8];
    uint8_t shift = (index % 8);
    return ((*tmp) >> shift) & 1;
}

bool tmc2240_cache(uint16_t icID, TMC2240CacheOp operation, uint8_t address, uint32_t *value)
{
    if (operation == TMC2240_CACHE_READ)
    {
        if (icID >= TMC2240_IC_CACHE_COUNT)
            return false;
        if (TMC2240_IS_READABLE(tmc2240_registerAccess[address]))
            return false;
        *value = tmc2240_shadowRegister[icID][address];
        return true;
    }
    else if (operation == TMC2240_CACHE_WRITE || operation == TMC2240_CACHE_FILL_DEFAULT)
    {
        if (icID >= TMC2240_IC_CACHE_COUNT)
            return false;
        tmc2240_shadowRegister[icID][address] = *value;
        if (operation == TMC2240_CACHE_WRITE)
        {
            tmc2240_setDirtyBit(icID, address, true);
        }
        return true;
    }
    return false;
}

void tmc2240_initCache(void)
{
    if (ARRAY_SIZE(tmc2240_RegisterConstants) == 0)
        return;

    size_t i, j, id;

    for (i = 0, j = 0; i < TMC2240_REGISTER_COUNT; i++)
    {
        if (tmc2240_registerAccess[i] != TMC2240_ACCESS_W_PRESET)
            continue;

        while (j < ARRAY_SIZE(tmc2240_RegisterConstants) &&
               (tmc2240_RegisterConstants[j].address < i))
            j++;

        if (j == ARRAY_SIZE(tmc2240_RegisterConstants))
            break;

        if (tmc2240_RegisterConstants[j].address == i)
        {
            for (id = 0; id < TMC2240_IC_CACHE_COUNT; id++)
            {
                uint32_t temp = tmc2240_RegisterConstants[j].value;
                tmc2240_cache(id, TMC2240_CACHE_FILL_DEFAULT, i, &temp);
            }
        }
    }
}

#else
// User must implement their own cache
extern bool tmc2240_cache(uint16_t icID, TMC2240CacheOp operation, uint8_t address, uint32_t *value);
#endif
#endif

// ============================================================================
// SPI Read/Write (through TMC4361A Cover interface)
// ============================================================================

static int32_t readRegisterSPI(uint16_t icID, uint8_t address);
static void writeRegisterSPI(uint16_t icID, uint8_t address, int32_t value);

int32_t tmc2240_readRegister(uint16_t icID, uint8_t address)
{
    uint32_t value;

    // Read from cache for write-only registers
    if (tmc2240_cache(icID, TMC2240_CACHE_READ, address, &value))
        return value;

    return readRegisterSPI(icID, address);
}

void tmc2240_writeRegister(uint16_t icID, uint8_t address, int32_t value)
{
    writeRegisterSPI(icID, address, value);

    // Cache the registers with write-only access
    tmc2240_cache(icID, TMC2240_CACHE_WRITE, address, (uint32_t *)&value);
}

static int32_t readRegisterSPI(uint16_t icID, uint8_t address)
{
    uint8_t data[5] = {0};

    // Clear write bit
    data[0] = address & TMC2240_ADDRESS_MASK;

    // First SPI transfer: send read request
    tmc2240_readWriteSPI(icID, &data[0], sizeof(data));

    // Rewrite address and clear write bit
    data[0] = address & TMC2240_ADDRESS_MASK;

    // Second SPI transfer: receive read reply
    tmc2240_readWriteSPI(icID, &data[0], sizeof(data));

    return ((int32_t)data[1] << 24) | ((int32_t)data[2] << 16) |
           ((int32_t)data[3] << 8)  | ((int32_t)data[4]);
}

static void writeRegisterSPI(uint16_t icID, uint8_t address, int32_t value)
{
    uint8_t data[5] = {0};

    data[0] = address | TMC2240_WRITE_BIT;
    data[1] = 0xFF & (value >> 24);
    data[2] = 0xFF & (value >> 16);
    data[3] = 0xFF & (value >> 8);
    data[4] = 0xFF & (value >> 0);

    // Send write request via HAL callback (routes through TMC4361A Cover)
    tmc2240_readWriteSPI(icID, &data[0], sizeof(data));
}

// ============================================================================
// High-Level Configuration API
// ============================================================================

void tmc2240_setRunCurrent(uint16_t icID, uint8_t irun)
{
    if (irun > 31) irun = 31;
    tmc2240_fieldWrite(icID, TMC2240_IRUN_FIELD, irun);
}

void tmc2240_setHoldCurrent(uint16_t icID, uint8_t ihold)
{
    if (ihold > 31) ihold = 31;
    tmc2240_fieldWrite(icID, TMC2240_IHOLD_FIELD, ihold);
}

void tmc2240_enableDriver(uint16_t icID, bool enable)
{
    // TOFF=0 disables driver, TOFF>0 enables
    if (enable) {
        uint32_t chopconf = tmc2240_readRegister(icID, TMC2240_CHOPCONF);
        uint8_t toff = (chopconf & TMC2240_TOFF_MASK) >> TMC2240_TOFF_SHIFT;
        if (toff == 0) {
            // Restore default TOFF=3
            tmc2240_fieldWrite(icID, TMC2240_TOFF_FIELD, 3);
        }
    } else {
        tmc2240_fieldWrite(icID, TMC2240_TOFF_FIELD, 0);
    }
}
