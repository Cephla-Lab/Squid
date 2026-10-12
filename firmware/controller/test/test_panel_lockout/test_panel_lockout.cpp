/*
  Behaviour of the joystick panel lock-out, on the real firmware sources.

  WHAT THIS PINS
  --------------
  While a commanded move or a homing is in progress the panel is locked out
  (panel_locked_out(), functions.cpp), and an axis the joystick was jogging
  must be brought to rest in the loop pass the lock-out begins in - not at
  check_joystick()'s next tick, which a command shorter than
  interval_send_joystick_update (30 ms) ends before. That has to hold however
  the stick reads when the command starts: still deflected, or just centred
  (a centred stick reads like one that was never deflected, so a stop decided
  from the packets misses it), or with no packet in between at all. The
  command's own axis is left to the command, an axis that is not jogging is not
  written to, and no deflection reaches a motor while the lock-out lasts.

  HOW IT COMPILES ON THE HOST
  ---------------------------
  The four firmware sources are #included into this translation unit, after
  panel_host_shim.h (the Arduino / Teensy stand-ins; see there for why it comes
  first). The TMC4361A primitives they call are defined below: setSpeed and
  stop are recorders, the rest do nothing. None of them exists in env:native's
  build_src_filter, so nothing collides; this test must stay in its own
  directory (its own binary) all the same.

  A LOOP PASS
  -----------
  main_controller_teensy41.ino runs joystick_packetSerial.update() (packets ->
  onJoystickPacketReceived()), then process_serial_message() (a command
  starts), ..., check_joystick(), ..., check_position() (a command ends). The
  helpers below play those steps in that order.
*/
#include <unity.h>

#include "panel_host_shim.h"

unsigned long tmc_test_delay_us_total = 0;   // declared by test_driver_sequence's <Arduino.h> shim

#include "global_defs.cpp"
#include "globals.cpp"
#include "functions.cpp"
#include "operations.cpp"

/* ------------------------------------------------------------------ recorder */

static int32_t motor_speed[TOTAL_AXES];   // what the joystick paths last told each axis: a jog speed, 0 = at rest
static unsigned motor_calls[TOTAL_AXES];  // setSpeed + stop calls per axis
static unsigned jog_calls_while_locked;   // setSpeed with a non-zero speed while panel_locked_out()

static int axis_of(const TMC4361ATypeDef *t) { return int(t - tmc4361); }

void tmc4361A_setSpeed(TMC4361ATypeDef *t, int32_t velocity)
{
  motor_speed[axis_of(t)] = velocity;
  motor_calls[axis_of(t)]++;
  if (velocity != 0 && panel_locked_out())
    jog_calls_while_locked++;
}
void tmc4361A_stop(TMC4361ATypeDef *t)
{
  motor_speed[axis_of(t)] = 0;
  motor_calls[axis_of(t)]++;
}
int32_t tmc4361A_vmmToMicrosteps(TMC4361ATypeDef *, float mm) { return int32_t(mm * 1000); }

/* the rest of what operations.cpp calls: never reached by the paths under test */
int32_t tmc4361A_readInt(TMC4361ATypeDef *, uint8_t) { return 0; }
void    tmc4361A_writeInt(TMC4361ATypeDef *, uint8_t, int32_t) {}
int32_t tmc4361A_currentPosition(TMC4361ATypeDef *) { return 0; }
int32_t tmc4361A_targetPosition(TMC4361ATypeDef *) { return 0; }
int8_t  tmc4361A_setCurrentPosition(TMC4361ATypeDef *, int32_t) { return 0; }
int8_t  tmc4361A_moveTo(TMC4361ATypeDef *, int32_t) { return 0; }
bool    tmc4361A_isRunning(TMC4361ATypeDef *, bool) { return false; }
int32_t tmc4361A_xmmToMicrosteps(TMC4361ATypeDef *, float) { return 0; }
uint8_t tmc4361A_readLimitSwitches(TMC4361ATypeDef *) { return 0; }
uint8_t tmc4361A_readSwitchEvent(TMC4361ATypeDef *) { return 0; }
void    tmc4361A_set_PID(TMC4361ATypeDef *, uint8_t) {}
void    tmc4361A_write_encoder(TMC4361ATypeDef *, int32_t) {}

/* ------------------------------------------------------------------- helpers */

static const int16_t STICK = 12000;   // a deflection, in panel units (full scale 32768)

