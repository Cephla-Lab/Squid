#include <unity.h>
#include <stdint.h>
#include <stdio.h>
#include <cstring>

#include "constants_protocol.h"

void setUp(void) {}
void tearDown(void) {}

/**
 * These tests verify that command byte layouts match the protocol specification.
 *
 * Command packet format (8 bytes):
 *   byte[0]: command ID (sequence number)
 *   byte[1]: command code
 *   byte[2-6]: parameters (varies by command)
 *   byte[7]: CRC-8
 *
 * This test file creates mock command packets and verifies the byte positions.
 */

// Helper to create a command packet
void create_command(uint8_t* buffer, uint8_t cmd_id, uint8_t cmd_code) {
    memset(buffer, 0, CMD_LENGTH);
    buffer[0] = cmd_id;
    buffer[1] = cmd_code;
    // CRC would be at buffer[7], but we don't compute it in these tests
}

/***************************************************************************************************/
/******************************** SET_PORT_INTENSITY Layout ****************************************/
/***************************************************************************************************/
// Byte layout: [cmd_id, 34, port, intensity_hi, intensity_lo, 0, 0, crc]

void test_set_port_intensity_command_code(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, SET_PORT_INTENSITY);
    TEST_ASSERT_EQUAL_UINT8(SET_PORT_INTENSITY, buffer[1]);
    TEST_ASSERT_EQUAL_UINT8(34, buffer[1]);
}

void test_set_port_intensity_port_byte(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, SET_PORT_INTENSITY);

    // Port index goes in byte[2]
    buffer[2] = 3;  // Port D4
    TEST_ASSERT_EQUAL_UINT8(3, buffer[2]);
}

void test_set_port_intensity_value_bytes(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, SET_PORT_INTENSITY);

    // Intensity value is 16-bit big-endian in bytes[3:4]
    uint16_t intensity = 32768;  // 50%
    buffer[3] = (intensity >> 8) & 0xFF;  // High byte
    buffer[4] = intensity & 0xFF;         // Low byte

    // Verify we can reconstruct the value
    uint16_t reconstructed = (buffer[3] << 8) | buffer[4];
    TEST_ASSERT_EQUAL_UINT16(32768, reconstructed);
}

/***************************************************************************************************/
/******************************** TURN_ON_PORT Layout **********************************************/
/***************************************************************************************************/
// Byte layout: [cmd_id, 35, port, 0, 0, 0, 0, crc]

void test_turn_on_port_command_code(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, TURN_ON_PORT);
    TEST_ASSERT_EQUAL_UINT8(TURN_ON_PORT, buffer[1]);
    TEST_ASSERT_EQUAL_UINT8(35, buffer[1]);
}

void test_turn_on_port_port_byte(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, TURN_ON_PORT);

    buffer[2] = 0;  // Port D1
    TEST_ASSERT_EQUAL_UINT8(0, buffer[2]);

    buffer[2] = 4;  // Port D5
    TEST_ASSERT_EQUAL_UINT8(4, buffer[2]);
}

/***************************************************************************************************/
/******************************** TURN_OFF_PORT Layout *********************************************/
/***************************************************************************************************/
// Byte layout: [cmd_id, 36, port, 0, 0, 0, 0, crc]

void test_turn_off_port_command_code(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, TURN_OFF_PORT);
    TEST_ASSERT_EQUAL_UINT8(TURN_OFF_PORT, buffer[1]);
    TEST_ASSERT_EQUAL_UINT8(36, buffer[1]);
}

/***************************************************************************************************/
/******************************** SET_PORT_ILLUMINATION Layout *************************************/
/***************************************************************************************************/
// Byte layout: [cmd_id, 37, port, intensity_hi, intensity_lo, on_flag, 0, crc]

void test_set_port_illumination_command_code(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, SET_PORT_ILLUMINATION);
    TEST_ASSERT_EQUAL_UINT8(SET_PORT_ILLUMINATION, buffer[1]);
    TEST_ASSERT_EQUAL_UINT8(37, buffer[1]);
}

void test_set_port_illumination_on_flag_byte(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, SET_PORT_ILLUMINATION);

    // on_flag is in byte[5]
    buffer[5] = 1;  // Turn on
    TEST_ASSERT_EQUAL_UINT8(1, buffer[5]);

    buffer[5] = 0;  // Turn off
    TEST_ASSERT_EQUAL_UINT8(0, buffer[5]);
}

