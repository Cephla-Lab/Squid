/*
 * TMC4361A.cpp
 *
 * TMC4361A motion controller driver implementation.
 * Based on official TMC-API, adapted for multi-IC support.
 *
 * Created: 2026-01-21
 */

#include "TMC4361A.h"
#include <Arduino.h>

// ============================================================================
// Cache Implementation
// ============================================================================

#if TMC4361A_CACHE == 1
#if TMC4361A_ENABLE_TMC_CACHE == 1

// Cache storage
uint8_t tmc4361A_dirtyBits[TMC4361A_IC_CACHE_COUNT][TMC4361A_REGISTER_COUNT / 8] = {0};
int32_t tmc4361A_shadowRegister[TMC4361A_IC_CACHE_COUNT][TMC4361A_REGISTER_COUNT];

void tmc4361A_setDirtyBit(uint16_t icID, uint8_t index, bool value)
{
    if (index >= TMC4361A_REGISTER_COUNT || icID >= TMC4361A_IC_CACHE_COUNT)
        return;

    uint8_t *tmp  = &tmc4361A_dirtyBits[icID][index / 8];
    uint8_t shift = (index % 8);
    uint8_t mask  = 1 << shift;
    *tmp = (((*tmp) & (~mask)) | ((value ? 1 : 0) << shift));
}

bool tmc4361A_getDirtyBit(uint16_t icID, uint8_t index)
{
    if (index >= TMC4361A_REGISTER_COUNT || icID >= TMC4361A_IC_CACHE_COUNT)
        return false;

    uint8_t *tmp  = &tmc4361A_dirtyBits[icID][index / 8];
    uint8_t shift = (index % 8);
    return ((*tmp) >> shift) & 1;
}

bool tmc4361A_cache(uint16_t icID, TMC4361ACacheOp operation, uint8_t address, uint32_t *value)
{
    if (operation == TMC4361A_CACHE_READ)
    {
        // Only supported chips have a cache
        if (icID >= TMC4361A_IC_CACHE_COUNT)
            return false;

        // Only non-readable registers need caching
        if (TMC4361A_IS_READABLE(tmc4361A_registerAccess[address]))
            return false;

        // Grab the value from cache
        *value = tmc4361A_shadowRegister[icID][address];
        return true;
    }
    else if (operation == TMC4361A_CACHE_WRITE || operation == TMC4361A_CACHE_FILL_DEFAULT)
    {
        // Only supported chips have a cache
        if (icID >= TMC4361A_IC_CACHE_COUNT)
            return false;

        // Write to shadow register
        tmc4361A_shadowRegister[icID][address] = *value;

        // Mark dirty only for actual writes (not default fills)
        if (operation == TMC4361A_CACHE_WRITE)
        {
            tmc4361A_setDirtyBit(icID, address, true);
        }
        return true;
    }

    return false;
}

void tmc4361A_initCache(void)
{
    // Check if we have constants defined
    if (ARRAY_SIZE(tmc4361A_RegisterConstants) == 0)
        return;

    size_t i, j, id;

    for (i = 0, j = 0; i < TMC4361A_REGISTER_COUNT; i++)
    {
        // Only handle hardware preset, write-only registers
        if (tmc4361A_registerAccess[i] != TMC4361A_ACCESS_W_PRESET)
            continue;

        // Search constant list for current address
        while (j < ARRAY_SIZE(tmc4361A_RegisterConstants) &&
               (tmc4361A_RegisterConstants[j].address < i))
            j++;

        // Abort at end of constant list
        if (j == ARRAY_SIZE(tmc4361A_RegisterConstants))
            break;

        // If we have an entry, fill the cache
        if (tmc4361A_RegisterConstants[j].address == i)
        {
            for (id = 0; id < TMC4361A_IC_CACHE_COUNT; id++)
            {
                uint32_t temp = tmc4361A_RegisterConstants[j].value;
                tmc4361A_cache(id, TMC4361A_CACHE_FILL_DEFAULT, i, &temp);
            }
        }
    }
}

#else
// User must implement their own cache
#endif // TMC4361A_ENABLE_TMC_CACHE

#else
// No cache - stub implementation
static inline bool tmc4361A_cache(uint16_t icID, TMC4361ACacheOp operation,
                                   uint8_t address, uint32_t *value)
{
    (void)icID;
    (void)address;
    (void)operation;
    (void)value;
    return false;
}
#endif // TMC4361A_CACHE

// ============================================================================
// SPI Read/Write Implementation
// ============================================================================

static int32_t readRegisterSPI(uint16_t icID, uint8_t address);
static void writeRegisterSPI(uint16_t icID, uint8_t address, int32_t value);

