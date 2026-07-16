#include "serial.h"
#include "axesmrg.h"
#include "build_opt.h"
#include "commandprocessor.h"
#include "config.h"
#include "illumination.h"
#include "joystick.h"
#include "trigger.h"
#include "tmc/motion/MotorControl.h"
#include "tmc/ic/TMC4361A/TMC4361A.h"
#include <stdarg.h>

// Protocol status bytes
static const uint8_t STATUS_COMPLETED    = 0;
static const uint8_t STATUS_IN_PROGRESS  = 1;
static const uint8_t STATUS_CRC_ERROR    = 2;

// firmware version (byte[22]: high nibble=major, low nibble=minor)
static const uint8_t FIRMWARE_VERSION_MAJOR = 1;
static const uint8_t FIRMWARE_VERSION_MINOR = 7;

// position-report period (10ms, consistent with legacy Squid)
static const uint32_t INTERVAL_SEND_POS_US = 10000;

static const uint8_t CRC_TABLE[256] = {
    0x00, 0x07, 0x0E, 0x09, 0x1C, 0x1B, 0x12, 0x15, 0x38, 0x3F, 0x36, 0x31,
    0x24, 0x23, 0x2A, 0x2D, 0x70, 0x77, 0x7E, 0x79, 0x6C, 0x6B, 0x62, 0x65,
    0x48, 0x4F, 0x46, 0x41, 0x54, 0x53, 0x5A, 0x5D, 0xE0, 0xE7, 0xEE, 0xE9,
    0xFC, 0xFB, 0xF2, 0xF5, 0xD8, 0xDF, 0xD6, 0xD1, 0xC4, 0xC3, 0xCA, 0xCD,
    0x90, 0x97, 0x9E, 0x99, 0x8C, 0x8B, 0x82, 0x85, 0xA8, 0xAF, 0xA6, 0xA1,
    0xB4, 0xB3, 0xBA, 0xBD, 0xC7, 0xC0, 0xC9, 0xCE, 0xDB, 0xDC, 0xD5, 0xD2,
    0xFF, 0xF8, 0xF1, 0xF6, 0xE3, 0xE4, 0xED, 0xEA, 0xB7, 0xB0, 0xB9, 0xBE,
    0xAB, 0xAC, 0xA5, 0xA2, 0x8F, 0x88, 0x81, 0x86, 0x93, 0x94, 0x9D, 0x9A,
    0x27, 0x20, 0x29, 0x2E, 0x3B, 0x3C, 0x35, 0x32, 0x1F, 0x18, 0x11, 0x16,
    0x03, 0x04, 0x0D, 0x0A, 0x57, 0x50, 0x59, 0x5E, 0x4B, 0x4C, 0x45, 0x42,
    0x6F, 0x68, 0x61, 0x66, 0x73, 0x74, 0x7D, 0x7A, 0x89, 0x8E, 0x87, 0x80,
    0x95, 0x92, 0x9B, 0x9C, 0xB1, 0xB6, 0xBF, 0xB8, 0xAD, 0xAA, 0xA3, 0xA4,
    0xF9, 0xFE, 0xF7, 0xF0, 0xE5, 0xE2, 0xEB, 0xEC, 0xC1, 0xC6, 0xCF, 0xC8,
    0xDD, 0xDA, 0xD3, 0xD4, 0x69, 0x6E, 0x67, 0x60, 0x75, 0x72, 0x7B, 0x7C,
    0x51, 0x56, 0x5F, 0x58, 0x4D, 0x4A, 0x43, 0x44, 0x19, 0x1E, 0x17, 0x10,
    0x05, 0x02, 0x0B, 0x0C, 0x21, 0x26, 0x2F, 0x28, 0x3D, 0x3A, 0x33, 0x34,
    0x4E, 0x49, 0x40, 0x47, 0x52, 0x55, 0x5C, 0x5B, 0x76, 0x71, 0x78, 0x7F,
    0x6A, 0x6D, 0x64, 0x63, 0x3E, 0x39, 0x30, 0x37, 0x22, 0x25, 0x2C, 0x2B,
    0x06, 0x01, 0x08, 0x0F, 0x1A, 0x1D, 0x14, 0x13, 0xAE, 0xA9, 0xA0, 0xA7,
    0xB2, 0xB5, 0xBC, 0xBB, 0x96, 0x91, 0x98, 0x9F, 0x8A, 0x8D, 0x84, 0x83,
    0xDE, 0xD9, 0xD0, 0xD7, 0xC2, 0xC5, 0xCC, 0xCB, 0xE6, 0xE1, 0xE8, 0xEF,
    0xFA, 0xFD, 0xF4, 0xF3};

