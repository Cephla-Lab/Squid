#include "joystick.h"
#include "axesmrg.h"
#include "build_opt.h"
#include "config.h"
#include "def_octopi_80120.h"
#include "serial.h"
#include "trigger.h"
#include "tmc/motion/MotorControl.h"
#include "tmc/ic/TMC4361A/TMC4361A.h"
#include <PacketSerial.h>

// =============================================================================
// External variables
// =============================================================================

// offset velocity (defined in commandprocessor.cpp)
extern float offset_velocity_x;
extern float offset_velocity_y;

// =============================================================================
// Internal constants
// =============================================================================

static const unsigned long JOYSTICK_UPDATE_INTERVAL_US = 30000; // 30ms

// =============================================================================
// Internal state
// =============================================================================

static PacketSerial joystickSerial;

// cached axis pointers and icID (looked up once at startup)
static Axis *axisX = nullptr;
static Axis *axisY = nullptr;
static Axis *axisZ = nullptr;
static uint8_t icID_X = 0;
static uint8_t icID_Y = 0;
static uint8_t icID_Z = 0;

// joystick data (written by the PacketSerial callback, read by the main loop)
static volatile int16_t joystick_delta_x = 0;
static volatile int16_t joystick_delta_y = 0;
static volatile bool flag_read_joystick = false; // set true when a new packet arrives, cleared after processing

// Focus-wheel state
static int32_t focusPosition = 0;
static volatile int32_t focusWheelDelta = 0;  // delta accumulated in the callback
static int32_t focusWheelPos = 0;             // previous absolute encoder position
static bool firstJoystickPacket = true;       // first-packet flag (only records the baseline)
static bool focusPositionSynced = false;      // whether focusPosition has been synced with the actual position

// Periodic timer
static elapsedMicros joystickTimer;

// protocol-frame statistics counters (read by S:JOYSTICK_STATS)
// byte[9] == 0 -> legacy packet (old joystick, no CRC)
// byte[9] != 0 -> new joystick, verify CRC-8-CCITT(buffer[0..8]), 0x00 mapped to 0x01
static uint32_t joystick_legacy_count = 0;
static uint32_t joystick_crc_ok_count = 0;
static uint32_t joystick_crc_fail_count = 0;

// =============================================================================
// PacketSerial callback: parse the hand controller's 10-byte message
// =============================================================================

static void onJoystickPacketReceived(const uint8_t *buffer, size_t size) {
  if (size != 10)
    return;

  // CRC compatibility gate: byte[9]==0 is treated as legacy (old joystick), non-zero verifies the CRC
  uint8_t recv_crc = buffer[9];
  if (recv_crc == 0x00) {
    joystick_legacy_count++;
  } else {
    uint8_t calc = serialProtocol.crc8ccitt(const_cast<byte *>(buffer), 9);
    if (calc == 0x00) calc = 0x01; // consistent with the mapping rule on the joystick side
    if (calc != recv_crc) {
      joystick_crc_fail_count++;
      return; // CRC mismatch, drop the packet
    }
    joystick_crc_ok_count++;
  }

  // bytes[0-3]: focus-wheel absolute encoder position (int32 BE)
  int32_t focusWheelNew = (int32_t)((uint32_t)buffer[0] << 24 |
                                     (uint32_t)buffer[1] << 16 |
                                     (uint32_t)buffer[2] << 8  |
                                     (uint32_t)buffer[3]);
  if (firstJoystickPacket) {
    // the first packet only records the baseline, produces no motion
    focusWheelPos = focusWheelNew;
    firstJoystickPacket = false;
  } else {
    int32_t pkt_delta = (focusWheelNew - focusWheelPos) * JOYSTICK_SIGN_Z;
    if (pkt_delta != 0) {
      DEBUG_PRINT("[FOCUS] pkt_delta=");
      DEBUG_PRINT(pkt_delta);
      DEBUG_PRINT(" focusWheelNew=");
      DEBUG_PRINTLN(focusWheelNew);
    }
    focusWheelDelta += pkt_delta;
    focusWheelPos = focusWheelNew;
  }

  // bytes[4-5]: X joystick (int16 BE)
  joystick_delta_x = (int16_t)((uint16_t)buffer[4] << 8 | (uint16_t)buffer[5]);
  joystick_delta_x *= JOYSTICK_SIGN_X;

  // bytes[6-7]: Y joystick (int16 BE)
  joystick_delta_y = (int16_t)((uint16_t)buffer[6] << 8 | (uint16_t)buffer[7]);
  joystick_delta_y *= JOYSTICK_SIGN_Y;

  // byte[8]: button
  if (buffer[8] != 0) {
    joystick_button_pressed = true;
    joystick_button_pressed_timestamp = millis();
  }

  flag_read_joystick = true;
}

// =============================================================================
// XY-axis joystick velocity control
// =============================================================================

