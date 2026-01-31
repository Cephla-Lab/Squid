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

  // Illumination safety interlock - turn off all TTL ports if interlock is triggered
  if (!INTERLOCK_OK())
  {
    digitalWrite(PIN_ILLUMINATION_D1, LOW);
    digitalWrite(PIN_ILLUMINATION_D2, LOW);
    digitalWrite(PIN_ILLUMINATION_D3, LOW);
    digitalWrite(PIN_ILLUMINATION_D4, LOW);
    digitalWrite(PIN_ILLUMINATION_D5, LOW);
  }

  // Illumination timeout check - auto-shutoff ports that have been on too long
  // Note: unsigned arithmetic handles millis() overflow correctly (wraps every ~49 days)
  for (int i = 0; i < NUM_TIMEOUT_PORTS; i++)
  {
    if (illumination_timer_active[i])
    {
      if (millis() - illumination_timer_start[i] >= illumination_timeout_ms)
      {
        turn_off_port(i);  // This also sets illumination_timer_active[i] = false
      }
    }
  }

  joystick_packetSerial.update();

  process_serial_message();
  do_camera_trigger();

  prepare_homing_x();
  prepare_homing_y();
  prepare_homing_z();
  prepare_homing_w();
  prepare_homing_w2();

  check_homing_x();
  check_homing_y();
  check_homing_z();
  check_homing_w();
  check_homing_w2();

  finalize_homing_x();
  finalize_homing_y();
  finalize_homing_z();
  finalize_homing_w();
  finalize_homing_w2();
  finalize_homing_xy();

  check_joystick();
  do_focus_control();

  send_position_update();
  check_position();
  check_limits();
}
