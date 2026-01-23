#include <unity.h>
#include <stdint.h>
#include <set>

// Include protocol constants directly - no Arduino dependencies
#include "constants_protocol.h"

void setUp(void) {}
void tearDown(void) {}

void test_command_ids_are_unique(void) {
    std::set<int> ids;
    int commands[] = {
        MOVE_X, MOVE_Y, MOVE_Z, MOVE_THETA, MOVE_W,
        HOME_OR_ZERO, MOVETO_X, MOVETO_Y, MOVETO_Z,
        SET_LIM, TURN_ON_ILLUMINATION, TURN_OFF_ILLUMINATION,
        SET_ILLUMINATION, SET_ILLUMINATION_LED_MATRIX,
        ACK_JOYSTICK_BUTTON_PRESSED, ANALOG_WRITE_ONBOARD_DAC,
        SET_DAC80508_REFDIV_GAIN, SET_ILLUMINATION_INTENSITY_FACTOR,
        MOVETO_W, SET_LIM_SWITCH_POLARITY, CONFIGURE_STEPPER_DRIVER,
        SET_MAX_VELOCITY_ACCELERATION, SET_LEAD_SCREW_PITCH,
        SET_OFFSET_VELOCITY, CONFIGURE_STAGE_PID, ENABLE_STAGE_PID,
        DISABLE_STAGE_PID, SET_HOME_SAFETY_MERGIN, SET_PID_ARGUMENTS,
        SEND_HARDWARE_TRIGGER, SET_STROBE_DELAY, SET_AXIS_DISABLE_ENABLE,
        SET_PIN_LEVEL, INITFILTERWHEEL, INITIALIZE, RESET
    };

    int num_commands = sizeof(commands) / sizeof(commands[0]);
    for (int i = 0; i < num_commands; i++) {
        // Check that this ID hasn't been seen before
        TEST_ASSERT_TRUE_MESSAGE(
            ids.find(commands[i]) == ids.end(),
            "Duplicate command ID found"
        );
        ids.insert(commands[i]);
    }
}

void test_command_ids_fit_in_byte(void) {
    int commands[] = {
        MOVE_X, MOVE_Y, MOVE_Z, MOVE_THETA, MOVE_W,
        HOME_OR_ZERO, MOVETO_X, MOVETO_Y, MOVETO_Z,
        SET_LIM, TURN_ON_ILLUMINATION, TURN_OFF_ILLUMINATION,
        SET_ILLUMINATION, SET_ILLUMINATION_LED_MATRIX,
        ACK_JOYSTICK_BUTTON_PRESSED, ANALOG_WRITE_ONBOARD_DAC,
        SET_DAC80508_REFDIV_GAIN, SET_ILLUMINATION_INTENSITY_FACTOR,
        MOVETO_W, SET_LIM_SWITCH_POLARITY, CONFIGURE_STEPPER_DRIVER,
        SET_MAX_VELOCITY_ACCELERATION, SET_LEAD_SCREW_PITCH,
        SET_OFFSET_VELOCITY, CONFIGURE_STAGE_PID, ENABLE_STAGE_PID,
        DISABLE_STAGE_PID, SET_HOME_SAFETY_MERGIN, SET_PID_ARGUMENTS,
        SEND_HARDWARE_TRIGGER, SET_STROBE_DELAY, SET_AXIS_DISABLE_ENABLE,
        SET_PIN_LEVEL, INITFILTERWHEEL, INITIALIZE, RESET
    };

    int num_commands = sizeof(commands) / sizeof(commands[0]);
    for (int i = 0; i < num_commands; i++) {
        TEST_ASSERT_TRUE_MESSAGE(
            commands[i] >= 0 && commands[i] <= 255,
            "Command ID must fit in a byte (0-255)"
        );
    }
}

void test_message_lengths(void) {
    TEST_ASSERT_EQUAL_INT(8, CMD_LENGTH);
    TEST_ASSERT_EQUAL_INT(24, MSG_LENGTH);
    TEST_ASSERT_TRUE(MSG_LENGTH > CMD_LENGTH);
}

void test_axis_ids_are_sequential(void) {
    TEST_ASSERT_EQUAL_INT(0, AXIS_X);
    TEST_ASSERT_EQUAL_INT(1, AXIS_Y);
    TEST_ASSERT_EQUAL_INT(2, AXIS_Z);
    TEST_ASSERT_EQUAL_INT(3, AXIS_THETA);
    TEST_ASSERT_EQUAL_INT(4, AXES_XY);
    TEST_ASSERT_EQUAL_INT(5, AXIS_W);
}

int main(int argc, char **argv) {
    UNITY_BEGIN();

    RUN_TEST(test_command_ids_are_unique);
    RUN_TEST(test_command_ids_fit_in_byte);
    RUN_TEST(test_message_lengths);
    RUN_TEST(test_axis_ids_are_sequential);

    return UNITY_END();
}
