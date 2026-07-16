#ifndef SERIAL_PROTOCOL_HANDLER_H
#define SERIAL_PROTOCOL_HANDLER_H

#include <Arduino.h>

class SerialProtocolHandler {
public:
    SerialProtocolHandler();
    
    // Initialize serial communication
    void begin(long baudRate = 2000000, uint32_t timeout = 200);
    
    // Check for and process a new command
    bool checkForCommand();
    
    // Get the command ID
    byte getCommandId() const { return cmd_id; }
    
    // Get the command execution status
    bool isCommandInProgress() const { return mcu_cmd_execution_in_progress; }
    
    // Get the checksum-error status
    bool hasChecksumError() const { return checksum_error; }
    
    // Get the received command data
    const byte* getCommandData() const { return buffer_rx; }
    
    // Send a response message
    void sendResponse(byte cmd_id, byte status,
                      int32_t x_pos, int32_t y_pos, int32_t z_pos,
                      int32_t w_pos = 0,
                      bool joystick_button_pressed = false);

    // 10ms periodic position reporting (called in loop())
    void send_position_update();
    
    // Send debug info
    void sendDebugInfo(const char* format, ...);
    
    // Set the command-execution-completed status
    void setCommandInProgress(bool in_progress) { 
        mcu_cmd_execution_in_progress = in_progress; 
    }
    
    // Get the command length
    static int getCommandLength() { return CMD_LENGTH; }

    // Get the message length
    static int getMessageLength() { return MSG_LENGTH; }
    
    // Serial debug-info handler
    void processSerialCommands();
    void processSerialDebugCommands();
    void processSerialStandardCommands();
    // CRC checksum function
    uint8_t crc8ccitt(byte *data, uint8_t len);

private:
    static const int CMD_LENGTH = 8;
    static const int MSG_LENGTH = 24;
    
    // Protocol identifiers
    static const byte DEBUG_PROTOCOL_HEADER_1 = 0x55;
    static const byte DEBUG_PROTOCOL_HEADER_2 = 0xAA;
    
    byte buffer_rx[512];
    volatile int buffer_rx_ptr;
    byte cmd_id;
    bool mcu_cmd_execution_in_progress;
    bool checksum_error;
    elapsedMicros _us_since_last_pos_update;
    // the any_moving computed by the previous send_position_update, used to detect the falling edge (movement-complete edge)
    // on the falling edge, immediately send an extra COMPLETED frame, saving the 0-10ms heartbeat wait
    bool _last_any_moving = false;
    
    // Debug-command buffer
    String debugCommandBuffer;
};

extern SerialProtocolHandler serialProtocol;

#endif