void test_set_port_illumination_full_packet(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 42, SET_PORT_ILLUMINATION);

    buffer[2] = 2;           // Port D3
    uint16_t intensity = 65535;  // 100%
    buffer[3] = (intensity >> 8) & 0xFF;
    buffer[4] = intensity & 0xFF;
    buffer[5] = 1;           // Turn on

    TEST_ASSERT_EQUAL_UINT8(42, buffer[0]);  // cmd_id
    TEST_ASSERT_EQUAL_UINT8(37, buffer[1]);  // cmd_code
    TEST_ASSERT_EQUAL_UINT8(2, buffer[2]);   // port
    TEST_ASSERT_EQUAL_UINT8(0xFF, buffer[3]); // intensity_hi
    TEST_ASSERT_EQUAL_UINT8(0xFF, buffer[4]); // intensity_lo
    TEST_ASSERT_EQUAL_UINT8(1, buffer[5]);   // on_flag
}

/***************************************************************************************************/
/******************************** SET_MULTI_PORT_MASK Layout ***************************************/
/***************************************************************************************************/
// Byte layout: [cmd_id, 38, mask_hi, mask_lo, on_hi, on_lo, 0, crc]

void test_set_multi_port_mask_command_code(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, SET_MULTI_PORT_MASK);
    TEST_ASSERT_EQUAL_UINT8(SET_MULTI_PORT_MASK, buffer[1]);
    TEST_ASSERT_EQUAL_UINT8(38, buffer[1]);
}

void test_set_multi_port_mask_16bit_masks(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, SET_MULTI_PORT_MASK);

    // port_mask = 0x001F (D1-D5)
    uint16_t port_mask = 0x001F;
    buffer[2] = (port_mask >> 8) & 0xFF;  // mask_hi
    buffer[3] = port_mask & 0xFF;         // mask_lo

    // on_mask = 0x0015 (D1, D3, D5 on; D2, D4 off)
    uint16_t on_mask = 0x0015;
    buffer[4] = (on_mask >> 8) & 0xFF;    // on_hi
    buffer[5] = on_mask & 0xFF;           // on_lo

    // Verify reconstruction
    uint16_t reconstructed_port = (buffer[2] << 8) | buffer[3];
    uint16_t reconstructed_on = (buffer[4] << 8) | buffer[5];

    TEST_ASSERT_EQUAL_HEX16(0x001F, reconstructed_port);
    TEST_ASSERT_EQUAL_HEX16(0x0015, reconstructed_on);
}

void test_set_multi_port_mask_high_ports(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, SET_MULTI_PORT_MASK);

    // Test with ports 8-15 (requires high byte)
    uint16_t port_mask = 0xFF00;  // Ports 8-15
    buffer[2] = (port_mask >> 8) & 0xFF;
    buffer[3] = port_mask & 0xFF;

    uint16_t reconstructed = (buffer[2] << 8) | buffer[3];
    TEST_ASSERT_EQUAL_HEX16(0xFF00, reconstructed);
    TEST_ASSERT_EQUAL_UINT8(0xFF, buffer[2]);  // High byte should be 0xFF
    TEST_ASSERT_EQUAL_UINT8(0x00, buffer[3]);  // Low byte should be 0x00
}

/***************************************************************************************************/
/******************************** TURN_OFF_ALL_PORTS Layout ****************************************/
/***************************************************************************************************/
// Byte layout: [cmd_id, 39, 0, 0, 0, 0, 0, crc]

void test_turn_off_all_ports_command_code(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, TURN_OFF_ALL_PORTS);
    TEST_ASSERT_EQUAL_UINT8(TURN_OFF_ALL_PORTS, buffer[1]);
    TEST_ASSERT_EQUAL_UINT8(39, buffer[1]);
}

