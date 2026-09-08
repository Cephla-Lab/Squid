#include "commandprocessor.h"
#include "axesmrg.h"
#include "build_opt.h"
#include "illumination.h"
#include "trigger.h"
#include "config.h"

// Protocol constants (from Squid constants_protocol.h)
static const int HOME_POSITIVE     = 0;
static const int HOME_NEGATIVE     = 1;
static const int HOME_OR_ZERO_ZERO = 2;

// SET_LIM limit codes
static const int LIM_CODE_X_POSITIVE = 0;
static const int LIM_CODE_X_NEGATIVE = 1;
static const int LIM_CODE_Y_POSITIVE = 2;
static const int LIM_CODE_Y_NEGATIVE = 3;
static const int LIM_CODE_Z_POSITIVE = 4;
static const int LIM_CODE_Z_NEGATIVE = 5;

// Limit-switch polarity
static const int POLARITY_ACTIVE_LOW  = 0;
static const int POLARITY_ACTIVE_HIGH = 1;
static const int POLARITY_DISABLED    = 2;

// offset velocity (consistent with the old architecture globals.cpp)
// enable_offset_velocity is already defined in def_octopi_80120.h
float offset_velocity_x = 0;
float offset_velocity_y = 0;

// protocol axis value -> axis name (nullptr = invalid axis)
static const char* protocolAxisToName(uint8_t protocolAxis) {
  switch (protocolAxis) {
    case 0: return "X";
    case 1: return "Y";
    case 2: return "Z";
    case 5: return "W";
    case 6: return "W2";
    case 7: return "Turret";   // 2026-05-29 objective turret (HOME_OR_ZERO axis=7)
    default: return nullptr;
  }
}

CommandProcessor commandProcessor;

CommandProcessor::CommandProcessor() {
  // constructor initialization code
}

CommandProcessor::~CommandProcessor() {
  // destructor cleanup code
}

// the following are the implementation skeletons of the individual command handlers
void CommandProcessor::handleMoveX(const byte *data) {
  int32_t relative_position =
      int32_t((uint32_t(data[2]) << 24) + (uint32_t(data[3]) << 16) +
              (uint32_t(data[4]) << 8) + uint32_t(data[5]));
  Axis *axis = axisManager.findAxisByName("X");
  if (axis)
    axis->moveAxis(relative_position);

  DEBUG_PRINTLN("Get MoveX Command");
}

void CommandProcessor::handleMoveY(const byte *data) {
  int32_t relative_position =
      int32_t((uint32_t(data[2]) << 24) + (uint32_t(data[3]) << 16) +
              (uint32_t(data[4]) << 8) + uint32_t(data[5]));
  Axis *axis = axisManager.findAxisByName("Y");
  if (axis)
    axis->moveAxis(relative_position);

  DEBUG_PRINTLN("Get MoveY Command");
}

void CommandProcessor::handleMoveZ(const byte *data) {
  int32_t relative_position =
      int32_t((uint32_t(data[2]) << 24) + (uint32_t(data[3]) << 16) +
              (uint32_t(data[4]) << 8) + uint32_t(data[5]));
  Axis *axis = axisManager.findAxisByName("Z");
  if (axis)
    axis->moveAxis(relative_position);

  DEBUG_PRINTLN("Get MoveZ Command");
}

void CommandProcessor::handleMoveTheta(const byte *data) {
  // TODO: implement MOVE_THETA command handling
  DEBUG_PRINTLN("CMD_NOT_IMPLEMENTED: MOVE_THETA");
}

void CommandProcessor::handleMoveW(const byte *data) {
  int32_t relative_position =
      int32_t((uint32_t(data[2]) << 24) + (uint32_t(data[3]) << 16) +
              (uint32_t(data[4]) << 8) + uint32_t(data[5]));
  Axis *axis = axisManager.findAxisByName("W");
  if (axis)
    axis->moveAxis(relative_position);

  DEBUG_PRINTLN("Get MoveW Command");
}