SerialProtocolHandler serialProtocol;

static const uint32_t VERSION = 106;

SerialProtocolHandler::SerialProtocolHandler()
    : buffer_rx_ptr(0), cmd_id(0), mcu_cmd_execution_in_progress(false),
      checksum_error(false) {
  memset(buffer_rx, 0, sizeof(buffer_rx));
}

void SerialProtocolHandler::begin(long baudRate, uint32_t timeout) {
  SerialUSB.begin(baudRate);
  delay(500);
  SerialUSB.setTimeout(timeout);
  buffer_rx_ptr = 0;
  while (!SerialUSB) {
    ; // wait for the serial connection
  }
}


void SerialProtocolHandler::sendDebugInfo(const char *format, ...) {
  char buffer[256];
  va_list args;
  va_start(args, format);
  vsnprintf(buffer, sizeof(buffer), format, args);
  va_end(args);

  // send debug info with the protocol header
  DEBUG_PRINTLN(buffer);
}

uint8_t SerialProtocolHandler::crc8ccitt(byte *data, uint8_t n) {
  uint8_t val = 0;
  uint8_t *pos = (uint8_t *)data;
  uint8_t *end = pos + n;

  while (pos < end) {
    val = CRC_TABLE[val ^ *pos];
    pos++;
  }

  return val;
}

bool SerialProtocolHandler::checkForCommand() {
  bool commandReceived = false;

  // read serial data
  while (SerialUSB.available()) {
    buffer_rx[buffer_rx_ptr] = SerialUSB.read();
    buffer_rx_ptr = buffer_rx_ptr + 1;

    if (buffer_rx_ptr == CMD_LENGTH) {
      buffer_rx_ptr = 0;
      cmd_id = buffer_rx[0];

      // checksum check
      uint8_t checksum = crc8ccitt(buffer_rx, CMD_LENGTH - 1);
      if (checksum != buffer_rx[CMD_LENGTH - 1]) {
        checksum_error = true;
        // flush the serial buffer, since byte-level desync can also cause this error
        while (SerialUSB.available()) {
          SerialUSB.read();
        }
        return false;
      } else {
        checksum_error = false;
        commandReceived = true;
        watchdog_reset_timer();
      }
      break; // process only one command at a time
    }
  }

  return commandReceived;
}

void SerialProtocolHandler::sendResponse(byte cmd_id, byte status,
                                         int32_t x_pos, int32_t y_pos,
                                         int32_t z_pos, int32_t w_pos,
                                         bool joystick_button_pressed) {
  byte buffer_tx[MSG_LENGTH];
  memset(buffer_tx, 0, MSG_LENGTH);

  buffer_tx[0] = cmd_id;
  buffer_tx[1] = status;

  // X-axis position (bytes 2-5)
  buffer_tx[2] = byte(x_pos >> 24);
  buffer_tx[3] = byte((x_pos >> 16) & 0xFF);
  buffer_tx[4] = byte((x_pos >> 8) & 0xFF);
  buffer_tx[5] = byte(x_pos & 0xFF);

  // Y-axis position (bytes 6-9)
  buffer_tx[6] = byte(y_pos >> 24);
  buffer_tx[7] = byte((y_pos >> 16) & 0xFF);
  buffer_tx[8] = byte((y_pos >> 8) & 0xFF);
  buffer_tx[9] = byte(y_pos & 0xFF);

  // Z-axis position (bytes 10-13)
  buffer_tx[10] = byte(z_pos >> 24);
  buffer_tx[11] = byte((z_pos >> 16) & 0xFF);
  buffer_tx[12] = byte((z_pos >> 8) & 0xFF);
  buffer_tx[13] = byte(z_pos & 0xFF);

  // W-axis position (bytes 14-17)
  buffer_tx[14] = byte(w_pos >> 24);
  buffer_tx[15] = byte((w_pos >> 16) & 0xFF);
  buffer_tx[16] = byte((w_pos >> 8) & 0xFF);
  buffer_tx[17] = byte(w_pos & 0xFF);

  // status byte byte[18]: bit0 = joystick button
  static const int BIT_POS_JOYSTICK_BUTTON = 0;
  buffer_tx[18] = (joystick_button_pressed ? (1 << BIT_POS_JOYSTICK_BUTTON) : 0);

  // bytes[19-21]: reserved

  // firmware version byte[22]: high nibble=major, low nibble=minor
  buffer_tx[22] = (FIRMWARE_VERSION_MAJOR << 4) | (FIRMWARE_VERSION_MINOR & 0x0F);

  // CRC-8-CCITT checksum (computed over byte[0..22])
  uint8_t checksum = crc8ccitt(buffer_tx, MSG_LENGTH - 1);
  buffer_tx[MSG_LENGTH - 1] = checksum;

  SerialUSB.write(buffer_tx, MSG_LENGTH);
}

