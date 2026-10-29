#include "src/init.h"
#include "src/operations.h"
#include "src/serial_communication.h"
#include "src/commands/sequence_commands.h"
#include "src/sequencer/seq_bind.h"
#include "src/sequencer/seq_selftest.h"

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
    seq_abort(seq::SeqError::HostAbort);  // a running sequence must fail visibly, not go dark
    watchdog_enabled = false;  // One-shot: don't keep firing every loop iteration
  }

  joystick_packetSerial.update();

  process_serial_message();
  do_camera_trigger();

  // Hardware sequencer: no-op unless a sequence is running. It also aborts the run through the
  // engine when the laser interlock opens (the block above only forces the pins low).
  seq_tick();
  seq_transport_tick();  // completes the pending SEQ_RUN / SEQ_CANCEL once the run is terminal
#ifdef SEQ_SELFTEST
  seq_selftest_tick();
#endif

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

  // The joystick and the focus wheel are ignored while a sequence runs: a nudged wheel must
  // not move Z in the middle of a stack (do_focus_control writes the Z target every pass).
  if (!seq_running())
  {
    check_joystick();
    do_focus_control();
  }

  send_position_update();
  check_position();
  check_limits();
}
