#include "commands.h"

#include "../init.h"                     // report_driver_probe()
#include "../tmc/drivers/driver_probe.h"
#include "../tmc/drivers/stepper_driver.h"

CommandCallback cmd_map[256] = {0};

void init_callbacks()
{
    cmd_map[MOVE_X] = &callback_move_x;
    cmd_map[MOVE_Y] = &callback_move_y;
    cmd_map[MOVE_Z] = &callback_move_z;
    cmd_map[MOVE_W] = &callback_move_w;
    cmd_map[MOVE_W2] = &callback_move_w2;
    cmd_map[MOVETO_X] = &callback_move_to_x;
    cmd_map[MOVETO_Y] = &callback_move_to_y;
    cmd_map[MOVETO_Z] = &callback_move_to_z;
    cmd_map[MOVETO_W] = &callback_move_to_w;
    cmd_map[MOVETO_W2] = &callback_move_to_w2;
    cmd_map[SET_LIM] = &callback_set_lim;
    cmd_map[SET_LIM_SWITCH_POLARITY] = &callback_set_lim_switch_polarity;
    cmd_map[SET_HOME_SAFETY_MERGIN] = &callback_set_home_safety_margin;
    cmd_map[SET_PID_ARGUMENTS] = &callback_set_pid_arguments;
    cmd_map[CONFIGURE_STEPPER_DRIVER] = &callback_configure_stepper_driver;
    cmd_map[SET_MAX_VELOCITY_ACCELERATION] = &callback_set_max_velocity_acceleration;
    cmd_map[SET_LEAD_SCREW_PITCH] = &callback_set_lead_screw_pitch;
    cmd_map[HOME_OR_ZERO] = &callback_home_or_zero;
    cmd_map[SET_OFFSET_VELOCITY] = &callback_set_offset_velocity;
    cmd_map[TURN_ON_ILLUMINATION] = &callback_turn_on_illumination;
    cmd_map[TURN_OFF_ILLUMINATION] = &callback_turn_off_illumination;
    cmd_map[SET_ILLUMINATION] = &callback_set_illumination;
    cmd_map[SET_ILLUMINATION_LED_MATRIX] = &callback_set_illumination_led_matrix;
    // Multi-port illumination commands (firmware v1.0+)
    cmd_map[SET_PORT_INTENSITY] = &callback_set_port_intensity;
    cmd_map[TURN_ON_PORT] = &callback_turn_on_port;
    cmd_map[TURN_OFF_PORT] = &callback_turn_off_port;
    cmd_map[SET_PORT_ILLUMINATION] = &callback_set_port_illumination;
    cmd_map[SET_MULTI_PORT_MASK] = &callback_set_multi_port_mask;
    cmd_map[TURN_OFF_ALL_PORTS] = &callback_turn_off_all_ports;
    cmd_map[SET_WATCHDOG_TIMEOUT] = &callback_set_watchdog_timeout;
    cmd_map[HEARTBEAT] = &callback_heartbeat;
    cmd_map[ACK_JOYSTICK_BUTTON_PRESSED] = &callback_ack_joystick_button_pressed;
    cmd_map[ANALOG_WRITE_ONBOARD_DAC] = &callback_analog_write_onboard_dac;
    cmd_map[SET_DAC80508_REFDIV_GAIN] = &callback_set_dac80508_defdiv_gain;
    cmd_map[SET_ILLUMINATION_INTENSITY_FACTOR] = &callback_set_illumination_intensity_factor;
    cmd_map[SET_STROBE_DELAY] = &callback_set_strobe_delay;
    cmd_map[SEND_HARDWARE_TRIGGER] = &callback_send_hardware_trigger;
    cmd_map[SET_PIN_LEVEL] = &callback_set_pin_level;
    cmd_map[CONFIGURE_STAGE_PID] = &callback_configure_stage_pid;
    cmd_map[ENABLE_STAGE_PID] = &callback_enable_stage_pid;
    cmd_map[DISABLE_STAGE_PID] = &callback_disable_stage_pid;
    cmd_map[INITFILTERWHEEL] = &callback_initfilterwheel;
    cmd_map[INITFILTERWHEEL_W2] = &callback_initfilterwheel_w2;
    cmd_map[SET_AXIS_DISABLE_ENABLE] = &callback_set_axis_disable_enable;
    cmd_map[SET_TRIGGER_MODE] = &callback_set_trigger_mode;

    cmd_map[INITIALIZE] = &callback_initialize;
    cmd_map[SET_ENCODER_REPORTING] = &callback_set_encoder_reporting;
    cmd_map[SET_PID_LIMITS] = &callback_set_pid_limits;
    cmd_map[SET_PID_HOME_ZONE] = &callback_set_pid_home_zone;
    cmd_map[SET_RAMP_PROFILE] = &callback_set_ramp_profile;
    cmd_map[SET_PID_TOLERANCE] = &callback_set_pid_tolerance;
    cmd_map[SET_COMPLETION_WINDOW] = &callback_set_completion_window;
    cmd_map[SET_PID_OPEN_ABOVE] = &callback_set_pid_open_above;
    cmd_map[SET_PID_P24] = &callback_set_pid_p24;
    cmd_map[SET_PID_KEEP_CLOSED_BELOW] = &callback_set_pid_keep_closed_below;
    cmd_map[SET_PID_PRECOMP] = &callback_set_pid_precomp;
    cmd_map[RESET] = &callback_reset;
}

