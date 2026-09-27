#include "move_settle.h"

#include "operations.h"   // pid_axis_is_homing, pid_realign_now, pid_raise_fault, pid_fail_move
#include "pid_policy.h"   // pid_in_home_zone

uint8_t loop_strategy[TOTAL_AXES] = {0};
SettleConfig settle_config[TOTAL_AXES];
SettleState settle_state[TOTAL_AXES] = {};

// The parameters the move in flight runs with: resolved once when it begins, so a SET_* command
// arriving mid-move cannot change the rules of a sequence that is half done.
static SettleParams settle_params[TOTAL_AXES];
// A dropped move-and-settle leaves the counter off the host's frame by this much until the ramp is idle
// and it can be re-based (settle_drop with keep_frame).
static int32_t settle_unframe[TOTAL_AXES] = {0};

// A ramp the chip stopped short (stop switch, virtual limit) never reports TARGET_REACHED, so
// tmc4361A_isRunning() stays true for ever. While a wait on the ramp lasts, XACTUAL is sampled at this
// spacing; a counter that has not moved a ustep for SETTLE_STALL_REST_US is a ramp that is not running,
// whatever the status flags say (50 ms: far beyond the slowest start of a bow-limited S-ramp). At the
// leg's target that is an arrival like any other; anywhere else the ramp was stopped short.
#define SETTLE_STALL_SAMPLE_US 25000u
#define SETTLE_STALL_REST_US   50000u
static SettleRestWatch settle_stall[TOTAL_AXES] = {};
static uint32_t settle_stall_t_us[TOTAL_AXES] = {0};

// The backstop behind every wait there is, here and in check_position(): a commanded move whose counter
// has not moved for this long is not going to finish on its own (the longest rest a healthy move has is
// its correction budget, 1 s from the first ramp stop, and every leg inside it moves the counter). It
// fails like a missed move - CMD_EXECUTION_ERROR, nothing latched - instead of staying IN_PROGRESS for ever.
#define SETTLE_CMD_REST_US 1500000u
static SettleRestWatch settle_cmd_rest[TOTAL_AXES] = {};
static uint32_t settle_cmd_rest_t_us[TOTAL_AXES] = {0};

// A cached window mean is the better plan start than a fresh single read (it resolves a fraction
// of a count) - for as long as the stage can be trusted not to have drifted under it.
#define SETTLE_MEAN_MAX_AGE_US 2000000u

static void settle_resolve(uint8_t axis, SettleParams *p);
static bool settle_encoder_is_evidence(uint8_t axis, int32_t host_pos);
#define SETTLE_FRESH_READS     4

// MOTION THE POLICY DID NOT ISSUE, WHILE ARMED (the focus wheel). Its ramps run open loop, so when it
// stops the counter - which IS the host's z - can be off the stage by the lost motion (~1.5 um on the
// Squid+ Z, depending on which way the wheel went last), and a focus the operator found by eye and
// the host saved as that z would be returned to at the wrong plane. So an episode is kept open from
// the first wheel ramp: where the encoder and the counter were when it started, and once the axis has
// been quiet for SETTLE_EXT_QUIET_US the stage is measured like any landing (wait, then a window) and
//   - the COUNTER IS RE-BASED ONTO THE ENCODER, without moving anything: the operator put the stage
//     where it should be; it is the number that was wrong;
//   - the play state follows from (encoder travel - scaled counter travel) since the start
//     (settle_observe_external), so the next move does not start from "unknown".
// A command that arrives before that closes the episode on the spot from a few fresh reads
// (settle_close_external): a relative move must start from where the stage IS.
#define SETTLE_EXT_QUIET_US       60000u
#define SETTLE_EXT_CLOSE_READS    8
static bool     ext_open[TOTAL_AXES] = {false};
static bool     ext_ref_valid[TOTAL_AXES] = {false};
static int32_t  ext_ref_enc_x16[TOTAL_AXES] = {0};
static int32_t  ext_ref_counter[TOTAL_AXES] = {0};
static int64_t  ext_travel_abs[TOTAL_AXES] = {0};
static int8_t   ext_last_dir[TOTAL_AXES] = {0};
static uint8_t  ext_phase[TOTAL_AXES] = {0};            // 0 = waiting for quiet, 1 = window
static uint32_t ext_t_us[TOTAL_AXES] = {0};             // quiet since / window opens at
static int64_t  ext_sum[TOTAL_AXES] = {0};
static uint16_t ext_n[TOTAL_AXES] = {0};
// Re-basing the counter onto an encoder that does not follow would let the axis walk while z reads
// constant, and the travel limits - which live on the counter - would stop meaning anything. So the
// episodes keep the same account the policy keeps for its own legs: net counter travel against net
// encoder travel since the encoder was last seen to follow; min_drive of the one with less than an
// eighth of it in the other is PID_FAULT_NO_RESPONSE, and nothing is re-based.
static bool     ext_resp_open[TOTAL_AXES] = {false};
static int64_t  ext_resp_drive[TOTAL_AXES] = {0};
static int32_t  ext_resp_enc_x16[TOTAL_AXES] = {0};

