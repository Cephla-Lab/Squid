#ifndef CONFIG_H
#define CONFIG_H

#include "axis.h"
#include "def_octopi_80120.h"

namespace Commands {
    const int MOVE_X = 0;
    const int MOVE_Y = 1;
    const int MOVE_Z = 2;
    const int MOVE_THETA = 3;
    const int MOVE_W = 4;
    const int HOME_OR_ZERO = 5;
    const int MOVETO_X = 6;
    const int MOVETO_Y = 7;
    const int MOVETO_Z = 8;
    const int SET_LIM = 9;
    const int TURN_ON_ILLUMINATION = 10;
    const int TURN_OFF_ILLUMINATION = 11;
    const int SET_ILLUMINATION = 12;
    const int SET_ILLUMINATION_LED_MATRIX = 13;
    const int ACK_JOYSTICK_BUTTON_PRESSED = 14;
    const int ANALOG_WRITE_ONBOARD_DAC = 15;
    const int SET_DAC80508_REFDIV_GAIN = 16;
    const int SET_ILLUMINATION_INTENSITY_FACTOR = 17;
    const int MOVETO_W = 18;
    const int MOVE_W2 = 19;
    const int SET_LIM_SWITCH_POLARITY = 20;
    const int CONFIGURE_STEPPER_DRIVER = 21;
    const int SET_MAX_VELOCITY_ACCELERATION = 22;
    const int SET_LEAD_SCREW_PITCH = 23;
    const int SET_OFFSET_VELOCITY = 24;
    const int CONFIGURE_STAGE_PID = 25;
    const int ENABLE_STAGE_PID = 26;
    const int DISABLE_STAGE_PID = 27;
    const int SET_HOME_SAFETY_MERGIN = 28;
    const int SET_PID_ARGUMENTS = 29;
    const int SEND_HARDWARE_TRIGGER = 30;
    const int SET_STROBE_DELAY = 31;
    const int SET_AXIS_DISABLE_ENABLE = 32;
    const int SET_TRIGGER_MODE = 33;
    // Multi-port illumination commands (v1.0+)
    const int SET_PORT_INTENSITY = 34;
    const int TURN_ON_PORT = 35;
    const int TURN_OFF_PORT = 36;
    const int SET_PORT_ILLUMINATION = 37;
    const int SET_MULTI_PORT_MASK = 38;
    const int TURN_OFF_ALL_PORTS = 39;
    // Safety and heartbeat
    const int SET_WATCHDOG_TIMEOUT = 40;  // set the serial watchdog timeout (ms); once enabled, a communication loss automatically turns off the lights
    const int SET_PIN_LEVEL = 41;
    const int HEARTBEAT = 42;             // no-op heartbeat (the watchdog is reset by received packets, not by this command)
    // 2026-05-29 E1 objective-changer-specific motion commands (octoaxes extension; legacy Squid does not send them, so drop-in is preserved).
    // MOVE_W/MOVETO_W are hardcoded to "W" with no axis index and cannot be reused; so, like W2, give E1 its own commands.
    const int MOVE_TURRET   = 44;             // E1 relative move, data[2..5] = int32 microsteps big-endian
    const int MOVETO_TURRET = 45;             // E1 absolute move
    const int INITFILTERWHEEL_W2 = 252;
    const int INITFILTERWHEEL = 253;
    const int INITIALIZE = 254;
    const int RESET = 255;
}

// Pin definitions
namespace Pins {
    const int DAC8050x_CS = 33;
    const int POWER_GOOD = 0;
    const int TMC4361_STANDARD_CLK = 37;
    const int TMC4361_EXPAND_CLK = 28;

    // Note: the X_AXIS_CS / Y_AXIS_CS constant names are legacy names based on the PCB pin labels,
    // and do not directly correspond to the chips of the physical X/Y motors (the hardware wiring is determined by the
    // axisName <-> CS pin mapping in octoaxes.ino, see the comment at octoaxes.ino:86-90).
    const int X_AXIS_CS = 41;
    const int Y_AXIS_CS = 36;
    const int Z_AXIS_CS = 35;
    const int W_AXIS_CS = 34;

    const int EXPAND1_AXIS_CS = 19;
    const int EXPAND2_AXIS_CS = 18;
    const int EXPAND3_AXIS_CS = 17;
    const int EXPAND4_AXIS_CS = 16;

    // W2 (the second filter wheel) reuses the original EXPAND4 hardware: CS=pin 16, CLK=pin 28 (TMC4361_EXPAND_CLK).
    // fully consistent with legacy Squid pin_TMC4361_CS[4]=16 / pin_TMC4361_CLK_W2=28.
    const int W2_AXIS_CS = EXPAND4_AXIS_CS;

