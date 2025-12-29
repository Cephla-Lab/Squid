#include "src/init.h"
#include "src/operations.h"
#include "src/serial_communication.h"

#include "src/def/def_v1.h"

void setup() {
  init_serial_communication();
  init_lasers_and_led_driver();
  init_power();
  init_camera();
  init_io();
  init_stages();
  init_callbacks();
}

void loop() {

  joystick_packetSerial.update();

  process_serial_message();
  do_camera_trigger();

  prepare_homing_x();
  prepare_homing_y();
  prepare_homing_z();
  prepare_homing_w();

  check_homing_x();
  check_homing_y();
  check_homing_z();
  check_homing_w();

  finalize_homing_x();
  finalize_homing_y();
  finalize_homing_z();
  finalize_homing_w();
  finalize_homing_xy();

  check_joystick();
  do_focus_control();

  send_position_update();
  check_position();
  check_limits();
}