void settle_config_default(uint8_t axis)
{
  SettleParams d;
  settle_params_default(&d);
  SettleConfig *c = &settle_config[axis];
  c->wait_half_ms = (uint8_t)(d.wait_us / 500u);
  c->window_half_ms = (uint8_t)(d.window_us / 500u);
  c->gain_x16 = d.gain_x16;
  c->max_trims = d.max_trims;
  c->max_reapproaches = d.max_reapproaches;
  c->bias_sigma_x16 = d.bias_sigma_x16;
  c->approach = d.approach;
  c->lost_motion_c_um = 0;
  c->carry_c_um = 0;
  c->full_push_c_um = 94;           // 10 usteps at 16 usteps/FS on a 0.3 mm screw
  c->learn_x16 = d.learn_x16;
  c->bias_c_um = 0;
  c->backoff_d_um = 30;             // 3 um: three times the lost motion measured on the Squid+ Z
  c->split_half_period_10us = 0;
  c->split_first_x256 = 128;
  c->split_max_um = 0;
  c->scale_ppm = 0;
  c->tol_over_c_um = 28;            // 0.28 um past the target is accepted (three 16-usteps/FS usteps on a 0.3 mm screw)
  c->quiet_pp_c_um = 0;
  c->max_quiet_windows = 0;
  c->finish_usteps = 0;             // off: a long leg goes to the target in one, as before
  c->finish_from_16usteps = 0;
}

void settle_model_reseed(uint8_t axis)
{
  if (axis < TOTAL_AXES) settle_state[axis].model_init = false;
}

bool settle_selected(uint8_t axis)
{
  return axis < TOTAL_AXES && loop_strategy[axis] == LOOP_STRATEGY_MOVE_SETTLE;
}

bool settle_armed(uint8_t axis)
{
  return settle_selected(axis) && pid_requested[axis] && encoder_configured[axis] && !pid_fault[axis];
}

bool settle_axis_busy(uint8_t axis)
{
  return axis < TOTAL_AXES && (settle_busy(&settle_state[axis]) || settle_unframe[axis] != 0);
}

int32_t settle_axis_offset(uint8_t axis)
{
  if (axis >= TOTAL_AXES) return 0;
  return settle_busy(&settle_state[axis]) ? settle_offset(&settle_state[axis]) : settle_unframe[axis];
}

void settle_external_motion(uint8_t axis, int32_t travel_usteps)
{
  if (axis >= TOTAL_AXES || !settle_selected(axis)) return;
  settle_resolve(axis, &settle_params[axis]);
  SettleState *s = &settle_state[axis];
  TMC4361ATypeDef *chip = &tmc4361[axis];
  if (!settle_armed(axis) || settle_busy(s) || settle_unframe[axis] != 0 || travel_usteps == 0)
  {
    // not armed (an open-loop command, the loop disabled): the encoder is not being acted on, the
    // old rule stands - a long ramp leaves the stage carried on its flank, anything else unknown
    ext_open[axis] = false;
    settle_note_external_motion(s, &settle_params[axis], travel_usteps);
    return;
  }
  uint32_t now = micros();
  if (!ext_open[axis])
  {
    // the episode starts here, BEFORE the ramp is issued: the stage is where the last window found it
    // (or is read now), the counter is the host's frame, and side / gap are kept as its start state
    int32_t counter = tmc4361A_currentPosition(chip);
    bool at_rest = !tmc4361A_isRunning(chip, 0);
    ext_open[axis] = true;
    ext_ref_counter[axis] = counter;
    ext_ref_valid[axis] = at_rest && settle_encoder_is_evidence(axis, counter);
    if (ext_ref_valid[axis])
    {
      if (s->last_mean_valid) ext_ref_enc_x16[axis] = s->last_mean_x16;
      else
      {
        int32_t sum = 0;
        for (uint8_t k = 0; k < SETTLE_FRESH_READS; k++) sum += tmc4361A_readInt(chip, TMC4361A_ENC_POS);
        ext_ref_enc_x16[axis] = (int32_t)settle_div_round((int64_t)sum * SETTLE_X16, SETTLE_FRESH_READS);
      }
    }
    ext_travel_abs[axis] = 0;
  }
  ext_travel_abs[axis] += travel_usteps < 0 ? -(int64_t)travel_usteps : travel_usteps;
  ext_last_dir[axis] = travel_usteps > 0 ? 1 : -1;
  ext_phase[axis] = 0; ext_t_us[axis] = now;
  s->last_mean_valid = false;
  s->leg_dir = 0; s->leg_learn = false;
  s->last_dir = ext_last_dir[axis];
}