static void packet(int16_t dx, int16_t dy)
{
  uint8_t b[JOYSTICK_MSG_LENGTH] = {0};
  uint32_t wheel = uint32_t(focuswheel_pos);   // the wheel does not turn in these tests
  b[0] = wheel >> 24; b[1] = wheel >> 16; b[2] = wheel >> 8; b[3] = wheel;
  uint16_t rx = uint16_t(int16_t(JOYSTICK_SIGN_X * dx));
  uint16_t ry = uint16_t(int16_t(JOYSTICK_SIGN_Y * dy));
  b[4] = rx >> 8; b[5] = rx; b[6] = ry >> 8; b[7] = ry;
  onJoystickPacketReceived(b, JOYSTICK_MSG_LENGTH);
}

/* check_joystick() with its 30 ms tick due or not */
static void joystick_pass(bool tick_due)
{
  us_since_last_joystick_update = tick_due ? interval_send_joystick_update + 1 : 0;
  check_joystick();
}

static void clear_recorder()
{
  for (int a = 0; a < TOTAL_AXES; a++) { motor_speed[a] = 0; motor_calls[a] = 0; }
  jog_calls_while_locked = 0;
}

void setUp(void)
{
  X_commanded_movement_in_progress = Y_commanded_movement_in_progress = Z_commanded_movement_in_progress = false;
  W_commanded_movement_in_progress = W2_commanded_movement_in_progress = false;
  is_homing_X = is_homing_Y = is_homing_Z = is_homing_XY = is_homing_W = is_homing_W2 = false;
  is_preparing_for_homing_X = is_preparing_for_homing_Y = is_preparing_for_homing_Z = false;
  is_preparing_for_homing_W = is_preparing_for_homing_W2 = false;
  for (int a = 0; a < TOTAL_AXES; a++) tmc4361[a].driver_type = DRIVER_TMC2660;
  enable_offset_velocity = false;
  first_packet_from_joystick_panel = false;
  focuswheel_pos = 0;
  joystick_delta_x = joystick_delta_y = 0;
  joystick_jogging_x = joystick_jogging_y = false;
  flag_read_joystick = false;
  joystick_pass(false);   // not locked out: re-arms check_joystick()'s lock-out edge
  clear_recorder();
}
void tearDown(void) {}

/* the operator jogs: a deflected packet, then check_joystick()'s tick */
static void jog(int16_t dx, int16_t dy)
{
  packet(dx, dy);
  joystick_pass(true);
}

/* ------------------------------------------------------------------- tests */

/* A command shorter than the tick, the stick held throughout: X is at rest from the pass the command
   starts in until it ends. After the command the panel is live again and the held stick resumes the jog. */
void test_held_stick_is_stopped_for_a_short_command(void)
{
  jog(STICK, 0);
  TEST_ASSERT_NOT_EQUAL_MESSAGE(0, motor_speed[x], "precondition: X is jogging");

  packet(STICK, 0);
  Y_commanded_movement_in_progress = true;   // process_serial_message(): a short Y move starts
  joystick_pass(false);                      // the tick is not due
  TEST_ASSERT_EQUAL_MESSAGE(0, motor_speed[x], "X must be at rest in the pass the command starts in");
  packet(STICK, 0);
  joystick_pass(false);
  Y_commanded_movement_in_progress = false;  // check_position(): the move ends before any tick
  TEST_ASSERT_EQUAL_MESSAGE(0, motor_speed[x], "X must stay at rest for the whole command");
  TEST_ASSERT_EQUAL_UINT_MESSAGE(0, jog_calls_while_locked, "no jog while locked out");

  packet(STICK, 0);
  joystick_pass(true);
  TEST_ASSERT_NOT_EQUAL_MESSAGE(0, motor_speed[x], "after the command a held stick jogs again (by design)");
}

/* The review case: the stick is centred as the command starts. The packets then read like a stick that
   was never deflected, and the jog the last tick started must still be stopped at once. Both axes. */
void test_newly_centred_stick_is_stopped_for_a_short_command(void)
{
  jog(STICK, -STICK);
  TEST_ASSERT_NOT_EQUAL_MESSAGE(0, motor_speed[x], "precondition: X is jogging");
  TEST_ASSERT_NOT_EQUAL_MESSAGE(0, motor_speed[y], "precondition: Y is jogging");

  packet(0, 0);
  Z_commanded_movement_in_progress = true;
  joystick_pass(false);
  TEST_ASSERT_EQUAL_MESSAGE(0, motor_speed[x], "X must be at rest in the pass the command starts in");
  TEST_ASSERT_EQUAL_MESSAGE(0, motor_speed[y], "Y must be at rest in the pass the command starts in");
  packet(0, 0);
  joystick_pass(false);
  Z_commanded_movement_in_progress = false;
  TEST_ASSERT_EQUAL_MESSAGE(0, motor_speed[x], "X must stay at rest for the whole command");
  TEST_ASSERT_EQUAL_MESSAGE(0, motor_speed[y], "Y must stay at rest for the whole command");
}