void SerialProtocolHandler::send_position_update() {
#ifdef DISABLE_BINARY_POS_UPDATE
  // a switch temporarily defined in build_opt.h: skip the 24-byte binary position reporting,
  // leaving only ASCII debug output on SerialUSB, convenient for the Arduino Serial Monitor
  return;
#endif

  // compute any_moving first, used to detect the "movement-complete" falling edge (true->false)
  bool any_moving = false;
  uint8_t count = axisManager.getAxisCount();
  for (uint8_t i = 0; i < count; i++) {
    Axis *axis = axisManager.getAxis(i);
    if (axis && (axis->isMoving() || axis->isHomingInProgress())) {
      any_moving = true;
      break;
    }
  }
  // completion edge: all axes just stopped. Bypass the 10ms heartbeat throttle and immediately send a COMPLETED frame,
  // so the host's wait_till_operation_is_completed is woken within < 1ms after the physical stop
  // (saves 5ms on average, 10ms heartbeat delay worst case). The falling edge fires only once per transition.
  bool falling_edge = _last_any_moving && !any_moving;
  _last_any_moving = any_moving;

  if (_us_since_last_pos_update < INTERVAL_SEND_POS_US && !falling_edge)
    return;
  _us_since_last_pos_update = 0;

  // read each axis position (microsteps, consistent with legacy Squid tmc4361A_currentPosition)
  // cache the axis pointers: findAxisByName constructs 4 Strings + 4 equals each time, accumulating per tick
  // ~40us * 10000 ticks ~= 400ms wasted. The axis pointers do not change during the axisManager lifetime,
  // so static caching is safe (even a first-time nullptr is the real situation, no retry needed) (#4, 2026-05-19)
  static Axis *xAxis = nullptr;
  static Axis *yAxis = nullptr;
  static Axis *zAxis = nullptr;
  static Axis *wAxis = nullptr;
  static bool axes_cached = false;
  if (!axes_cached) {
    xAxis = axisManager.findAxisByName("X");
    yAxis = axisManager.findAxisByName("Y");
    zAxis = axisManager.findAxisByName("Z");
    wAxis = axisManager.findAxisByName("W");
    axes_cached = true;
  }

  int32_t x_pos = xAxis ? xAxis->getCurrentPositionMicrosteps() : 0;
  int32_t y_pos = yAxis ? yAxis->getCurrentPositionMicrosteps() : 0;
  int32_t z_pos = zAxis ? zAxis->getCurrentPositionMicrosteps() : 0;
  int32_t w_pos = wAxis ? wAxis->getCurrentPositionMicrosteps() : 0;

  // joystick-button fail-safe: auto-clear if not ACKed within 1000ms
  if (joystick_button_pressed &&
      millis() - joystick_button_pressed_timestamp > 1000) {
    joystick_button_pressed = false;
  }

  uint8_t status;
  if (checksum_error)
    status = STATUS_CRC_ERROR;
  else
    status = any_moving ? STATUS_IN_PROGRESS : STATUS_COMPLETED;

  sendResponse(cmd_id, status, x_pos, y_pos, z_pos, w_pos,
               joystick_button_pressed);
}

