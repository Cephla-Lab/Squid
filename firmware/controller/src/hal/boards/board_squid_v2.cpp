/**
 * Squid v2 board profile (GET_INFO descriptor) — STUB, values provisional.
 * Linked into teensy41_boardv2 (-DBOARD_SQUID_V2) and native test_board_v2.
 * Refined in Phase C/D against the real v2 hardware.
 */

#include "hal/board.h"

namespace hal {

const protocol::InfoPayload& board_descriptor() {
    static const protocol::InfoPayload d = {
        2,   // board_id (Squid v2)
        0,   // board_rev
        1,   // mcu_id (Teensy 4.1 / RT1062)
        5,   // n_axes (provisional)
        // v2 is known TMC2240 on the populated axes (not runtime-probed).
        {DRIVER_TMC2240, DRIVER_TMC2240, DRIVER_TMC2240, DRIVER_TMC2240, DRIVER_TMC2240, 0, 0, 0},
        8,                    // n_dacs (provisional)
        5,                    // n_illum_ttl (provisional)
        1,                    // has_led_matrix
        kNumCamTriggersV2,    // n_cam_triggers (8)
        kNumReadyInputsV2,    // n_ready_inputs (2 direct + 8 expander)
        16,                   // max_program_channels
        0,                    // feature_bits (TBD)
    };
    return d;
}

}  // namespace hal
