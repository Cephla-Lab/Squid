/**
 * Protocol v2.0 implementation.
 *
 * Handles packet reception, validation, command dispatch, and response generation.
 */

#include "protocol_v2.h"
#include "utils/crc16.h"
#include "globals.h"
#include "global_defs.h"
#include "tmc/TMC4361A.h"
#include "def/def_v1.h"

#include <Arduino.h>
#include <string.h>

// External variables from functions.cpp
extern volatile uint8_t trigger_mode;

/***************************************************************************************************/
/**************************************** Receive State Machine ************************************/
/***************************************************************************************************/

enum RxState {
    RX_WAIT_HEADER_0,
    RX_WAIT_HEADER_1,
    RX_WAIT_LENGTH_0,
    RX_WAIT_LENGTH_1,
    RX_WAIT_PAYLOAD,
    RX_WAIT_CRC_0,
    RX_WAIT_CRC_1,
};

static RxState rx_state = RX_WAIT_HEADER_0;
static uint8_t rx_buffer[RX_BUFFER_SIZE];
static uint16_t rx_payload_length = 0;
static uint16_t rx_payload_received = 0;
static uint16_t rx_crc_received = 0;

// Track illumination state (not in current globals, need to add)
static uint8_t illumination_channel_states = 0;
static uint8_t current_led_pattern = 0;

/***************************************************************************************************/
/**************************************** Forward Declarations *************************************/
/***************************************************************************************************/

static void process_command(const uint8_t* payload, uint16_t length);
static void handle_cmd_get_state(uint8_t cmd_id);
static void handle_cmd_reset(uint8_t cmd_id);
static void handle_cmd_get_version(uint8_t cmd_id);
static void handle_unknown_command(uint8_t cmd_id, uint8_t cmd_type);

/***************************************************************************************************/
/**************************************** Initialization *******************************************/
/***************************************************************************************************/

void protocol_v2_init()
{
    rx_state = RX_WAIT_HEADER_0;
    rx_payload_length = 0;
    rx_payload_received = 0;
    rx_crc_received = 0;

    // Initialize illumination state tracking
    illumination_channel_states = 0;
    current_led_pattern = 0;
}

/***************************************************************************************************/
/**************************************** Packet Reception *****************************************/
/***************************************************************************************************/

void protocol_v2_process()
{
    while (SerialUSB.available()) {
        uint8_t byte = SerialUSB.read();

        switch (rx_state) {
            case RX_WAIT_HEADER_0:
                if (byte == PACKET_HEADER_0) {
                    rx_state = RX_WAIT_HEADER_1;
                }
                // Else: discard byte, stay in this state (scanning for header)
                break;

            case RX_WAIT_HEADER_1:
                if (byte == PACKET_HEADER_1) {
                    rx_state = RX_WAIT_LENGTH_0;
                } else if (byte == PACKET_HEADER_0) {
                    // Could be start of new header, stay in WAIT_HEADER_1
                    rx_state = RX_WAIT_HEADER_1;
                } else {
                    // Not a valid header, go back to scanning
                    rx_state = RX_WAIT_HEADER_0;
                }
                break;

            case RX_WAIT_LENGTH_0:
                rx_payload_length = byte;  // Low byte first (little-endian)
                rx_state = RX_WAIT_LENGTH_1;
                break;

            case RX_WAIT_LENGTH_1:
                rx_payload_length |= ((uint16_t)byte << 8);  // High byte

                // Validate length
                if (rx_payload_length == 0 || rx_payload_length > PACKET_MAX_PAYLOAD) {
                    // Invalid length, scan for new header
                    rx_state = RX_WAIT_HEADER_0;
                } else {
                    rx_payload_received = 0;
                    rx_state = RX_WAIT_PAYLOAD;
                }
                break;

            case RX_WAIT_PAYLOAD:
                rx_buffer[rx_payload_received++] = byte;
                if (rx_payload_received >= rx_payload_length) {
                    rx_state = RX_WAIT_CRC_0;
                }
                break;

            case RX_WAIT_CRC_0:
                rx_crc_received = byte;  // Low byte first (little-endian)
                rx_state = RX_WAIT_CRC_1;
                break;

            case RX_WAIT_CRC_1:
                rx_crc_received |= ((uint16_t)byte << 8);  // High byte

                // Calculate CRC over length + payload
                // CRC is calculated over: length_lo, length_hi, payload[0..N-1]
                uint8_t crc_data[PACKET_MAX_PAYLOAD + 2];
                crc_data[0] = rx_payload_length & 0xFF;
                crc_data[1] = (rx_payload_length >> 8) & 0xFF;
                memcpy(&crc_data[2], rx_buffer, rx_payload_length);

                uint16_t calculated_crc = crc16_ccitt(crc_data, rx_payload_length + 2);

                if (calculated_crc == rx_crc_received) {
                    // Valid packet - process command
                    process_command(rx_buffer, rx_payload_length);
                }
                // Else: CRC mismatch, discard packet (could log error)

                // Reset state machine for next packet
                rx_state = RX_WAIT_HEADER_0;
                break;
        }
    }
}