void SerialProtocolHandler::processSerialCommands() {
  static uint32_t lastPrint = 0;
  if (millis() - lastPrint > 5000) {  // print once every 5 seconds
    DEBUG_PRINT("LOOP_ALIVE:");
    DEBUG_PRINTLN(SerialUSB.available());
    lastPrint = millis();
  }

  if (SerialUSB.available() >= 2) {
    DEBUG_PRINT("RX_AVAIL:");
    DEBUG_PRINTLN(SerialUSB.available());  // debug: data received

    // peek at the first two bytes without removing them
    int firstByte = SerialUSB.peek();

    if (firstByte == DEBUG_PROTOCOL_HEADER_1) {
      // peek at the second byte (the second byte is at index 1)
      // we must read the first byte before we can peek at the second byte
      SerialUSB.read();                  // remove the first byte
      int secondByte = SerialUSB.peek(); // peek at the second byte

      if (secondByte == DEBUG_PROTOCOL_HEADER_2) {
        // confirmed it is the debug protocol, remove the second byte
        SerialUSB.read(); // remove the second byte
        processSerialDebugCommands();
      } else {
        // not the debug protocol, put the first byte back into the buffer
        // since we already removed the first byte, we need to put it back into buffer_rx
        buffer_rx[0] = DEBUG_PROTOCOL_HEADER_1;
        buffer_rx_ptr = 1;
        // continue handling the standard command
        // the second byte is still in the serial buffer and will be read in checkForCommand
        processSerialStandardCommands();
      }
    } else {
      // not a debug protocol header, handle the standard command
      processSerialStandardCommands();
    }
  } else if (SerialUSB.available() == 1) {
    // only one byte available, handle the standard command directly
    processSerialStandardCommands();
  }
}