void callback_default()
{
    // TODO: This is for future use, e.g., when we implement immediate error reporting back to the
    // host. For now, do nothing.
}

void callback_ack_joystick_button_pressed()
{
    joystick_button_pressed = false;
}

void callback_analog_write_onboard_dac()
{
    int dac = buffer_rx[2];
    uint16_t value = ( uint16_t(buffer_rx[3]) * 256 + uint16_t(buffer_rx[4]) );
    set_DAC8050x_output(dac, value);
}

void callback_set_dac80508_defdiv_gain()
{
    uint8_t div   = buffer_rx[2];
    uint8_t gains = buffer_rx[3];
    set_DAC8050x_gain(div, gains);
}

void callback_set_strobe_delay()
{
    strobe_delay[buffer_rx[2]] = uint32_t(buffer_rx[3]) << 24 | uint32_t(buffer_rx[4]) << 16 | uint32_t(buffer_rx[5]) << 8 | uint32_t(buffer_rx[6]);
}

void callback_send_hardware_trigger()
{
    // Some (all?) the arrays used by the trigger timer interrupt use data types that don't have
    // atomic writes, so we need to disable interrupts here to make sure the timer interrupt
    // doesn't get partially written values.
    noInterrupts();
    int camera_channel = buffer_rx[2] & 0x0f;

    // For level trigger mode, ignore new triggers while one is already active
    if (trigger_mode != 0 && trigger_output_level[camera_channel] == LOW) {
        interrupts();
        return;
    }

    control_strobe[camera_channel] = buffer_rx[2] >> 7;
    illumination_on_time[camera_channel] = uint32_t(buffer_rx[3]) << 24 | uint32_t(buffer_rx[4]) << 16 | uint32_t(buffer_rx[5]) << 8 | uint32_t(buffer_rx[6]);
    digitalWrite(camera_trigger_pins[camera_channel], LOW);
    timestamp_trigger_rising_edge[camera_channel] = micros();
    trigger_output_level[camera_channel] = LOW;
    interrupts();
}

void callback_set_pin_level()
{
    int pin = buffer_rx[2];
    bool level = buffer_rx[3];
    digitalWrite(pin, level);
}

void callback_configure_stage_pid()
{
    uint8_t axis = protocol_axis_to_internal(buffer_rx[2]);
    if (axis == 0xFF) return;  // Invalid axis

    int flip_direction = buffer_rx[3];
    int transitions_per_revolution = (buffer_rx[4] << 8) + buffer_rx[5];
    // Init encoder. transitions per revolution, velocity filter wait time (# of clock cycles), IIR filter exponent, vmean update frequency, invert direction (must increase as microsteps increases)
    tmc4361A_init_ABN_encoder(&tmc4361[axis], transitions_per_revolution, 32, 4, 512, flip_direction);
    // Align the encoder frame with XACTUAL under the scale and direction just
    // written. ENC_POS is derived from the raw count, so a zero taken earlier
    // (homing, or the chip reset) under a different ENC_IN_RES / invert setting
    // does not survive this write - the first bench run found the loop error
    // pinned at the int16 clip for exactly that reason.
    tmc4361A_write_encoder(&tmc4361[axis], tmc4361A_currentPosition(&tmc4361[axis]));

    // Closed-loop correction velocity ceiling (PID_DV_CLIP): the host's
    // SET_PID_LIMITS value if it has sent one, else the axis's max velocity as
    // before 1.6. A bench tuning session sets this low first, so that a wrong
    // encoder sign cannot run the axis away faster than the watchdog reacts.
    uint32_t dv_clip;
    if (pid_dv_clip_usteps[axis] != 0)
        dv_clip = pid_dv_clip_usteps[axis];
    else if (axis == x)
        dv_clip = tmc4361A_vmmToMicrosteps(&tmc4361[axis], MAX_VELOCITY_X_mm);
    else if (axis == y)
        dv_clip = tmc4361A_vmmToMicrosteps(&tmc4361[axis], MAX_VELOCITY_Y_mm);
    else if (axis == z)
        dv_clip = tmc4361A_vmmToMicrosteps(&tmc4361[axis], MAX_VELOCITY_Z_mm);
    else
        dv_clip = tmc4361A_vmmToMicrosteps(&tmc4361[axis], MAX_VELOCITY_W_mm);

    // Loop deadband (PID_TOLERANCE: below this error the chip stops correcting) and
    // target-reached tolerance (CL_TR_TOLERANCE: what tmc4361A_isRunning() accepts as
    // arrived). Both are in microsteps, so a fixed number is a physical size that
    // scales with microstepping: master's 25 usteps is 0.15 um at 256 usteps/FS on Z
    // but 2.3 um at 16, where a 20 x 1 um closed-loop stack landed up to 2.2 um off.
    // Default: TWO ENCODER COUNTS, computed from the encoder resolution just written.
    // A deadband below one count makes the loop chase the quantisation after every
    // move (15 kHz bursts on the bench); two counts tolerates a 1-count error and was
    // acoustically identical to open loop. This reproduces master's 25 usteps at 256
    // usteps/FS (1.5 counts -> rounds to the same behaviour) at every resolution.
    // SET_PID_TOLERANCE overrides both in physical units.
    uint32_t default_tol = (axis == w || axis == w2) ? 2 : 25;  // legacy, if the encoder resolution is unusable
    if (transitions_per_revolution > 0) {
        uint32_t usteps_per_rev = (uint32_t)tmc4361[axis].microsteps * (uint32_t)tmc4361[axis].stepsPerRev;
        default_tol = (2u * usteps_per_rev + (uint32_t)transitions_per_revolution / 2u) / (uint32_t)transitions_per_revolution;
        if (default_tol < 1) default_tol = 1;
    }
    uint32_t pid_tol = pid_tolerance_usteps[axis] ? pid_tolerance_usteps[axis] : default_tol;
    uint32_t tr_tol  = pid_tr_tolerance_usteps[axis] ? pid_tr_tolerance_usteps[axis] : default_tol;

    // Init PID. target reach tolerance, position error tolerance, P, I, and D coefficients, max speed, winding limit, derivative update rate
    bool configured = false;
    if (axis == x || axis == y) {
        tmc4361A_init_PID(&tmc4361[axis], tr_tol, pid_tol, axes_pid_arg[axis].p, axes_pid_arg[axis].i, axes_pid_arg[axis].d, dv_clip, 32767, 2);
        configured = true;
    }
    else if (axis == z) {
        tmc4361A_init_PID(&tmc4361[axis], tr_tol, pid_tol, axes_pid_arg[axis].p, axes_pid_arg[axis].i, axes_pid_arg[axis].d, dv_clip, 4096, 2);
        configured = true;
    }
    else if (axis == w) {
        if (enable_filterwheel == true) {
            tmc4361A_init_PID(&tmc4361[axis], tr_tol, pid_tol, axes_pid_arg[axis].p, axes_pid_arg[axis].i, axes_pid_arg[axis].d, dv_clip, 4096, 2);
            configured = true;
        }
    }
    else if (axis == w2) {
        if (enable_filterwheel_w2 == true) {
            tmc4361A_init_PID(&tmc4361[axis], tr_tol, pid_tol, axes_pid_arg[axis].p, axes_pid_arg[axis].i, axes_pid_arg[axis].d, dv_clip, 4096, 2);
            configured = true;
        }
    }

    // Bookkeeping for ENABLE_STAGE_PID's gate and for the deviation watchdog.
    // Default watchdog limit: 0.25 mm of loop error (0.25 rev on a wheel) - far
    // above any sane following error, far below a travel end.
    encoder_configured[axis] = configured;
    pid_fault[axis] = false;
    if (configured && pid_max_dev_usteps[axis] == 0)
        pid_max_dev_usteps[axis] = tmc4361A_xmmToMicrosteps(&tmc4361[axis], 0.25f);
}