    // Control-pin arrays
    const uint8_t CONTROL_PINS[] = {EXPAND1_AXIS_CS, EXPAND2_AXIS_CS, EXPAND3_AXIS_CS, EXPAND4_AXIS_CS};
    const uint8_t STANDARD_CONTROL_PINS[] = {W_AXIS_CS, Z_AXIS_CS, Y_AXIS_CS, X_AXIS_CS};
    const size_t NUM_CONTROL_PINS = 4;
    const size_t NUM_STANDARD_CONTROL_PINS = 4;

    // Illumination TTL ports (D1-D5)
    // Note: the D3/D4 pins are not in sequential order, consistent with the legacy light-source codes
    const int ILLUMINATION_D1 = 5;
    const int ILLUMINATION_D2 = 4;
    const int ILLUMINATION_D3 = 22;
    const int ILLUMINATION_D4 = 3;
    const int ILLUMINATION_D5 = 23;

    // Laser safety interlock (LOW = safe)
    const int ILLUMINATION_INTERLOCK = 2;

    // LED matrix (APA102, 128 pixels)
    const int LED_MATRIX_DATA  = 26;
    const int LED_MATRIX_CLOCK = 27;

    // LED driver LT3932 SYNC (16 MHz PWM)
    const int LED_DRIVER_SYNC = 25;

    // Camera trigger
    const int CAMERA_TRIGGER_1 = 29;
    const int CAMERA_TRIGGER_2 = 30;
    const int CAMERA_TRIGGER_3 = 31;
    const int CAMERA_TRIGGER_4 = 32;
}

// System configuration
namespace SystemConfig {
    const uint32_t TMC4361_CLOCK_FREQUENCY = 16000000;
    const unsigned long LIMIT_CHECK_INTERVAL = 3000;
}

// Axis constant definitions
namespace AxisConstDefinition {
		const float R_sense_xy = 0.22;
		const float R_sense_z = 0.43;
		const float R_sense_objective = 0.22;
		const float R_sense_filter = 0.1;

		const int FULLSTEPS_PER_REV_X = 200;
		const int FULLSTEPS_PER_REV_Y = 200;
		const int FULLSTEPS_PER_REV_Z = 200;
		const int FULLSTEPS_PER_REV_FILTER = 200;
		const int FULLSTEPS_PER_REV_OBJECTIVES = 200;
		const int FULLSTEPS_PER_REV_THETA = 200;

		const float SCREW_PITCH_X_MM = 2.54;
		const float SCREW_PITCH_Y_MM = 2.54;
		const float SCREW_PITCH_Z_MM = 0.3;   // conservative default for the old Z. The new Z (LE143S-W0601, 1mm pitch) is overridden by SET_LEAD_SCREW_PITCH sent at GUI startup (see software Z_AXIS_VARIANT)
		const float SCREW_PITCH_FILTERWHEEL_MM = 1;   // 2026-05-21 matches legacy Squid SCREW_PITCH_W_MM=1 (chip-side microstep semantics consistent with the GUI algorithm)
		const float SCREW_PITCH_OBJECTIVES_MM = 1;

		const int MICROSTEPPING_X = 256;
		const int MICROSTEPPING_Y = 256;
		const int MICROSTEPPING_Z = 256;
		const int MICROSTEPPING_FILTERWHEEL = 8;      // 2026-05-26 path C speed optimization v2: 16->8 (BOW truncation further eased from 7x to 3.6x, matching the historically best microstep=8 config from 2026-02, physical floor ~70ms per slot)
		const int MICROSTEPPING_OBJECTIVES = 64;

		// encoder resolution (um/pulse)
		const float ENCODER_RESOLUTION_UM_X = 0.05;
		const float ENCODER_RESOLUTION_UM_Y = 0.05;
		const float ENCODER_RESOLUTION_UM_Z = 0.1;

		// Homing microstepping (default 256)
		const int HOMING_MICROSTEPPING_X = 256;
		const int HOMING_MICROSTEPPING_Y = 256;
		const int HOMING_MICROSTEPPING_Z = 256;
		const int HOMING_MICROSTEPPING_FILTERWHEEL = 256;
		const int HOMING_MICROSTEPPING_OBJECTIVES = 256;

		// 2026-05-11 first speed-optimization round: matches legacy Squid HCS v2 config
		// legacy Squid configuration_HCS_v2.ini: max_velocity_x/y/z_mm = 30/30/3.8
		// AMAX_Z 100 measured to actually increase Z 1mm time from 697->1569ms (+125%), suspected to be
		// motor_adjustBows auto-computing too large a BOW + insufficient motor torque causing an abnormal ramp.
		// keep the vmax increase, roll Z acceleration back to the original 20 mm/s2.
		const float MAX_VELOCITY_X_mm = 30;
		const float MAX_VELOCITY_Y_mm = 30;
		const float MAX_VELOCITY_Z_mm = 3.8;
		const float MAX_VELOCITY_FILTERWHEEL_mm = 4.2 * SCREW_PITCH_FILTERWHEEL_MM;
		const float MAX_VELOCITY_OBJECTIVES_mm = 0.5 * SCREW_PITCH_OBJECTIVES_MM;

