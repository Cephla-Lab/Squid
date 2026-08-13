#include "init.h"

#include "tmc/drivers/driver_probe.h"
#include "tmc/drivers/stepper_driver.h"

/*
  Boot-time driver report (design M7: host visibility of the driver type is a
  boot-time serial log, not a protocol change).

  Emitted per axis IMMEDIATELY AFTER that axis is probed and initialised, and
  only for axes that were probed. That restriction is the point: driver_probe_raw
  initialises to 0, which is indistinguishable from a genuine all-zeros read, and
  driver_type is DRIVER_UNKNOWN in both cases - so a line printed for an
  unprobed axis would read exactly like the failure the bench gate is looking
  for. An axis that was never probed has no line at all.

  What the gate reads (design 10, step 0): a TMC2660 axis must come back with a
  word that is neither 0x00000000 nor 0xFFFFFFFF and whose byte [31:24] is not
  0x40. Read the verdict alongside the word - driver_probe_raw is the LAST of
  three reads, not a summary, so a zero word beside a TMC2660 verdict is one
  flaky read; the failure is a DRIVER_UNKNOWN verdict.

  This writes ASCII onto the same USB serial link that carries the 24-byte status
  packets. At boot that link is idle - loop() has not run, so no packet has been
  sent - and on the filter-wheel path the host resynchronises by sliding a byte
  at a time until a CRC matches (software/control/microcontroller.py,
  read_received_packet), costing a few "Bad checksum" warnings. That cost is
  accepted deliberately: the gate it serves is what decides whether this firmware
  is safe for the installed base, and the design requires measuring the warm
  filter-wheel path too, because that one re-probes an already-configured 2660 at
  RDSEL = 2 where SG and SE are both zero at standstill.
*/
void report_driver_probe(uint8_t axis)
{
  // Indexed by INTERNAL axis index (def_v1.h), which is not the protocol order.
  static const char *const AXIS_LABEL[TOTAL_AXES] = {"Y", "X", "Z", "W", "W2"};
  static_assert(y == 0 && x == 1 && z == 2 && w == 3 && w2 == 4,
                "AXIS_LABEL is written out in internal axis-index order");

  if (axis >= TOTAL_AXES)
    return;

  // Zero-padded to 8 digits, and hand-formatted rather than printf'd: the raw
  // word is read byte by byte at the bench ("is [31:24] 0x40?"), so suppressed
  // leading zeros would move the byte boundaries, and pulling newlib's
  // formatted-output machinery in for one diagnostic line costs ~24 KB of flash.
  static const char HEX_DIGITS[] = "0123456789ABCDEF";
  uint32_t raw = tmc4361[axis].driver_probe_raw;
  char hex[9];
  for (int i = 7; i >= 0; i--) {
    hex[i] = HEX_DIGITS[raw & 0x0F];
    raw >>= 4;
  }
  hex[8] = '\0';

  SerialUSB.print("[TMC] ");
  SerialUSB.print(AXIS_LABEL[axis]);
  SerialUSB.print(": ");
  SerialUSB.print(tmc_driver_name(tmc4361[axis].driver_type));
  SerialUSB.print(" probe_raw=0x");
  SerialUSB.println(hex);
}

void init_serial_communication()
{
    // Initialize Native USB port
    SerialUSB.begin(2000000);
    delay(500);
    SerialUSB.setTimeout(200);
  
    // Joystick packet serial
    Serial5.begin(115200);
    joystick_packetSerial.setStream(&Serial5);
    joystick_packetSerial.setPacketHandler(&onJoystickPacketReceived);
}

void init_lasers_and_led_driver() {
#ifndef DISABLE_LASER_INTERLOCK
  // laser safety interlock
  pinMode(PIN_ILLUMINATION_INTERLOCK, INPUT_PULLUP);
#endif

  // Illumination Control TTL Ports
  pinMode(PIN_ILLUMINATION_D1, OUTPUT);
  digitalWrite(PIN_ILLUMINATION_D1, LOW);

  pinMode(PIN_ILLUMINATION_D2, OUTPUT);
  digitalWrite(PIN_ILLUMINATION_D2, LOW);

  pinMode(PIN_ILLUMINATION_D3, OUTPUT);
  digitalWrite(PIN_ILLUMINATION_D3, LOW);

  pinMode(PIN_ILLUMINATION_D4, OUTPUT);
  digitalWrite(PIN_ILLUMINATION_D4, LOW);

  pinMode(PIN_ILLUMINATION_D5, OUTPUT);
  digitalWrite(PIN_ILLUMINATION_D5, LOW);

  // LED drivers
  pinMode(pin_LT3932_SYNC, OUTPUT);
  analogWriteFrequency(pin_LT3932_SYNC, 2000000);
  analogWrite(pin_LT3932_SYNC, 128);

  // led matrix
  FastLED.addLeds<APA102, LED_MATRIX_DATA_PIN, LED_MATRIX_CLOCK_PIN, BGR, 1>(matrix, NUM_LEDS);  // 1 MHz clock rate

  // strobe timer
  strobeTimer.begin(ISR_strobeTimer, strobeTimer_interval_us);
}

