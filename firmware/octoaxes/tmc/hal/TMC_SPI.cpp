/*
 * TMC_SPI.cpp
 *
 * Implementation of SPI Hardware Abstraction Layer for TMC ICs.
 *
 * Created: 2026-01-21
 */

#include "TMC_SPI.h"
#include <SPI.h>
#include <Arduino.h>

#ifdef USE_HC154_CS
// octoaxesplus (squid++ dual-camera): the csPin field semantics change to a 74HC154 channel number (0-15)
// before a transaction call Pins::hc154_select(ch) to select the target channel; after the transaction return to the idle channel
// Note: tmc/ is a symlink from octoaxesplus to octoaxes; the relative path "../../config.h"
// after symlink resolution points to octoaxes/config.h (no HC154 symbols),
// so use a bare include and rely on the PlatformIO src_dir search path
#include "config.h"
#endif

// ============================================================================
// Configuration Constants (from config.h Pins namespace)
// ============================================================================

#ifndef USE_HC154_CS
// octoaxes direct GPIO CS
#define PIN_CS_X     41
#define PIN_CS_Y     36
#define PIN_CS_Z     35
#define PIN_CS_W     34
#define PIN_CS_W2    16   // 2026-05-26 W2 reuses the original EXPAND4 hardware (CS=pin 16, CLK=pin 28),
                          // fully consistent with legacy Squid pin_TMC4361_CS[4]=16 / pin_TMC4361_CLK_W2=28
#define PIN_CS_E1    19   // 2026-05-29 E1 objective turret (CS=pin 19 = EXPAND1_AXIS_CS, CLK=pin 28)
#endif

// Clock source identifiers
#define CLOCK_STANDARD  0   // Pin 37
#define CLOCK_EXPAND    1   // Pin 28

// SPI Configuration
#define TMC_SPI_SPEED       500000      // 500 kHz
#define TMC_SPI_MODE        SPI_MODE0   // CPOL=0, CPHA=0
#define TMC_SPI_BIT_ORDER   MSBFIRST
#define TMC_CS_DELAY_US     100         // Delay after CS low

// ============================================================================
// IC Configuration Array
// ============================================================================

const TMC_IC_Config tmc_ic_configs[TMC4361A_IC_COUNT] = {
    // Note: the order must match the axisManager.addAxis() call order!
#ifdef USE_HC154_CS
    // squid++ XYZW1W2 + E1 six axes: addAxis order Y(0), X(1), Z(2), W1(3), W2(4), E1(5)
    // single clock set (EXPAND_CLK removed), everything uses CLOCK_STANDARD
    // 2026-06-02 E1 objective turret enabled: the icID=5 slot connects to HC154_AXIS_R (ch3); icID 6-7 are still placeholders
    { .csPin = (uint8_t)Pins::HC154_AXIS_Y,  .clockSource = CLOCK_STANDARD },  // icID=0
    { .csPin = (uint8_t)Pins::HC154_AXIS_X,  .clockSource = CLOCK_STANDARD },  // icID=1
    { .csPin = (uint8_t)Pins::HC154_AXIS_Z1, .clockSource = CLOCK_STANDARD },  // icID=2 (axisName="Z")
    { .csPin = (uint8_t)Pins::HC154_AXIS_W1, .clockSource = CLOCK_STANDARD },  // icID=3 (ch6, uses the original Z2 CS)
    { .csPin = (uint8_t)Pins::HC154_AXIS_W2, .clockSource = CLOCK_STANDARD },  // icID=4 (ch4, uses the original T CS)
    { .csPin = (uint8_t)Pins::HC154_AXIS_R,  .clockSource = CLOCK_STANDARD },  // icID=5 (ch3, objective turret axisName="Turret")
    { .csPin = (uint8_t)Pins::HC154_AXIS_F2, .clockSource = CLOCK_STANDARD },  // icID=6 placeholder
    { .csPin = (uint8_t)Pins::HC154_AXIS_F1, .clockSource = CLOCK_STANDARD },  // icID=7 placeholder
#else
    // octoaxes 6 axes: add order Y(0), X(1), Z(2), W(3), W2(4), E1(5)
    // W2/E1 use CLOCK_EXPAND (pin 28); W2 is consistent with legacy Squid pin_TMC4361_CLK_W2,
    // E1 (objective) shares the same expansion clock line (multiple TMC4361A chips can share a clock)
    { .csPin = PIN_CS_Y,  .clockSource = CLOCK_STANDARD },
    { .csPin = PIN_CS_X,  .clockSource = CLOCK_STANDARD },
    { .csPin = PIN_CS_Z,  .clockSource = CLOCK_STANDARD },
    { .csPin = PIN_CS_W,  .clockSource = CLOCK_STANDARD },
    { .csPin = PIN_CS_W2, .clockSource = CLOCK_EXPAND  },
    { .csPin = PIN_CS_E1, .clockSource = CLOCK_EXPAND  },  // icID=5 objective turret
#endif
};