		const float MAX_ACCELERATION_X_mm = 500;
		const float MAX_ACCELERATION_Y_mm = 500;
		const float MAX_ACCELERATION_Z_mm = 20;
		const float MAX_ACCELERATION_FILTERWHEEL_mm = 400 * SCREW_PITCH_FILTERWHEEL_MM;
		// 2026-05-29 objectives branch: measured 200 mm/s2 with the weak 1A current loses steps badly,
		// lowered to 80 mm/s2 to leave margin. Used together with EXPAND1_AXIS.currentRange=1 (2A) + motorCurrentMA=1800.
		const float MAX_ACCELERATION_OBJECTIVES_mm = 80 * SCREW_PITCH_OBJECTIVES_MM;

		const float HOMING_VELOCITY_X_MM = 10;
		const float HOMING_VELOCITY_Y_MM = 30;  // 2026-05-12 measured: 256 microsteps + 30 mm/s is quietest
		const float HOMING_VELOCITY_Z_MM = 1;   // safe boot default = 1mm/s (old Z historical value, drop-in equivalent; legacy Squid has no channel to send the homing speed, so this default is all it can use). For the new Z, the octoaxes GUI sends S:SET_HOMING_VEL per variant at startup to raise it to 2mm/s (avoiding long-travel homing timeouts)
		const float HOMING_VELOCITY_FILTERWHEEL_MM = 0.15 * SCREW_PITCH_FILTERWHEEL_MM;
		const float HOMING_VELOCITY_OBJECTIVES_MM = 0.25 * SCREW_PITCH_OBJECTIVES_MM;

		// motor current setting (mA) -- peak current, not RMS
		// TMC2660 formula: I_peak = (CS+1)/32 * V_FS/R_sense, I_rms = I_peak/sqrt(2)
		// CS range 0~31, out-of-range is clamped, the actual peak is limited by R_sense
		// chip absolute max: 4A peak (2.8A RMS)
		const float X_MOTOR_PEAK_CURRENT_mA = 1000;       // R=0.22ohm -> CS=9, actual 0.97A
		const float Y_MOTOR_PEAK_CURRENT_mA = 1000;       // R=0.22ohm -> CS=9, actual 0.97A
		// 2026-06-03 newz branch: Z defaults to the conservative old value (500mA); the current of the new Z (LE143S-W0601, rated 1.5A)
		// is overridden by CONFIGURE_STEPPER_DRIVER sent at GUI startup (see software Z_AXIS_VARIANT="new" -> 1500mA).
		// This lets one firmware support both old and new Z boards: at the boot instant (before GUI config) the new motor gets only 500mA = weak but safe, avoiding overcurrent on the old motor.
		// driver auto-detect (DRIVER_AUTO): old Z=TMC2660 uses R_sense; new Z=TMC2240 uses ICS+currentRange.
		const float Z_MOTOR_PEAK_CURRENT_mA = 500;        // conservative default R=0.43ohm -> CS=21, actual 0.47A (the new Z is raised to 1500mA by the GUI)
		const float FILTERWHEEL_MOTOR_PEAK_CURRENT_mA = 3100; // R=0.10ohm -> CS=31 (max), actual 3.1A
		// 2026-05-29 objectives branch: the weak 1A current loses steps with the gear-reduced objective. Raised to 1800mA.
		// objective driver board R_sense=0.22ohm (only effective on the TMC2660 path; TMC2240 uses the integrated current sense ICS and ignores this resistor).
		// EXPAND1_AXIS.driverType=DRIVER_AUTO auto-detects the chip on power-up, then selects the path:
		// - TMC2240 (ICS):  currentRange=1 -> I_FS=2A, IRUN=(1800/1000)/2*32-1=28 -> 1.81A peak
		// - TMC2660 (R_S):  r_sense=0.22ohm, 1800mA -> CS~=16 -> ~1.7A peak
		// the two paths give similar current (~1.7-1.8A), enough torque for the gear-reduced objective. A driver board with R_sense != 0.22ohm requires recomputing CS.
		const float OBJECTIVES_MOTOR_PEAK_CURRENT_mA = 1800;

		const float X_MOTOR_I_HOLD = 0.25;
		const float Y_MOTOR_I_HOLD = 0.25;
		const float Z_MOTOR_I_HOLD = 0.5;    // conservative default (the new Z is raised to 0.75 by the GUI, to resist sag on the vertical axis)
		const float FILTERWHEEL_MOTOR_I_HOLD = 0.5;
		const float OBJECTIVES_MOTOR_I_HOLD = 0.5;

		const float X_SAFEMARGIN = 0.05;
		const float Y_SAFEMARGIN = 0.05;
		const float Z_SAFEMARGIN = 0.05;
		const float FILTERWHEEL_SAFEMARGIN = 0.2;
		const float OBJECTIVES_SAFEMARGIN = 0.004;