void test_turn_off_all_ports_no_params(void) {
    uint8_t buffer[CMD_LENGTH];
    create_command(buffer, 1, TURN_OFF_ALL_PORTS);

    // Bytes 2-6 should all be 0 (no parameters)
    TEST_ASSERT_EQUAL_UINT8(0, buffer[2]);
    TEST_ASSERT_EQUAL_UINT8(0, buffer[3]);
    TEST_ASSERT_EQUAL_UINT8(0, buffer[4]);
    TEST_ASSERT_EQUAL_UINT8(0, buffer[5]);
    TEST_ASSERT_EQUAL_UINT8(0, buffer[6]);
}

/***************************************************************************************************/
/******************************** Response Byte Layout *********************************************/
/***************************************************************************************************/
// Response packet format (24 bytes):
//   byte[0]: command ID
//   byte[1]: execution status
//   byte[2-5]: X position
//   byte[6-9]: Y position
//   byte[10-13]: Z position
//   byte[14-17]: Theta position
//   byte[18]: buttons and switches
//   byte[19-21]: reserved
//   byte[22]: firmware version (nibble-encoded)
//   byte[23]: CRC-8

void test_response_layout_constants(void) {
    TEST_ASSERT_EQUAL_INT(24, MSG_LENGTH);
    TEST_ASSERT_TRUE(MSG_LENGTH > CMD_LENGTH);
}

void test_response_version_byte_position(void) {
    // Firmware version is at byte 22
    uint8_t response[MSG_LENGTH];
    memset(response, 0, MSG_LENGTH);

    // Set version 1.0 (0x10)
    response[22] = 0x10;

    // Verify position
    TEST_ASSERT_EQUAL_UINT8(0x10, response[22]);

    // Verify decoding
    uint8_t major = (response[22] >> 4) & 0x0F;
    uint8_t minor = response[22] & 0x0F;
    TEST_ASSERT_EQUAL_UINT8(1, major);
    TEST_ASSERT_EQUAL_UINT8(0, minor);
}

void test_response_execution_status_byte(void) {
    uint8_t response[MSG_LENGTH];
    memset(response, 0, MSG_LENGTH);

    // Execution status is at byte 1
    response[1] = COMPLETED_WITHOUT_ERRORS;
    TEST_ASSERT_EQUAL_UINT8(0, response[1]);

    response[1] = IN_PROGRESS;
    TEST_ASSERT_EQUAL_UINT8(1, response[1]);

    response[1] = CMD_CHECKSUM_ERROR;
    TEST_ASSERT_EQUAL_UINT8(2, response[1]);
}

/***************************************************************************************************/
/**************************** Driver fail-safe guards (source scan) ********************************/
/***************************************************************************************************/
/*
  WHY A TEST THAT READS SOURCE AS TEXT.

  An axis whose driver the probe could not identify must reject motion. That is
  enforced by guards in src/commands/stage_commands.cpp (host move commands) and
  src/operations.cpp (joystick and focus wheel), and NEITHER FILE CAN BE
  COMPILED BY env:native — both reach Arduino, FastLED, PacketSerial and the
  Teensy pin map through globals.h / functions.h. test_driver_sequence pins the
  PREDICATE (tmc_driver_ready against every row of the probe's decision table),
  but before this case nothing at all pinned the CALL SITES: deleting any guard
  line left the whole suite green and shipped an axis that moves at unknown
  current.

  So this scans the two files as text. That is a blunt instrument and it is
  worth being honest about its limits:

    - IT IS BRITTLE TO RENAMES. Rename a callback or the helper and this fails
      even though the code is correct. That is the intended trade: a failure
      here is a prompt to update the expected counts deliberately, which is
      exactly the moment to re-check that every entry point is still guarded.
    - It counts raw text, comments included. The comments in both files are
      deliberately written without a "(" after the helper names so they do not
      inflate the counts; keep it that way when editing them.
    - It proves a guard is PRESENT and TEXTUALLY BEFORE the motion call in the
      same function. It cannot prove the guard is reachable, correct, or that
      the right axis was passed. Those are covered by review, not by this.

  A real firmware-level test of the call sites needs the whole command layer
  host-compilable, which is a much larger piece of work than this task.
*/

static char g_source[192 * 1024];

/* The native test binary's working directory is not contractual, so try the
   plausible roots and fail loudly if none of them holds the file — a silently
   skipped scan would be worse than no scan at all. */