void CommandProcessor::handleHomeOrZero(const byte *data) {
  // data[2]: protocol axis value (0=X,1=Y,2=Z,4=XY,5=W,6=W2)
  // data[3]: HOME_POSITIVE=0 (toward +), HOME_NEGATIVE=1 (toward -), HOME_OR_ZERO_ZERO=2 (zero only)
  if (data[3] == HOME_OR_ZERO_ZERO) {
    // zero mode: set the current position to 0, no movement
    if (data[2] == 4) {  // AXES_XY combined
      Axis *axX = axisManager.findAxisByName("X");
      Axis *axY = axisManager.findAxisByName("Y");
      if (axX) axX->setCurrentPosition(0.0f);
      if (axY) axY->setCurrentPosition(0.0f);
    } else {
      const char *name = protocolAxisToName(data[2]);
      if (name) {
        Axis *axis = axisManager.findAxisByName(name);
        if (axis) axis->setCurrentPosition(0.0f);
      }
    }
    return;
  }
  // Homing mode (HOME_POSITIVE / HOME_NEGATIVE):
  // 2026-05-11: parse the direction from protocol data[3], compatible with legacy Squid software
  // legacy Squid microcontroller.py:88 derives data[3] from stage_movement_sign_x,
  // legacy Squid firmware (main_controller_teensy41.ino:1252) reads data[3] to decide the direction.
  // previously octoaxes ignored data[3] and used only config.homing_direct, which behaves correctly under the octoaxes GUI
  // (constants.py's sign pairs consistently with config.homing_direct), but when legacy Squid software's
  // data[3] is not interpreted -> the direction may reverse (X homes toward the physical + end and hits the limit).
  //
  // compatibility strategy: override config.homing_direct with data[3]:
  // HOME_POSITIVE (0) -> homing_direct = +1 (toward +)
  // HOME_NEGATIVE (1) -> homing_direct = -1 (toward -)
  // written permanently into _config; subsequent startHoming() then uses the new direction.
  int8_t new_direct = (data[3] == HOME_NEGATIVE) ? -1 : +1;
  if (data[2] == 4) {  // AXES_XY combined homing
    Axis *axX = axisManager.findAxisByName("X");
    Axis *axY = axisManager.findAxisByName("Y");
    if (axX) {
      axX->getMutableConfig().homing_direct = new_direct;
      axX->startHoming();
    }
    if (axY) {
      axY->getMutableConfig().homing_direct = new_direct;
      axY->startHoming();
    }
  } else {
    const char *name = protocolAxisToName(data[2]);
    if (name) {
      Axis *axis = axisManager.findAxisByName(name);
      if (axis) {
        axis->getMutableConfig().homing_direct = new_direct;
        axis->startHoming();
      }
    }
  }
}

void CommandProcessor::handleMoveToX(const byte *data) {
  int32_t absolute_position =
      int32_t((uint32_t(data[2]) << 24) + (uint32_t(data[3]) << 16) +
              (uint32_t(data[4]) << 8) + uint32_t(data[5]));
  Axis *axis = axisManager.findAxisByName("X");
  if (axis)
    axis->moveToPositionMicrosteps(absolute_position);

  DEBUG_PRINTLN("Get MoveToX Command");
}

void CommandProcessor::handleMoveToY(const byte *data) {
  int32_t absolute_position =
      int32_t((uint32_t(data[2]) << 24) + (uint32_t(data[3]) << 16) +
              (uint32_t(data[4]) << 8) + uint32_t(data[5]));
  Axis *axis = axisManager.findAxisByName("Y");
  if (axis)
    axis->moveToPositionMicrosteps(absolute_position);

  DEBUG_PRINTLN("Get MoveToY Command");
}