void callback_enable_stage_pid()
{
    uint8_t axis = protocol_axis_to_internal(buffer_rx[2]);
    if (axis == 0xFF) return;  // Invalid axis

    /*
      This is an actuator path, not a configuration write. PID_BPG0 sets
      ENC_IN_CONF.REGULATION_MODUS, which hands the axis to the TMC4361A's
      closed loop: from that write on the controller drives the motor
      continuously to null the encoder error, with no further command from the
      host. On an axis the probe could not identify, the current scaling — and
      therefore the torque — is unknown, so this is gated exactly like a move.

      It reports the rejection rather than dropping it silently: ENABLE_STAGE_PID
      is a host command, so the host is owed an answer. That is the same split
      the rest of the branch makes — host commands report through
      axis_driver_ready, the joystick and focus-wheel paths in operations.cpp
      reject silently because there is no command to attribute a failure to.

      Note this also gates the PID_BPG0 re-enables in finalize_homing_* : they
      fire only when stage_PID_enabled[axis] is set, and this is the only writer
      that sets it.
    */
    if (!axis_driver_ready(axis)) return;

    // The loop nulls XACTUAL - ENC_POS using ENC_IN_RES to scale the encoder.
    // With no CONFIGURE_STAGE_PID since the last chip reset that scale is the
    // reset value and the "error" is garbage: enabling would drive the axis at
    // PID_DV_CLIP toward nowhere. Refuse, and say so through the status byte.
    if (!encoder_configured[axis])
    {
        report_move_error();   // early return: nothing of this command's to unwind
        return;
    }

    // Closing the loop makes the chip slew the axis by the CURRENT error at up to
    // PID_DV_CLIP. If that error is already beyond the watchdog limit (encoder
    // frame offset from XACTUAL, e.g. INITIALIZE zeroed ENC_POS mid-travel and
    // nobody homed since), enabling would be exactly the run-away the watchdog
    // exists to stop - so refuse up front instead of tripping a moment later.
    if (pid_max_dev_usteps[axis] > 0)
    {
        int32_t dev = tmc4361A_read_deviation(&tmc4361[axis]);
        if (dev > pid_max_dev_usteps[axis] || dev < -pid_max_dev_usteps[axis])
        {
            report_move_error();
            return;
        }
    }

    pid_fault[axis] = false;
    pid_requested[axis] = true;
    pid_realign_pending[axis] = false;   // an explicit enable takes the frames as they are (gate below)

    // Inside the home exclusion zone the encoder may not follow the actuator
    // (stage resting on its stop while the actuator retracts), so the loop is not
    // engaged here: it is recorded as requested and check_closed_loop() engages
    // it once the axis is outside the zone with a small error. The status flags
    // (ENC_FLAG_PID_ENABLED / ENC_FLAG_PID_ZONE) tell the host which it got.
    int32_t zone = pid_home_zone_usteps[axis];
    int32_t pos = tmc4361A_currentPosition(&tmc4361[axis]);
    if (zone > 0 && pos > -zone && pos < zone)
    {
        pid_zone_hold[axis] = true;
        stage_PID_enabled[axis] = 0;
        return;
    }
    // Never engage while the ramp runs (see check_closed_loop): coming in at speed
    // adds the correction velocity on top of VMAX and stalled the second bench Z.
    // Record the request; check_closed_loop() engages it when the axis stops.
    if (tmc4361A_isRunning(&tmc4361[axis], 0))
    {
        pid_zone_hold[axis] = true;
        stage_PID_enabled[axis] = 0;
        return;
    }
    pid_zone_hold[axis] = false;
    tmc4361A_set_PID(&tmc4361[axis], PID_BPG0);
    stage_PID_enabled[axis] = 1;
}