		const float X_SAFEPOSITION = 0.6;
		const float Y_SAFEPOSITION = 0.6;
		const float Z_SAFEPOSITION = 0.7;
		const float FILTERWHEEL_SAFEPOSITION = 0;
		const float OBJECTIVES_SAFEPOSITION = 0;
}

// Illumination-system configuration
namespace IlluminationConfig {
    // DAC80508 register addresses
    const uint8_t DAC_CONFIG_ADDR = 0x03;
    const uint8_t DAC_GAIN_ADDR   = 0x04;
    const uint8_t DAC_DAC_ADDR    = 0x08;

    // default DAC gain: div=0x00, gains=0x80 (channels 0-6 gain 1, channel 7 gain 2)
    const uint8_t DAC_DEFAULT_DIV   = 0x00;
    const uint8_t DAC_DEFAULT_GAINS = 0x80;

    // LED matrix (APA102, 128 pixels, BGR order)
    const int   NUM_LEDS          = 128;
    const int   LED_MAX_INTENSITY = 100;
    const float GREEN_ADJUSTMENT  = 1.0f;
    const float RED_ADJUSTMENT    = 1.0f;
    const float BLUE_ADJUSTMENT   = 1.0f;

    // default global intensity factor (Squid LED 0-1.5V)
    const float DEFAULT_INTENSITY_FACTOR = 0.6f;

    // number of ports (D1-D16)
    const int NUM_PORTS = 16;

    // illumination light-source codes (legacy API, kept consistent with the protocol)
    // LED matrix patterns: 0-8
    const int LED_ARRAY_FULL       = 0;
    const int LED_ARRAY_LEFT_HALF  = 1;
    const int LED_ARRAY_RIGHT_HALF = 2;
    const int LED_ARRAY_LEFTB_RIGHTR = 3;
    const int LED_ARRAY_LOW_NA     = 4;
    const int LED_ARRAY_LEFT_DOT   = 5;
    const int LED_ARRAY_RIGHT_DOT  = 6;
    const int LED_ARRAY_TOP_HALF   = 7;
    const int LED_ARRAY_BOTTOM_HALF = 8;
    const int LED_EXTERNAL_FET     = 20;
    // TTL-port light-source codes (note: D3/D4 are out of order!)
    const int D1 = 11;
    const int D2 = 12;
    const int D3 = 14;  // out of order!
    const int D4 = 13;  // out of order!
    const int D5 = 15;
}

// Axis configuration
namespace AxisConfigs {

    // X-axis configuration
    const Axis::AxisConfig X_AXIS = {
        .clockFrequency = SystemConfig::TMC4361_CLOCK_FREQUENCY,
        .homingSwitch = LEFT_SW,
        .leftSwitchPolarity = 0,
        .rightSwitchPolarity = 0,
        .leftIsInactive = 0,
        .rightIsInactive = 0,
        .leftFlipped = true,
        .rightFlipped = true,
        .enableLeftLimitSwitch = true,
        .enableRightLimitSwitch = true,
        .r_sense = AxisConstDefinition::R_sense_xy,
        .screwPitchMM = AxisConstDefinition::SCREW_PITCH_X_MM,
        .fullStepsPerRev = AxisConstDefinition::FULLSTEPS_PER_REV_X,
        .microstepping = AxisConstDefinition::MICROSTEPPING_X,
        .homingMicrostepping = AxisConstDefinition::HOMING_MICROSTEPPING_X,
        .maxVelocityMM = AxisConstDefinition::MAX_VELOCITY_X_mm,
        .maxAccelerationMM = AxisConstDefinition::MAX_ACCELERATION_X_mm,
        .homingVelocityMM = AxisConstDefinition::HOMING_VELOCITY_X_MM,
        .motorCurrentMA = AxisConstDefinition::X_MOTOR_PEAK_CURRENT_mA,
        .holdCurrent = AxisConstDefinition::X_MOTOR_I_HOLD,
        .homeSafetyMarginMM = AxisConstDefinition::X_SAFEMARGIN,
        .homeSafetyPositionMM = AxisConstDefinition::X_SAFEPOSITION,
        // StallGuard parameters (only used by TMC2660 SG2; TMC2240 SG4 is
        // temporarily skipped where it is enabled in axis.cpp, parameters kept to enable after SG4 tuning)
        .enableStallSensitivity = true,
        .stallSensitivity = 12,
        .useSShapedRamp = true,
        .astartMM = 0,
        .dfinalMM = 0,
        .homing_timeout_ms = 30000,
        .homing_direct = -1,
        .driverType = DRIVER_AUTO,
        .currentRange = 0,
        .enableEncoder = false,
        .encoderLinesPerRev = (uint16_t)(AxisConstDefinition::SCREW_PITCH_X_MM * 1000 / AxisConstDefinition::ENCODER_RESOLUTION_UM_X),
        .invertEncoderDir = false,
        .invert_direction = false   // 2026-05-25 hardware direction inversion, default false
    };