void CommandProcessor::handleMoveToZ(const byte *data) {
  int32_t absolute_position =
      int32_t((uint32_t(data[2]) << 24) + (uint32_t(data[3]) << 16) +
              (uint32_t(data[4]) << 8) + uint32_t(data[5]));
  Axis *axis = axisManager.findAxisByName("Z");
  if (axis)
    axis->moveToPositionMicrosteps(absolute_position);

  DEBUG_PRINTLN("Get MoveToZ Command");
}

void CommandProcessor::handleSetLim(const byte *data) {
  // data[2]: LIM_CODE (0-5), data[3..6]: limit value (microsteps, 32-bit big-endian)
  int32_t value = int32_t((uint32_t(data[3]) << 24) | (uint32_t(data[4]) << 16) |
                          (uint32_t(data[5]) << 8)  |  uint32_t(data[6]));
  const char *axisName = nullptr;
  int direction = 0;
  switch (data[2]) {
    case LIM_CODE_X_POSITIVE: axisName = "X"; direction =  1; break;
    case LIM_CODE_X_NEGATIVE: axisName = "X"; direction = -1; break;
    case LIM_CODE_Y_POSITIVE: axisName = "Y"; direction =  1; break;
    case LIM_CODE_Y_NEGATIVE: axisName = "Y"; direction = -1; break;
    case LIM_CODE_Z_POSITIVE: axisName = "Z"; direction =  1; break;
    case LIM_CODE_Z_NEGATIVE: axisName = "Z"; direction = -1; break;
    default: return;
  }
  Axis *axis = axisManager.findAxisByName(axisName);
  if (axis)
    axis->setOneSoftLimit(direction, value);
}

void CommandProcessor::handleTurnOnIllumination(const byte *data) {
  // matches legacy Squid main_controller_teensy41.ino:1529: do not touch illumination_source.
  // the legacy Squid host's turn_on_illumination() command packet has cmd[2]=0; if we read data[2] and write source here,
  // it would force source to 0 (LED_ARRAY_FULL = brightfield), lighting up brightfield after switching to a fluorescence channel.
  turn_on_illumination();
}

void CommandProcessor::handleTurnOffIllumination(const byte *data) {
  turn_off_illumination();
}

void CommandProcessor::handleSetIllumination(const byte *data) {
  set_illumination(data[2], (uint16_t(data[3]) << 8) + uint16_t(data[4]));
}

void CommandProcessor::handleSetIlluminationLEDMatrix(const byte *data) {
  set_illumination_led_matrix(data[2], data[3], data[4], data[5]);
}

void CommandProcessor::handleAckJoystickButtonPressed(const byte *data) {
  joystick_button_pressed = false;
}

void CommandProcessor::handleAnalogWriteOnboardDAC(const byte *data) {
  int channel = data[2];
  uint16_t value = (uint16_t(data[3]) << 8) | uint16_t(data[4]);
  set_DAC8050x_output(channel, value);
}

void CommandProcessor::handleSetDAC80508RefDivGain(const byte *data) {
  set_DAC8050x_gain(data[2], data[3]);
}

void CommandProcessor::handleSetIlluminationIntensityFactor(const byte *data) {
  illumination_intensity_factor = float(data[2]) / 100.0f;
}

void CommandProcessor::handleSetPortIntensity(const byte *data) {
  set_port_intensity(data[2], (uint16_t(data[3]) << 8) | uint16_t(data[4]));
}

void CommandProcessor::handleTurnOnPort(const byte *data) {
  turn_on_port(data[2]);
}

void CommandProcessor::handleTurnOffPort(const byte *data) {
  turn_off_port(data[2]);
}

void CommandProcessor::handleSetPortIllumination(const byte *data) {
  set_port_intensity(data[2], (uint16_t(data[3]) << 8) | uint16_t(data[4]));
  if (data[5] != 0) turn_on_port(data[2]);
  else              turn_off_port(data[2]);
}

