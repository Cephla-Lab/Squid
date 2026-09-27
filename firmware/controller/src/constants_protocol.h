/**
 * Protocol constants for firmware-software communication.
 *
 * This header contains protocol-related constants that are shared between
 * firmware and software. It has NO Arduino/hardware dependencies, making it
 * suitable for inclusion in native (host) unit tests.
 *
 * For hardware-specific constants (pins, timers, etc.), see constants.h.
 */

#ifndef CONSTANTS_PROTOCOL_H
#define CONSTANTS_PROTOCOL_H

/***************************************************************************************************/
/***************************************** Communications ******************************************/
/***************************************************************************************************/
// Command packet: 8 bytes
// byte[0]: command ID
// byte[1]: command code
// byte[2-5]: parameters (big-endian)
// byte[6]: reserved
// byte[7]: CRC-8

static const int CMD_LENGTH = 8;
static const int MSG_LENGTH = 24;

// Command codes
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
static const int MOVE_W2 = 19;
static const int SET_LIM_SWITCH_POLARITY = 20;
static const int CONFIGURE_STEPPER_DRIVER = 21;
static const int SET_MAX_VELOCITY_ACCELERATION = 22;
static const int SET_LEAD_SCREW_PITCH = 23;
static const int SET_OFFSET_VELOCITY = 24;
static const int CONFIGURE_STAGE_PID = 25;
static const int ENABLE_STAGE_PID = 26;
static const int DISABLE_STAGE_PID = 27;
// Note: "MERGIN" is intentionally misspelled to match legacy constant name
static const int SET_HOME_SAFETY_MERGIN = 28;
static const int SET_PID_ARGUMENTS = 29;
static const int SEND_HARDWARE_TRIGGER = 30;
static const int SET_STROBE_DELAY = 31;
static const int SET_AXIS_DISABLE_ENABLE = 32;
static const int SET_TRIGGER_MODE = 33;

// Multi-port illumination commands (firmware v1.0+)
// Separate commands (matches existing SET_ILLUMINATION pattern)
static const int SET_PORT_INTENSITY = 34;      // Set DAC intensity for specific port only
static const int TURN_ON_PORT = 35;            // Turn on GPIO for specific port
static const int TURN_OFF_PORT = 36;           // Turn off GPIO for specific port
// Combined command (convenience)
static const int SET_PORT_ILLUMINATION = 37;   // Set intensity + on/off in one command
// Multi-port commands
static const int SET_MULTI_PORT_MASK = 38;     // Set on/off for multiple ports (partial update)
static const int TURN_OFF_ALL_PORTS = 39;      // Turn off all illumination ports
static const int SET_WATCHDOG_TIMEOUT = 40;   // Set serial watchdog timeout and enable
static const int SET_PIN_LEVEL = 41;
static const int HEARTBEAT = 42;              // No-op keepalive for watchdog
static const int MOVETO_W2 = 43;              // Absolute move on the W2 filter wheel
// Encoder / closed-loop diagnostics (firmware 1.6). Both are OFF by default and are
// turned off again by RESET and INITIALIZE, so the shipping packet is unchanged
// unless a host asks. Packet use is in serial_communication.cpp.
static const int SET_ENCODER_REPORTING = 44;  // [2]=axis, [3]=ENCODER_REPORT_* mode
static const int SET_PID_LIMITS = 45;         // [2]=axis, [3..4]=max closed-loop correction velocity (mm/s x100),
                                              // [5..6]=deviation watchdog limit (um); 0 keeps the current value
static const int SET_RAMP_PROFILE = 47;       // [2]=axis, [3]=RAMP_PROFILE_* : S-shaped (bow-limited) or trapezoidal ramp
static const int RAMP_PROFILE_TRAPEZOID = 1;
static const int RAMP_PROFILE_SSHAPE = 2;
static const int SET_COMPLETION_WINDOW = 49;  // [2]=axis, [3..4]=window in 0.1 um of travel (for the wheels, whose
                                              // "mm" is one revolution, 1e-4 rev = 0.036 deg): a move reports COMPLETED as
                                              // soon as |XACTUAL - target| <= window while the ramp finishes. 0 = at the
                                              // exact target (default, unchanged behaviour). Not applied to homing.
static const int SET_PID_TOLERANCE = 48;      // [2]=axis, [3..4]=loop deadband (PID_TOLERANCE) in 0.01 um, [5..6]=target-reached
                                              // tolerance (CL_TR_TOLERANCE) in 0.01 um; 0 keeps the current value