// SET_RAMP_PROFILE (47): [2] protocol axis, [3] RAMP_PROFILE_TRAPEZOID (1) or
// RAMP_PROFILE_SSHAPE (2). Rewrites the ramp registers at once. Not reset by
// INITIALIZE (the host sets it once with the other motion parameters).
void callback_set_ramp_profile()
{
    uint8_t axis = protocol_axis_to_internal(buffer_rx[2]);
    if (axis == 0xFF) return;
    uint8_t profile = buffer_rx[3];
    if (profile != RAMP_PROFILE_TRAPEZOID && profile != RAMP_PROFILE_SSHAPE) return;
    tmc4361[axis].ramp_profile = profile;
    tmc4361A_sRampInit(&tmc4361[axis]);
}

// SET_COMPLETION_WINDOW (49): [2] protocol axis, [3..4] window in 0.1 um of travel. While a
// move is inside the window the ramp is still finishing, but the host is told COMPLETED so an
// exposure can start while the last part is travelled. Meant for the filter wheels, where the
// filter's clear aperture covers the field for the last few degrees of a slot change (their
// "mm" is one revolution, so 1e-4 rev = 0.036 deg per unit). 0 (default) = complete only at the
// exact target with the ramp stopped, as before. Homing is not affected. Not intended for a
// closed-loop axis: the PID completion waits for the encoder error, this does not.
void callback_set_completion_window()
{
    uint8_t axis = protocol_axis_to_internal(buffer_rx[2]);
    if (axis == 0xFF) return;
    uint16_t units = (uint16_t(buffer_rx[3]) << 8) + uint16_t(buffer_rx[4]);
    int32_t v = tmc4361A_xmmToMicrosteps(&tmc4361[axis], float(units) / 10000.0f);
    completion_window_usteps[axis] = v < 0 ? -v : v;
}

// SET_PID_OPEN_ABOVE (50): [2] protocol axis, [3..4] ramp velocity in 0.01 mm/s. A requested
// closed loop is opened while |VACTUAL| exceeds this and re-engages once the ramp has slowed
// below it (7/8 of it, for hysteresis). The loop misbehaves only when its correction saturates,
// which happens at cruise speed (second bench Z, 2026-09-07: limit cycle at every cruise speed,
// stall from 2.5 mm/s); focus steps of a few um never get there (1 um at 300 mm/s2 peaks at
// 0.55 mm/s), so with the threshold around 1 mm/s they run fully closed-loop with the in-flight
// timing, and only repositioning moves open the loop. 0 (default) is rest-only; a value at or
// above VMAX keeps the loop engaged throughout (the behaviour the Squid+ bench qualified).
// Stored in pps (VACTUAL units) at the current microstep setting.
void callback_set_pid_open_above()
{
    uint8_t axis = protocol_axis_to_internal(buffer_rx[2]);
    if (axis == 0xFF) return;
    uint16_t v_x100 = (uint16_t(buffer_rx[3]) << 8) + uint16_t(buffer_rx[4]);
    // vmmToMicrosteps returns the VMAX register format (8 fractional bits); VACTUAL is integer pps
    int32_t pps = tmc4361A_vmmToMicrosteps(&tmc4361[axis], float(v_x100) / 100.0f) >> 8;
    pid_open_above_pps[axis] = pps < 0 ? -pps : pps;
}

// SET_PID_KEEP_CLOSED_BELOW (52): [2] protocol axis, [3..4] move length in um. Commanded moves up to
// this length run with the loop engaged throughout (see pid_before_move); 0 = off. Stored in usteps
// at the current microstep setting.
void callback_set_pid_keep_closed_below()
{
    uint8_t axis = protocol_axis_to_internal(buffer_rx[2]);
    if (axis == 0xFF) return;
    uint16_t um = (uint16_t(buffer_rx[3]) << 8) + uint16_t(buffer_rx[4]);
    int32_t u = tmc4361A_xmmToMicrosteps(&tmc4361[axis], float(um) / 1000.0f);
    pid_keep_closed_usteps[axis] = u < 0 ? -u : u;
}