static const char *load_source(const char *relative_path)
{
    static const char *const PREFIXES[] = {"", "../", "../../", "../../../", "../../../../"};

    for (unsigned i = 0; i < sizeof PREFIXES / sizeof PREFIXES[0]; i++)
    {
        char path[512];
        snprintf(path, sizeof path, "%s%s", PREFIXES[i], relative_path);

        FILE *f = fopen(path, "rb");
        if (!f)
            continue;

        size_t n = fread(g_source, 1, sizeof g_source - 1, f);
        int truncated = (n == sizeof g_source - 1);
        fclose(f);

        g_source[n] = '\0';

        /* A truncated read would silently undercount the guards. */
        TEST_ASSERT_FALSE_MESSAGE(truncated, "g_source is too small for the file being scanned");
        return g_source;
    }
    return NULL;
}

static unsigned count_occurrences(const char *haystack, const char *needle)
{
    unsigned n = 0;
    size_t len = strlen(needle);
    for (const char *p = strstr(haystack, needle); p != NULL; p = strstr(p + len, needle))
        n++;
    return n;
}

/* The guard must appear after the function's signature and before the first
   call in it that puts a motor in motion. */
static void assert_guard_precedes_motion(const char *source, const char *file_label,
                                         const char *signature, const char *guard,
                                         const char *motion_call)
{
    static char msg[512];

    const char *fn = strstr(source, signature);
    snprintf(msg, sizeof msg, "%s: could not find `%s` — renamed?", file_label, signature);
    TEST_ASSERT_NOT_NULL_MESSAGE(fn, msg);

    const char *g = strstr(fn, guard);
    const char *m = strstr(fn, motion_call);

    snprintf(msg, sizeof msg, "%s: `%s` commands motion with no %s guard — a "
                              "DRIVER_UNKNOWN axis would move at unknown current",
             file_label, signature, guard);
    TEST_ASSERT_NOT_NULL_MESSAGE(g, msg);

    snprintf(msg, sizeof msg, "%s: `%s` no longer contains `%s` — this scan is "
                              "pinning the wrong call",
             file_label, signature, motion_call);
    TEST_ASSERT_NOT_NULL_MESSAGE(m, msg);

    snprintf(msg, sizeof msg, "%s: in `%s` the %s guard comes AFTER `%s`; state is "
                              "written, or the motor commanded, before the check",
             file_label, signature, guard, motion_call);
    TEST_ASSERT_TRUE_MESSAGE(g < m, msg);
}

void test_stage_commands_guards_every_move_entry_point(void)
{
    const char *src = load_source("src/commands/stage_commands.cpp");
    TEST_ASSERT_NOT_NULL_MESSAGE(src, "could not open src/commands/stage_commands.cpp from any "
                                      "candidate working directory");

    /* 1 definition + 10 uses. The uses are: move_x, move_y, move_z,
       dispatch_filterwheel_move, move_to_x, move_to_y, move_to_z, and three in
       callback_home_or_zero (x and y for AXES_XY, plus the single-axis case). */
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(11, count_occurrences(src, "axis_driver_ready("),
        "stage_commands.cpp must hold exactly 11 axis_driver_ready references: "
        "1 definition and 10 call sites. A lower count means a move entry point "
        "lost its fail-safe");

    /* The helper reaches the predicate exactly once, in its own body. */
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(1, count_occurrences(src, "tmc_driver_ready("),
        "axis_driver_ready() must be the single place stage_commands.cpp consults "
        "the predicate");

    /* The guard must not clear mcu_cmd_execution_in_progress: it rejects before
       the callback claims that flag, and clearing it reports an unrelated axis
       still in motion as finished. */
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(1, count_occurrences(src, "report_move_error();\n        return false;"),
        "axis_driver_ready() must reject with report_move_error(), never "
        "mark_move_failed() — see the task 8 report section 3");

    assert_guard_precedes_motion(src, "stage_commands.cpp", "void callback_move_x()",
                                 "axis_driver_ready(", "tmc4361A_moveTo(");
    assert_guard_precedes_motion(src, "stage_commands.cpp", "void callback_move_y()",
                                 "axis_driver_ready(", "tmc4361A_moveTo(");
    assert_guard_precedes_motion(src, "stage_commands.cpp", "void callback_move_z()",
                                 "axis_driver_ready(", "tmc4361A_moveTo(");
    assert_guard_precedes_motion(src, "stage_commands.cpp", "void callback_move_to_x()",
                                 "axis_driver_ready(", "tmc4361A_moveTo(");
    assert_guard_precedes_motion(src, "stage_commands.cpp", "void callback_move_to_y()",
                                 "axis_driver_ready(", "tmc4361A_moveTo(");
    assert_guard_precedes_motion(src, "stage_commands.cpp", "void callback_move_to_z()",
                                 "axis_driver_ready(", "tmc4361A_moveTo(");
    /* Covers callback_move_w, _w2, _move_to_w and _move_to_w2, which are tail
       calls into this dispatcher and hold no motion call of their own. */
    assert_guard_precedes_motion(src, "stage_commands.cpp", "static void dispatch_filterwheel_move(",
                                 "axis_driver_ready(", "tmc4361A_moveTo(");
    /* Homing, not zeroing: setCurrentPosition is a halt and re-origin, so the
       zeroing branch is deliberately ungated and holds no setSpeed. */
    assert_guard_precedes_motion(src, "stage_commands.cpp", "void callback_home_or_zero()",
                                 "axis_driver_ready(", "tmc4361A_setSpeed(");
}