void CommandProcessor::handleSetMultiPortMask(const byte *data) {
  uint16_t port_mask = (uint16_t(data[2]) << 8) | uint16_t(data[3]);
  uint16_t on_mask   = (uint16_t(data[4]) << 8) | uint16_t(data[5]);
  for (int i = 0; i < IlluminationConfig::NUM_PORTS; i++) {
    if (port_mask & (1 << i)) {
      if (on_mask & (1 << i)) turn_on_port(i);
      else                    turn_off_port(i);
    }
  }
}

void CommandProcessor::handleTurnOffAllPorts(const byte *data) {
  turn_off_all_ports();
}

void CommandProcessor::handleSetWatchdogTimeout(const byte *data) {
  uint32_t timeout = ((uint32_t)data[2] << 24) | ((uint32_t)data[3] << 16)
                   | ((uint32_t)data[4] << 8)  | (uint32_t)data[5];
  watchdog_set_timeout(timeout);
}

void CommandProcessor::handleHeartbeat(const byte *data) {
  // no-op: the watchdog timer is already reset when a valid serial message is received
}

void CommandProcessor::handleMoveW2(const byte *data) {
  // legacy Squid MOVE_W2 (cmd 19): relative move, data[2..5] is int32 microsteps big-endian.
  // when the W2 board is absent, axesmrg::beginAll has deleted this axis -> findAxisByName returns nullptr ->
  // silent no-op (the response packet reports COMPLETED immediately).
  int32_t relative_position =
      int32_t((uint32_t(data[2]) << 24) + (uint32_t(data[3]) << 16) +
              (uint32_t(data[4]) << 8) + uint32_t(data[5]));
  Axis *axis = axisManager.findAxisByName("W2");
  if (axis)
    axis->moveAxis(relative_position);

  DEBUG_PRINTLN("Get MoveW2 Command");
}

void CommandProcessor::handleMoveTurret(const byte *data) {
  // 2026-05-29 MOVE_TURRET (cmd 44): objective turret relative move, data[2..5] is int32 microsteps big-endian.
  // when the E1 board is absent, axesmrg::beginAll has deleted this axis -> findAxisByName returns nullptr -> silent no-op.
  int32_t relative_position =
      int32_t((uint32_t(data[2]) << 24) + (uint32_t(data[3]) << 16) +
              (uint32_t(data[4]) << 8) + uint32_t(data[5]));
  Axis *axis = axisManager.findAxisByName("Turret");
  if (axis)
    axis->moveAxis(relative_position);

  DEBUG_PRINTLN("Get MoveTurret Command");
}

void CommandProcessor::handleMoveToTurret(const byte *data) {
  // 2026-05-29 MOVETO_TURRET (cmd 45): objective turret absolute move.
  int32_t absolute_position =
      int32_t((uint32_t(data[2]) << 24) + (uint32_t(data[3]) << 16) +
              (uint32_t(data[4]) << 8) + uint32_t(data[5]));
  Axis *axis = axisManager.findAxisByName("Turret");
  if (axis)
    axis->moveToPositionMicrosteps(absolute_position);

  DEBUG_PRINTLN("Get MoveToTurret Command");
}

void CommandProcessor::handleSetTriggerMode(const byte *data) {
  if (data[2] <= 1)
    trigger_mode = data[2];
}

void CommandProcessor::handleMoveToW(const byte *data) {
  int32_t absolute_position =
      int32_t((uint32_t(data[2]) << 24) + (uint32_t(data[3]) << 16) +
              (uint32_t(data[4]) << 8) + uint32_t(data[5]));
  Axis *axis = axisManager.findAxisByName("W");
  if (axis)
    axis->moveToPositionMicrosteps(absolute_position);

  DEBUG_PRINTLN("Get MoveToW Command");
}