void SerialProtocolHandler::processSerialDebugCommands() {
  // read until the newline
  String command = SerialUSB.readStringUntil('\n');
  command.trim(); // strip leading and trailing whitespace

  if (command.length() > 0) {
    if (command == "S:VERSION") {
      // the version reply is always sent (not gated by ENABLE_DEBUG)
      char vbuf[32];
      snprintf(vbuf, sizeof(vbuf), "S:VERSION:%lu", (unsigned long)VERSION);
      SerialUSB.println(vbuf);
      return;
    }

    if (command == "S:Engine Start") {
      // keep command compatibility; the startup sequence is no longer needed
      sendDebugInfo("System already running (Engine Start is no longer required)");
      return;
    }

    if (command == "S:ENCPOS") {
      char buf[120];
      // first print the W-axis encoder register diagnostics
      uint8_t wID = 3;
      uint32_t genConf = tmc4361A_readRegister(wID, TMC4361A_GENERAL_CONF);
      uint32_t encInConf = tmc4361A_readRegister(wID, TMC4361A_ENC_IN_CONF);
      uint32_t stepConf = tmc4361A_readRegister(wID, TMC4361A_STEP_CONF);
      uint32_t encInRes = tmc4361A_readRegister(wID, TMC4361A_ENC_IN_RES);
      snprintf(buf, sizeof(buf), "S:ENCDIAG:W GENERAL_CONF=0x%08lX diff_dis=%d ser_mode=%d",
               (unsigned long)genConf, (int)((genConf >> 12) & 1), (int)((genConf >> 10) & 3));
      SerialUSB.println(buf);
      snprintf(buf, sizeof(buf), "S:ENCDIAG:W ENC_IN_CONF=0x%08lX STEP_CONF=0x%08lX ENC_IN_RES=%lu",
               (unsigned long)encInConf, (unsigned long)stepConf, (unsigned long)encInRes);
      SerialUSB.println(buf);

      // print each axis's encoder position
      for (uint8_t i = 0; i < axisManager.getAxisCount(); i++) {
        Axis *axis = axisManager.getAxis(i);
        if (axis) {
          int32_t encPos = (int32_t)tmc4361A_readRegister(i, TMC4361A_ENC_POS);
          int32_t xActual = (int32_t)tmc4361A_readRegister(i, TMC4361A_XACTUAL);
          snprintf(buf, sizeof(buf), "S:ENCPOS:%s:enc=%ld xactual=%ld dev=%ld",
                   axis->getAxisName(), (long)encPos, (long)xActual, (long)(encPos - xActual));
          SerialUSB.println(buf);
        }
      }
      SerialUSB.println("S:ENCPOS:END");
      return;
    }

    if (command == "S:HWINFO") {
      char buf[64];
      for (uint8_t i = 0; i < axisManager.getAxisCount(); i++) {
        Axis *axis = axisManager.getAxis(i);
        if (axis) {
          const char *driverName;
          switch (axis->getDriverType()) {
            case DRIVER_TMC2660: driverName = "TMC2660"; break;
            case DRIVER_TMC2240: driverName = "TMC2240"; break;
            default:             driverName = "UNKNOWN"; break;
          }
          snprintf(buf, sizeof(buf), "S:HWINFO:%s:TMC4361A+%s",
                   axis->getAxisName(), driverName);
          SerialUSB.println(buf);
        }
      }
      SerialUSB.println("S:HWINFO:END");
      return;
    }

    // S:JOYSTICK_STATS -- print hand-controller protocol-frame statistics
    // legacy = byte[9]==0 (old joystick has no CRC)
    // crc_ok / crc_fail = new joystick CRC-8-CCITT verification results
    if (command == "S:JOYSTICK_STATS") {
      joystick_print_stats();
      return;
    }

    // S:DUMPREGS [axisName]
    // no argument -> dump all axes; with an argument (X/Y/Z/W) -> dump only the specified axis
    // for diagnosing a freeze on-site: print the key TMC4361A registers to locate the ramp-generator-anomaly root cause
    if (command.startsWith("S:DUMPREGS")) {
      String filter = command.length() > 11 ? command.substring(11) : String("");
      filter.trim();
      char buf[160];
      for (uint8_t i = 0; i < axisManager.getAxisCount(); i++) {
        Axis *axis = axisManager.getAxis(i);
        if (!axis) continue;
        const char *name = axis->getAxisName();
        if (filter.length() > 0 && filter != String(name)) continue;

        uint8_t icID = axis->getIcID();
        uint32_t status   = tmc4361A_readRegister(icID, TMC4361A_STATUS);
        uint32_t events   = tmc4361A_readRegister(icID, TMC4361A_EVENTS);
        uint32_t rampMode = tmc4361A_readRegister(icID, TMC4361A_RAMPMODE);
        uint32_t refConf  = tmc4361A_readRegister(icID, TMC4361A_REFERENCE_CONF);
        int32_t  xactual  = (int32_t)tmc4361A_readRegister(icID, TMC4361A_XACTUAL);
        int32_t  xtarget  = (int32_t)tmc4361A_readRegister(icID, TMC4361A_XTARGET);
        int32_t  vactual  = (int32_t)tmc4361A_readRegister(icID, TMC4361A_VACTUAL);
        int32_t  vmax     = (int32_t)tmc4361A_readRegister(icID, TMC4361A_VMAX);
        int32_t  vstopL   = (int32_t)tmc4361A_readRegister(icID, TMC4361A_VIRT_STOP_LEFT);
        int32_t  vstopR   = (int32_t)tmc4361A_readRegister(icID, TMC4361A_VIRT_STOP_RIGHT);
        uint32_t stepConf = tmc4361A_readRegister(icID, TMC4361A_STEP_CONF);

        snprintf(buf, sizeof(buf),
                 "S:DUMP %s STATUS=0x%08lX EVENTS=0x%08lX RAMPMODE=0x%08lX",
                 name, (unsigned long)status, (unsigned long)events,
                 (unsigned long)rampMode);
        SerialUSB.println(buf);
        snprintf(buf, sizeof(buf),
                 "S:DUMP %s XACTUAL=%ld XTARGET=%ld VACTUAL=%ld VMAX=%ld",
                 name, (long)xactual, (long)xtarget, (long)vactual, (long)vmax);
        SerialUSB.println(buf);
        snprintf(buf, sizeof(buf),
                 "S:DUMP %s VSTOP_L=%ld VSTOP_R=%ld REFCONF=0x%08lX STEP_CONF=0x%08lX",
                 name, (long)vstopL, (long)vstopR,
                 (unsigned long)refConf, (unsigned long)stepConf);
        SerialUSB.println(buf);
        snprintf(buf, sizeof(buf),
                 "S:DUMP %s isMoving=%d state=%d softLimEn=%d needReenable=%d",
                 name, (int)axis->isMoving(), (int)axis->getCurrentState(),
                 (int)axis->isSoftLimitsEnabled(), 0);
        SerialUSB.println(buf);
      }
      SerialUSB.println("S:DUMPREGS:END");
      return;
    }

    // S:SET_HOMING_VEL <axisName> <vel_mm_per_s>
    // for diagnostics: set homingVelocityMM at runtime without reflashing firmware
    // e.g.: S:SET_HOMING_VEL Y 5.0
    if (command.startsWith("S:SET_HOMING_VEL")) {
      String rest = command.substring(16);
      rest.trim();
      int sp = rest.indexOf(' ');
      if (sp < 0) {
        SerialUSB.println("S:SET_HOMING_VEL:ERR:missing_args");
        return;
      }
      String axisName = rest.substring(0, sp);
      String velStr = rest.substring(sp + 1);
      axisName.trim();
      velStr.trim();
      float vel = velStr.toFloat();
      bool found = false;
      for (uint8_t i = 0; i < axisManager.getAxisCount(); i++) {
        Axis *axis = axisManager.getAxis(i);
        if (!axis) continue;
        if (axisName != String(axis->getAxisName())) continue;
        axis->getMutableConfig().homingVelocityMM = vel;
        char buf[80];
        snprintf(buf, sizeof(buf), "S:SET_HOMING_VEL:OK:%s=%.3f", axis->getAxisName(), vel);
        SerialUSB.println(buf);
        found = true;
        break;
      }
      if (!found) {
        SerialUSB.print("S:SET_HOMING_VEL:ERR:axis_not_found:");
        SerialUSB.println(axisName);
      }
      return;
    }

    // handle other debug commands
    DEBUG_PRINT("Serial:TO_AXISMGR:");
    DEBUG_PRINTLN(command);  // debug point - dispatched to AxisManager

    bool success = axisManager.processCommand(command);
    if (!success) {
      sendDebugInfo("Command processing failed: %s", command.c_str());
    }
  }
}