// ============================================================================
// Debug Status Storage (Optional)
// ============================================================================

#ifdef TMC_SPI_DEBUG
static uint8_t  tmc_lastStatus[TMC4361A_IC_COUNT] = {0};
static uint32_t tmc_transferCount[TMC4361A_IC_COUNT] = {0};
#endif

// ============================================================================
// Initialization
// ============================================================================

void tmc_spi_init(void)
{
#ifdef USE_HC154_CS
    // 74HC154 address-pin init; all channel outputs default to 0
    Pins::hc154_init();
#else
    // Initialize all CS pins as OUTPUT and set HIGH (inactive)
    for (uint8_t i = 0; i < TMC4361A_IC_COUNT; i++) {
        pinMode(tmc_ic_configs[i].csPin, OUTPUT);
        digitalWrite(tmc_ic_configs[i].csPin, HIGH);
    }
#endif

    // Initialize SPI bus
    SPI.begin();
}

// ============================================================================
// TMC4361A SPI Callbacks
// ============================================================================

void tmc4361A_readWriteSPI(uint16_t icID, uint8_t *data, size_t dataLength)
{
    // Validate IC ID
    if (icID >= TMC4361A_IC_COUNT) {
        return;
    }

    uint8_t csPin = tmc_ic_configs[icID].csPin;

    // Begin SPI transaction
    SPI.beginTransaction(SPISettings(TMC_SPI_SPEED, TMC_SPI_BIT_ORDER, TMC_SPI_MODE));

#ifdef USE_HC154_CS
    // 74HC154: select the target channel; the other channels are automatically pulled high (always exactly one low)
    Pins::hc154_select(csPin);
#else
    // Assert CS (active low)
    digitalWrite(csPin, LOW);
#endif

    // Wait for chip ready
    delayMicroseconds(TMC_CS_DELAY_US);

    // Full-duplex transfer
    for (size_t i = 0; i < dataLength; i++) {
        data[i] = SPI.transfer(data[i]);
    }

#ifdef USE_HC154_CS
    // return to EXPAND_NSCS1 (a placeholder channel with no SPI device attached)
    Pins::hc154_select((uint8_t)Pins::HC154_EXPAND_NSCS1);
#else
    // Deassert CS
    digitalWrite(csPin, HIGH);
#endif

    // End SPI transaction
    SPI.endTransaction();

#ifdef TMC_SPI_DEBUG
    tmc_transferCount[icID]++;
#endif
}

void tmc4361A_setStatus(uint16_t icID, uint8_t *data)
{
    // Validate IC ID
    if (icID >= TMC4361A_IC_COUNT) {
        return;
    }

#ifdef TMC_SPI_DEBUG
    // Store status byte (first byte of response)
    tmc_lastStatus[icID] = data[0];
#endif

    // Status byte interpretation (for future error handling):
    // Bit 7: RESET_FLAG - Indicates reset occurred
    // Bit 6: DRV_ERR - Driver error
    // Bit 5: UV_SF - Undervoltage
    // Bit 4-0: Various status flags

    // Currently just store for debugging, can be extended for error handling
    (void)data;  // Suppress unused parameter warning if debug disabled
}

// ============================================================================
// TMC2660 SPI Callbacks (Reserved)
// ============================================================================

void tmc2660_readWriteSPI(uint16_t icID, uint8_t *data, size_t dataLength)
{
    // Reserved for direct SPI communication with TMC2660
    // Currently TMC2660 is controlled through TMC4361A Cover interface
    //
    // If direct SPI is needed in the future, implement similar to tmc4361A_readWriteSPI
    // but with TMC2660-specific timing and data format (20-bit datagrams)

    (void)icID;
    (void)data;
    (void)dataLength;
}

// ============================================================================
// Utility Functions
// ============================================================================

uint8_t tmc_getCSPin(uint16_t icID)
{
    if (icID >= TMC4361A_IC_COUNT) {
        return 0xFF;  // Invalid
    }
    return tmc_ic_configs[icID].csPin;
}

uint8_t tmc_getClockSource(uint16_t icID)
{
    if (icID >= TMC4361A_IC_COUNT) {
        return 0xFF;  // Invalid
    }
    return tmc_ic_configs[icID].clockSource;
}

bool tmc_isValidICID(uint16_t icID)
{
    return (icID < TMC4361A_IC_COUNT);
}

// ============================================================================
// Debug Functions (Optional)
// ============================================================================

#ifdef TMC_SPI_DEBUG
uint8_t tmc_getLastStatus(uint16_t icID)
{
    if (icID >= TMC4361A_IC_COUNT) {
        return 0xFF;
    }
    return tmc_lastStatus[icID];
}

uint32_t tmc_getTransferCount(uint16_t icID)
{
    if (icID >= TMC4361A_IC_COUNT) {
        return 0;
    }
    return tmc_transferCount[icID];
}
#endif