int32_t tmc4361A_readRegister(uint16_t icID, uint8_t address)
{
    uint32_t value;

    // Read from cache for write-only registers
    if (tmc4361A_cache(icID, TMC4361A_CACHE_READ, address, &value))
        return value;

    return readRegisterSPI(icID, address);
}

void tmc4361A_writeRegister(uint16_t icID, uint8_t address, int32_t value)
{
    writeRegisterSPI(icID, address, value);
}

static void writeRegisterSPI(uint16_t icID, uint8_t address, int32_t value)
{
    uint8_t data[5] = {0};

    data[0] = address | TMC4361A_WRITE_BIT;
    data[1] = 0xFF & (value >> 24);
    data[2] = 0xFF & (value >> 16);
    data[3] = 0xFF & (value >> 8);
    data[4] = 0xFF & (value >> 0);

    // Send write request via HAL callback
    tmc4361A_readWriteSPI(icID, &data[0], sizeof(data));

    // Update status
    tmc4361A_setStatus(icID, &data[0]);

    // Update cache
    tmc4361A_cache(icID, TMC4361A_CACHE_WRITE, address, (uint32_t *)&value);
}

static int32_t readRegisterSPI(uint16_t icID, uint8_t address)
{
    uint8_t data[5] = {0};

    // Clear write bit
    address = address & TMC4361A_ADDRESS_MASK;

    // First SPI transfer: send read request
    data[0] = address;
    tmc4361A_readWriteSPI(icID, &data[0], sizeof(data));

    // Second SPI transfer: receive read reply
    data[0] = address;
    tmc4361A_readWriteSPI(icID, &data[0], sizeof(data));

    // Update status
    tmc4361A_setStatus(icID, &data[0]);

    // Combine bytes to 32-bit value
    return ((int32_t)data[1] << 24) |
           ((int32_t)data[2] << 16) |
           ((int32_t)data[3] << 8)  |
           ((int32_t)data[4]);
}

// ============================================================================
// Cover Interface (for TMC2660 / TMC2240 communication)
// ============================================================================

void tmc4361A_readWriteCover(uint16_t icID, uint8_t *data, size_t length)
{
    // Wait helper
    auto waitCover = []() {
        volatile uint32_t dummy;
        for (uint32_t i = 0; i < 100; i++) {
            dummy = i;
        }
        (void)dummy;
    };

    if (length >= 5)
    {
        // ====================================================================
        // TMC2240: 40-bit cover datagram (5 bytes)
        // data[0] = address byte (bit 7 = write flag)
        // data[1..4] = 32-bit data (MSB first)
        // ====================================================================

        // write COVER_HIGH first (address byte)
        int32_t coverHigh = (int32_t)data[0];
        tmc4361A_writeRegister(icID, TMC4361A_COVER_HIGH, coverHigh);

        // then write COVER_LOW (32-bit data) -- writing COVER_LOW triggers the SPI transfer
        int32_t coverLow = ((int32_t)data[1] << 24) |
                           ((int32_t)data[2] << 16) |
                           ((int32_t)data[3] << 8)  |
                           ((int32_t)data[4]);
        tmc4361A_writeRegister(icID, TMC4361A_COVER_LOW, coverLow);

        // wait for the transfer to complete (40-bit needs a longer wait than 20-bit)
        delayMicroseconds(50);

        // read the response
        int32_t responseHigh = tmc4361A_readRegister(icID, TMC4361A_COVER_DRV_HIGH);
        int32_t responseLow  = tmc4361A_readRegister(icID, TMC4361A_COVER_DRV_LOW);

        data[0] = (uint8_t)(responseHigh & 0xFF);
        data[1] = (responseLow >> 24) & 0xFF;
        data[2] = (responseLow >> 16) & 0xFF;
        data[3] = (responseLow >> 8)  & 0xFF;
        data[4] = (responseLow >> 0)  & 0xFF;
    }
    else if (length >= 3)
    {
        // ====================================================================
        // TMC2660: 20-bit cover datagram (3 bytes, padded to 24 bits)
        // ====================================================================

        // Write to COVER_LOW register (lower 24 bits of cover datagram)
        int32_t coverValue = ((int32_t)data[0] << 16) |
                             ((int32_t)data[1] << 8)  |
                             ((int32_t)data[2]);

        tmc4361A_writeRegister(icID, TMC4361A_COVER_LOW, coverValue);

        // Wait for cover transfer to complete
        waitCover();

        // Read response from COVER_DRV_LOW
        int32_t response = tmc4361A_readRegister(icID, TMC4361A_COVER_DRV_LOW);

        // Extract response bytes
        data[0] = (response >> 16) & 0xFF;
        data[1] = (response >> 8)  & 0xFF;
        data[2] = (response >> 0)  & 0xFF;
    }
}