/***************************************************************************************************/
/**************************************** Response Building ****************************************/
/***************************************************************************************************/

void protocol_v2_build_response(ResponsePacket& response, uint8_t cmd_id,
                                 ResponseStatus status, ErrorCode error)
{
    // Clear response structure
    memset(&response, 0, sizeof(ResponsePacket));

    // Command acknowledgment
    response.cmd_id = cmd_id;
    response.status = status;
    response.error_code = error;

    // System mode
    // For now, always NORMAL (no HSA or error state tracking yet)
    response.system_mode = MODE_NORMAL;

    // Axis states - X, Y, Z, W
    // Map current firmware axis indices to response structure

    // X axis (index 0)
    response.axes[0].position_usteps = X_use_encoder ? X_pos : tmc4361A_currentPosition(&tmc4361[x]);
    response.axes[0].target_usteps = X_commanded_target_position;
    if (is_homing_X || is_preparing_for_homing_X) {
        response.axes[0].state = AXIS_HOMING;
    } else if (X_commanded_movement_in_progress) {
        response.axes[0].state = AXIS_MOVING;
    } else {
        response.axes[0].state = AXIS_IDLE;
    }
    response.axes[0].homed = home_X_found ? 1 : 0;

    // Y axis (index 1)
    response.axes[1].position_usteps = Y_use_encoder ? Y_pos : tmc4361A_currentPosition(&tmc4361[y]);
    response.axes[1].target_usteps = Y_commanded_target_position;
    if (is_homing_Y || is_preparing_for_homing_Y) {
        response.axes[1].state = AXIS_HOMING;
    } else if (Y_commanded_movement_in_progress) {
        response.axes[1].state = AXIS_MOVING;
    } else {
        response.axes[1].state = AXIS_IDLE;
    }
    response.axes[1].homed = home_Y_found ? 1 : 0;

    // Z axis (index 2)
    response.axes[2].position_usteps = Z_use_encoder ? Z_pos : tmc4361A_currentPosition(&tmc4361[z]);
    response.axes[2].target_usteps = Z_commanded_target_position;
    if (is_homing_Z || is_preparing_for_homing_Z) {
        response.axes[2].state = AXIS_HOMING;
    } else if (Z_commanded_movement_in_progress) {
        response.axes[2].state = AXIS_MOVING;
    } else {
        response.axes[2].state = AXIS_IDLE;
    }
    response.axes[2].homed = home_Z_found ? 1 : 0;

    // W axis (index 3) - mapped to FILTER2 in v2 but keep at index 3 for compatibility
    response.axes[3].position_usteps = tmc4361A_currentPosition(&tmc4361[w]);
    response.axes[3].target_usteps = W_commanded_target_position;
    if (is_homing_W || is_preparing_for_homing_W) {
        response.axes[3].state = AXIS_HOMING;
    } else if (W_commanded_movement_in_progress) {
        response.axes[3].state = AXIS_MOVING;
    } else {
        response.axes[3].state = AXIS_IDLE;
    }
    response.axes[3].homed = home_W_found ? 1 : 0;

    // DAC values - would need to track these in globals
    // For now, leave as 0 (TODO: add DAC value tracking)

    // Illumination state
    response.illum_on_mask = illumination_channel_states;
    response.led_pattern = current_led_pattern;

    // Joystick state
    response.joystick_delta_x = joystick_delta_x;
    response.joystick_delta_y = joystick_delta_y;
    response.buttons = joystick_button_pressed ? 0x01 : 0x00;
}