// The episode ends with the stage measured at mean_x16: play state, then the counter onto the encoder.
static void settle_ext_finish(uint8_t axis, int32_t mean_x16, uint32_t now_us)
{
  TMC4361ATypeDef *chip = &tmc4361[axis];
  SettleState *s = &settle_state[axis];
  const SettleParams *p = &settle_params[axis];
  ext_open[axis] = false;
  int32_t counter = tmc4361A_currentPosition(chip);
  int64_t off_x16 = (int64_t)mean_x16 - (int64_t)counter * SETTLE_X16;
  if (!settle_encoder_is_evidence(axis, counter)
      || (pid_max_dev_usteps[axis] > 0 && settle_abs64(off_x16) > (int64_t)pid_max_dev_usteps[axis] * SETTLE_X16))
  {
    // the home zone, a realignment still owed, or further apart than the watchdog allows: not the
    // wheel's lost motion. Nothing is re-based; the next move judges it (and faults if it must).
    settle_note_external_motion(s, p, 0);
    return;
  }
  if (ext_ref_valid[axis])
  {
    if (!ext_resp_open[axis]) { ext_resp_open[axis] = true; ext_resp_drive[axis] = 0; ext_resp_enc_x16[axis] = ext_ref_enc_x16[axis]; }
    ext_resp_drive[axis] += (int64_t)counter - ext_ref_counter[axis];
    if (settle_abs64(ext_resp_drive[axis]) >= p->min_drive_usteps)
    {
      int64_t moved = settle_abs64((int64_t)mean_x16 - ext_resp_enc_x16[axis]);
      if (moved * 8 < settle_abs64(ext_resp_drive[axis]) * SETTLE_X16)
      {
        ext_resp_open[axis] = false;
        pid_raise_fault(axis, PID_FAULT_NO_RESPONSE);   // opens, stops, latches; the wheel is refused until acknowledged
        return;
      }
      ext_resp_open[axis] = false;                      // it follows: the account starts over
    }
  }
  // the scale is trusted to a quarter of a full step over 2000 of them (0.75 mm on a 0.3 mm screw)
  bool reckon = ext_ref_valid[axis] && ext_travel_abs[axis] <= (int64_t)2000 * p->learn_clip_usteps * 4;
  settle_observe_external(s, p, counter - ext_ref_counter[axis], (int64_t)mean_x16 - ext_ref_enc_x16[axis],
                          ext_last_dir[axis], reckon);
  int32_t here = (int32_t)settle_div_round(mean_x16, SETTLE_X16);
  if (here != counter)
  {
    tmc4361A_rebase_position(chip, here);
    if (axis == z) focusPosition += here - counter;   // the wheel's target lives in the same frame
  }
  s->last_mean_x16 = mean_x16; s->last_mean_valid = true; s->last_mean_us = now_us;
}

// ENABLE, in mid-session: whatever moved the stage since the policy last knew it, it was not the policy.
// The counter adopts the encoder's reading; nothing moves. The stage's place in the play stays what the
// open-loop ramps left it as (settle_note_external_motion: carried on the flank of a long ramp, unknown
// after a short one) - bench 2026-09-20: calling it unknown here cost the first plane 0.3-0.7 um and the
// model drifted for the rest of the series (11 back-offs and a miss in 150 moves, against none).
void settle_adopt_position(uint8_t axis)
{
  if (axis >= TOTAL_AXES || !settle_armed(axis)) return;
  TMC4361ATypeDef *chip = &tmc4361[axis];
  SettleState *s = &settle_state[axis];
  settle_resolve(axis, &settle_params[axis]);
  ext_open[axis] = false; ext_resp_open[axis] = false;
  s->last_mean_valid = false; s->leg_dir = 0; s->leg_learn = false;
  if (settle_busy(s) || settle_unframe[axis] != 0 || tmc4361A_isRunning(chip, 0)) return;
  int32_t counter = tmc4361A_currentPosition(chip);
  if (!settle_encoder_is_evidence(axis, counter)) return;       // the home zone, a realignment owed: the frames are not comparable here
  int32_t sum = 0;
  for (uint8_t k = 0; k < SETTLE_EXT_CLOSE_READS; k++) sum += tmc4361A_readInt(chip, TMC4361A_ENC_POS);
  int32_t mean_x16 = (int32_t)settle_div_round((int64_t)sum * SETTLE_X16, SETTLE_EXT_CLOSE_READS);
  int32_t here = (int32_t)settle_div_round(mean_x16, SETTLE_X16);
  if (pid_max_dev_usteps[axis] > 0 && settle_abs64((int64_t)here - counter) > pid_max_dev_usteps[axis]) return;
  if (here != counter)
  {
    tmc4361A_rebase_position(chip, here);
    if (axis == z) focusPosition += here - counter;
  }
  s->last_mean_x16 = mean_x16; s->last_mean_valid = true; s->last_mean_us = micros();
}