    // Y-axis configuration
    const Axis::AxisConfig Y_AXIS = {
        .clockFrequency = SystemConfig::TMC4361_CLOCK_FREQUENCY,
        .homingSwitch = LEFT_SW,
        .leftSwitchPolarity = 0,
        .rightSwitchPolarity = 0,
        .leftIsInactive = 0,
        .rightIsInactive = 0,
        .leftFlipped = true,
        .rightFlipped = true,
        .enableLeftLimitSwitch = true,
        .enableRightLimitSwitch = true,
        .r_sense = AxisConstDefinition::R_sense_xy,
        .screwPitchMM = AxisConstDefinition::SCREW_PITCH_Y_MM,
        .fullStepsPerRev = AxisConstDefinition::FULLSTEPS_PER_REV_Y,
        .microstepping = AxisConstDefinition::MICROSTEPPING_Y,
        .homingMicrostepping = AxisConstDefinition::HOMING_MICROSTEPPING_Y,
        .maxVelocityMM = AxisConstDefinition::MAX_VELOCITY_Y_mm,
        .maxAccelerationMM = AxisConstDefinition::MAX_ACCELERATION_Y_mm,
        .homingVelocityMM = AxisConstDefinition::HOMING_VELOCITY_Y_MM,
        .motorCurrentMA = AxisConstDefinition::Y_MOTOR_PEAK_CURRENT_mA,
        .holdCurrent = AxisConstDefinition::Y_MOTOR_I_HOLD,
        .homeSafetyMarginMM = AxisConstDefinition::Y_SAFEMARGIN,
        .homeSafetyPositionMM = AxisConstDefinition::Y_SAFEPOSITION,
        // same as X: StallGuard parameters only used by TMC2660; TMC2240 is skipped where enabled
        .enableStallSensitivity = true,
        .stallSensitivity = 12,
        .useSShapedRamp = true,
        .astartMM = 0,
        .dfinalMM = 0,
        .homing_timeout_ms = 40000,
        .homing_direct = -1,
        .driverType = DRIVER_AUTO,
        .currentRange = 0,
        .enableEncoder = false,
        .encoderLinesPerRev = (uint16_t)(AxisConstDefinition::SCREW_PITCH_Y_MM * 1000 / AxisConstDefinition::ENCODER_RESOLUTION_UM_Y),
        .invertEncoderDir = false,
        .invert_direction = false   // 2026-05-25 hardware direction inversion, default false
    };