void test_operations_guards_the_operator_driven_motion_paths(void)
{
    const char *src = load_source("src/operations.cpp");
    TEST_ASSERT_NOT_NULL_MESSAGE(src, "could not open src/operations.cpp from any candidate "
                                      "working directory");

    /* X joystick block, Y joystick block, focus wheel. */
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(3, count_occurrences(src, "tmc_driver_ready("),
        "operations.cpp must gate all three operator-driven motion paths: the X "
        "and Y joystick blocks and do_focus_control(). Rejecting host moves holds "
        "the joystick gate permanently open, so a missing one here is worse than "
        "it looks");

    /* Silent by design — these are not host commands and must not attribute a
       hardware fault to whatever the host last sent. */
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, count_occurrences(src, "mcu_cmd_execution_status ="),
        "the operations.cpp gates must reject silently: assigning "
        "mcu_cmd_execution_status here mislabels an unrelated command as failed. "
        "(The needle carries the ` =` so the prose above the gates, which names "
        "the variable, does not trip this.)");

    assert_guard_precedes_motion(src, "operations.cpp", "void check_joystick()",
                                 "tmc_driver_ready(", "tmc4361A_setSpeed(");
    assert_guard_precedes_motion(src, "operations.cpp", "void do_focus_control()",
                                 "tmc_driver_ready(", "tmc4361A_moveTo(");
}

int main(int argc, char **argv) {
    UNITY_BEGIN();

    // SET_PORT_INTENSITY tests
    RUN_TEST(test_set_port_intensity_command_code);
    RUN_TEST(test_set_port_intensity_port_byte);
    RUN_TEST(test_set_port_intensity_value_bytes);

    // TURN_ON_PORT tests
    RUN_TEST(test_turn_on_port_command_code);
    RUN_TEST(test_turn_on_port_port_byte);

    // TURN_OFF_PORT tests
    RUN_TEST(test_turn_off_port_command_code);

    // SET_PORT_ILLUMINATION tests
    RUN_TEST(test_set_port_illumination_command_code);
    RUN_TEST(test_set_port_illumination_on_flag_byte);
    RUN_TEST(test_set_port_illumination_full_packet);

    // SET_MULTI_PORT_MASK tests
    RUN_TEST(test_set_multi_port_mask_command_code);
    RUN_TEST(test_set_multi_port_mask_16bit_masks);
    RUN_TEST(test_set_multi_port_mask_high_ports);

    // TURN_OFF_ALL_PORTS tests
    RUN_TEST(test_turn_off_all_ports_command_code);
    RUN_TEST(test_turn_off_all_ports_no_params);

    // Response layout tests
    RUN_TEST(test_response_layout_constants);
    RUN_TEST(test_response_version_byte_position);
    RUN_TEST(test_response_execution_status_byte);

    // Driver fail-safe guards (source scan)
    RUN_TEST(test_stage_commands_guards_every_move_entry_point);
    RUN_TEST(test_operations_guards_the_operator_driven_motion_paths);

    return UNITY_END();
}
