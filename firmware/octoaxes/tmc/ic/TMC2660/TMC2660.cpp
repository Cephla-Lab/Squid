/*
 * TMC2660.cpp
 *
 * TMC2660 stepper driver implementation.
 * Communicates through TMC4361A Cover interface.
 *
 * Created: 2026-01-21
 */

#include "TMC2660.h"
#include "../TMC4361A/TMC4361A.h"

// ============================================================================
// Cache Implementation
// ============================================================================

#if TMC2660_CACHE == 1
#if TMC2660_ENABLE_TMC_CACHE == 1

int32_t tmc2660_shadowRegister[TMC2660_IC_CACHE_COUNT][TMC2660_REGISTER_COUNT];

bool tmc2660_cache(uint16_t icID, TMC2660CacheOp operation, uint8_t address, uint32_t *value)
{
    if (operation == TMC2660_CACHE_READ)
    {
        if (icID >= TMC2660_IC_CACHE_COUNT)
            return false;

        // Only non-readable registers use cache
        if (TMC2660_IS_READABLE(tmc2660_registerAccess[address]))
            return false;

        *value = tmc2660_shadowRegister[icID][address];
        return true;
    }
    else if (operation == TMC2660_CACHE_WRITE || operation == TMC2660_CACHE_FILL_DEFAULT)
    {
        if (icID >= TMC2660_IC_CACHE_COUNT)
            return false;

        tmc2660_shadowRegister[icID][address] = *value;
        return true;
    }

    return false;
}

void tmc2660_initCache(void)
{
    // Initialize shadow registers with preset values
    for (uint8_t icID = 0; icID < TMC2660_IC_CACHE_COUNT; icID++)
    {
        for (uint8_t addr = 0; addr < TMC2660_REGISTER_COUNT; addr++)
        {
            tmc2660_shadowRegister[icID][addr] = tmc2660_sampleRegisterPreset[addr];
        }
    }
}

#endif
#endif

// ============================================================================
// Cover Interface Communication
// ============================================================================

// Send datagram through TMC4361A Cover interface
static void sendCoverDatagram(uint8_t icID, uint32_t datagram)
{
    uint8_t data[3];

    // TMC2660 uses 20-bit datagrams, MSB first
    data[0] = (datagram >> 16) & 0xFF;
    data[1] = (datagram >> 8) & 0xFF;
    data[2] = datagram & 0xFF;

    // Send through TMC4361A Cover interface
    tmc4361A_readWriteCover(icID, data, 3);

    // Extract response (20-bit, right-shifted by 4)
    uint32_t response = ((uint32_t)data[0] << 16) | ((uint32_t)data[1] << 8) | data[2];
    response = (response >> 4) & 0xFFFFF;

    // Determine which response register based on RDSEL
    uint8_t rdsel = TMC2660_GET_RDSEL(tmc2660_shadowRegister[icID][TMC2660_DRVCONF]);

    // Store response in appropriate shadow register
    tmc2660_shadowRegister[icID][rdsel] = response;
    tmc2660_shadowRegister[icID][TMC2660_RESPONSE_LATEST] = response;
}

// ============================================================================
// Register Read/Write Implementation
// ============================================================================

void tmc2660_writeRegister(uint8_t icID, uint8_t address, uint32_t value)
{
    // Only write to write-only registers (addresses 8-F)
    if (TMC2660_IS_READONLY_REGISTER(address))
        return;

    // Mask to 20 bits
    value &= 0x0FFFFF;

    // Cache the value
    tmc2660_cache(icID, TMC2660_CACHE_WRITE, address, &value);

    // Construct datagram: address bits are encoded in the value
    // The address mapping: DRVCTRL=8, CHOPCONF=C, SMARTEN=D, SGCSCONF=E, DRVCONF=F
    // Real address in datagram: (address & 0x07) << 17
    uint32_t datagram = TMC2660_DATAGRAM((address & 0x07), value);

    // Send through Cover interface
    sendCoverDatagram(icID, datagram);
}

uint32_t tmc2660_readRegister(uint8_t icID, uint8_t address)
{
    if (icID >= TMC2660_IC_CACHE_COUNT || address >= TMC2660_REGISTER_COUNT)
        return 0;

    uint32_t value;

    // Read from cache for write-only registers
    if (tmc2660_cache(icID, TMC2660_CACHE_READ, address, &value))
        return value;

    // For response registers, return cached value
    // (They are updated automatically after each write)
    return tmc2660_shadowRegister[icID][address];
}

uint8_t tmc2660_getStatusBits(uint8_t icID)
{
    if (icID >= TMC2660_IC_CACHE_COUNT)
        return 0;

    return tmc2660_shadowRegister[icID][TMC2660_RESPONSE_LATEST] & TMC2660_STATUS_MASK;
}

// ============================================================================
// High-Level Configuration API
// ============================================================================