static void check_joystick() {
  // X axis
  if (axisX && !axisX->isMoving() && !axisX->isHomingInProgress()) {
    int16_t delta = joystick_delta_x;
    if (delta != 0) {
      float velocity = offset_velocity_x +
                        (float(delta) / 32768.0f) *
                        AxisConstDefinition::MAX_VELOCITY_X_mm;
      int32_t velInternal = motor_velocityMMToInternal(icID_X, velocity);
      motor_setVelocityInternal(icID_X, velInternal);
    } else {
      if (enable_offset_velocity)
        motor_setVelocityInternal(icID_X,
            motor_velocityMMToInternal(icID_X, offset_velocity_x));
      else
        motor_stop(icID_X);
    }
  }

  // Y axis
  if (axisY && !axisY->isMoving() && !axisY->isHomingInProgress()) {
    int16_t delta = joystick_delta_y;
    if (delta != 0) {
      float velocity = offset_velocity_y +
                        (float(delta) / 32768.0f) *
                        AxisConstDefinition::MAX_VELOCITY_Y_mm;
      int32_t velInternal = motor_velocityMMToInternal(icID_Y, velocity);
      motor_setVelocityInternal(icID_Y, velInternal);
    } else {
      if (enable_offset_velocity)
        motor_setVelocityInternal(icID_Y,
            motor_velocityMMToInternal(icID_Y, offset_velocity_y));
      else
        motor_stop(icID_Y);
    }
  }
}

// =============================================================================
// Z-axis focus-wheel control
// =============================================================================

static void do_focus_control() {
  if (!axisZ || axisZ->isHomingInProgress())
    return;

  // read and zero the accumulated delta
  noInterrupts();
  int32_t delta = focusWheelDelta;
  focusWheelDelta = 0;
  interrupts();

  if (delta == 0)
    return;

  // on first use, sync from the actual position to avoid a stale position at init (inconsistent before/after homing)
  if (!focusPositionSynced) {
    focusPosition = motor_getPositionMicrosteps(icID_Z);
    focusPositionSynced = true;
  }

  focusPosition += delta;

  // soft-limit clamp: only effective when soft limits are enabled (valid values exist only after the host's SET_LIMITS)
  if (axisZ->isSoftLimitsEnabled()) {
    int32_t lowerLimit = (int32_t)tmc4361A_readRegister(icID_Z, TMC4361A_VIRT_STOP_LEFT);
    int32_t upperLimit = (int32_t)tmc4361A_readRegister(icID_Z, TMC4361A_VIRT_STOP_RIGHT);
    if (focusPosition < lowerLimit)
      focusPosition = lowerLimit;
    if (focusPosition > upperLimit)
      focusPosition = upperLimit;
  }

  [[maybe_unused]] int32_t xactual_before = motor_getPositionMicrosteps(icID_Z);
  DEBUG_PRINT("[FOCUS] do_focus delta=");
  DEBUG_PRINT(delta);
  DEBUG_PRINT(" target=");
  DEBUG_PRINT(focusPosition);
  DEBUG_PRINT(" xactual_before=");
  DEBUG_PRINTLN(xactual_before);

  motor_moveToMicrosteps(icID_Z, focusPosition);
}

// =============================================================================
// Public API
// =============================================================================

void joystick_init() {
  // initialize Serial5 @ 115200bps
  Serial5.begin(115200);
  joystickSerial.setStream(&Serial5);
  joystickSerial.setPacketHandler(&onJoystickPacketReceived);

  // cache the axis pointers and icID
  axisX = axisManager.findAxisByName("X");
  axisY = axisManager.findAxisByName("Y");
  axisZ = axisManager.findAxisByName("Z");

  if (axisX) icID_X = axisX->getIcID();
  if (axisY) icID_Y = axisY->getIcID();
  if (axisZ) {
    icID_Z = axisZ->getIcID();
    // initialize the focus position to the Z axis's current position
    focusPosition = motor_getPositionMicrosteps(icID_Z);
  }

  joystickTimer = 0;

  DEBUG_PRINTLN("Joystick system initialized");
}

void joystick_update() {
  // receive PacketSerial data
  joystickSerial.update();

  // XY joystick: only process when a new packet arrives (consistent with Squid flag_read_joystick)
  if (flag_read_joystick) {
    if (joystickTimer >= JOYSTICK_UPDATE_INTERVAL_US) {
      joystickTimer -= JOYSTICK_UPDATE_INTERVAL_US;
      check_joystick();
    }
    flag_read_joystick = false;
  }

  // Z focus wheel: run unconditionally every loop (consistent with Squid, outside flag_read_joystick)
  do_focus_control();
}

void joystick_print_stats() {
  // send directly via SerialUSB.println (not DEBUG_PRINTLN) to ensure that even in the production env (teensy41)
  // the counters can still be queried; matches the same pattern as S:HWINFO / S:VERSION
  char buf[96];
  snprintf(buf, sizeof(buf),
           "JOYSTICK_STATS legacy=%lu crc_ok=%lu crc_fail=%lu",
           (unsigned long)joystick_legacy_count,
           (unsigned long)joystick_crc_ok_count,
           (unsigned long)joystick_crc_fail_count);
  SerialUSB.println(buf);
}
