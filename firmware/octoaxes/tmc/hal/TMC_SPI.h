/*
 * TMC_SPI.h
 *
 * Hardware Abstraction Layer for TMC SPI communication.
 * Provides SPI callback functions for TMC4361A and TMC2660 drivers.
 *
 * Created: 2026-01-21
 */

#ifndef TMC_SPI_H_
#define TMC_SPI_H_

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

// ============================================================================
// IC Count Configuration
// ============================================================================

#ifdef USE_HC154_CS
// squid++ dual-camera 8 axes: Y, X, Z1, F1, Z2, F2, R, T
#define TMC4361A_IC_COUNT   8
#define TMC2660_IC_COUNT    8
#else
// octoaxes 6 axes: Y, X, Z, W, W2, E1 (2026-05-26 W2 took over the original E4 hardware CS=pin 16/CLK=pin 28;
// 2026-05-29 E1 enabled as the objective turret (Objectives), CS=pin 19/CLK=pin 28; the original E3 is not enabled)
#define TMC4361A_IC_COUNT   6
#define TMC2660_IC_COUNT    6
#endif

// ============================================================================
// IC Identifier Enumeration
// ============================================================================

typedef enum {
    // Note: icID is actually determined by the addAxis() call order (see TMC_SPI.cpp tmc_ic_configs[]).
    // current octoaxes order: X(0) Y(1) Z(2) W(3) W2(4) E1(5). The enum below is for semantic reference only.
    IC_X  = 0,   // X axis
    IC_Y  = 1,   // Y axis
    IC_Z  = 2,   // Z axis
    IC_W  = 3,   // W axis (Filter wheel 1)
    IC_W2 = 4,   // W2 axis (Filter wheel 2, took over the original E4 hardware)
    IC_E1 = 5    // Expand 1 (Objectives, objective turret)
} TMC_IC_ID;

// ============================================================================
// IC Configuration Structure
// ============================================================================

typedef struct {
    uint8_t csPin;          // Chip select pin number
    uint8_t clockSource;    // 0 = standard clock (Pin 37), 1 = expand clock (Pin 28)
} TMC_IC_Config;

// ============================================================================
// Global IC Configuration Array (defined in TMC_SPI.cpp)
// ============================================================================

extern const TMC_IC_Config tmc_ic_configs[TMC4361A_IC_COUNT];

// ============================================================================
// Initialization
// ============================================================================

/**
 * @brief Initialize SPI and all CS pins
 *
 * Configures all CS pins as OUTPUT and sets them HIGH.
 * Initializes SPI bus with appropriate settings.
 */
void tmc_spi_init(void);

// ============================================================================
// TMC4361A SPI Callbacks
// ============================================================================

/**
 * @brief SPI read/write callback for TMC4361A
 *
 * This function is called by the TMC4361A driver to perform SPI transfers.
 * It handles CS pin control and full-duplex data transfer.
 *
 * @param icID      IC identifier (0 to TMC4361A_IC_COUNT-1)
 * @param data      Buffer for data to send/receive (modified in place)
 * @param dataLength Number of bytes to transfer
 */
void tmc4361A_readWriteSPI(uint16_t icID, uint8_t *data, size_t dataLength);

/**
 * @brief Status callback for TMC4361A
 *
 * Called after each SPI transfer to process the status byte.
 * Can be used for diagnostics and error monitoring.
 *
 * @param icID  IC identifier
 * @param data  Buffer containing the response data (first byte is status)
 */
void tmc4361A_setStatus(uint16_t icID, uint8_t *data);

// ============================================================================
// TMC2660 SPI Callbacks (for direct SPI mode, reserved for future use)
// ============================================================================

/**
 * @brief SPI read/write callback for TMC2660 (direct SPI mode)
 *
 * Reserved for future use. Currently TMC2660 is controlled through
 * TMC4361A Cover interface.
 *
 * @param icID      IC identifier
 * @param data      Buffer for data to send/receive
 * @param dataLength Number of bytes to transfer
 */
void tmc2660_readWriteSPI(uint16_t icID, uint8_t *data, size_t dataLength);

// ============================================================================
// Utility Functions
// ============================================================================

/**
 * @brief Get CS pin for a given IC ID
 *
 * @param icID  IC identifier
 * @return CS pin number, or 0xFF if invalid
 */
uint8_t tmc_getCSPin(uint16_t icID);

/**
 * @brief Get clock source for a given IC ID
 *
 * @param icID  IC identifier
 * @return 0 for standard clock, 1 for expand clock, 0xFF if invalid
 */
uint8_t tmc_getClockSource(uint16_t icID);

/**
 * @brief Check if IC ID is valid
 *
 * @param icID  IC identifier
 * @return true if valid, false otherwise
 */
bool tmc_isValidICID(uint16_t icID);

// ============================================================================
// Debug/Status (Optional)
// ============================================================================

#ifdef TMC_SPI_DEBUG
/**
 * @brief Get last SPI status byte for an IC
 *
 * @param icID  IC identifier
 * @return Last status byte received
 */
uint8_t tmc_getLastStatus(uint16_t icID);

/**
 * @brief Get SPI transfer count for an IC
 *
 * @param icID  IC identifier
 * @return Number of SPI transfers performed
 */
uint32_t tmc_getTransferCount(uint16_t icID);
#endif

#ifdef __cplusplus
}
#endif

#endif /* TMC_SPI_H_ */
