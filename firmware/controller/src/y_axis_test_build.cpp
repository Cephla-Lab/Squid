#include "y_axis_test_build.h"

#ifdef Y_AXIS_TEST_BUILD

#include "globals.h"

#pragma message("TEMPORARY Y-AXIS TEST IMAGE: Y limit switches ignored, Y home does not move, Y current capped (y_axis_test_build.h)")

bool y_axis_test_take_home_command()
{
    bool is_home = buffer_rx[3] == HOME_NEGATIVE || buffer_rx[3] == HOME_POSITIVE;
    if (!is_home || (buffer_rx[2] != AXIS_Y && buffer_rx[2] != AXES_XY))
        return false;

    // What the zeroing branch of callback_home_or_zero does for Y: halt (setCurrentPosition writes
    // VMAX = 0), make this position 0, and the axis is not homed.
    tmc4361A_setCurrentPosition(&tmc4361[y], 0);
    Y_pos = 0;
    hv_invalidate_axis(&homing_validity, AXIS_Y);

    if (buffer_rx[2] == AXIS_Y)
        return true;   // finished, like a zero: mcu_cmd_execution_in_progress is not claimed

    // XY home: X still homes on its switch. Byte 3 is X's direction in both commands, so the same
    // bytes read as a home of X. Nothing reads buffer_rx again after the callback returns.
    buffer_rx[2] = AXIS_X;
    return false;
}

#endif /* Y_AXIS_TEST_BUILD */
