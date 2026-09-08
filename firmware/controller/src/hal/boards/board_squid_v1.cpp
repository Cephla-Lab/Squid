/**
 * Squid v1 board profile (GET_INFO descriptor).
 * Linked into teensy41 (default) and native test_board.
 */

#include "hal/board.h"

namespace hal {

const protocol::InfoPayload& board_descriptor() {
    // Positional aggregate init (C++11); field order per protocol::InfoPayload.
    static const protocol::InfoPayload d = {
        1,   // board_id (Squid v1)
        0,   // board_rev
        1,   // mcu_id (Teensy 4.1 / RT1062)
        5,   // n_axes: X, Y, Z, FILTER1, FILTER2
        // axis_driver[8]: probed at runtime (Phase M) -> 0 for now.
        {DRIVER_NONE, DRIVER_NONE, DRIVER_NONE, DRIVER_NONE, DRIVER_NONE, 0, 0, 0},
        8,                    // n_dacs (DAC80508)
        5,                    // n_illum_ttl
        1,                    // has_led_matrix
        kNumCamTriggersV1,    // n_cam_triggers (4, pins 29-32)
        kNumReadyInputsV1,    // n_ready_inputs (1 shared ready line)
        16,                   // max_program_channels
        0,                    // feature_bits (TBD)
    };
    return d;
}

}  // namespace hal