    // Z-axis configuration
    // ───────────────────────────────────────────────────────────────────
    // * Z-variant software switch: switching old/new Z only changes one line, Z_AXIS_VARIANT in software/octoaxes/constants.py
    // (the GUI sends pitch/current/microstepping at startup + limit polarity via cmd 20); [no firmware reflash needed, no compile switch needed].
    // After the positive/negative limit sensors were physically swapped on 06-09, the only firmware-side difference between old/new Z = limit polarity (new=1/old=0), which
    // is sent by the host via cmd 20 (SET_LIM_SWITCH_POLARITY) and overridden by reapplyLimitSwitches() re-writing the chip,
    // so the original #define Z_VARIANT_NEW compile switch was removed (2026-06-09). The fields below are the "boot-window defaults"
    // (effective before GUI config, overridden by what is sent afterward); homingSwitch/flip/enable/invertEncoder values for old/new Z
    // are already identical; only the polarity needs software differentiation, so the new default value 1 is used here.
    // pitch/current/microstepping are overridden by the GUI; currentRange=1 is common to both boards.
    // (WARNING) the new-Z limit behavior on the octoaxes mainline board has not yet been tested on that board (different connector/wiring) -- if testing finds homingSwitch/
    // flip must differ from these defaults, verify with software/common/tests/z_limit_monitor.py before adjusting (see the Turret cautionary example).
    const Axis::AxisConfig Z_AXIS = {
        .clockFrequency = SystemConfig::TMC4361_CLOCK_FREQUENCY,
        .homingSwitch = RGHT_SW,         // boot default (both old/new Z use RGHT_SW; after the 06-09 sensor swap, home connects to the STOPR pin without flipping and is read directly as the STOPR bit)
        .leftSwitchPolarity = 0,         // boot default = old Z (0, active-low) -- the octoaxes mainline has the old Z installed, so the firmware must default to supporting the old Z; the new Z (1) is switched via cmd 20 sent at GUI startup
        .rightSwitchPolarity = 0,
        .polarityAffectsChip = true,     // Z only: allow cmd 20 to write the polarity to the chip (Z-variant software switch; the new Z sends 1 to override the boot default 0); X/Y etc. omit it = false, not writing the chip, preserving legacy Squid drop-in
        .leftIsInactive = 0,
        .rightIsInactive = 0,
        .leftFlipped = false,    // false for both old/new Z (the 06-09 sensor swap cancels the coordinate inversion, so INVERT_STOP_DIRECTION is not needed)
        .rightFlipped = false,
        .enableLeftLimitSwitch = true,   // true for both old/new Z (the chip's upper/lower hard stops work fine)
        .enableRightLimitSwitch = true,
        .r_sense = AxisConstDefinition::R_sense_z,
        .screwPitchMM = AxisConstDefinition::SCREW_PITCH_Z_MM,
        .fullStepsPerRev = AxisConstDefinition::FULLSTEPS_PER_REV_Z,
        .microstepping = AxisConstDefinition::MICROSTEPPING_Z,
        .homingMicrostepping = AxisConstDefinition::HOMING_MICROSTEPPING_Z,
        .maxVelocityMM = AxisConstDefinition::MAX_VELOCITY_Z_mm,
        .maxAccelerationMM = AxisConstDefinition::MAX_ACCELERATION_Z_mm,
        .homingVelocityMM = AxisConstDefinition::HOMING_VELOCITY_Z_MM,
        .motorCurrentMA = AxisConstDefinition::Z_MOTOR_PEAK_CURRENT_mA,
        .holdCurrent = AxisConstDefinition::Z_MOTOR_I_HOLD,
        .homeSafetyMarginMM = AxisConstDefinition::Z_SAFEMARGIN,
        .homeSafetyPositionMM = AxisConstDefinition::Z_SAFEPOSITION,
        .enableStallSensitivity = false,
        .stallSensitivity = 6,
        .useSShapedRamp = true,
        .astartMM = 0,
        .dfinalMM = 0,
        .homing_timeout_ms = 60000,   // 60s: leaves ample margin for new-Z + legacy-Squid (can only use the default 1mm/s, ~34.5mm travel, ~34.5s worst case). Increasing the timeout has no side effects
        .homing_direct = 1,
        .driverType = DRIVER_AUTO,
        .currentRange = 1,         // 2026-06-03 newz: TMC2240 ICS I_FS=2A (needed for the new Z's 1.5A). Safe for both Z boards: old Z=TMC2660 ignores this field (uses R_sense), new Z=TMC2240 uses it -> one firmware fits both
        .enableEncoder = false,
        .encoderLinesPerRev = (uint16_t)(AxisConstDefinition::SCREW_PITCH_Z_MM * 1000 / AxisConstDefinition::ENCODER_RESOLUTION_UM_Z),
        .invertEncoderDir = true,   // boot default (ENC-3, not effective while enableEncoder=false); at runtime overridden by GUI CONFIGURE_STAGE_PID per constants.py encoder_flip_direction
        .invert_direction = false   // 2026-05-25 hardware direction inversion, default false
    };

    // W axis 4 configuration (filter wheel)
    // 2026-05-26 W .invert_direction reverted to false: makes octoaxes firmware byte-level identical to legacy Squid firmware.
    // the old decision (set true on 2026-05-25) intended to correct home+offset to land at the center of slot 1 after this hardware's mirror assembly,
    // but at the cost of also inverting the physical direction of all MOVE_W (next/previous) and MOVETO_W, inconsistent with legacy Squid,
    // violating the CLAUDE.md "byte-level drop-in replacement" goal.
    // now reverted to byte-level consistency: the home+offset physical position is exactly the same as legacy Squid (+2.87 deg on your hardware,
    // not centered on slot 1 -- this is legacy Squid's inherent behavior on this hardware, caused by the hardware mirror assembly,
    // not an octoaxes bug). Precise slot-1 alignment requires a hardware-level fix (reassembling the wheel).
    const Axis::AxisConfig W_AXIS = {
        .clockFrequency = SystemConfig::TMC4361_CLOCK_FREQUENCY,
        .homingSwitch = LEFT_SW,
        .leftSwitchPolarity = 0,
        .rightSwitchPolarity = 0,
        .leftIsInactive = 0,
        .rightIsInactive = 0,
        .leftFlipped = false,
        .rightFlipped = false,
        .enableLeftLimitSwitch = true,
        .enableRightLimitSwitch = false,
        .r_sense = AxisConstDefinition::R_sense_filter,
        .screwPitchMM = AxisConstDefinition::SCREW_PITCH_FILTERWHEEL_MM,
        .fullStepsPerRev = AxisConstDefinition::FULLSTEPS_PER_REV_FILTER,
        .microstepping = AxisConstDefinition::MICROSTEPPING_FILTERWHEEL,
        .homingMicrostepping = AxisConstDefinition::HOMING_MICROSTEPPING_FILTERWHEEL,
        .maxVelocityMM = AxisConstDefinition::MAX_VELOCITY_FILTERWHEEL_mm,
        .maxAccelerationMM = AxisConstDefinition::MAX_ACCELERATION_FILTERWHEEL_mm,
        .homingVelocityMM = AxisConstDefinition::HOMING_VELOCITY_FILTERWHEEL_MM,
        .motorCurrentMA = AxisConstDefinition::FILTERWHEEL_MOTOR_PEAK_CURRENT_mA,
        .holdCurrent = AxisConstDefinition::FILTERWHEEL_MOTOR_I_HOLD,
        .homeSafetyMarginMM = AxisConstDefinition::FILTERWHEEL_SAFEMARGIN,
        .homeSafetyPositionMM = AxisConstDefinition::FILTERWHEEL_SAFEPOSITION,
        .enableStallSensitivity = false,
        .stallSensitivity = 6,
        .useSShapedRamp = true,
        .astartMM = 22.5f * AxisConstDefinition::SCREW_PITCH_FILTERWHEEL_MM,  // 2026-05-26 path C v2: ASTART=22.5 rev/s2, equivalent to the historically best chip register value 288,000 ustep/s2 in the microstep=8 era (history: 180 rev/s2 * 1600 ustep/rev = 288K; now needs 22.5 * 12800 = 288K). Avoids overshoot at short distances, still gains jerk-start acceleration at long distances.
        .dfinalMM = 0,                                   // same as astart
        .homing_timeout_ms = 80000,
        .homing_direct = 1,
        .driverType = DRIVER_AUTO,
        .currentRange = 2,
        .enableEncoder = false,
        .encoderLinesPerRev = 4000,
        .invertEncoderDir = false,
        .invert_direction = false   // 2026-05-26 reverted to byte-level drop-in: physical direction consistent with legacy Squid firmware (see the comment above this struct)
    };