// SET_PID_PRECOMP (53): [2] protocol axis, [3..4] residual after counter-increasing moves, [5..6] after
// counter-decreasing moves; int16, 0.01 um, ENC_POS - XACTUAL at rest (the tuner's 'residual' action).
// On the Squid+ Z an open-loop move leaves the stage 3.5 +/- 0.3 um short of the counter, always the
// same way for a given direction (2026-09-08, 96 + 60 + 30 + 30 moves); a rest-only loop then spends
// two to three time constants closing it. Aiming the open-loop ramp past the target by that residual
// lands the stage within ~0.3 um, and rewriting the counter to the true target at rest leaves the loop
// one time constant of work. See pid_before_move / check_closed_loop.
void callback_set_pid_precomp()
{
    uint8_t axis = protocol_axis_to_internal(buffer_rx[2]);
    if (axis == 0xFF) return;
    int16_t pos = int16_t((uint16_t(buffer_rx[3]) << 8) + uint16_t(buffer_rx[4]));
    int16_t neg = int16_t((uint16_t(buffer_rx[5]) << 8) + uint16_t(buffer_rx[6]));
    // signed: xmmToMicrosteps scales by |usteps per mm|, direction is in the sign of the value
    pid_precomp_usteps[axis][1] = tmc4361A_xmmToMicrosteps(&tmc4361[axis], float(pos) / 100000.0f);
    pid_precomp_usteps[axis][0] = tmc4361A_xmmToMicrosteps(&tmc4361[axis], float(neg) / 100000.0f);
}

// SET_PID_TOLERANCE (48): [2] protocol axis, [3..4] loop deadband in 0.01 um, [5..6]
// target-reached tolerance in 0.01 um; 0 keeps the current value. Applied at once if
// the encoder is configured (the two registers are plain writes) and by every later
// CONFIGURE_STAGE_PID. Minimum 1 ustep.
void callback_set_pid_tolerance()
{
    uint8_t axis = protocol_axis_to_internal(buffer_rx[2]);
    if (axis == 0xFF) return;
    uint16_t dead_c = (uint16_t(buffer_rx[3]) << 8) + uint16_t(buffer_rx[4]);
    uint16_t tr_c   = (uint16_t(buffer_rx[5]) << 8) + uint16_t(buffer_rx[6]);
    if (dead_c != 0)
    {
        int32_t v = tmc4361A_xmmToMicrosteps(&tmc4361[axis], float(dead_c) / 100000.0f);
        if (v < 0) v = -v;
        pid_tolerance_usteps[axis] = v < 1 ? 1 : (uint32_t)v;
        if (encoder_configured[axis])
        {
            tmc4361A_writeInt(&tmc4361[axis], TMC4361A_PID_TOLERANCE_WR, pid_tolerance_usteps[axis]);
            tmc4361[axis].pid_tolerance = pid_tolerance_usteps[axis];
        }
    }
    if (tr_c != 0)
    {
        int32_t v = tmc4361A_xmmToMicrosteps(&tmc4361[axis], float(tr_c) / 100000.0f);
        if (v < 0) v = -v;
        pid_tr_tolerance_usteps[axis] = v < 1 ? 1 : (uint32_t)v;
        if (encoder_configured[axis])
        {
            tmc4361A_writeInt(&tmc4361[axis], TMC4361A_CL_TR_TOLERANCE_WR, pid_tr_tolerance_usteps[axis]);
            tmc4361[axis].target_tolerance = pid_tr_tolerance_usteps[axis];
        }
    }
}

// SET_PID_HOME_ZONE (46): [2] protocol axis, [3..4] zone half-width in um around
// the home position (XACTUAL = 0). 0 disables the zone. Inside the zone the loop
// is held open; see check_closed_loop() for the engage / release rules.
void callback_set_pid_home_zone()
{
    uint8_t axis = protocol_axis_to_internal(buffer_rx[2]);
    if (axis == 0xFF) return;
    uint16_t zone_um = (uint16_t(buffer_rx[3]) << 8) + uint16_t(buffer_rx[4]);
    int32_t zone = (zone_um == 0) ? 0 : tmc4361A_xmmToMicrosteps(&tmc4361[axis], float(zone_um) / 1000.0f);
    pid_home_zone_usteps[axis] = (zone < 0) ? -zone : zone;
}

// SET_ENCODER_REPORTING (44): [2] protocol axis, [3] ENCODER_REPORT_* mode.
// Chooses which axis's encoder the status packet carries and how; see
// send_position_update(). Reporting is a read-only diagnostic: it moves nothing.
void callback_set_encoder_reporting()
{
    uint8_t axis = protocol_axis_to_internal(buffer_rx[2]);
    uint8_t mode = buffer_rx[3];

    // Leaving mode 2 must hand the position field back to XACTUAL for every axis.
    X_use_encoder = false;
    Y_use_encoder = false;
    Z_use_encoder = false;

    if (axis == 0xFF || mode == ENCODER_REPORT_OFF || mode > ENCODER_REPORT_ENC_AS_POSITION)
    {
        encoder_report_axis = 0xFF;
        encoder_report_mode = ENCODER_REPORT_OFF;
        return;
    }
    encoder_report_axis = axis;
    encoder_report_mode = mode;
    if (mode == ENCODER_REPORT_ENC_AS_POSITION)
    {
        if (axis == x) X_use_encoder = true;
        else if (axis == y) Y_use_encoder = true;
        else if (axis == z) Z_use_encoder = true;
    }
}

