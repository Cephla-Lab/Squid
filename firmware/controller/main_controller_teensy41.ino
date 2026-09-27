#include "src/init.h"
#include "src/operations.h"
#include "src/move_settle.h"
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

  // Serial watchdog - auto-shutoff illumination if software stops communicating
  if (watchdog_enabled && (millis() - last_serial_message_time >= watchdog_timeout_ms))
  {
    turn_off_all_ports();
    watchdog_enabled = false;  // One-shot: don't keep firing every loop iteration
  }

  joystick_packetSerial.update();
#ifdef BENCH_WHEEL_INJECT
  bench_wheel_service();   // BENCH BUILDS ONLY: scheduled focus-wheel packets, handled where the panel's are
  bench_panel_service();   // BENCH BUILDS ONLY: whole synthetic panel packets, through the panel's handler
#endif
#ifdef BENCH_SETTLE_TRACE
  bench_settle_trace_service();   // BENCH BUILDS ONLY: a few lines per pass of a settle-trace dump the host asked for
#endif

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
  // Ordering is a latency choice, not the correctness one: check_closed_loop() gets
  // the chance to re-engage a rest-only loop before check_position() looks at the
  // axis, so a settled move is acknowledged on this iteration rather than the next.
  // Correctness is check_position()'s own pid_engage_pending() test - the two
  // functions read STATUS separately, ~0.36 ms apart, so a ramp that stops between
  // the reads would otherwise be acknowledged with the loop still held open.
  check_closed_loop();
  // Move-and-settle (SET_LOOP_STRATEGY): the axes it owns are skipped by check_closed_loop() and
  // advanced here, right before check_position() for the same latency reason.
  check_move_settle();
  check_position();
  check_limits();
}