static const int SET_PID_HOME_ZONE = 46;      // [2]=axis, [3..4]=home exclusion zone (um): within this distance of
                                              // the home position the loop is held open (see check_closed_loop);
                                              // 0 disables the zone
static const int SET_PID_OPEN_ABOVE = 50;     // [2]=axis, [3..4]=ramp velocity (mm/s x100) above which a requested
                                              // closed loop is opened while the axis moves; it re-engages as the ramp
                                              // slows below it (see check_closed_loop). 0 (default) = rest-only: open
                                              // for every move, engaged only at rest. >= VMAX = engaged throughout.
// Move-and-settle (firmware 1.7): the feedforward alternative to the TMC4361A's PID - see move_settle_policy.h.
// Every field of the four SET_MOVE_SETTLE_* commands is literal (no "0 = keep"): RESET restores the firmware
// defaults and a host sends all of them, so nothing an earlier session left behind survives.
static const int SET_LOOP_STRATEGY = 51;      // [2]=axis, [3]=LOOP_STRATEGY_*: what ENABLE_STAGE_PID hands the axis to. Refused
                                              // (CMD_EXECUTION_ERROR) while a loop is requested: DISABLE first.
static const int LOOP_STRATEGY_CHIP_PID = 0;  // the TMC4361A's PID (default; the behaviour of firmware 1.6)
static const int LOOP_STRATEGY_MOVE_SETTLE = 1; // move-and-settle: open-loop ramps planned in the encoder's frame, finite
                                              // corrections from a window-averaged reading, nothing regulating at rest
static const int SET_MOVE_SETTLE_MEASURE = 52;  // [2]=axis, [3]=settle time after the ramp stops before the window opens, 0.5 ms
                                              // (default 10 = 5 ms), [4]=averaging window in 0.5 ms (one ring period; default
                                              // 18 = 9 ms), [5]=trim gain in 1/16 (default 12), [6]=bits 0-3 max trims per
                                              // approach (default 6), bits 4-5 MOVE_SETTLE_APPROACH_*, bits 6-7 max re-approaches (default 2)
static const int MOVE_Z_FROM_MEASURED = 1;      // MOVE_Z [6]: the relative move starts from where the stage IS (the encoder), not from the
                                              // last target. Move-and-settle only; any other value, strategy or firmware: the plain move.
static const int MOVE_Z_RETRY_LAST = 2;         // MOVE_Z [6]: move to the LAST move-and-settle target on Z again, whatever the payload - the
                                              // host's retry of a MISSED move planned from the encoder (its target was measured + correction,
                                              // which the host cannot know). The payload is the host's best estimate as a relative move,
                                              // used as-is where the flag means nothing: old firmware, other strategies, no such target.
static const int SET_MOVE_SETTLE_SCALE = 57;    // [2]=axis, [3..4]=int16, ppm: stage travel per motor travel minus one - the screw's lead
                                              // against the encoder's scale (-1100 = the stage moves 0.11 um less per 100 um than the
                                              // counter). Every leg is planned through it and it is learned from long continuing legs
                                              // (learning gain of SET_MOVE_SETTLE_MODEL); +-5000 at most. Re-seeds the model.
static const int BENCH_INJECT_FOCUS_WHEEL = 58; // BENCH BUILDS ONLY (-DBENCH_WHEEL_INJECT; without it the number is unassigned and ignored):
                                              // [2..3]=int16 wheel travel per packet, usteps (the panel sends multiples of 16),
                                              // [4..5]=uint16 delay before the first packet, ms, [6]=packets (0 = 1), one every
                                              // 8 ms. Each goes through the lines the panel's packet goes through
                                              // (onJoystickPacketReceived): focusPosition += travel; focus_wheel_pending = true.
static const int BENCH_DUMP_SETTLE_TRACE = 59;  // BENCH BUILDS ONLY (-DBENCH_SETTLE_TRACE; without it the number is unassigned and ignored):
                                              // [2]=0 start the dump of the settle trace - one record per measured leg of every
                                              // move-and-settle move, kept in RAM - as ASCII lines on this link (STH / ST / STE,
                                              // move_settle.cpp), oldest first; refused (CMD_EXECUTION_ERROR) while a
                                              // move-and-settle is in flight on Z. [2]=1 clear the ring.