    // Expansion axis 1 configuration (objectives turret)
    // 2026-05-29 ported from the objectives branch after on-hardware testing: this board's objective home sensor is physically connected to the TMC4361A's
    // RIGHT input pin (verified with dump_axis_state.py: at home STOPR_ACTIVE_F=1 / leaving=0).
    // so homingSwitch=RGHT_SW, enableRight=true, enableLeft=false.
    // Objectives::performHomingSequence was changed to dynamically use _config.homingSwitch (no longer hardcoding OBSW_SW).
    const Axis::AxisConfig EXPAND1_AXIS = {
        .clockFrequency = SystemConfig::TMC4361_CLOCK_FREQUENCY,
        .homingSwitch = RGHT_SW,
        .leftSwitchPolarity = 0,
        .rightSwitchPolarity = 0,
        .leftIsInactive = 1,
        .rightIsInactive = 1,
        .leftFlipped = false,
        .rightFlipped = false,
        .enableLeftLimitSwitch = false,
        .enableRightLimitSwitch = true,
        .r_sense = AxisConstDefinition::R_sense_objective,
        .screwPitchMM = AxisConstDefinition::SCREW_PITCH_OBJECTIVES_MM,
        .fullStepsPerRev = AxisConstDefinition::FULLSTEPS_PER_REV_OBJECTIVES,
        .microstepping = AxisConstDefinition::MICROSTEPPING_OBJECTIVES,
        .homingMicrostepping = AxisConstDefinition::HOMING_MICROSTEPPING_OBJECTIVES,
        .maxVelocityMM = AxisConstDefinition::MAX_VELOCITY_OBJECTIVES_mm,
        .maxAccelerationMM = AxisConstDefinition::MAX_ACCELERATION_OBJECTIVES_mm,
        .homingVelocityMM = AxisConstDefinition::HOMING_VELOCITY_OBJECTIVES_MM,
        .motorCurrentMA = AxisConstDefinition::OBJECTIVES_MOTOR_PEAK_CURRENT_mA,
        .holdCurrent = AxisConstDefinition::OBJECTIVES_MOTOR_I_HOLD,
        .homeSafetyMarginMM = AxisConstDefinition::OBJECTIVES_SAFEMARGIN,
        .homeSafetyPositionMM = AxisConstDefinition::OBJECTIVES_SAFEPOSITION,
        .enableStallSensitivity = false,
        .stallSensitivity = 15,
        .useSShapedRamp = true,
        .astartMM = 0,
        .dfinalMM = 0,
        .homing_timeout_ms = 80000,
        .homing_direct = 1,
        .driverType = DRIVER_AUTO,
        .currentRange = 1,         // 2026-05-29 TMC2240 I_FS=2A (the original 0=1A lost steps with the gear-reduced objective)
        .enableEncoder = false,
        .encoderLinesPerRev = 0,
        .invertEncoderDir = false,
        .invert_direction = false   // 2026-05-25 hardware direction inversion, default false
    };