void init_power()
{
  // power good pin
  pinMode(pin_PG, INPUT_PULLUP);

  // wait for PG to turn high
  delay(100);
  while (!digitalRead(pin_PG))
  {
    delay(50);
  }
}

void init_camera()
{
  for (int i = 0; i < 4; i++)
  {
    pinMode(camera_trigger_pins[i], OUTPUT);
    digitalWrite(camera_trigger_pins[i], HIGH);
  }
}

void init_io()
{
  for (int i = 0; i < num_digital_pins; i++)
  {
    pinMode(digitial_output_pins[i], OUTPUT);
    digitalWrite(digitial_output_pins[i], LOW);
  }
}

void init_stages()
{
  // disable all axes (including W2 at index 4)
  for (int i = 0; i < 5; i++)
  {
    pinMode(pin_TMC4361_CS[i], OUTPUT);
    digitalWrite(pin_TMC4361_CS[i], HIGH);
  }

  // timer - does not work with SPI
  /*
    IntervalTimer systemTimer;
    systemTimer.begin(timer_interruptHandler, TIMER_PERIOD);
  */

  // DAC pins
  pinMode(DAC8050x_CS_pin, OUTPUT);
  digitalWrite(DAC8050x_CS_pin, HIGH);

  /*********************************************************************************************************
   ************************************** TMC4361A + TMC2660 beginning *************************************
   *********************************************************************************************************/
  // PID (including W2 at index 4)
  for (int i = 0; i < 5; i++) {
    stage_PID_enabled[i] = 0;

    axes_pid_arg[i].p = (1<<12);
    axes_pid_arg[i].i = 0;
    axes_pid_arg[i].d = 0;
  }

  // clock for X, Y, Z, W (pin 37)
  pinMode(pin_TMC4361_CLK, OUTPUT);
  analogWriteFrequency(pin_TMC4361_CLK, clk_Hz_TMC4361);
  analogWrite(pin_TMC4361_CLK, 128); // 50% duty

  // clock for W2 (pin 28) - same frequency as main clock
  pinMode(pin_TMC4361_CLK_W2, OUTPUT);
  analogWriteFrequency(pin_TMC4361_CLK_W2, clk_Hz_TMC4361);
  analogWrite(pin_TMC4361_CLK_W2, 128); // 50% duty

  // initialize TMC4361 structs with default values and initialize CS pins
  for (int i = 0; i < STAGE_AXES; i++)
  {
    // initialize the tmc4361 with their channel number and default configuration
    tmc4361A_init(&tmc4361[i], pin_TMC4361_CS[i], &tmc4361_configs[i], tmc4361A_defaultRegisterResetState);
    // set the chip select pins
    pinMode(pin_TMC4361_CS[i], OUTPUT);
    digitalWrite(pin_TMC4361_CS[i], HIGH);
  }

  // Per-axis driver parameters. r_sense is used only by TMC2660 axes and
  // current_range only by TMC2240 axes; both are set unconditionally because the
  // probe has not run yet and neither is known to be the live one.
  //
  // This must precede the first tmc_driver_set_current() below. tmc4361A_init()
  // just above zeroed both fields, and a TMC2660 axis asked for current with
  // r_sense = 0 encodes CS = 0 - minimum current, a stage that cannot move.
  tmc4361[x].r_sense = R_sense_xy;  tmc4361[x].current_range = CURRENT_RANGE_XY;
  tmc4361[y].r_sense = R_sense_xy;  tmc4361[y].current_range = CURRENT_RANGE_XY;
  tmc4361[z].r_sense = R_sense_z;   tmc4361[z].current_range = CURRENT_RANGE_Z;

  // SPI
  SPI.begin();
  delayMicroseconds(5000);

  // Identify the power stage on each axis, then initialise it. The probe needs
  // SPI, so it cannot run any earlier than this; it is done one axis at a time,
  // immediately before that axis's driver init, because the probe starts the
  // automatic SPI output and leaves SPIOUT_CONF at its own word until the
  // driver's init() writes back the format that driver actually speaks. A
  // separate earlier pass over all axes would hold every one of them in that
  // state for the duration of the pass instead of for ~800 us.
  //
  // The verdict is cached in tmc4361[i].driver_type, so callback_initialize can
  // re-init the drivers later without re-probing. init_filterwheel_axis() is the
  // exception and must probe every time - see commands.cpp.
  for (int i = 0; i < STAGE_AXES; i++)
  {
    tmc_driver_probe(&tmc4361[i]);
    tmc_driver_init(&tmc4361[i], clk_Hz_TMC4361); // set up ICs with SPI control and other parameters
    report_driver_probe(i);                       // after init, so the probe's SPIOUT_CONF window stays short
  }

  // Motor configurations. Current is in mA and the driver seam converts it.
  // These follow SPI.begin() AND tmc_driver_init() because they now write
  // registers: master could call its predecessor before SPI.begin() only
  // because that function wrote struct fields and nothing else.
  tmc4361A_motor_config(&tmc4361[x], X_MOTOR_RMS_CURRENT_mA, X_MOTOR_I_HOLD, SCREW_PITCH_X_MM, FULLSTEPS_PER_REV_X, MICROSTEPPING_X);
  tmc4361A_motor_config(&tmc4361[y], Y_MOTOR_RMS_CURRENT_mA, Y_MOTOR_I_HOLD, SCREW_PITCH_Y_MM, FULLSTEPS_PER_REV_Y, MICROSTEPPING_Y);
  tmc4361A_motor_config(&tmc4361[z], Z_MOTOR_RMS_CURRENT_mA, Z_MOTOR_I_HOLD, SCREW_PITCH_Z_MM, FULLSTEPS_PER_REV_Z, MICROSTEPPING_Z); // need to make current scaling on TMC2660 is > 16 (out of 31)

  // enable limit switch reading
  tmc4361A_enableLimitSwitch(&tmc4361[x], lft_sw_pol[x], LEFT_SW, flip_limit_switch_x);
  tmc4361A_enableLimitSwitch(&tmc4361[x], rht_sw_pol[x], RGHT_SW, flip_limit_switch_x);
  tmc4361A_enableLimitSwitch(&tmc4361[y], lft_sw_pol[y], LEFT_SW, flip_limit_switch_y);
  tmc4361A_enableLimitSwitch(&tmc4361[y], rht_sw_pol[y], RGHT_SW, flip_limit_switch_y);
  tmc4361A_enableLimitSwitch(&tmc4361[z], rht_sw_pol[z], RGHT_SW, false);
  tmc4361A_enableLimitSwitch(&tmc4361[z], lft_sw_pol[z], LEFT_SW, false); // removing this causes z homing to not work properly

  // motion profile configuration
  max_acceleration_usteps[x] = tmc4361A_ammToMicrosteps(&tmc4361[x], MAX_ACCELERATION_X_mm);
  max_acceleration_usteps[y] = tmc4361A_ammToMicrosteps(&tmc4361[y], MAX_ACCELERATION_Y_mm);
  max_acceleration_usteps[z] = tmc4361A_ammToMicrosteps(&tmc4361[z], MAX_ACCELERATION_Z_mm);
  max_acceleration_usteps[w] = tmc4361A_ammToMicrosteps(&tmc4361[w], MAX_ACCELERATION_W_mm);
  max_velocity_usteps[x] = tmc4361A_vmmToMicrosteps(&tmc4361[x], MAX_VELOCITY_X_mm);
  max_velocity_usteps[y] = tmc4361A_vmmToMicrosteps(&tmc4361[y], MAX_VELOCITY_Y_mm);
  max_velocity_usteps[z] = tmc4361A_vmmToMicrosteps(&tmc4361[z], MAX_VELOCITY_Z_mm);
  max_velocity_usteps[w] = tmc4361A_vmmToMicrosteps(&tmc4361[w], MAX_VELOCITY_W_mm);
  for (int i = 0; i < STAGE_AXES; i++)
  {
    // initialize ramp with default values
    tmc4361A_setMaxSpeed(&tmc4361[i], max_velocity_usteps[i]);
    tmc4361A_setMaxAcceleration(&tmc4361[i], max_acceleration_usteps[i]);
    tmc4361[i].rampParam[ASTART_IDX] = 0;
    tmc4361[i].rampParam[DFINAL_IDX] = 0;
    tmc4361A_sRampInit(&tmc4361[i]);

    tmc4361A_set_PID(&tmc4361[i], PID_DISABLE);
  }

  // homing switch settings
  tmc4361A_enableHomingLimit(&tmc4361[x], lft_sw_pol[x], TMC4361_homing_sw[x], home_safety_margin[x]);
  tmc4361A_enableHomingLimit(&tmc4361[y], lft_sw_pol[y], TMC4361_homing_sw[y], home_safety_margin[y]);
  tmc4361A_enableHomingLimit(&tmc4361[z], rht_sw_pol[z], TMC4361_homing_sw[z], home_safety_margin[z]);

  /*********************************************************************************************************
   ***************************************** TMC4361A + TMC2660 end ****************************************
   *********************************************************************************************************/
  // DAC init
  set_DAC8050x_config();
  set_DAC8050x_default_gain();

  // motor stall prevention. Return value is a bool where 1 = accepted; both
  // calls are in range, and master discarded it here too.
  tmc_driver_config_stallguard(&tmc4361[x], 12, true, 1);
  tmc_driver_config_stallguard(&tmc4361[y], 12, true, 1);
}
