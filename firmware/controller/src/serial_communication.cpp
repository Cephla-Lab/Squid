#include "serial_communication.h"

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
      uint8_t checksum = crc8ccitt(buffer_rx, CMD_LENGTH - 1);
      if (checksum != buffer_rx[CMD_LENGTH - 1])
      {
        checksum_error = true;
        // empty the serial buffer because byte-level out-of-sync can also cause this error
        while (SerialUSB.available())
          SerialUSB.read();
        return;
      }
      else
      {
        checksum_error = false;
      }

      CommandCallback p_callback = cmd_map[buffer_rx[1]];
      if (!p_callback) {
        callback_default();
      } else {
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

    buffer_tx[0] = cmd_id;
    if (checksum_error)
      buffer_tx[1] = CMD_CHECKSUM_ERROR; // cmd_execution_status
    else
      buffer_tx[1] = mcu_cmd_execution_in_progress ? IN_PROGRESS : COMPLETED_WITHOUT_ERRORS; // cmd_execution_status

    uint32_t X_pos_int32t = uint32_t( X_use_encoder ? X_pos : int32_t(tmc4361A_currentPosition(&tmc4361[x])) );
    buffer_tx[2] = byte(X_pos_int32t >> 24);
    buffer_tx[3] = byte((X_pos_int32t >> 16) % 256);
    buffer_tx[4] = byte((X_pos_int32t >> 8) % 256);
    buffer_tx[5] = byte((X_pos_int32t) % 256);

    uint32_t Y_pos_int32t = uint32_t( Y_use_encoder ? Y_pos : int32_t(tmc4361A_currentPosition(&tmc4361[y])) );
    buffer_tx[6] = byte(Y_pos_int32t >> 24);
    buffer_tx[7] = byte((Y_pos_int32t >> 16) % 256);
    buffer_tx[8] = byte((Y_pos_int32t >> 8) % 256);
    buffer_tx[9] = byte((Y_pos_int32t) % 256);

    uint32_t Z_pos_int32t = uint32_t( Z_use_encoder ? Z_pos : int32_t(tmc4361A_currentPosition(&tmc4361[z])) );
    buffer_tx[10] = byte(Z_pos_int32t >> 24);
    buffer_tx[11] = byte((Z_pos_int32t >> 16) % 256);
    buffer_tx[12] = byte((Z_pos_int32t >> 8) % 256);
    buffer_tx[13] = byte((Z_pos_int32t) % 256);

    // fail-safe clearing of the joystick_button_pressed bit (in case the ack is not received)
    if (joystick_button_pressed && millis() - joystick_button_pressed_timestamp > 1000)
      joystick_button_pressed = false;

    buffer_tx[18] &= ~ (1 << BIT_POS_JOYSTICK_BUTTON); // clear the joystick button bit
    buffer_tx[18] = buffer_tx[18] | joystick_button_pressed << BIT_POS_JOYSTICK_BUTTON;

    // Firmware version in byte 22: high nibble = major, low nibble = minor
    buffer_tx[22] = (FIRMWARE_VERSION_MAJOR << 4) | (FIRMWARE_VERSION_MINOR & 0x0F);

   // Calculate and fill out the checksum.  NOTE: This must be after all other buffer_tx modifications are done!
   uint8_t checksum = crc8ccitt(buffer_tx, MSG_LENGTH - 1);
   buffer_tx[MSG_LENGTH - 1] = checksum;

    if(!DEBUG_MODE)
      SerialUSB.write(buffer_tx,MSG_LENGTH);
    else
    {
      SerialUSB.print("focus: ");
      SerialUSB.print(focuswheel_pos);
      // Serial.print(buffer[3]);
      SerialUSB.print(", joystick delta x: ");
      SerialUSB.print(joystick_delta_x);
      SerialUSB.print(", joystick delta y: ");
      SerialUSB.print(joystick_delta_y);
      SerialUSB.print(", btns: ");
      SerialUSB.print(btns);
      SerialUSB.print(", PG:");
      SerialUSB.println(digitalRead(pin_PG));
    }
    flag_send_pos_update = false;
    
  }
}