// SET_PID_LIMITS (45): [2] protocol axis, [3..4] max closed-loop correction
// velocity in mm/s x 100, [5..6] deviation watchdog limit in um. A zero field
// keeps the current value. Written to the TMC4361A at once if the encoder is
// configured, and re-applied by every later CONFIGURE_STAGE_PID.
void callback_set_pid_limits()
{
    uint8_t axis = protocol_axis_to_internal(buffer_rx[2]);
    if (axis == 0xFF) return;

    uint16_t v_x100 = (uint16_t(buffer_rx[3]) << 8) + uint16_t(buffer_rx[4]);
    uint16_t dev_um = (uint16_t(buffer_rx[5]) << 8) + uint16_t(buffer_rx[6]);

    if (v_x100 != 0)
    {
        pid_dv_clip_usteps[axis] = (uint32_t)tmc4361A_vmmToMicrosteps(&tmc4361[axis], float(v_x100) / 100.0f);
        if (encoder_configured[axis])
            tmc4361A_set_PID_dv_clip(&tmc4361[axis], pid_dv_clip_usteps[axis]);
    }
    if (dev_um != 0)
        pid_max_dev_usteps[axis] = tmc4361A_xmmToMicrosteps(&tmc4361[axis], float(dev_um) / 1000.0f);
}

void callback_disable_stage_pid()
{
    uint8_t axis = protocol_axis_to_internal(buffer_rx[2]);
    if (axis == 0xFF) return;  // Invalid axis

    tmc4361A_set_PID(&tmc4361[axis], PID_DISABLE);
    stage_PID_enabled[axis] = 0;
    pid_requested[axis] = false;
    pid_zone_hold[axis] = false;
}

// Helper function for filter wheel initialization (shared by W and W2)
static void init_filterwheel_axis(uint8_t axis)
{
    tmc4361A_init(&tmc4361[axis], pin_TMC4361_CS[axis], &tmc4361_configs[axis], tmc4361A_defaultRegisterResetState);
    pinMode(pin_TMC4361_CS[axis], OUTPUT);
    digitalWrite(pin_TMC4361_CS[axis], HIGH);

    // Per-axis driver parameters, re-set on every run: tmc4361A_init() above
    // zeroes r_sense, and a TMC2660 wheel asked for current with r_sense = 0
    // encodes CS = 0, i.e. minimum current.
    tmc4361[axis].r_sense = R_sense_w;
    tmc4361[axis].current_range = CURRENT_RANGE_W;

    // Probe EVERY time this runs, not only on the first INITFILTERWHEEL.
    // tmc4361A_init() on the first line of this function resets driver_type to
    // DRIVER_UNKNOWN, so a cached boot-time verdict does not survive to here;
    // skipping the probe would leave the wheel at DRIVER_UNKNOWN and rejecting
    // every move. This is also the design's second gate path: it re-probes an
    // already-configured TMC2660 (SDOFF = 1, RDSEL = 2), which cold boot never
    // exercises.
    tmc_driver_probe(&tmc4361[axis]);
    tmc_driver_init(&tmc4361[axis], clk_Hz_TMC4361);
#ifdef TMC_PROBE_REPORT_RUNTIME
    // BENCH BUILDS ONLY. This runs mid-session, with the host already reading
    // status packets, and the report is ASCII on that same link. The host
    // accepts any 24-byte window ending in a zero byte, the packets carry
    // buffer_tx[19..21] = 0 and this text carries no zero at all, so a
    // MISALIGNED window is reliably accepted and the host reports a garbage
    // stage position as if it were real. See report_driver_probe() in init.cpp.
    //
    // Enabled with -D TMC_PROBE_REPORT_RUNTIME (platformio.ini) for design
    // section 10 step 0, which needs the raw word from THIS path too: it
    // re-probes an already-configured TMC2660 at SDOFF = 1 / RDSEL = 2, where SG
    // and SE are both zero at standstill, and cold boot never exercises it.
    report_driver_probe(axis);
#endif
    tmc4361A_motor_config(&tmc4361[axis], W_MOTOR_RMS_CURRENT_mA, W_MOTOR_I_HOLD, SCREW_PITCH_W_MM, FULLSTEPS_PER_REV_W, MICROSTEPPING_W);
    tmc4361A_enableLimitSwitch(&tmc4361[axis], lft_sw_pol[axis], LEFT_SW, false);

    // Calculate velocity and acceleration (ensures values are set for both W and W2)
    max_velocity_usteps[axis] = tmc4361A_vmmToMicrosteps(&tmc4361[axis], MAX_VELOCITY_W_mm);
    max_acceleration_usteps[axis] = tmc4361A_ammToMicrosteps(&tmc4361[axis], MAX_ACCELERATION_W_mm);

    tmc4361A_setMaxSpeed(&tmc4361[axis], max_velocity_usteps[axis]);
    tmc4361A_setMaxAcceleration(&tmc4361[axis], max_acceleration_usteps[axis]);
    tmc4361[axis].rampParam[ASTART_IDX] = 0;
    tmc4361[axis].rampParam[DFINAL_IDX] = 0;
    tmc4361A_sRampInit(&tmc4361[axis]);

    tmc4361A_set_PID(&tmc4361[axis], PID_DISABLE);
    stage_PID_enabled[axis] = 0;
    encoder_configured[axis] = false;   // tmc4361A_init() above reset the chip
    pid_fault[axis] = false;
    pid_requested[axis] = false;
    pid_zone_hold[axis] = false;

    // The index flag is enabled as the LEFT stop switch above, but enableHomingLimit()
    // sets STOP_LEFT_IS_HOME, which turns that input into the HOME_REF input. The
    // TMC4361A datasheet rev 1.26, 8.3.4 "Homing with STOPL or STOPR": with stop_left_is_home
    // = 1 "the stop event at STOPL only occurs when the home range is crossed after STOPL
    // becomes active" (home range = X_HOME +/- HOME_SAFETY_MARGIN). This firmware never
    // starts home tracking (START_HOME_TRACKING), so that condition is never met and the
    // flag does not stop the wheel in either direction; homing polls the switch state and
    // uses X_LATCH instead (check_homing_w). Bench-verified 2026-09-07: forward and
    // backward crossings, full turns and single slots, all completed. The host relies on
    // this to take the shortest path between slots (SquidFilterWheel.wrap). The same
    // mechanism is why X/Y/Z are stopped on their switches in software (check_limits).
    tmc4361A_enableHomingLimit(&tmc4361[axis], rht_sw_pol[axis], TMC4361_homing_sw[axis], home_safety_margin[axis]);
    tmc4361A_disableVirtualLimitSwitch(&tmc4361[axis], -1);
    tmc4361A_disableVirtualLimitSwitch(&tmc4361[axis], 1);
}

