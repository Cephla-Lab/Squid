/**
 * Protocol v2.0 constants and structures for firmware-software communication.
 *
 * This header defines the v2 protocol which uses:
 * - Variable-length packets with 0xAA 0xBB header
 * - CRC-16 CCITT for error detection
 * - Full system state in every response
 *
 * For hardware-specific constants (pins, timers, etc.), see constants.h.
 */

#ifndef PROTOCOL_V2_H
#define PROTOCOL_V2_H

#include <stdint.h>

/***************************************************************************************************/
/***************************************** Packet Format *******************************************/
/***************************************************************************************************/

// Packet structure:
// [Header: 0xAA 0xBB] [Length: uint16 LE] [Payload: 1-506 bytes] [CRC-16: uint16 LE]
// Total max: 512 bytes

static const uint8_t PACKET_HEADER_0 = 0xAA;
static const uint8_t PACKET_HEADER_1 = 0xBB;
static const uint16_t PACKET_HEADER = 0xBBAA;  // Little-endian

static const uint16_t PACKET_MAX_PAYLOAD = 506;
static const uint16_t PACKET_MAX_SIZE = 512;
static const uint16_t PACKET_OVERHEAD = 6;  // 2 header + 2 length + 2 CRC

// Receive buffer must be large enough for max packet
static const uint16_t RX_BUFFER_SIZE = 512;

/***************************************************************************************************/
/***************************************** Command Types *******************************************/
/***************************************************************************************************/

// Command payload structure:
// [command_id: uint8] [command_type: uint8] [payload: variable]

enum CommandType : uint8_t {
    // Motion (0x01-0x0F)
    CMD_MOVE_AXIS           = 0x01,
    CMD_MOVE_RELATIVE       = 0x02,
    CMD_HOME_AXIS           = 0x03,
    CMD_STOP_AXIS           = 0x04,
    CMD_STOP_ALL            = 0x05,
    CMD_ENABLE_AXIS         = 0x06,
    CMD_INIT_FILTER_WHEEL   = 0x07,

    // Configuration (0x10-0x1F)
    CMD_SET_AXIS_PARAMS     = 0x10,
    CMD_GET_AXIS_PARAMS     = 0x11,
    CMD_SET_CAMERA_PARAMS   = 0x12,
    CMD_SET_PID_PARAMS      = 0x13,
    CMD_ENABLE_PID          = 0x14,
    CMD_DISABLE_PID         = 0x15,

    // Analog/Digital Output (0x20-0x2F)
    CMD_SET_DAC             = 0x20,
    CMD_SET_TTL             = 0x21,
    CMD_CONFIG_GPIO         = 0x22,
    CMD_WRITE_GPIO          = 0x23,
    CMD_READ_GPIO           = 0x24,
    CMD_SET_DAC_GAIN        = 0x25,

    // Illumination (0x30-0x3F)
    CMD_SET_ILLUMINATION    = 0x30,
    CMD_SET_LED_MATRIX      = 0x31,
    CMD_PULSE_ILLUMINATION  = 0x32,

    // Camera (0x40-0x4F)
    CMD_TRIGGER_CAMERA      = 0x40,

    // HSA (0x50-0x5F) - reserved for future
    CMD_HSA_UPLOAD_HEADER   = 0x50,
    CMD_HSA_UPLOAD_ACTIONS  = 0x51,
    CMD_HSA_UPLOAD_TRIGGER_PROFILE = 0x52,
    CMD_HSA_UPLOAD_INTENSITY= 0x53,
    CMD_HSA_START           = 0x54,
    CMD_HSA_CANCEL          = 0x55,

    // System (0xF0-0xFF)
    CMD_GET_STATE           = 0xF0,
    CMD_ACK_ERROR           = 0xF1,
    CMD_GET_VERSION         = 0xF2,
    CMD_INITIALIZE          = 0xFE,
    CMD_RESET               = 0xFF,
};

/***************************************************************************************************/
/**************************************** Response Status ******************************************/
/***************************************************************************************************/

enum ResponseStatus : uint8_t {
    STATUS_OK           = 0x00,  // Command completed successfully
    STATUS_ACCEPTED     = 0x01,  // Command started (motion in progress)
    STATUS_REJECTED     = 0x02,  // Command rejected (see error_code)
    STATUS_ERROR        = 0x03,  // System in error state
};