void tmc2660_initDriver(uint8_t icID)
{
    // Initialize cache
    tmc2660_initCache();

    // Write default configuration
    // DRVCONF: RDSEL=0 (microstep position), VSENSE=1 (low sense resistor range)
    uint32_t drvconf = TMC2660_SET_RDSEL(0) | TMC2660_SET_VSENSE(1);
    tmc2660_writeRegister(icID, TMC2660_DRVCONF, drvconf);

    // CHOPCONF: TBL=2, HEND=3, HSTRT=4, TOFF=5 (standard SpreadCycle)
    uint32_t chopconf = TMC2660_SET_TBL(2) | TMC2660_SET_HEND(3) |
                        TMC2660_SET_HSTRT(4) | TMC2660_SET_TOFF(5);
    tmc2660_writeRegister(icID, TMC2660_CHOPCONF, chopconf);

    // SGCSCONF: CS=16 (mid current), SGT=0
    uint32_t sgcsconf = TMC2660_SET_CS(16) | TMC2660_SET_SGT(0);
    tmc2660_writeRegister(icID, TMC2660_SGCSCONF, sgcsconf);

    // SMARTEN: Disabled by default
    uint32_t smarten = 0;
    tmc2660_writeRegister(icID, TMC2660_SMARTEN, smarten);

    // DRVCTRL: 256 microsteps, interpolation enabled
    uint32_t drvctrl = TMC2660_SET_MRES(0) | TMC2660_SET_INTERPOL(1);
    tmc2660_writeRegister(icID, TMC2660_DRVCTRL, drvctrl);
}

void tmc2660_setRunCurrent(uint8_t icID, uint8_t current)
{
    if (current > 31) current = 31;

    uint32_t value = tmc2660_readRegister(icID, TMC2660_SGCSCONF);
    value &= ~TMC2660_SET_CS(0x1F);  // Clear CS field
    value |= TMC2660_SET_CS(current);
    tmc2660_writeRegister(icID, TMC2660_SGCSCONF, value);
}

void tmc2660_setMicrostepResolution(uint8_t icID, uint8_t mres)
{
    if (mres > 8) mres = 8;

    uint32_t value = tmc2660_readRegister(icID, TMC2660_DRVCTRL);
    value &= ~TMC2660_SET_MRES(0x0F);  // Clear MRES field
    value |= TMC2660_SET_MRES(mres);
    tmc2660_writeRegister(icID, TMC2660_DRVCTRL, value);
}

void tmc2660_setInterpolation(uint8_t icID, bool enable)
{
    uint32_t value = tmc2660_readRegister(icID, TMC2660_DRVCTRL);
    value &= ~TMC2660_SET_INTERPOL(1);  // Clear INTPOL bit
    value |= TMC2660_SET_INTERPOL(enable ? 1 : 0);
    tmc2660_writeRegister(icID, TMC2660_DRVCTRL, value);
}

void tmc2660_setChopperConfig(uint8_t icID, uint8_t toff, uint8_t hstrt, int8_t hend, uint8_t tbl)
{
    // Clamp values
    if (toff > 15) toff = 15;
    if (hstrt > 7) hstrt = 7;
    if (hend < -3) hend = -3;
    if (hend > 12) hend = 12;
    if (tbl > 3) tbl = 3;

    // HEND is stored as hend + 3 (offset)
    uint8_t hend_reg = (uint8_t)(hend + 3);

    uint32_t value = TMC2660_SET_TOFF(toff) | TMC2660_SET_HSTRT(hstrt) |
                     TMC2660_SET_HEND(hend_reg) | TMC2660_SET_TBL(tbl);
    tmc2660_writeRegister(icID, TMC2660_CHOPCONF, value);
}

void tmc2660_enableDriver(uint8_t icID, bool enable)
{
    uint32_t value = tmc2660_readRegister(icID, TMC2660_CHOPCONF);

    if (enable)
    {
        // Ensure TOFF > 0 to enable driver
        if (TMC2660_GET_TOFF(value) == 0)
        {
            value |= TMC2660_SET_TOFF(5);  // Default TOFF
        }
    }
    else
    {
        // Set TOFF = 0 to disable driver
        value &= ~TMC2660_SET_TOFF(0x0F);
    }

    tmc2660_writeRegister(icID, TMC2660_CHOPCONF, value);
}

void tmc2660_setStallGuardThreshold(uint8_t icID, int8_t threshold)
{
    // Clamp to valid range
    if (threshold < -64) threshold = -64;
    if (threshold > 63) threshold = 63;

    // SGT is a 7-bit signed value stored in bits 8-14
    uint8_t sgt = (uint8_t)(threshold & 0x7F);

    uint32_t value = tmc2660_readRegister(icID, TMC2660_SGCSCONF);
    value &= ~TMC2660_SET_SGT(0x7F);
    value |= TMC2660_SET_SGT(sgt);
    tmc2660_writeRegister(icID, TMC2660_SGCSCONF, value);
}

void tmc2660_setStallGuardFilter(uint8_t icID, bool enable)
{
    uint32_t value = tmc2660_readRegister(icID, TMC2660_SGCSCONF);
    value &= ~TMC2660_SET_SFILT(1);
    value |= TMC2660_SET_SFILT(enable ? 1 : 0);
    tmc2660_writeRegister(icID, TMC2660_SGCSCONF, value);
}