// A command is about to be planned: if wheel motion is still unaccounted for, account for it now.
void settle_close_external(uint8_t axis)
{
  if (axis >= TOTAL_AXES || !ext_open[axis]) return;
  TMC4361ATypeDef *chip = &tmc4361[axis];
  if (!settle_armed(axis) || settle_busy(&settle_state[axis]) || tmc4361A_isRunning(chip, 0))
  {
    // still moving (or no longer ours to judge): this command is planned on the counter, as before
    ext_open[axis] = false;
    settle_note_external_motion(&settle_state[axis], &settle_params[axis], 0);
    return;
  }
  int32_t sum = 0;
  for (uint8_t k = 0; k < SETTLE_EXT_CLOSE_READS; k++) sum += tmc4361A_readInt(chip, TMC4361A_ENC_POS);
  settle_ext_finish(axis, (int32_t)settle_div_round((int64_t)sum * SETTLE_X16, SETTLE_EXT_CLOSE_READS), micros());
}

// One pass of an open episode, at rest between moves: quiet long enough -> wait -> window -> finish
static void settle_ext_service(uint8_t axis)
{
  TMC4361ATypeDef *chip = &tmc4361[axis];
  const SettleParams *p = &settle_params[axis];
  uint32_t now = micros();
  if (tmc4361A_isRunning(chip, 0) || (axis == z && focus_wheel_pending))
  {
    ext_phase[axis] = 0; ext_t_us[axis] = now;
    return;
  }
  if (ext_phase[axis] == 0)
  {
    if ((uint32_t)(now - ext_t_us[axis]) < SETTLE_EXT_QUIET_US) return;
    ext_phase[axis] = 1; ext_t_us[axis] = now + p->wait_us; ext_sum[axis] = 0; ext_n[axis] = 0;
    return;
  }
  if ((int32_t)(now - ext_t_us[axis]) < 0) return;                       // wrap-safe "window not open yet"
  ext_sum[axis] += tmc4361A_readInt(chip, TMC4361A_ENC_POS); ext_n[axis]++;
  if ((uint32_t)(now - ext_t_us[axis]) < p->window_us || ext_n[axis] < p->min_samples) return;
  settle_ext_finish(axis, (int32_t)settle_div_round(ext_sum[axis] * SETTLE_X16, ext_n[axis]), now);
}

// Where the stage IS, in the host's frame, for a move that is to be relative to that (laser
// autofocus: the correction was measured from the stage's real position, not from the last target -
// which the stage may sit anywhere inside the accepted band of). `fallback` when it cannot be said.
int32_t settle_measured_position(uint8_t axis, int32_t fallback)
{
  if (axis >= TOTAL_AXES || !settle_armed(axis)) return fallback;
  TMC4361ATypeDef *chip = &tmc4361[axis];
  SettleState *s = &settle_state[axis];
  if (settle_busy(s) || settle_unframe[axis] != 0 || tmc4361A_isRunning(chip, 0)) return fallback;
  if (!settle_encoder_is_evidence(axis, fallback)) return fallback;
  int32_t sum = 0;
  for (uint8_t k = 0; k < SETTLE_FRESH_READS; k++) sum += tmc4361A_readInt(chip, TMC4361A_ENC_POS);
  int32_t enc_x16 = (int32_t)settle_div_round((int64_t)sum * SETTLE_X16, SETTLE_FRESH_READS);
  if (s->last_mean_valid && (uint32_t)(micros() - s->last_mean_us) < SETTLE_MEAN_MAX_AGE_US
      && settle_abs64((int64_t)enc_x16 - s->last_mean_x16) <= (int64_t)settle_params[axis].learn_clip_usteps * SETTLE_X16 * 3 / 8)
    enc_x16 = s->last_mean_x16;
  int32_t here = (int32_t)settle_div_round(enc_x16, SETTLE_X16);
  if (pid_max_dev_usteps[axis] > 0 && settle_abs64((int64_t)here - fallback) > pid_max_dev_usteps[axis]) return fallback;
  return here;
}

// The target the last move-and-settle move resolved. For a move relative to the MEASURED position that is
// (encoder + correction), which the host cannot know: its own retry to (counter + correction) would move the
// plane by whatever the counter was off the stage (up to the accepted band). Offered only after a MISSED move:
// rep_missed is set by settle_miss and cleared by the next settle_begin, and `target` stands until then
// (settle_give_up and settle_drop touch neither).
bool settle_last_target(uint8_t axis, int32_t *target)
{
  if (axis >= TOTAL_AXES || !settle_armed(axis) || settle_axis_busy(axis)) return false;
  const SettleState *s = &settle_state[axis];
  if (!s->rep_missed || !settle_encoder_is_evidence(axis, s->target)) return false;
  *target = s->target;
  return true;
}

