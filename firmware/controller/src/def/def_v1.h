#ifndef DEF_OCTOPI_80120_H
#define DEF_OCTOPI_80120_H

#include "../global_defs.h"

#include <Arduino.h>

// LED matrix
#define DOTSTAR_NUM_LEDS 128

// Internal axis indices for array access (tmc4361[], axes_pid_arg[], etc.).
// IMPORTANT: These are INTERNAL indices, NOT protocol constants!
// The protocol uses different values (see constants_protocol.h):
//   Protocol: AXIS_X=0, AXIS_Y=1, AXIS_Z=2, AXIS_W=5, AXIS_W2=6
//   Internal: x=1, y=0, z=2, w=3, w2=4
// Use protocol_axis_to_internal() to convert protocol values to these indices.
static const uint8_t x = 1;
static const uint8_t y = 0;
static const uint8_t z = 2;
static const uint8_t w = 3;   // First filter wheel
static const uint8_t w2 = 4;  // Second filter wheel

// TMC2660 — external sense resistor.
static const float R_sense_xy = 0.22;
static const float R_sense_z = 0.43;
static const float R_sense_w = 0.105;  // Used by both W and W2 (identical hardware)

// TMC2240 — integrated current sense, no external resistor. CURRENT_RANGE
// selects the sine PEAK full-scale current: 0 = 0.98 A, 1 = 2.0 A, 2/3 = 3.0 A
// (KIFS/R_ref with R_ref = 12 kOhm, confirmed on the Squid 2240 board).
//
// Ranges are chosen so IRUN lands in ADI's recommended 16..31 band, at
// GLOBALSCALER = 256 and with the corrected (sqrt(2)-bearing) formula in
// drivers/driver_math.h:
//   X/Y 1000 mA rms -> range 1 -> IRUN 21 -> 972 mA delivered
//   Z    500 mA rms -> range 0 -> IRUN 22 -> 498 mA delivered
//   W   1900 mA rms -> range 2 -> IRUN 27 -> 1856 mA delivered, i.e. 87% of the
//                                 part's 2121 mA rms ceiling — the axis with the
//                                 least headroom, worth a thermal look on the bench.
//
// ONLY 0, 1 and 2 are legal, which is what the static_assert below enforces.
// Both consumers mask this value with & 0x03 — tmc2240_ifs_peak_a() when it
// picks I_FS (driver_math.h) and tmc2240_drv_conf_value() when it builds
// DRV_CONF (tmc2240_regs.h) — so a 4 would not fail loudly, it would become
// range 0 in both places and quietly deliver about a third of the intended
// current on an axis that reports success. Nothing on the wire can reach these
// (cmd 21 carries mA and microsteps, not the range), so a compile-time check is
// the whole of the validation needed.
static const uint8_t CURRENT_RANGE_XY = 1;
static const uint8_t CURRENT_RANGE_Z  = 0;
static const uint8_t CURRENT_RANGE_W  = 2;  // Used by both W and W2 (identical hardware)

static_assert(CURRENT_RANGE_XY <= 2 && CURRENT_RANGE_Z <= 2 && CURRENT_RANGE_W <= 2,
              "CURRENT_RANGE must be 0, 1 or 2: tmc2240_ifs_peak_a() and "
              "tmc2240_drv_conf_value() both mask with & 0x03, so 4 silently "
              "becomes range 0 (~1/3 of the intended current)");

// limit switch
static const bool flip_limit_switch_x = true;
static const bool flip_limit_switch_y = true;

// Motorized stage
static const int FULLSTEPS_PER_REV_X = 200;
static const int FULLSTEPS_PER_REV_Y = 200;
static const int FULLSTEPS_PER_REV_Z = 200;
static const int FULLSTEPS_PER_REV_W = 200;   // Used by both W and W2
static const int FULLSTEPS_PER_REV_W2 = 200;  // Kept for documentation (W2 uses W settings)
static const int FULLSTEPS_PER_REV_THETA = 200;

static const float HOMING_VELOCITY_X = 0.8;
static const float HOMING_VELOCITY_Y = 0.8;
static const float HOMING_VELOCITY_Z = 0.5;
static const float HOMING_VELOCITY_W = 0.15 * SCREW_PITCH_W_MM;   // Used by both W and W2
static const float HOMING_VELOCITY_W2 = 0.15 * SCREW_PITCH_W_MM;  // Kept for documentation (W2 uses W settings)

static const long X_NEG_LIMIT_MM = -130;
static const long X_POS_LIMIT_MM = 130;
static const long Y_NEG_LIMIT_MM = -130;
static const long Y_POS_LIMIT_MM = 130;
static const long Z_NEG_LIMIT_MM = -20;
static const long Z_POS_LIMIT_MM = 20;

#endif // DEF_OCTOPI_80120_H