void callback_initfilterwheel()
{
    enable_filterwheel = true;
    init_filterwheel_axis(w);
}

void callback_initfilterwheel_w2()
{
    enable_filterwheel_w2 = true;
    init_filterwheel_axis(w2);
}

void callback_set_axis_disable_enable()
{
    uint8_t axis = protocol_axis_to_internal(buffer_rx[2]);
    if (axis == 0xFF) return;  // Invalid axis

    int status = buffer_rx[3];
    if (status == 0) {
        tmc_driver_enable(&tmc4361[axis], false);
    }
    else {
        tmc_driver_enable(&tmc4361[axis], true);
    }
}

void callback_set_trigger_mode()
{
    if (buffer_rx[2] <= 1)
        trigger_mode = buffer_rx[2];
}

void callback_initialize()
{
    // reset z target position so that z does not move when "current position" for z is set to 0
    focusPosition = 0;
    first_packet_from_joystick_panel = true;
    // Re-initialise the TMC4361A and its power stage on each stage axis.
    //
    // This path does NOT call tmc4361A_init(), so driver_type still holds the
    // verdict the boot probe cached — and an axis the boot probe could not
    // identify would otherwise stay move-rejecting until someone power-cycles
    // the instrument, since W/W2 are the only axes with a runtime re-probe.
    // INITIALIZE is an explicit re-initialisation command, so it is the right
    // place to give the operator a second look at a dead axis.
    //
    // Only DRIVER_UNKNOWN axes are re-probed. An axis that WAS identified keeps
    // its verdict untouched, which matters for two reasons. It preserves M5 —
    // a probed TMC2660 sees exactly master's SPI traffic on this path, with no
    // probe datagrams inserted ahead of the init. And it keeps the healthy
    // axes away from a read the design has not yet closed: re-probing an
    // already-configured TMC2660 reads it at SDOFF = 1 / RDSEL = 2, where SG
    // and SE are both zero at standstill, and whether that can come back
    // all-zeros is exactly the open question design section 10 step 0 goes to
    // the bench to answer (see TMC4361A.h on driver_probe_raw). If it can, an
    // unconditional re-probe here would let INITIALIZE turn a working stage
    // axis into a rejected one — the opposite of the recovery this is for.
    for (int i = 0; i < STAGE_AXES; i++)
    {
        if (tmc4361[i].driver_type == DRIVER_UNKNOWN)
            tmc_driver_probe(&tmc4361[i]);
        tmc_driver_init(&tmc4361[i], clk_Hz_TMC4361); // set up ICs with SPI control and other parameters
    }

    // Re-apply run current. Master got this for free: tmc4361A_tmc2660_init()
    // ended in tmc4361A_cScaleInit(), which rewrote SGCSCONF from the retained
    // cscaleParam. tmc2240_driver_init() deliberately does the opposite - it
    // seeds IHOLD_IRUN to zero so a 2240 is never energised at an unknown
    // current - and leaves the real value to the caller, so a 2240 axis that
    // was only INITIALIZEd would sit at IRUN = 0 and produce no torque. The
    // host does follow INITIALIZE with cmd 21 today, but that is its ordering,
    // not an invariant of this firmware.
    //
    // On a TMC2660 axis this repeats the cScaleInit the init above just did,
    // from the same struct fields, so the registers land on the same values.
    tmc_driver_set_current(&tmc4361[x], X_MOTOR_RMS_CURRENT_mA, X_MOTOR_I_HOLD);
    tmc_driver_set_current(&tmc4361[y], Y_MOTOR_RMS_CURRENT_mA, Y_MOTOR_I_HOLD);
    tmc_driver_set_current(&tmc4361[z], Z_MOTOR_RMS_CURRENT_mA, Z_MOTOR_I_HOLD);

    // enable limit switch reading
    tmc4361A_enableLimitSwitch(&tmc4361[x], lft_sw_pol[x], LEFT_SW, flip_limit_switch_x);
    tmc4361A_enableLimitSwitch(&tmc4361[x], rht_sw_pol[x], RGHT_SW, flip_limit_switch_x);
    tmc4361A_enableLimitSwitch(&tmc4361[y], lft_sw_pol[y], LEFT_SW, flip_limit_switch_y);
    tmc4361A_enableLimitSwitch(&tmc4361[y], rht_sw_pol[y], RGHT_SW, flip_limit_switch_y);
    tmc4361A_enableLimitSwitch(&tmc4361[z], rht_sw_pol[z], RGHT_SW, false);
    tmc4361A_enableLimitSwitch(&tmc4361[z], lft_sw_pol[z], LEFT_SW, false);

    // motion profile
    uint32_t max_velocity_usteps[STAGE_AXES];
    uint32_t max_acceleration_usteps[STAGE_AXES];
    max_acceleration_usteps[x] = tmc4361A_ammToMicrosteps(&tmc4361[x], MAX_ACCELERATION_X_mm);
    max_acceleration_usteps[y] = tmc4361A_ammToMicrosteps(&tmc4361[y], MAX_ACCELERATION_Y_mm);
    max_acceleration_usteps[z] = tmc4361A_ammToMicrosteps(&tmc4361[z], MAX_ACCELERATION_Z_mm);

    max_velocity_usteps[x] = tmc4361A_vmmToMicrosteps(&tmc4361[x], MAX_VELOCITY_X_mm);
    max_velocity_usteps[y] = tmc4361A_vmmToMicrosteps(&tmc4361[y], MAX_VELOCITY_Y_mm);
    max_velocity_usteps[z] = tmc4361A_vmmToMicrosteps(&tmc4361[z], MAX_VELOCITY_Z_mm);

    for (int i = 0; i < STAGE_AXES; i++) {
        // initialize ramp with default values
        tmc4361A_setMaxSpeed(&tmc4361[i], max_velocity_usteps[i]);
        tmc4361A_setMaxAcceleration(&tmc4361[i], max_acceleration_usteps[i]);
        tmc4361[i].rampParam[ASTART_IDX] = 0;
        tmc4361[i].rampParam[DFINAL_IDX] = 0;
        tmc4361A_sRampInit(&tmc4361[i]);

        // tmc_driver_init() above reset the TMC4361A, which wiped ENC_IN_RES and
        // REGULATION_MODUS: the loop is off in hardware and the encoder scale is
        // gone, so the host must CONFIGURE_STAGE_PID again before ENABLE_STAGE_PID.
        // Keep the bookkeeping honest about that.
        stage_PID_enabled[i] = 0;
        encoder_configured[i] = false;
        pid_fault[i] = false;
        pid_requested[i] = false;
        pid_zone_hold[i] = false;
    }
    encoder_report_axis = 0xFF;
    encoder_report_mode = ENCODER_REPORT_OFF;
    X_use_encoder = false;
    Y_use_encoder = false;
    Z_use_encoder = false;

    // homing switch settings
    tmc4361A_enableHomingLimit(&tmc4361[x], lft_sw_pol[x], TMC4361_homing_sw[x], home_safety_margin[x]);
    tmc4361A_enableHomingLimit(&tmc4361[y], lft_sw_pol[y], TMC4361_homing_sw[y], home_safety_margin[y]);
    tmc4361A_enableHomingLimit(&tmc4361[z], rht_sw_pol[z], TMC4361_homing_sw[z], home_safety_margin[z]);

    // DAC init
    set_DAC8050x_config();
    set_DAC8050x_default_gain();

    // reset trigger mode to normal
    trigger_mode = 0;
}