// What the status packet shows as the axis position. At rest and during commanded moves: the counter,
// in the host's frame (nominal targets, nothing flickering). While focus-wheel motion is unaccounted
// for: the ENCODER - the counter is off the stage by the lost motion then, and when the episode closes
// it is re-based onto this very number, so the display follows the stage and never jumps.
int32_t settle_report_position(uint8_t axis, int32_t counter)
{
  if (axis >= TOTAL_AXES || !settle_selected(axis)) return counter;
  if (ext_open[axis] && settle_armed(axis) && !settle_busy(&settle_state[axis]) && settle_encoder_is_evidence(axis, counter))
  {
    int32_t enc = tmc4361A_readInt(&tmc4361[axis], TMC4361A_ENC_POS);
    if (pid_max_dev_usteps[axis] == 0 || settle_abs64((int64_t)enc - counter) <= pid_max_dev_usteps[axis]) return enc;
    return counter;
  }
  return counter - settle_axis_offset(axis);
}

void settle_drop(uint8_t axis, bool keep_frame)
{
  if (axis >= TOTAL_AXES) return;
  SettleState *s = &settle_state[axis];
  settle_unframe[axis] = (keep_frame && settle_busy(s)) ? settle_offset(s) : 0;
  settle_rest_reset(&settle_stall[axis]);   // the wait for the ramp it leaves running starts here
  settle_abort(s);
  ext_open[axis] = false;
  settle_note_external_motion(s, &settle_params[axis], 0);
}

// um (or, on a wheel, 1e-3 rev) to usteps, to nearest
static int32_t settle_um_to_usteps(uint8_t axis, float um)
{
  TMC4361ATypeDef *chip = &tmc4361[axis];
  if (chip->threadPitch <= 0.0f) return 0;
  float u = um * 0.001f * (float)((uint32_t)chip->microsteps * (uint32_t)chip->stepsPerRev) / chip->threadPitch;
  return (int32_t)(u + 0.5f);
}

static void settle_resolve(uint8_t axis, SettleParams *p)
{
  const SettleConfig *c = &settle_config[axis];
  TMC4361ATypeDef *chip = &tmc4361[axis];
  settle_params_default(p);
  // The acceptance band is the target-reached tolerance CONFIGURE_STAGE_PID / SET_PID_TOLERANCE hold
  // for the axis (two encoder counts by default); the chip's deadband has no meaning here.
  p->tol_usteps = chip->target_tolerance > 0 ? (int32_t)chip->target_tolerance
                : (chip->pid_tolerance > 0 ? (int32_t)chip->pid_tolerance : 25);
  p->tol_over_usteps = settle_um_to_usteps(axis, c->tol_over_c_um * 0.01f);
  p->wait_us = (uint32_t)c->wait_half_ms * 500u;
  p->window_us = (uint32_t)(c->window_half_ms ? c->window_half_ms : 1) * 500u;
  p->gain_x16 = c->gain_x16 ? c->gain_x16 : 1;
  p->max_trims = c->max_trims;
  p->max_reapproaches = c->max_reapproaches;
  p->approach = c->approach;
  p->lost_motion_usteps = settle_um_to_usteps(axis, c->lost_motion_c_um * 0.01f);
  p->carry_usteps = settle_um_to_usteps(axis, c->carry_c_um * 0.01f);
  p->fast_min_usteps = settle_um_to_usteps(axis, c->full_push_c_um * 0.01f);
  if (p->fast_min_usteps < 1) p->fast_min_usteps = 1;
  p->learn_x16 = c->learn_x16;
  p->learn_clip_usteps = (int32_t)chip->microsteps / 4;
  if (p->learn_clip_usteps < 1) p->learn_clip_usteps = 1;
  // the screw's lead against the encoder's scale: seeded by the host, learned from continuing legs of
  // 128 quarter-steps (48 um on a 0.3 mm screw) up, in full from 1024 (0.38 mm)
  p->scale_ppm = c->scale_ppm;
  p->scale_min_usteps = 128 * p->learn_clip_usteps;
  p->scale_ref_usteps = 1024 * p->learn_clip_usteps;
  // what may be learned: a carry of up to one full step, lost motion of up to two
  p->carry_max_usteps = (int32_t)chip->microsteps;
  p->lost_max_usteps = 2 * (int32_t)chip->microsteps;
  p->bias_usteps = settle_um_to_usteps(axis, c->bias_c_um * 0.01f);
  p->bias_sigma_x16 = c->bias_sigma_x16;
  p->backoff_usteps = settle_um_to_usteps(axis, c->backoff_d_um * 0.1f);
  // a back-off that does not clear the lost motion ends on the wrong flank: keep it beyond
  int32_t backoff_min = p->lost_motion_usteps + 4 * p->tol_usteps;
  if (p->backoff_usteps < backoff_min) p->backoff_usteps = backoff_min;
  p->max_dev_usteps = pid_max_dev_usteps[axis];
  // the same floor the loop's response watch uses: two full steps, at least eight tolerances
  p->min_drive_usteps = 2 * (int32_t)chip->microsteps;
  if (p->min_drive_usteps < 8 * p->tol_usteps) p->min_drive_usteps = 8 * p->tol_usteps;
  p->split_half_period_us = (uint32_t)c->split_half_period_10us * 10u;
  p->split_first_x256 = c->split_first_x256 ? c->split_first_x256 : 128;
  p->split_max_usteps = settle_um_to_usteps(axis, (float)c->split_max_um);
  p->quiet_pp_usteps = settle_um_to_usteps(axis, c->quiet_pp_c_um * 0.01f);
  p->max_quiet_windows = c->max_quiet_windows;
  // long legs end with a short finishing leg: both arrive in usteps (the host converted them), so a later
  // CONFIGURE_STEPPER_DRIVER is NOT honoured here - the host resends them with the rest of the settings
  p->finish_usteps = (int32_t)c->finish_usteps;
  p->finish_from_usteps = (int32_t)c->finish_from_16usteps * 16;
}