void CommandProcessor::handleSetLimSwitchPolarity(const byte *data) {
  // data[2]: protocol axis; data[3]: polarity (0=ACTIVE_LOW, 1=ACTIVE_HIGH, 2=DISABLED)
  if (data[3] == POLARITY_DISABLED)
    return;
  const char *name = protocolAxisToName(data[2]);
  if (!name) return;
  Axis *axis = axisManager.findAxisByName(name);
  if (!axis) return;
  uint8_t polarity = data[3];
  axis->getMutableConfig().leftSwitchPolarity = polarity;
  axis->getMutableConfig().rightSwitchPolarity = polarity;
  // only for axes with polarityAffectsChip=true (=Z), rewrite the polarity into the chip REFERENCE_CONF -- this is the key to the "Z-variant software switch"
  // (the host sends the polarity per Z_AXIS_VARIANT, so switching needs no reflash). Fixed-hardware-polarity axes like X/Y only update the struct and do not touch the chip,
  // consistent with legacy Squid firmware (legacy Squid cmd 20 also only sets a software variable), avoiding the X/Y polarity sent by legacy Squid wrongly flipping the chip.
  if (axis->getConfig().polarityAffectsChip)
    axis->reapplyLimitSwitches();
}

void CommandProcessor::handleConfigureStepperDriver(const byte *data) {
  // data[2]: protocol axis; data[3]: microstepping; data[4..5]: RMS current (mA); data[6]: hold current (0-255)
  const char *name = protocolAxisToName(data[2]);
  if (!name) return;
  Axis *axis = axisManager.findAxisByName(name);
  if (!axis) return;

  // microstepping special handling: 0->1, 1-128->as-is, >128->256
  int microstepping = data[3];
  if (microstepping > 128)
    microstepping = 256;
  if (microstepping == 0)
    microstepping = 1;

  float currentMA = float((uint16_t(data[4]) << 8) | uint16_t(data[5]));
  float holdRatio = float(data[6]) / 255.0f;

  axis->configureDriver((uint16_t)microstepping, currentMA, holdRatio);
}

void CommandProcessor::handleSetMaxVelocityAcceleration(const byte *data) {
  // data[2]: protocol axis; data[3:4]: velocity*100 (mm/s); data[5:6]: acceleration*10 (mm/s2)
  const char *name = protocolAxisToName(data[2]);
  if (!name) return;
  Axis *axis = axisManager.findAxisByName(name);
  if (!axis) return;
  float vel_mm = float((uint16_t(data[3]) << 8) | data[4]) / 100.0f;
  float acc_mm = float((uint16_t(data[5]) << 8) | data[6]) / 10.0f;
  axis->setMotionParameters(vel_mm, acc_mm);
}

void CommandProcessor::handleSetLeadScrewPitch(const byte *data) {
  // data[2]: protocol axis; data[3..4]: pitch*1000 (uint16, mm)
  const char *name = protocolAxisToName(data[2]);
  if (!name) return;
  Axis *axis = axisManager.findAxisByName(name);
  if (!axis) return;

  float pitchMM = float((uint16_t(data[3]) << 8) | uint16_t(data[4])) / 1000.0f;
  axis->setLeadScrewPitch(pitchMM);
}

void CommandProcessor::handleSetOffsetVelocity(const byte *data) {
  // consistent with the old architecture callback_set_offset_velocity:
  // only store the value when enable_offset_velocity is true, for use by the joystick loop
  if (!enable_offset_velocity) return;

  // data[3..6]: int32 big-endian (um/s), /1000000 -> mm/s
  float velocityMM =
      float(int32_t(uint32_t(data[3]) << 24 | uint32_t(data[4]) << 16 |
                    uint32_t(data[5]) << 8 | uint32_t(data[6]))) /
      1000000.0f;

  switch (data[2]) {
    case 0: offset_velocity_x = velocityMM; break;  // AXIS_X
    case 1: offset_velocity_y = velocityMM; break;  // AXIS_Y
  }
}

void CommandProcessor::handleConfigureStagePID(const byte *data) {
  // data[2]: protocol axis; data[3]: flip_direction; data[4:5]: transitions_per_rev (big-endian)
  const char *name = protocolAxisToName(data[2]);
  if (!name) return;
  Axis *axis = axisManager.findAxisByName(name);
  if (!axis) return;
  bool flip_direction = data[3];
  uint16_t transitions_per_rev = (uint16_t(data[4]) << 8) | uint16_t(data[5]);
  axis->configureStagePID(flip_direction, transitions_per_rev);
}

