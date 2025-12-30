#include "init.h"

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
  pinMode(LASER_INTERLOCK, INPUT_PULLUP);
#endif

  // enable pins
  pinMode(LASER_405nm, OUTPUT);
  digitalWrite(LASER_405nm, LOW);

  pinMode(LASER_488nm, OUTPUT);
  digitalWrite(LASER_488nm, LOW);

  pinMode(LASER_638nm, OUTPUT);
  digitalWrite(LASER_638nm, LOW);

  pinMode(LASER_561nm, OUTPUT);
  digitalWrite(LASER_561nm, LOW);

  pinMode(LASER_730nm, OUTPUT);
  digitalWrite(LASER_730nm, LOW);

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
  for (int i = 0; i < 6; i++)
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
  // disable all axes 
  for (int i = 0; i < 4; i++)
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
  // PID
  for (int i = 0; i < 4; i++) {
    stage_PID_enabled[i] = 0;

    axes_pid_arg[i].p = (1<<12);
    axes_pid_arg[i].i = 0;
    axes_pid_arg[i].d = 0;
  }

  // clock
  pinMode(pin_TMC4361_CLK, OUTPUT);
  analogWriteFrequency(pin_TMC4361_CLK, clk_Hz_TMC4361);
  analogWrite(pin_TMC4361_CLK, 128); // 50% duty

  // initialize TMC4361 structs with default values and initialize CS pins
  for (int i = 0; i < STAGE_AXES; i++)
  {
    // initialize the tmc4361 with their channel number and default configuration
    tmc4361A_init(&tmc4361[i], pin_TMC4361_CS[i], &tmc4361_configs[i], tmc4361A_defaultRegisterResetState);
    // set the chip select pins
    pinMode(pin_TMC4361_CS[i], OUTPUT);
    digitalWrite(pin_TMC4361_CS[i], HIGH);
  }

  // motor configurations
  tmc4361A_tmc2660_config(&tmc4361[x], (X_MOTOR_RMS_CURRENT_mA / 1000)*R_sense_xy / 0.2298, X_MOTOR_I_HOLD, 1, 1, 1, SCREW_PITCH_X_MM, FULLSTEPS_PER_REV_X, MICROSTEPPING_X);
  tmc4361A_tmc2660_config(&tmc4361[y], (Y_MOTOR_RMS_CURRENT_mA / 1000)*R_sense_xy / 0.2298, Y_MOTOR_I_HOLD, 1, 1, 1, SCREW_PITCH_Y_MM, FULLSTEPS_PER_REV_Y, MICROSTEPPING_Y);
  tmc4361A_tmc2660_config(&tmc4361[z], (Z_MOTOR_RMS_CURRENT_mA / 1000)*R_sense_z / 0.2298, Z_MOTOR_I_HOLD, 1, 1, 1, SCREW_PITCH_Z_MM, FULLSTEPS_PER_REV_Z, MICROSTEPPING_Z); // need to make current scaling on TMC2660 is > 16 (out of 31)

  // SPI
  SPI.begin();
  delayMicroseconds(5000);

  // initilize TMC4361 and TMC2660 - turn on functionality
  for (int i = 0; i < STAGE_AXES; i++)
    tmc4361A_tmc2660_init(&tmc4361[i], clk_Hz_TMC4361); // set up ICs with SPI control and other parameters

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

  // motor stall prevention
  tmc4361A_config_init_stallGuard(&tmc4361[x], 12, true, 1);
  tmc4361A_config_init_stallGuard(&tmc4361[y], 12, true, 1);
}