// Every ramp target stays inside the host's travel limits AND the range tmc4361A_moveTo() accepts
static void settle_limits(uint8_t axis, int32_t *lo, int32_t *hi)
{
  long l = INT32_MIN, h = INT32_MAX;
  if (axis == x)      { l = X_NEG_LIMIT; h = X_POS_LIMIT; }
  else if (axis == y) { l = Y_NEG_LIMIT; h = Y_POS_LIMIT; }
  else if (axis == z) { l = Z_NEG_LIMIT; h = Z_POS_LIMIT; }
  if (l < tmc4361[axis].xmin) l = tmc4361[axis].xmin;
  if (h > tmc4361[axis].xmax) h = tmc4361[axis].xmax;
  *lo = (int32_t)l; *hi = (int32_t)h;
}

// The encoder may be acted on for a stage resting at `host_pos`: frames aligned since the last
// homing, no homing in progress, outside the home zone (pid_policy.h: inside it the stage may be on
// its stop while the actuator moves, and what reads as an error is a mechanical gap).
static bool settle_encoder_is_evidence(uint8_t axis, int32_t host_pos)
{
  return !pid_realign_pending[axis] && !pid_axis_is_homing(axis)
      && !pid_in_home_zone(pid_home_zone_usteps[axis], host_pos);
}

// Could not finish (a leg the chip refused, a ramp it stopped short): put the counter back on the
// host's frame at `host_pos` and fail the command - COMPLETED would claim a position it does not have.
static void settle_give_up(uint8_t axis, int32_t host_pos)
{
  tmc4361A_rebase_position(&tmc4361[axis], host_pos);
  if (axis == z) focusPosition = host_pos;
  settle_abort(&settle_state[axis]);
  settle_note_external_motion(&settle_state[axis], &settle_params[axis], 0);
  pid_fail_move(axis);
}

// `ramp_idle`: the caller KNOWS the ramp is idle (it just measured at rest), so DONE may re-base at once
static int8_t settle_execute(uint8_t axis, const SettleActions *a, bool ramp_idle)
{
  TMC4361ATypeDef *chip = &tmc4361[axis];
  SettleState *s = &settle_state[axis];
  if (a->fault)
  {
    pid_raise_fault(axis, a->fault);   // opens, stops, latches, fails the move - and drops this move-and-settle
    return ERR_OUT_OF_RANGE;
  }
  if (a->stopped_short || a->missed)
  {
    // stopped short: the chip ended the ramp (switch, virtual limit). Missed: the corrections ran out
    // with the encoder answering. Either way the counter goes to where the stage IS, the command
    // fails, and nothing is latched: the axis stays usable and the host decides what to do about it.
    settle_give_up(axis, a->rebase_to);
    return ERR_OUT_OF_RANGE;
  }
  if (a->move)
  {
    int8_t rc = tmc4361A_moveTo(chip, a->move_to);
    if (rc != NO_ERR)
    {
      // cannot happen with settle_limits(), which includes moveTo's own range; if it does, the
      // stage is where the last window found it
      int32_t here = s->last_mean_valid ? (int32_t)settle_div_round(s->last_mean_x16, SETTLE_X16)
                                        : tmc4361A_currentPosition(chip) - settle_offset(s);
      settle_give_up(axis, here);
      return rc;
    }
  }
  if (a->move) settle_rest_reset(&settle_stall[axis]);   // a new leg: the stopped-ramp watch starts over
  if (a->done && s->x_leg != a->rebase_to)
  {
    if (ramp_idle)
      tmc4361A_rebase_position(chip, a->rebase_to);   // at rest at x_leg: XACTUAL is the host's frame again
    else
    {
      settle_unframe[axis] = s->x_leg - a->rebase_to;  // not known to be at rest: check_move_settle() re-bases once it is
      settle_rest_reset(&settle_stall[axis]);
    }
  }
  return NO_ERR;
}