enum ErrorCode : uint8_t {
    ERR_NONE            = 0x00,
    ERR_INVALID_CMD     = 0x01,
    ERR_INVALID_AXIS    = 0x02,
    ERR_AXIS_BUSY       = 0x03,
    ERR_AXIS_NOT_HOMED  = 0x04,
    ERR_LIMIT_REACHED   = 0x05,
    ERR_CHECKSUM        = 0x06,
    ERR_PACKET_TOO_SHORT= 0x07,
    ERR_PACKET_TOO_LONG = 0x08,
    ERR_SYSTEM_IN_ERROR = 0x09,
    ERR_HSA_RUNNING     = 0x0A,
    ERR_INTERLOCK       = 0x0B,
};

/***************************************************************************************************/
/****************************************** Axis IDs ***********************************************/
/***************************************************************************************************/

// V2 Axis IDs (prefixed to avoid conflict with legacy constants_protocol.h)
enum V2AxisId : uint8_t {
    V2_AXIS_X       = 0,
    V2_AXIS_Y       = 1,
    V2_AXIS_Z       = 2,
    V2_AXIS_FILTER1 = 3,  // Filter wheel 1
    V2_AXIS_TURRET  = 4,  // Objective turret
    V2_AXIS_FILTER2 = 5,  // Filter wheel 2 (W axis in current firmware)
    V2_AXIS_AUX1    = 6,
    V2_AXIS_AUX2    = 7,
    V2_NUM_AXES     = 8,
};

// Map old axis indices to new
// Current firmware: x=0, y=1, z=2, w=3
// V2 protocol: X=0, Y=1, Z=2, Filter1=3, Turret=4, Filter2/W=5
static const uint8_t LEGACY_W_AXIS = 3;  // Old firmware W axis index

enum AxisState : uint8_t {
    AXIS_IDLE    = 0,
    AXIS_MOVING  = 1,
    AXIS_HOMING  = 2,
    AXIS_ERROR   = 3,
};

/***************************************************************************************************/
/**************************************** System Modes *********************************************/
/***************************************************************************************************/

enum SystemMode : uint8_t {
    MODE_NORMAL  = 0,
    MODE_HSA     = 1,
    MODE_ERROR   = 2,
};

/***************************************************************************************************/
/************************************** Response Structure *****************************************/
/***************************************************************************************************/

// Response is sent after every command
// Structure matches current firmware capabilities

#pragma pack(push, 1)

struct AxisStatus {
    int32_t position_usteps;   // Current position in microsteps
    int32_t target_usteps;     // Target position (for progress tracking)
    uint8_t state;             // AxisState enum
    uint8_t error_code;        // Axis-specific error
    uint8_t homed;             // 0=not homed, 1=homed
    uint8_t reserved;
};  // 12 bytes

struct ResponsePacket {
    // Command acknowledgment (3 bytes)
    uint8_t cmd_id;            // Echo of command_id from request
    uint8_t status;            // ResponseStatus enum
    uint8_t error_code;        // ErrorCode (if status != OK)

    // System state (1 byte)
    uint8_t system_mode;       // SystemMode enum

    // Axis states (4 axes × 12 = 48 bytes)
    // Only X, Y, Z, W for now (expand to 8 later)
    AxisStatus axes[4];

    // DAC values (8 × 2 = 16 bytes)
    uint16_t dac_values[8];

    // Illumination (2 bytes)
    uint8_t illum_on_mask;     // Which illumination channels are ON
    uint8_t led_pattern;       // Current LED matrix pattern (0 = none)

    // Joystick state (5 bytes)
    int16_t joystick_delta_x;
    int16_t joystick_delta_y;
    uint8_t buttons;           // Bit 0 = joystick button pressed

    // Reserved (3 bytes for alignment)
    uint8_t reserved[3];
};  // Total: 78 bytes

#pragma pack(pop)

static const uint16_t RESPONSE_SIZE = sizeof(ResponsePacket);

/***************************************************************************************************/
/*************************************** CRC-16 Function *******************************************/
/***************************************************************************************************/

// CRC-16 CCITT (polynomial 0x1021, initial value 0xFFFF)
uint16_t crc16_ccitt(const uint8_t* data, uint16_t length);

/***************************************************************************************************/
/************************************** Protocol Functions *****************************************/
/***************************************************************************************************/

// Initialize protocol (call from setup())
void protocol_v2_init();

// Process incoming serial data (call from loop())
void protocol_v2_process();

// Send response packet
void protocol_v2_send_response(const ResponsePacket& response);

// Build response packet with current system state
void protocol_v2_build_response(ResponsePacket& response, uint8_t cmd_id,
                                 ResponseStatus status, ErrorCode error = ERR_NONE);

#endif // PROTOCOL_V2_H