static const int BENCH_PANEL_STREAM = 60;       // BENCH BUILDS ONLY (-DBENCH_WHEEL_INJECT; without it the number is unassigned and ignored):
                                              // whole panel packets, built as the panel builds them and handed to the panel's
                                              // handler (onJoystickPacketReceived) one every 2 ms, the panel's cadence, from the
                                              // main loop - the parser, the first-packet handling, the lock-out and the wheel
                                              // accumulation as with the panel, which command 58 bypasses. [2]=op: 1 wheel
                                              // stream, [3..4]=int16 wheel travel per packet, usteps (the panel sends multiples
                                              // of 16), [5..6]=uint16 packets (0 = 1); 2 joystick stream, [3..4]=int16 x,
                                              // [5..6]=int16 y as the panel encodes them, 100 packets (200 ms; the host repeats
                                              // the command for a longer deflection); 3 release, one packet with the wheel where
                                              // it is and the joystick at 0,0 (the panel's idle packet); 0 report, one ASCII
                                              // line on this link, at once: PN,<real packets>,<real packets in the last
                                              // 1000 ms>,<focuswheel_pos>,<panel_locked_out()>,<synthetic packets>*CC (NMEA
                                              // checksum, like the settle trace's). A new stream replaces a pending one.
static const int SET_MOVE_SETTLE_FINISH = 61;   // [2]=axis, [3..4]=uint16 finishing leg, usteps (0 = off), [5..6]=uint16 threshold in
                                              // units of 16 usteps (0 = the finishing leg's own length). A move whose first approach
                                              // leg would want more stage travel than the threshold is issued as two approach legs:
                                              // the long one aimed the finishing leg short of the target, then, from its measured
                                              // landing, an ordinary continuing leg for the rest (bench 2026-09-26: a fast leg of
                                              // 0.5-3 mm lands +-1 um off the model, a 50 um leg after it lands like any short step;
                                              // move_settle_policy.h). Refused (CMD_EXECUTION_ERROR) when both are non-zero and the
                                              // finishing leg is not shorter than the threshold. Does not re-seed the model.
static const int MOVE_SETTLE_APPROACH_MOVE_DIRECTION = 0; // approach from the side the move comes from (default)
static const int MOVE_SETTLE_APPROACH_POSITIVE = 1;       // always finish travelling +: a move toward - goes beyond and comes back
static const int MOVE_SETTLE_APPROACH_NEGATIVE = 2;       // always finish travelling -
static const int SET_MOVE_SETTLE_FEEDFORWARD = 53; // [2]=axis, [3..4]=lost motion flank to flank on a reversal, 0.01 um - where the
                                              // learning starts (default 0; start UNDER the real value), [5]=undershoot bias of the first
                                              // landing, 0.01 um (default 0), [6]=back-off distance, 0.1 um (default 30 = 3 um;
                                              // must exceed the lost motion)
static const int SET_MOVE_SETTLE_SHAPER = 54;   // [2]=axis, [3..4]=half the ring period in 10 us (0 = off, default; 442 for the
                                              // 113 Hz Squid+ Z), [5]=share of the leg that goes first in 1/256 (128 = half),
                                              // [6]=longest leg that is split, um
static const int SET_MOVE_SETTLE_ACCEPT = 55;   // [2]=axis, [3..4]=accepted overshoot in 0.01 um (0 = the target tolerance, i.e.
                                              // symmetric), [5]=ring peak-to-peak required before DONE, 0.01 um (0 = not required),
                                              // [6]=extra windows DONE may wait for it
static const int SET_MOVE_SETTLE_MODEL = 56;    // [2]=axis, [3]=carry: how far a full push leaves the stage beyond the drive flank,
                                              // 0.01 um - where the learning starts (default 0), [4]=push from which the carry is
                                              // full, 0.01 um (default 94 = 10 usteps at 16 usteps/FS), [5]=learning gain in 1/16
                                              // (default 4; 0 = the model stays as configured), [6]=learned undershoot bias of the
                                              // first landing in landing-scatter sigmas, 1/16 (default 8 = half a sigma; 0 = the
                                              // fixed bias of SET_MOVE_SETTLE_FEEDFORWARD only). Sending this or
                                              // SET_MOVE_SETTLE_FEEDFORWARD re-seeds the learned model from the configured values.
// SET_ENCODER_REPORTING modes
static const int ENCODER_REPORT_OFF = 0;
static const int ENCODER_REPORT_ENC_IN_THETA = 1;    // bytes 14-17 = ENC_POS of the axis (usteps), byte 19 = ENC_FLAG_*,
                                                     // bytes 20-21 = int16 ENC_POS_DEV (ENC_POS - XACTUAL as the chip reports it, clipped)