void callback_reset()
{
    mcu_cmd_execution_in_progress = false;
    X_commanded_movement_in_progress = false;
    Y_commanded_movement_in_progress = false;
    Z_commanded_movement_in_progress = false;
    W_commanded_movement_in_progress = false;
    W2_commanded_movement_in_progress = false;
    is_homing_X = false;
    is_homing_Y = false;
    is_homing_Z = false;
    is_homing_W = false;
    is_homing_W2 = false;
    is_homing_XY = false;
    home_X_found = false;
    home_Y_found = false;
    home_Z_found = false;
    home_W_found = false;
    home_W2_found = false;
    is_preparing_for_homing_X = false;
    is_preparing_for_homing_Y = false;
    is_preparing_for_homing_Z = false;
    is_preparing_for_homing_W = false;
    is_preparing_for_homing_W2 = false;
    cmd_id = 0;
    trigger_mode = 0;

    // Encoder diagnostics are a host-session thing: a fresh host must see the
    // shipping packet until it asks otherwise. Watchdog faults are cleared with it.
    encoder_report_axis = 0xFF;
    encoder_report_mode = ENCODER_REPORT_OFF;
    X_use_encoder = false;
    Y_use_encoder = false;
    Z_use_encoder = false;
    for (uint8_t i = 0; i < TOTAL_AXES; i++)
    {
        pid_fault[i] = false;
        pid_requested[i] = false;
        pid_zone_hold[i] = false;
        pid_realign_pending[i] = false;
        // The loop CONFIGURATION goes back to the firmware defaults as well. The host sends
        // only the values it wants to change (0 = keep the default), so anything a tool or an
        // earlier session left here would otherwise survive the reset: on 2026-09-08 a 3.5 mm/s
        // loop-mode threshold outlived a RESET and a "rest-only" test ran engaged in flight.
        pid_max_dev_usteps[i] = 0;
        pid_dv_clip_usteps[i] = 0;
        pid_home_zone_usteps[i] = 0;
        pid_tolerance_usteps[i] = 0;
        pid_tr_tolerance_usteps[i] = 0;
        completion_window_usteps[i] = 0;
        pid_open_above_pps[i] = 0;
        pid_keep_closed_usteps[i] = 0;
        pid_short_move[i] = false;
        pid_precomp_usteps[i][0] = 0;
        pid_precomp_usteps[i][1] = 0;
        pid_true_target_pending[i] = false;
    }
}