    // Expansion axis 3 configuration (Z-axis configuration)
    const Axis::AxisConfig EXPAND3_AXIS = {
        .clockFrequency = SystemConfig::TMC4361_CLOCK_FREQUENCY,
        .homingSwitch = RGHT_SW,
        .leftSwitchPolarity = 0,
        .rightSwitchPolarity = 0,
        .leftIsInactive = 0,
        .rightIsInactive = 0,
        .leftFlipped = false,
        .rightFlipped = false,
        .enableLeftLimitSwitch = true,
        .enableRightLimitSwitch = true,
        .r_sense = AxisConstDefinition::R_sense_z,
        .screwPitchMM = AxisConstDefinition::SCREW_PITCH_Z_MM,
        .fullStepsPerRev = AxisConstDefinition::FULLSTEPS_PER_REV_Z,
        .microstepping = AxisConstDefinition::MICROSTEPPING_Z,
        .homingMicrostepping = AxisConstDefinition::HOMING_MICROSTEPPING_Z,
        .maxVelocityMM = AxisConstDefinition::MAX_VELOCITY_Z_mm,
        .maxAccelerationMM = AxisConstDefinition::MAX_ACCELERATION_Z_mm,
        .homingVelocityMM = AxisConstDefinition::HOMING_VELOCITY_Z_MM,
        .motorCurrentMA = AxisConstDefinition::Z_MOTOR_PEAK_CURRENT_mA,
        .holdCurrent = AxisConstDefinition::Z_MOTOR_I_HOLD,
        .homeSafetyMarginMM = AxisConstDefinition::Z_SAFEMARGIN,
        .homeSafetyPositionMM = AxisConstDefinition::Z_SAFEPOSITION,
        .enableStallSensitivity = false,
        .stallSensitivity = 6,
        .useSShapedRamp = true,
        .astartMM = 0,
        .dfinalMM = 0,
        .homing_timeout_ms = 20000,
        .homing_direct = 1,
        .driverType = DRIVER_AUTO,
        .currentRange = 1,         // audit F-8: unified to 1 with Z_AXIS. EXPAND3 reuses the Z template; if a 1.5A new Z (TMC2240 I_FS=2A) is connected, this value is needed for correct current; old Z TMC2660 ignores this field, safe. EXPAND3 is currently not instantiated
        .enableEncoder = false,
        .encoderLinesPerRev = 0,
        .invertEncoderDir = false,
        .invert_direction = false   // 2026-05-25 hardware direction inversion, default false
    };

    // Expansion axis 4 configuration (filter wheel)
    const Axis::AxisConfig EXPAND4_AXIS = {
        .clockFrequency = SystemConfig::TMC4361_CLOCK_FREQUENCY,
        .homingSwitch = LEFT_SW,
        .leftSwitchPolarity = 0,
        .rightSwitchPolarity = 0,
        .leftIsInactive = 0,
        .rightIsInactive = 0,
        .leftFlipped = false,
        .rightFlipped = false,
        .enableLeftLimitSwitch = true,
        .enableRightLimitSwitch = false,
        .r_sense = AxisConstDefinition::R_sense_filter,
        .screwPitchMM = AxisConstDefinition::SCREW_PITCH_FILTERWHEEL_MM,
        .fullStepsPerRev = AxisConstDefinition::FULLSTEPS_PER_REV_FILTER,
        .microstepping = AxisConstDefinition::MICROSTEPPING_FILTERWHEEL,
        .homingMicrostepping = AxisConstDefinition::HOMING_MICROSTEPPING_FILTERWHEEL,
        .maxVelocityMM = AxisConstDefinition::MAX_VELOCITY_FILTERWHEEL_mm,
        .maxAccelerationMM = AxisConstDefinition::MAX_ACCELERATION_FILTERWHEEL_mm,
        .homingVelocityMM = AxisConstDefinition::HOMING_VELOCITY_FILTERWHEEL_MM,
        .motorCurrentMA = AxisConstDefinition::FILTERWHEEL_MOTOR_PEAK_CURRENT_mA,
        .holdCurrent = AxisConstDefinition::FILTERWHEEL_MOTOR_I_HOLD,
        .homeSafetyMarginMM = AxisConstDefinition::FILTERWHEEL_SAFEMARGIN,
        .homeSafetyPositionMM = AxisConstDefinition::FILTERWHEEL_SAFEPOSITION,
        .enableStallSensitivity = false,
        .stallSensitivity = 6,
        .useSShapedRamp = true,
        .astartMM = 22.5f * AxisConstDefinition::SCREW_PITCH_FILTERWHEEL_MM,  // 2026-05-26 path C v2: W2 same as W (22.5 rev/s2 ~= 288K ustep/s2 chip register, see the W_AXIS comment)
        .dfinalMM = 0,
        .homing_timeout_ms = 80000,
        .homing_direct = 1,
        .driverType = DRIVER_AUTO,
        .currentRange = 0,
        .enableEncoder = false,
        .encoderLinesPerRev = 0,
        .invertEncoderDir = false,
        .invert_direction = false   // 2026-05-26 W2 same as W, reverted to byte-level drop-in (see the comment above W_AXIS)
    };
}

#endif