void protocol_v2_send_response(const ResponsePacket& response)
{
    // Build packet: header + length + payload + CRC
    uint8_t packet[PACKET_OVERHEAD + RESPONSE_SIZE];
    uint16_t payload_length = RESPONSE_SIZE;

    // Header
    packet[0] = PACKET_HEADER_0;
    packet[1] = PACKET_HEADER_1;

    // Length (little-endian)
    packet[2] = payload_length & 0xFF;
    packet[3] = (payload_length >> 8) & 0xFF;

    // Payload (response structure)
    memcpy(&packet[4], &response, RESPONSE_SIZE);

    // Calculate CRC over length + payload
    uint16_t crc = crc16_ccitt(&packet[2], payload_length + 2);

    // CRC (little-endian)
    packet[4 + RESPONSE_SIZE] = crc & 0xFF;
    packet[5 + RESPONSE_SIZE] = (crc >> 8) & 0xFF;

    // Send packet
    SerialUSB.write(packet, PACKET_OVERHEAD + RESPONSE_SIZE);
}

/***************************************************************************************************/
/**************************************** Command Dispatch *****************************************/
/***************************************************************************************************/

static void process_command(const uint8_t* payload, uint16_t length)
{
    // Minimum payload: cmd_id (1) + cmd_type (1) = 2 bytes
    if (length < 2) {
        // Send error response
        ResponsePacket response;
        protocol_v2_build_response(response, 0, STATUS_REJECTED, ERR_PACKET_TOO_SHORT);
        protocol_v2_send_response(response);
        return;
    }

    uint8_t cmd_id = payload[0];
    uint8_t cmd_type = payload[1];

    switch (cmd_type) {
        case CMD_GET_STATE:
            handle_cmd_get_state(cmd_id);
            break;

        case CMD_RESET:
            handle_cmd_reset(cmd_id);
            break;

        case CMD_GET_VERSION:
            handle_cmd_get_version(cmd_id);
            break;

        // TODO: Add more command handlers as needed

        default:
            handle_unknown_command(cmd_id, cmd_type);
            break;
    }
}

/***************************************************************************************************/
/**************************************** Command Handlers *****************************************/
/***************************************************************************************************/

static void handle_cmd_get_state(uint8_t cmd_id)
{
    // Simply return current state
    ResponsePacket response;
    protocol_v2_build_response(response, cmd_id, STATUS_OK);
    protocol_v2_send_response(response);
}

static void handle_cmd_reset(uint8_t cmd_id)
{
    // Reset firmware state (copied from existing callback_reset)
    mcu_cmd_execution_in_progress = false;
    X_commanded_movement_in_progress = false;
    Y_commanded_movement_in_progress = false;
    Z_commanded_movement_in_progress = false;
    W_commanded_movement_in_progress = false;
    is_homing_X = false;
    is_homing_Y = false;
    is_homing_Z = false;
    is_homing_W = false;
    is_homing_XY = false;
    home_X_found = false;
    home_Y_found = false;
    home_Z_found = false;
    home_W_found = false;
    is_preparing_for_homing_X = false;
    is_preparing_for_homing_Y = false;
    is_preparing_for_homing_Z = false;
    is_preparing_for_homing_W = false;
    trigger_mode = 0;

    // Reset illumination state
    illumination_channel_states = 0;
    current_led_pattern = 0;

    // Send response
    ResponsePacket response;
    protocol_v2_build_response(response, cmd_id, STATUS_OK);
    protocol_v2_send_response(response);
}

static void handle_cmd_get_version(uint8_t cmd_id)
{
    // For now, just return status OK
    // TODO: Could include version info in response
    ResponsePacket response;
    protocol_v2_build_response(response, cmd_id, STATUS_OK);
    protocol_v2_send_response(response);
}

static void handle_unknown_command(uint8_t cmd_id, uint8_t cmd_type)
{
    ResponsePacket response;
    protocol_v2_build_response(response, cmd_id, STATUS_REJECTED, ERR_INVALID_CMD);
    protocol_v2_send_response(response);
}