/* The stop does not wait for a packet: none arrives between the command's start and the pass. */
void test_jog_is_stopped_without_a_packet_in_the_lockout(void)
{
  jog(STICK, 0);
  Z_commanded_movement_in_progress = true;
  joystick_pass(false);
  TEST_ASSERT_EQUAL_MESSAGE(0, motor_speed[x], "X must be at rest in the pass the command starts in");
}

/* A homing is a lock-out too. */
void test_jog_is_stopped_when_a_homing_starts(void)
{
  jog(0, STICK);
  is_preparing_for_homing_Z = true;
  joystick_pass(false);
  TEST_ASSERT_EQUAL_MESSAGE(0, motor_speed[y], "Y must be at rest when a Z homing starts");
}

/* An axis the joystick is not jogging is not written to when a command starts: a controller without a
   panel sees no new motor writes. */
void test_idle_axes_are_not_touched(void)
{
  Z_commanded_movement_in_progress = true;
  joystick_pass(false);
  joystick_pass(false);
  Z_commanded_movement_in_progress = false;
  TEST_ASSERT_EQUAL_UINT_MESSAGE(0, motor_calls[x], "X is idle: no write");
  TEST_ASSERT_EQUAL_UINT_MESSAGE(0, motor_calls[y], "Y is idle: no write");
}

/* The command's own axis belongs to the command: the lock-out leaves it alone. */
void test_commanded_axis_is_left_to_the_command(void)
{
  jog(STICK, STICK);
  clear_recorder();
  X_commanded_movement_in_progress = true;
  joystick_pass(false);
  TEST_ASSERT_EQUAL_UINT_MESSAGE(0, motor_calls[x], "X is commanded: the lock-out must not write to it");
  TEST_ASSERT_EQUAL_UINT_MESSAGE(1, motor_calls[y], "Y is jogging: one stop");
  TEST_ASSERT_EQUAL_MESSAGE(0, motor_speed[y], "Y must be at rest");
}

/* A long command: deflected packets and due ticks throughout never start a jog. */
void test_deflection_never_reaches_a_motor_while_locked_out(void)
{
  Y_commanded_movement_in_progress = true;
  for (int i = 0; i < 20; i++)
  {
    packet(STICK, -STICK);
    joystick_pass(i % 5 == 4);
  }
  Y_commanded_movement_in_progress = false;
  TEST_ASSERT_EQUAL_UINT_MESSAGE(0, jog_calls_while_locked, "no jog while locked out");
  TEST_ASSERT_EQUAL_MESSAGE(0, motor_speed[x], "X never jogged");
}

/* Back-to-back commands: A ends in check_position() (after check_joystick()), the next pass takes a
   deflected packet while unlocked, and B starts in process_serial_message() before check_joystick() runs.
   check_joystick() sees no unlocked pass in between; the deflection must still not jog X during B. */
void test_deflection_between_back_to_back_commands_never_jogs(void)
{
  Y_commanded_movement_in_progress = true;    // command A
  joystick_pass(false);
  Y_commanded_movement_in_progress = false;   // check_position(): A ends, after check_joystick()

  packet(STICK, 0);                           // next pass: joystick_packetSerial.update(), unlocked
  Z_commanded_movement_in_progress = true;    // process_serial_message(): queued command B starts
  joystick_pass(true);                        // the tick is due
  TEST_ASSERT_EQUAL_UINT_MESSAGE(0, jog_calls_while_locked, "no jog while locked out");
  TEST_ASSERT_EQUAL_MESSAGE(0, motor_speed[x], "X must not jog during B");
  Z_commanded_movement_in_progress = false;
}

int main(int argc, char **argv)
{
  UNITY_BEGIN();
  RUN_TEST(test_held_stick_is_stopped_for_a_short_command);
  RUN_TEST(test_newly_centred_stick_is_stopped_for_a_short_command);
  RUN_TEST(test_jog_is_stopped_without_a_packet_in_the_lockout);
  RUN_TEST(test_jog_is_stopped_when_a_homing_starts);
  RUN_TEST(test_idle_axes_are_not_touched);
  RUN_TEST(test_commanded_axis_is_left_to_the_command);
  RUN_TEST(test_deflection_never_reaches_a_motor_while_locked_out);
  RUN_TEST(test_deflection_between_back_to_back_commands_never_jogs);
  return UNITY_END();
}
