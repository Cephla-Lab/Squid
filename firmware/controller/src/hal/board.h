/**
 * Compile-time board profiles and the GET_INFO descriptor.
 *
 * Exactly one board_*.cpp is linked per build (selected by the platformio src
 * filter and -DBOARD_SQUID_V2); each defines board_descriptor() with its own
 * InfoPayload. Board-scoped pin/wiring constants live here for Phase C to
 * consume when it configures GPIO.
 *
 * Pure declarations + POD constants — no Arduino, no heap.
 */

#ifndef HAL_BOARD_H
#define HAL_BOARD_H

#include <stdint.h>

#include "protocol/frames.h"

namespace hal {

// Values for InfoPayload.axis_driver (mirror the frames.h field comment).
enum AxisDriver : uint8_t {
    DRIVER_NONE = 0,
    DRIVER_TMC2660 = 1,
    DRIVER_TMC2240 = 2,
};

// --- Squid v1 board -------------------------------------------------------
// 4 camera triggers on Teensy pins 29-32; one shared ready line.
static const uint8_t kCamTriggerPinsV1[] = {29, 30, 31, 32};
static const uint8_t kNumCamTriggersV1 = 4;
static const uint8_t kNumReadyInputsV1 = 1;
static const uint8_t kBoardReadyLinePinV1 = 0;  // TBD — Hongquan to assign (last open hw item)

// --- Squid v2 board (STUB — values provisional, refined in Phase C/D) -----
static const uint8_t kNumCamTriggersV2 = 8;
// 2 direct + 8 MCP23S17-expander ready inputs. Only up to kMaxCameras (8)
// camera-ready lines map to the u8 cam_ready_mask; n_ready_inputs is the total
// count of physical ready inputs and may exceed 8.
static const uint8_t kNumReadyInputsV2 = 10;

// The board descriptor returned by GET_INFO.
const protocol::InfoPayload& board_descriptor();

}  // namespace hal

#endif  // HAL_BOARD_H