// ============================================================================
// Status Detection API
// ============================================================================

bool tmc2660_isStalled(uint8_t icID)
{
    uint8_t status = tmc2660_getStatusBits(icID);
    return TMC2660_GET_SGF(status) != 0;
}

bool tmc2660_isOvertemperature(uint8_t icID)
{
    uint8_t status = tmc2660_getStatusBits(icID);
    return TMC2660_GET_OT(status) != 0;
}

bool tmc2660_isOvertemperatureWarning(uint8_t icID)
{
    uint8_t status = tmc2660_getStatusBits(icID);
    return TMC2660_GET_OTPW(status) != 0;
}

bool tmc2660_isShortToGroundA(uint8_t icID)
{
    uint8_t status = tmc2660_getStatusBits(icID);
    return TMC2660_GET_S2GA(status) != 0;
}

bool tmc2660_isShortToGroundB(uint8_t icID)
{
    uint8_t status = tmc2660_getStatusBits(icID);
    return TMC2660_GET_S2GB(status) != 0;
}

bool tmc2660_isOpenLoadA(uint8_t icID)
{
    uint8_t status = tmc2660_getStatusBits(icID);
    return TMC2660_GET_OLA(status) != 0;
}

bool tmc2660_isOpenLoadB(uint8_t icID)
{
    uint8_t status = tmc2660_getStatusBits(icID);
    return TMC2660_GET_OLB(status) != 0;
}

bool tmc2660_isStandstill(uint8_t icID)
{
    uint8_t status = tmc2660_getStatusBits(icID);
    return TMC2660_GET_STST(status) != 0;
}

// ============================================================================
// Diagnostic API
// ============================================================================

uint16_t tmc2660_getStallGuardValue(uint8_t icID)
{
    // Request SG2 value by setting RDSEL=1
    uint32_t drvconf = tmc2660_readRegister(icID, TMC2660_DRVCONF);
    uint8_t oldRdsel = TMC2660_GET_RDSEL(drvconf);

    // Temporarily switch to RDSEL=1 (StallGuard)
    drvconf &= ~TMC2660_SET_RDSEL(0x03);
    drvconf |= TMC2660_SET_RDSEL(1);
    tmc2660_writeRegister(icID, TMC2660_DRVCONF, drvconf);

    // Read StallGuard value (10-bit, in RESPONSE1)
    uint32_t response = tmc2660_readRegister(icID, TMC2660_RESPONSE1);
    uint16_t sg2 = TMC2660_GET_SG(response);

    // Restore original RDSEL
    drvconf &= ~TMC2660_SET_RDSEL(0x03);
    drvconf |= TMC2660_SET_RDSEL(oldRdsel);
    tmc2660_writeRegister(icID, TMC2660_DRVCONF, drvconf);

    return sg2;
}

uint16_t tmc2660_getMicrostepPosition(uint8_t icID)
{
    // Request microstep position by setting RDSEL=0
    uint32_t drvconf = tmc2660_readRegister(icID, TMC2660_DRVCONF);
    uint8_t oldRdsel = TMC2660_GET_RDSEL(drvconf);

    // Temporarily switch to RDSEL=0 (Microstep)
    drvconf &= ~TMC2660_SET_RDSEL(0x03);
    drvconf |= TMC2660_SET_RDSEL(0);
    tmc2660_writeRegister(icID, TMC2660_DRVCONF, drvconf);

    // Read microstep position (10-bit, in RESPONSE0)
    uint32_t response = tmc2660_readRegister(icID, TMC2660_RESPONSE0);
    uint16_t mstep = TMC2660_GET_MSTEP(response);

    // Restore original RDSEL
    drvconf &= ~TMC2660_SET_RDSEL(0x03);
    drvconf |= TMC2660_SET_RDSEL(oldRdsel);
    tmc2660_writeRegister(icID, TMC2660_DRVCONF, drvconf);

    return mstep;
}

uint8_t tmc2660_getActualCurrentScale(uint8_t icID)
{
    // Request current scale by setting RDSEL=2
    uint32_t drvconf = tmc2660_readRegister(icID, TMC2660_DRVCONF);
    uint8_t oldRdsel = TMC2660_GET_RDSEL(drvconf);

    // Temporarily switch to RDSEL=2 (CoolStep)
    drvconf &= ~TMC2660_SET_RDSEL(0x03);
    drvconf |= TMC2660_SET_RDSEL(2);
    tmc2660_writeRegister(icID, TMC2660_DRVCONF, drvconf);

    // Read SE value (5-bit, in RESPONSE2)
    uint32_t response = tmc2660_readRegister(icID, TMC2660_RESPONSE2);
    uint8_t se = TMC2660_GET_SE(response);

    // Restore original RDSEL
    drvconf &= ~TMC2660_SET_RDSEL(0x03);
    drvconf |= TMC2660_SET_RDSEL(oldRdsel);
    tmc2660_writeRegister(icID, TMC2660_DRVCONF, drvconf);

    return se;
}