void CommandProcessor::handleEnableStagePID(const byte *data) {
  // data[2]: protocol axis
  const char *name = protocolAxisToName(data[2]);
  if (!name) return;
  Axis *axis = axisManager.findAxisByName(name);
  if (axis) axis->enableStagePID();
}

void CommandProcessor::handleDisableStagePID(const byte *data) {
  // data[2]: protocol axis
  const char *name = protocolAxisToName(data[2]);
  if (!name) return;
  Axis *axis = axisManager.findAxisByName(name);
  if (axis) axis->disableStagePID();
}

void CommandProcessor::handleSetHomeSafetyMargin(const byte *data) {
  // data[2]: protocol axis; data[3..4]: margin*1000 (uint16, mm)
  const char *name = protocolAxisToName(data[2]);
  if (!name) return;
  Axis *axis = axisManager.findAxisByName(name);
  if (!axis) return;

  float marginMM = float((uint16_t(data[3]) << 8) | uint16_t(data[4])) / 1000.0f;
  axis->setHomeSafetyMargin(marginMM);
}

void CommandProcessor::handleSetPIDArguments(const byte *data) {
  // data[2]: protocol axis; data[3:4]: P (big-endian uint16); data[5]: I (uint8); data[6]: D (uint8)
  const char *name = protocolAxisToName(data[2]);
  if (!name) return;
  Axis *axis = axisManager.findAxisByName(name);
  if (!axis) return;
  uint16_t p = (uint16_t(data[3]) << 8) | uint16_t(data[4]);
  uint8_t  i = data[5];
  uint8_t  d = data[6];
  axis->setPIDArguments(p, i, d);
}

void CommandProcessor::handleSendHardwareTrigger(const byte *data) {
  int camera_channel = data[2] & 0x0F;
  if (camera_channel >= NUM_TRIGGER_CHANNELS)
    return;

  noInterrupts();

  // in Level trigger mode, if the channel is already triggering, drop the new command to avoid overwriting the in-progress timing
  if (trigger_mode != TRIGGER_MODE_NORMAL &&
      trigger_output_level[camera_channel] == LOW) {
    interrupts();
    return;
  }

  control_strobe[camera_channel] = (data[2] >> 7) & 0x01;
  illumination_on_time_us[camera_channel] =
      (uint32_t(data[3]) << 24) | (uint32_t(data[4]) << 16) |
      (uint32_t(data[5]) << 8)  |  uint32_t(data[6]);

  // pull the trigger pin LOW (start of the negative pulse)
  digitalWrite(camera_trigger_pins[camera_channel], LOW);
  trigger_output_level[camera_channel] = LOW;
  timestamp_trigger_rising_edge[camera_channel] = micros();

  // reset the strobe state
  strobe_on[camera_channel] = false;

  interrupts();
}

void CommandProcessor::handleSetStrobeDelay(const byte *data) {
  int channel = data[2];
  if (channel >= NUM_TRIGGER_CHANNELS)
    return;
  strobe_delay_us[channel] =
      (uint32_t(data[3]) << 24) | (uint32_t(data[4]) << 16) |
      (uint32_t(data[5]) << 8)  |  uint32_t(data[6]);
}

void CommandProcessor::handleSetAxisDisableEnable(const byte *data) {
  // data[2]: protocol axis; data[3]: 0=disable, 1=enable
  const char *name = protocolAxisToName(data[2]);
  if (!name) return;
  Axis *axis = axisManager.findAxisByName(name);
  if (!axis) return;
  if (data[3] == 0) axis->disableAxis();
  else              axis->enableAxis();
}