static const int ENCODER_REPORT_ENC_AS_POSITION = 2; // as 1, and the axis's own position field carries ENC_POS
static const int ENCODER_REPORT_MOVE_SETTLE = 3;       // as 1, but bytes 20-21 carry the last move-and-settle's report instead of the
                                                     // clipped deviation (the host has ENC_POS and XACTUAL in the same packet):
                                                     // byte 20 = int8 first landing, usteps, + = short of the target in the
                                                     // approach direction; byte 21 = MOVE_SETTLE_REPORT_* bits
static const int MOVE_SETTLE_REPORT_TRIMS_MASK = 15;       // byte 21 bits 0-3: trims used (clipped)
static const int MOVE_SETTLE_REPORT_BACKED_OFF = 4;  // bit 4: the move needed a back-off and a fresh approach
static const int MOVE_SETTLE_REPORT_MISSED = 5;      // bit 5: budget spent outside the tolerance: the command failed (CMD_EXECUTION_ERROR),
                                                     // no fault latched, the position fields show where the stage is
static const int MOVE_SETTLE_REPORT_LIMITED = 6;           // bit 6: a leg was clamped to the travel limits
static const int MOVE_SETTLE_REPORT_BUSY = 7;              // bit 7: a move-and-settle is in progress
// byte 19 flag bits, valid only while reporting is active
static const int ENC_FLAG_REPORTING = 0;     // reporting active
static const int ENC_FLAG_PID_ENABLED = 1;   // closed loop enabled on the reported axis
static const int ENC_FLAG_PID_FAULT = 2;     // deviation watchdog disabled the closed loop (sticky until DISABLE_STAGE_PID acknowledges, ENABLE_STAGE_PID validates, or CONFIGURE/INITIALIZE/RESET)
static const int ENC_FLAG_PID_ZONE = 3;      // loop requested but held open (home zone, or homing); re-engages automatically outside the zone
static const int ENC_FLAG_AXIS_SHIFT = 4;    // bits 4-6: protocol axis id being reported
static const int INITFILTERWHEEL_W2 = 252;
static const int INITFILTERWHEEL = 253;
static const int INITIALIZE = 254;
static const int RESET = 255;

// Command execution status
static const int COMPLETED_WITHOUT_ERRORS = 0;
static const int IN_PROGRESS = 1;
static const int CMD_CHECKSUM_ERROR = 2;
static const int CMD_INVALID = 3;
static const int CMD_EXECUTION_ERROR = 4;

// Home/zero command types
static const int HOME_NEGATIVE = 1;
static const int HOME_POSITIVE = 0;
static const int HOME_OR_ZERO_ZERO = 2;

// Axis identifiers for protocol communication (sent from software to firmware).
// IMPORTANT: These are PROTOCOL constants, NOT internal array indices!
// The firmware uses different internal indices (see def/def_v1.h):
//   Protocol: AXIS_X=0, AXIS_Y=1, AXIS_Z=2, AXIS_W=5, AXIS_W2=6
//   Internal: x=1, y=0, z=2, w=3, w2=4
// Use protocol_axis_to_internal() to convert when accessing arrays.
static const int AXIS_X = 0;
static const int AXIS_Y = 1;
static const int AXIS_Z = 2;
static const int AXIS_THETA = 3;
static const int AXES_XY = 4;
static const int AXIS_W = 5;
static const int AXIS_W2 = 6;

// Button/switch bit positions in response packet
static const int BIT_POS_JOYSTICK_BUTTON = 0;