// A wait on the ramp generator, bounded: true once the counter has been at rest for SETTLE_STALL_REST_US
// (XACTUAL read every SETTLE_STALL_SAMPLE_US, not every pass - a register read is 0.3 ms).
static bool settle_ramp_at_rest(uint8_t axis, uint32_t now_us, int32_t *xactual)
{
  if (settle_stall[axis].armed && (uint32_t)(now_us - settle_stall_t_us[axis]) < SETTLE_STALL_SAMPLE_US) return false;
  int32_t xa = tmc4361A_currentPosition(&tmc4361[axis]);
  settle_stall_t_us[axis] = now_us;
  if (!settle_rest_step(&settle_stall[axis], xa, now_us, SETTLE_STALL_REST_US)) return false;
  *xactual = xa;
  return true;
}

// The backstop (SETTLE_CMD_REST_US). Whatever the axis was waiting for, it ends here: the counter goes
// back to the host's frame where it stands - which also takes the target from any ramp still owed -
// and the command fails, nothing latched.
static bool settle_command_timed_out(uint8_t axis)
{
  if (!pid_move_in_progress(axis) || pid_axis_is_homing(axis))
  {
    settle_rest_reset(&settle_cmd_rest[axis]);
    return false;
  }
  uint32_t now = micros();
  if (settle_cmd_rest[axis].armed && (uint32_t)(now - settle_cmd_rest_t_us[axis]) < SETTLE_STALL_SAMPLE_US) return false;
  int32_t xa = tmc4361A_currentPosition(&tmc4361[axis]);
  settle_cmd_rest_t_us[axis] = now;
  if (!settle_rest_step(&settle_cmd_rest[axis], xa, now, SETTLE_CMD_REST_US)) return false;
  settle_rest_reset(&settle_cmd_rest[axis]);
  int32_t host_pos = xa - settle_axis_offset(axis);
  settle_unframe[axis] = 0;
  ext_open[axis] = false;
  settle_give_up(axis, host_pos);
  return true;
}

int8_t settle_move_to(uint8_t axis, int32_t target)
{
  TMC4361ATypeDef *chip = &tmc4361[axis];
  SettleState *s = &settle_state[axis];

  ext_resp_open[axis] = false;   // a commanded move keeps its own response account (the policy's)
  // a dropped move-and-settle still owes its re-base: settle it into this move's bookkeeping
  bool was_busy = settle_busy(s);
  int32_t owed = settle_unframe[axis];
  settle_unframe[axis] = 0;

  settle_resolve(axis, &settle_params[axis]);
  SettleInputs in = {};
  SettleActions out = {};
  in.now_us = micros();
  in.xactual = tmc4361A_currentPosition(chip);
  settle_limits(axis, &in.limit_lo, &in.limit_hi);
  in.correct_allowed = settle_encoder_is_evidence(axis, target);

  int32_t host_pos = in.xactual - (was_busy ? settle_offset(s) : owed);
  bool at_rest = !was_busy && owed == 0 && !tmc4361A_isRunning(chip, 0);
  bool trusted = at_rest && in.correct_allowed && settle_encoder_is_evidence(axis, host_pos);
  // A move planned on the counter may stop `finish` short of its target to be finished from the encoder
  // (settle_begin): only where a stage resting THERE can be measured - a split point inside the home zone is not
  int32_t finish = settle_params[axis].finish_usteps;
  int32_t split_dir = target > host_pos ? 1 : (target < host_pos ? -1 : 0);
  in.split_allowed = finish > 0 && settle_encoder_is_evidence(axis, target - split_dir * finish);
  int32_t enc_x16 = 0;
  if (trusted)
  {
    // Where the stage IS, read now: what the last window saw may have moved since (it settles back
    // after a fast leg, creeps after a trim - bench 2026-09-20). The cached window mean is the finer
    // number (a fraction of a count) and is used only while the fresh reading agrees with it.
    int32_t sum = 0;
    for (uint8_t k = 0; k < SETTLE_FRESH_READS; k++)
      sum += tmc4361A_readInt(chip, TMC4361A_ENC_POS);
    enc_x16 = (int32_t)settle_div_round((int64_t)sum * SETTLE_X16, SETTLE_FRESH_READS);
    if (s->last_mean_valid && (uint32_t)(in.now_us - s->last_mean_us) < SETTLE_MEAN_MAX_AGE_US
        && settle_abs64((int64_t)enc_x16 - s->last_mean_x16) <= (int64_t)settle_params[axis].learn_clip_usteps * SETTLE_X16 * 3 / 8)
      enc_x16 = s->last_mean_x16;
  }
  if (!was_busy && owed != 0)
  {
    // the counter is still off the host's frame by `owed` and the ramp may be running: plan this
    // move on the counter in that frame, exactly as for a command that interrupts a move-and-settle
    s->state = SETTLE_MOVE; s->goal = 0; s->x_leg = owed;   // settle_offset() == owed
  }
  settle_begin(s, &settle_params[axis], target, enc_x16, trusted, &in, &out);
  return settle_execute(axis, &out, false);
}