void CommandProcessor::handleSetPinLevel(const byte *data) {
  // defensive: if the pin requested by the host was not explicitly set OUTPUT in illumination_init,
  // digitalWrite in INPUT mode does not change the actual level. Force OUTPUT on the first write.
  pinMode(data[2], OUTPUT);
  digitalWrite(data[2], data[3]);
}

void CommandProcessor::handleInitFilterWheel(const byte *data) {
  // 2026-05-26 fix a byte-level drop-in deviation:
  // legacy Squid callback_initfilterwheel (commands.cpp:188-192) is an atomic operation:
  //   enable_filterwheel = true;
  //   init_filterwheel_axis(w);    // chip reconfiguration only (SW_RESET + register writes)
  // does **not** trigger homing, does **not** set mcu_cmd_execution_in_progress = true.
  // the actual W homing is triggered separately by a subsequent home_w() (HOME_OR_ZERO + AXIS_W).
  //
  // bug history: previously axis->startHoming() here triggered W homing ->
  // during legacy Squid software's init_filter_wheel(W) + sleep(0.5) + configure_squidfilter(W),
  // octoaxes caused wait_till_operation_is_completed to time out 5s after set_leadscrew_pitch
  // (during homing any_moving=true -> status=IN_PROGRESS, so wait is not woken).
  //
  // fix: no-op + log. The W axis is already configured in filter-wheel mode at axesmrg::beginAll startup
  // (W_AXIS template), and the chip is already initialized at startup. A subsequent configure_squidfilter rewrites
  // key registers such as microstep/current/VMAX/AMAX, so no re-initialization is needed here.
  DEBUG_PRINTLN("INITFILTERWHEEL: no-op (W configured at startup; awaiting HOME_OR_ZERO for actual homing)");
}

void CommandProcessor::handleInitFilterWheelW2(const byte *data) {
  // same as handleInitFilterWheel: legacy Squid callback_initfilterwheel_w2 (commands.cpp:194-198)
  // only enable_filterwheel_w2=true + chip re-init, does not trigger homing. See the handleInitFilterWheel comment.
  DEBUG_PRINTLN("INITFILTERWHEEL_W2: no-op (W2 configured at startup; awaiting HOME_OR_ZERO for actual homing)");
}

void CommandProcessor::handleInitialize(const byte *data) {
  // matches legacy Squid behavior: cmd 254 INITIALIZE = equivalent to "power-cycle".
  // legacy Squid writes RESET_REG=0x52535400 on the first line of tmc4361A_tmc2660_init to soft-reset the chip,
  // then rewrites all configuration. This way, after the host restarts the GUI (chip not power-cycled), residual state such as XACTUAL/EVENTS/RAMPMODE
  // is cleared, so cmd 9 SET_LIM and cmd 29 HOME can start from a clean state.
  //
  // equivalent to the SW_RESET = 0x52535400 on the first line of motor_initMotionController inside Axis::begin().
  // after beginAll, handleReset is called to reset the C++ software state machine (_currentState/_isMoving, etc.).
  if (!axisManager.beginAll()) {
    DEBUG_PRINTLN("INITIALIZE: beginAll FAILED");
  }
  uint8_t count = axisManager.getAxisCount();
  for (uint8_t i = 0; i < count; i++) {
    Axis *axis = axisManager.getAxis(i);
    if (axis) axis->handleReset();
  }
  // DAC + trigger reset
  set_DAC8050x_config();
  set_DAC8050x_default_gain();
  trigger_mode = TRIGGER_MODE_NORMAL;
  DEBUG_PRINTLN("INITIALIZE: chip SW_RESET + reconfig + state machine reset done");
}

void CommandProcessor::handleReset(const byte *data) {
  // stop all axis motion, reset the trigger state
  trigger_mode = TRIGGER_MODE_NORMAL;
  uint8_t count = axisManager.getAxisCount();
  for (uint8_t i = 0; i < count; i++) {
    Axis *axis = axisManager.getAxis(i);
    if (axis) axis->handleReset();
  }
  DEBUG_PRINTLN("RESET: all axes stopped, trigger_mode = 0");
}
