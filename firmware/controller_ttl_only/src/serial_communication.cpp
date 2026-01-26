/**
 * TTL-Only Firmware - Serial Communication Implementation
 */

#include "serial_communication.h"
#include "commands.h"

void process_serial_message()
{
    while (SerialUSB.available())
    {
        buffer_rx[buffer_rx_ptr] = SerialUSB.read();
        buffer_rx_ptr = buffer_rx_ptr + 1;

        if (buffer_rx_ptr == CMD_LENGTH)
        {
            buffer_rx_ptr = 0;
            cmd_id = buffer_rx[0];

            // Validate CRC
            uint8_t checksum = crc8ccitt(buffer_rx, CMD_LENGTH - 1);
            if (checksum != buffer_rx[CMD_LENGTH - 1])
            {
                checksum_error = true;
                // Empty serial buffer due to possible byte-level desync
                while (SerialUSB.available())
                    SerialUSB.read();
                return;
            }
            else
            {
                checksum_error = false;
            }

            // Dispatch command
            CommandCallback p_callback = cmd_map[buffer_rx[1]];
            if (!p_callback)
            {
                callback_default();
            }
            else
            {
                p_callback();
            }
        }
    }
}

void send_position_update()
{
    if (us_since_last_pos_update > interval_send_pos_update)
    {
        us_since_last_pos_update = 0;

        // Build response packet
        buffer_tx[0] = cmd_id;

        // Status: checksum error, or completed (no in-progress for TTL-only)
        if (checksum_error)
            buffer_tx[1] = CMD_CHECKSUM_ERROR;
        else
            buffer_tx[1] = COMPLETED_WITHOUT_ERRORS;

        // X, Y, Z positions - return zeros (no stage hardware)
        // X position (bytes 2-5)
        buffer_tx[2] = 0;
        buffer_tx[3] = 0;
        buffer_tx[4] = 0;
        buffer_tx[5] = 0;

        // Y position (bytes 6-9)
        buffer_tx[6] = 0;
        buffer_tx[7] = 0;
        buffer_tx[8] = 0;
        buffer_tx[9] = 0;

        // Z position (bytes 10-13)
        buffer_tx[10] = 0;
        buffer_tx[11] = 0;
        buffer_tx[12] = 0;
        buffer_tx[13] = 0;

        // Theta/W position (bytes 14-17) - zeros
        buffer_tx[14] = 0;
        buffer_tx[15] = 0;
        buffer_tx[16] = 0;
        buffer_tx[17] = 0;

        // Buttons/flags (byte 18) - no joystick
        buffer_tx[18] = 0;

        // Reserved bytes 19-22
        buffer_tx[19] = 0;
        buffer_tx[20] = 0;
        buffer_tx[21] = 0;
        buffer_tx[22] = 0;

        // CRC (byte 23)
        uint8_t tx_checksum = crc8ccitt(buffer_tx, MSG_LENGTH - 1);
        buffer_tx[MSG_LENGTH - 1] = tx_checksum;

        SerialUSB.write(buffer_tx, MSG_LENGTH);
    }
}