void check_move_settle()
{
  for (uint8_t i = 0; i < TOTAL_AXES; i++)
  {
    if (!settle_selected(i)) continue;
    TMC4361ATypeDef *chip = &tmc4361[i];
    SettleState *s = &settle_state[i];

    if (settle_command_timed_out(i)) continue;

    if (settle_unframe[i] != 0 && !settle_busy(s))
    {
      // a dropped move-and-settle: re-base once the ramp it left running is idle - or stands still
      int32_t stood_at;
      if (tmc4361A_isRunning(chip, 0) && !settle_ramp_at_rest(i, micros(), &stood_at)) continue;
      int32_t here = tmc4361A_currentPosition(chip) - settle_unframe[i];
      settle_unframe[i] = 0;
      tmc4361A_rebase_position(chip, here);
      if (i == z)
      {
        // The command the dropped move-and-settle was serving is still owed its target (the drop may
        // have caught a back-off leg): finish it on the counter, as an open-loop move would have.
        if (Z_commanded_movement_in_progress && here != Z_commanded_target_position)
          tmc4361A_moveTo(chip, Z_commanded_target_position);
        else
          focusPosition = here;
      }
      continue;
    }
    if (!settle_armed(i))
    {
      if (settle_busy(s)) settle_drop(i, true);
      continue;
    }
    if (pid_axis_is_homing(i))
    {
      // Homing owns the axis and redefines both frames at the switch; remember it, as the loop's
      // policy does, so the first rest outside the zone aligns them (pid_realign_now).
      pid_realign_pending[i] = true;
      settle_drop(i, false);
      continue;
    }

    if (!settle_busy(s))
    {
      // At rest between moves nothing is read - except while a realignment is owed: a move that did
      // not come through a command (the post-homing lift, the focus wheel) must be able to settle it.
      if (pid_realign_pending[i] && !tmc4361A_isRunning(chip, 0)
          && !pid_in_home_zone(pid_home_zone_usteps[i], tmc4361A_currentPosition(chip)))
      {
        if (pid_realign_now(i)) { s->last_mean_valid = false; ext_open[i] = false; }   /* the encoder's frame just moved under the cached mean */
      }
      else if (ext_open[i]) settle_ext_service(i);      // wheel motion waiting to be measured
      continue;
    }

    SettleInputs in = {};
    SettleActions out = {};
    in.now_us = micros();
    settle_limits(i, &in.limit_lo, &in.limit_hi);
    if (s->state == SETTLE_MOVE)
    {
      in.ramp_running = tmc4361A_isRunning(chip, 0);
      int32_t stood_at;
      if (in.ramp_running && settle_ramp_at_rest(i, in.now_us, &stood_at))
      {
        // at rest with the flags still saying "running": at x_cmd the leg has arrived and is measured
        // like any other; short of it the policy hands the frame back and the move fails
        in.ramp_running = false;
        in.xactual = stood_at;
      }
      else if (!in.ramp_running)
      {
        in.xactual = tmc4361A_currentPosition(chip);
        // first rest outside the zone of the first commanded move after a homing: align the frames
        // now, inside the move, so that its completion already stands on aligned frames
        if (pid_realign_pending[i] && !pid_in_home_zone(pid_home_zone_usteps[i], s->target))
        {
          if (!pid_realign_now(i)) continue;   // refused: the fault is latched and this move-and-settle dropped
        }
      }
    }
    else if (s->state == SETTLE_MEASURE)
    {
      in.enc = tmc4361A_readInt(chip, TMC4361A_ENC_POS);
      in.enc_valid = true;
    }
    in.correct_allowed = settle_encoder_is_evidence(i, s->target);
    settle_step(s, &settle_params[i], &in, &out);
    settle_execute(i, &out, true);   // DONE only ever comes out of a pass that found the ramp idle
  }
}

uint8_t settle_report_landing(uint8_t axis)
{
  return axis < TOTAL_AXES ? (uint8_t)settle_state[axis].rep_first_landing : 0;
}

uint8_t settle_report_bits(uint8_t axis)
{
  if (axis >= TOTAL_AXES) return 0;
  const SettleState *s = &settle_state[axis];
  uint8_t trims = s->rep_trims > MOVE_SETTLE_REPORT_TRIMS_MASK ? MOVE_SETTLE_REPORT_TRIMS_MASK : s->rep_trims;
  uint8_t b = trims;
  if (s->rep_reapproaches > 0) b |= (1 << MOVE_SETTLE_REPORT_BACKED_OFF);
  if (s->rep_missed) b |= (1 << MOVE_SETTLE_REPORT_MISSED);
  if (s->rep_limited) b |= (1 << MOVE_SETTLE_REPORT_LIMITED);
  if (settle_axis_busy(axis)) b |= (1 << MOVE_SETTLE_REPORT_BUSY);
  return b;
}
