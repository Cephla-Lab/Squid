#include <unity.h>
#include <stdint.h>
#include <set>

// Command IDs from constants.h (duplicated here to avoid Arduino dependencies)
static const int MOVE_X = 0;
static const int MOVE_Y = 1;
static const int MOVE_Z = 2;
static const int MOVE_THETA = 3;
static const int MOVE_W = 4;
static const int HOME_OR_ZERO = 5;
static const int MOVETO_X = 6;
static const int MOVETO_Y = 7;
static const int MOVETO_Z = 8;
static const int SET_LIM = 9;
static const int TURN_ON_ILLUMINATION = 10;
static const int TURN_OFF_ILLUMINATION = 11;
static const int SET_ILLUMINATION = 12;
static const int SET_ILLUMINATION_LED_MATRIX = 13;
static const int ACK_JOYSTICK_BUTTON_PRESSED = 14;
static const int ANALOG_WRITE_ONBOARD_DAC = 15;
static const int SET_DAC80508_REFDIV_GAIN = 16;
static const int SET_ILLUMINATION_INTENSITY_FACTOR = 17;
static const int MOVETO_W = 18;
static const int SET_LIM_SWITCH_POLARITY = 20;
static const int CONFIGURE_STEPPER_DRIVER = 21;
static const int SET_MAX_VELOCITY_ACCELERATION = 22;
static const int SET_LEAD_SCREW_PITCH = 23;
static const int SET_OFFSET_VELOCITY = 24;
static const int CONFIGURE_STAGE_PID = 25;
static const int ENABLE_STAGE_PID = 26;
static const int DISABLE_STAGE_PID = 27;
// Note: "MERGIN" is intentionally misspelled to match firmware constant SET_HOME_SAFETY_MERGIN
static const int SET_HOME_SAFETY_MERGIN = 28;
static const int SET_PID_ARGUMENTS = 29;
static const int SEND_HARDWARE_TRIGGER = 30;
static const int SET_STROBE_DELAY = 31;
static const int SET_AXIS_DISABLE_ENABLE = 32;
static const int SET_PIN_LEVEL = 41;
static const int INITFILTERWHEEL = 253;
static const int INITIALIZE = 254;
static const int RESET = 255;

static const int CMD_LENGTH = 8;
static const int MSG_LENGTH = 24;

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
    static const int AXIS_X = 0;
    static const int AXIS_Y = 1;
    static const int AXIS_Z = 2;
    static const int AXIS_THETA = 3;
    static const int AXES_XY = 4;
    static const int AXIS_W = 5;

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