// Status byte 18, bits 4-6: closed-loop fault latched on X / Y / Z (protocol order). Set in
// every packet, whether or not encoder reporting is on, so a fault with no command in flight
// still reaches the host. Sticky until the host acknowledges it: DISABLE_STAGE_PID (run open-loop
// knowingly) or ENABLE_STAGE_PID (after its deviation check passes); CONFIGURE_STAGE_PID,
// INITIALIZE and RESET clear it as part of re-initialising the axis.
static const int BIT_POS_PID_FAULT_X = 4;
static const int BIT_POS_PID_FAULT_Y = 5;
static const int BIT_POS_PID_FAULT_Z = 6;
// Why the loop faulted, per stage axis, whenever encoder reporting is OFF (bytes 19-21 carry the
// reported axis's flags and clipped deviation while it is on). Four bits per axis, packed so that
// byte 19 bit 0 - ENC_FLAG_REPORTING - stays clear: X's cause in byte 19 bits 1-4, Y's in byte 20
// bits 0-3, Z's in byte 20 bits 4-7; byte 21 stays 0. 0 while no fault is latched; cleared with the
// fault bit.
static const int PID_FAULT_CAUSE_X_SHIFT = 1;   // byte 19
static const int PID_FAULT_CAUSE_Y_SHIFT = 0;   // byte 20
static const int PID_FAULT_CAUSE_Z_SHIFT = 4;   // byte 20
static const int PID_FAULT_CAUSE_MASK = 15;     // four bits per cause (decimal: the host parity test reads decimal only)
static const int PID_FAULT_NONE = 0;
static const int PID_FAULT_WATCHDOG = 1;          // engaged: |ENC_POS - XACTUAL| exceeded SET_PID_LIMITS
static const int PID_FAULT_NO_PROGRESS = 2;       // engaged at rest: the error stopped shrinking (frozen encoder, stuck stage)
static const int PID_FAULT_TIMEOUT = 3;           // engaged at rest: the correction did not finish in its time budget
static const int PID_FAULT_REALIGN_REFUSED = 4;   // first engage after homing: frame offset beyond home zone + watchdog
static const int PID_FAULT_REENGAGE_REFUSED = 5;  // at rest, frames aligned, still beyond the watchdog: encoder stopped following
static const int PID_FAULT_TRAVEL = 6;            // engaged at rest: the correction travelled the watchdog distance without converging
static const int PID_FAULT_NO_RESPONSE = 7;       // engaged at rest: the chip drove the motor and the encoder did not respond (frozen feedback / stage not following)
static const int PID_FAULT_STOP_SWITCH = 8;       // engaged: a reference switch is active and the correction was driving toward it (the chip's stop gates the ramp, not the correction)

// Limit switch codes (for SET_LIM command)
static const int LIM_CODE_X_POSITIVE = 0;
static const int LIM_CODE_X_NEGATIVE = 1;
static const int LIM_CODE_Y_POSITIVE = 2;
static const int LIM_CODE_Y_NEGATIVE = 3;
static const int LIM_CODE_Z_POSITIVE = 4;
static const int LIM_CODE_Z_NEGATIVE = 5;

// Limit switch polarity
static const int ACTIVE_LOW = 0;
static const int ACTIVE_HIGH = 1;
static const int DISABLED = 2;

/***************************************************************************************************/
/***************************************** Illumination ********************************************/
/***************************************************************************************************/
// LED matrix patterns
static const int ILLUMINATION_SOURCE_LED_ARRAY_FULL = 0;
static const int ILLUMINATION_SOURCE_LED_ARRAY_LEFT_HALF = 1;
static const int ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_HALF = 2;
static const int ILLUMINATION_SOURCE_LED_ARRAY_LEFTB_RIGHTR = 3;
static const int ILLUMINATION_SOURCE_LED_ARRAY_LOW_NA = 4;
static const int ILLUMINATION_SOURCE_LED_ARRAY_LEFT_DOT = 5;
static const int ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_DOT = 6;
static const int ILLUMINATION_SOURCE_LED_ARRAY_TOP_HALF = 7;
static const int ILLUMINATION_SOURCE_LED_ARRAY_BOTTOM_HALF = 8;
static const int ILLUMINATION_SOURCE_LED_EXTERNAL_FET = 20;

// Illumination Control TTL Ports - port-based names (preferred)
// These correspond to controller_port D1-D5 in software configuration
// Note: D3/D4 source codes are non-sequential (14, 13) for historical API compatibility
static const int ILLUMINATION_D1 = 11;
static const int ILLUMINATION_D2 = 12;
static const int ILLUMINATION_D3 = 14;
static const int ILLUMINATION_D4 = 13;
static const int ILLUMINATION_D5 = 15;

// Illumination Control TTL Ports - legacy wavelength-based names (deprecated, kept for compatibility)
// Use ILLUMINATION_D1-D5 for new code; wavelength is configured in software YAML
static const int ILLUMINATION_SOURCE_405NM = 11;
static const int ILLUMINATION_SOURCE_488NM = 12;
static const int ILLUMINATION_SOURCE_561NM = 14;
static const int ILLUMINATION_SOURCE_638NM = 13;
static const int ILLUMINATION_SOURCE_730NM = 15;

#endif // CONSTANTS_PROTOCOL_H