void SerialProtocolHandler::processSerialStandardCommands() {
  if (checkForCommand()) {
    const byte *data = getCommandData();
    byte command = data[1];

    switch (command) {
    case Commands::MOVE_X:
      commandProcessor.handleMoveX(data);
      break;

    case Commands::MOVE_Y:
      commandProcessor.handleMoveY(data);
      break;

    case Commands::MOVE_Z:
      commandProcessor.handleMoveZ(data);
      break;

    case Commands::MOVE_THETA:
      commandProcessor.handleMoveTheta(data);
      break;

    case Commands::MOVE_W:
      commandProcessor.handleMoveW(data);
      break;

    case Commands::MOVE_W2:
      commandProcessor.handleMoveW2(data);
      break;

    case Commands::MOVE_TURRET:
      commandProcessor.handleMoveTurret(data);
      break;

    case Commands::MOVETO_TURRET:
      commandProcessor.handleMoveToTurret(data);
      break;

    case Commands::HOME_OR_ZERO:
      commandProcessor.handleHomeOrZero(data);
      break;

    case Commands::MOVETO_X:
      commandProcessor.handleMoveToX(data);
      break;

    case Commands::MOVETO_Y:
      commandProcessor.handleMoveToY(data);
      break;

    case Commands::MOVETO_Z:
      commandProcessor.handleMoveToZ(data);
      break;

    case Commands::SET_LIM:
      commandProcessor.handleSetLim(data);
      break;

    case Commands::TURN_ON_ILLUMINATION:
      commandProcessor.handleTurnOnIllumination(data);
      break;

    case Commands::TURN_OFF_ILLUMINATION:
      commandProcessor.handleTurnOffIllumination(data);
      break;

    case Commands::SET_ILLUMINATION:
      commandProcessor.handleSetIllumination(data);
      break;

    case Commands::SET_ILLUMINATION_LED_MATRIX:
      commandProcessor.handleSetIlluminationLEDMatrix(data);
      break;

    case Commands::ACK_JOYSTICK_BUTTON_PRESSED:
      commandProcessor.handleAckJoystickButtonPressed(data);
      break;

    case Commands::ANALOG_WRITE_ONBOARD_DAC:
      commandProcessor.handleAnalogWriteOnboardDAC(data);
      break;

    case Commands::SET_DAC80508_REFDIV_GAIN:
      commandProcessor.handleSetDAC80508RefDivGain(data);
      break;

    case Commands::SET_ILLUMINATION_INTENSITY_FACTOR:
      commandProcessor.handleSetIlluminationIntensityFactor(data);
      break;

    case Commands::SET_TRIGGER_MODE:
      commandProcessor.handleSetTriggerMode(data);
      break;

    case Commands::SET_PORT_INTENSITY:
      commandProcessor.handleSetPortIntensity(data);
      break;

    case Commands::TURN_ON_PORT:
      commandProcessor.handleTurnOnPort(data);
      break;

    case Commands::TURN_OFF_PORT:
      commandProcessor.handleTurnOffPort(data);
      break;

    case Commands::SET_PORT_ILLUMINATION:
      commandProcessor.handleSetPortIllumination(data);
      break;

    case Commands::SET_MULTI_PORT_MASK:
      commandProcessor.handleSetMultiPortMask(data);
      break;

    case Commands::TURN_OFF_ALL_PORTS:
      commandProcessor.handleTurnOffAllPorts(data);
      break;

    case Commands::SET_WATCHDOG_TIMEOUT:
      commandProcessor.handleSetWatchdogTimeout(data);
      break;

    case Commands::SET_PIN_LEVEL:
      commandProcessor.handleSetPinLevel(data);
      break;

    case Commands::HEARTBEAT:
      commandProcessor.handleHeartbeat(data);
      break;

    case Commands::MOVETO_W:
      commandProcessor.handleMoveToW(data);
      break;

    case Commands::SET_LIM_SWITCH_POLARITY:
      commandProcessor.handleSetLimSwitchPolarity(data);
      break;

    case Commands::CONFIGURE_STEPPER_DRIVER:
      commandProcessor.handleConfigureStepperDriver(data);
      break;

    case Commands::SET_MAX_VELOCITY_ACCELERATION:
      commandProcessor.handleSetMaxVelocityAcceleration(data);
      break;

    case Commands::SET_LEAD_SCREW_PITCH:
      commandProcessor.handleSetLeadScrewPitch(data);
      break;

    case Commands::SET_OFFSET_VELOCITY:
      commandProcessor.handleSetOffsetVelocity(data);
      break;

    case Commands::CONFIGURE_STAGE_PID:
      commandProcessor.handleConfigureStagePID(data);
      break;

    case Commands::ENABLE_STAGE_PID:
      commandProcessor.handleEnableStagePID(data);
      break;

    case Commands::DISABLE_STAGE_PID:
      commandProcessor.handleDisableStagePID(data);
      break;

    case Commands::SET_HOME_SAFETY_MERGIN:
      commandProcessor.handleSetHomeSafetyMargin(data);
      break;

    case Commands::SET_PID_ARGUMENTS:
      commandProcessor.handleSetPIDArguments(data);
      break;

    case Commands::SEND_HARDWARE_TRIGGER:
      commandProcessor.handleSendHardwareTrigger(data);
      break;

    case Commands::SET_STROBE_DELAY:
      commandProcessor.handleSetStrobeDelay(data);
      break;

    case Commands::SET_AXIS_DISABLE_ENABLE:
      commandProcessor.handleSetAxisDisableEnable(data);
      break;

    case Commands::INITFILTERWHEEL:
      commandProcessor.handleInitFilterWheel(data);
      break;

    case Commands::INITFILTERWHEEL_W2:
      commandProcessor.handleInitFilterWheelW2(data);
      break;

    case Commands::INITIALIZE:
      commandProcessor.handleInitialize(data);
      break;

    case Commands::RESET:
      commandProcessor.handleReset(data);
      break;

    default:
      break;
    }
  }
}
