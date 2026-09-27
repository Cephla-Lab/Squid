#include <unity.h>
#include <math.h>
#include <stdlib.h>
#include "move_settle_policy.h"

void setUp(void) {}
void tearDown(void) {}

/*
  move_settle_policy.h against a simulated stage. The plant is the one the 2026-09-19 traces describe: a
  motor that is a position source, a nut with lost motion (the stage only follows once the motor
  has crossed the gap), an encoder quantised to whole usteps in a frame offset from the counter's,
  and a lightly damped 113 Hz ring after every ramp. The harness plays move_settle.cpp's part: it
  feeds the policy only what its state asks for, executes the actions, and re-bases the counter on
  DONE - so what is asserted is the behaviour the chip would see.
*/

struct Sim {
    /* plant */
    double   motor;          /* counter frame, usteps */
    double   stage;          /* counter frame, usteps: follows the motor outside the gap */
    double   gap;            /* total lost motion, usteps */
    double   frame;          /* encoder frame - counter frame */
    double   gain;           /* stage travel per motor travel once the gap is taken up */
    double   lead_ppm;       /* the screw's lead against the encoder's scale: the drive flank travels (1 + lead) per ustep */
    double   lead_off;       /* ... accumulated: flank = motor + lead_off */
    double   carry;          /* a push of full_push usteps or more leaves the stage this far beyond the flank; shorter ones in proportion */
    double   carry_neg;      /* carry of legs going negative, when it differs (0 = the same): gravity on a vertical axis */
    double   full_push;      /* 8 usteps unless a test says otherwise */
    double   enc_q;          /* encoder quantum in usteps (1 unless a test says otherwise: 4.27 at 64 usteps/FS) */
    double   kick;           /* applied to the stage once, right after the next ramp has landed (a disturbance) */
    double   kick_always;    /* applied after EVERY landing */
    bool     frozen;         /* the encoder never changes */
    int32_t  frozen_value;
    double   ring_amp;       /* usteps per ustep of the leg, capped at 3 */
    double   ring_start_s;
    double   ring_a;
    /* chip */
    int32_t  xactual, xtarget;
    double   ramp_end_s;
    int32_t  hard_stop_at;   /* the chip stops the ramp here (INT32_MAX = never) */
    /* harness */
    double   now_s;
    uint32_t t0_us;          /* micros() at now_s = 0, to test wrap */
    int32_t  lo, hi;
    bool     correct_allowed;
    bool     split_allowed;
    /* log */
    int      legs;
    int32_t  leg_to[64];
    double   leg_at_s[64];
    int64_t  travel;
    bool     done, stopped_short, missed;
    int32_t  rebase_to;
    uint8_t  fault;
};

static void sim_init(Sim *m)
{
    Sim z = {};
    *m = z;
    m->gain = 1.0; m->hard_stop_at = INT32_MAX; m->full_push = 8; m->enc_q = 1;
    m->lo = -1000000; m->hi = 1000000;
    m->correct_allowed = true;
    m->t0_us = 1000u;
}

static uint32_t sim_micros(const Sim *m) { return m->t0_us + (uint32_t)llround(m->now_s * 1e6); }

static void sim_move_motor(Sim *m, double to)
{
    double d = to - m->motor;
    m->motor = to;
    m->lead_off += d * m->lead_ppm * 1e-6;
    double flank = m->motor + m->lead_off;
    /* backlash: the stage is dragged only by the flank the motor pushes with */
    double push = 0;
    if (flank - m->stage > m->gap / 2) { push = flank - m->gap / 2 - m->stage; m->stage += push * m->gain; }
    if (flank - m->stage < -m->gap / 2) { push = m->stage - (flank + m->gap / 2); m->stage -= push * m->gain; }
    if (push > 0 && m->carry > 0)
    {
        /* bench 2026-09-20: a leg that pushes the stage leaves it beyond the drive flank, a trim nearly in contact */
        double c = ((d < 0 && m->carry_neg > 0) ? m->carry_neg : m->carry) * (push >= m->full_push ? 1.0 : push / m->full_push);
        m->stage += (d > 0 ? c : -c);
        if (m->stage > flank + m->gap / 2) m->stage = flank + m->gap / 2;
        if (m->stage < flank - m->gap / 2) m->stage = flank - m->gap / 2;
    }
    m->ring_a = fabs(d) * m->ring_amp; if (m->ring_a > 3) m->ring_a = 3;
    m->ring_start_s = m->now_s;
}

static int32_t sim_encoder(const Sim *m)
{
    if (m->frozen) return m->frozen_value;
    double t = m->now_s - m->ring_start_s;
    double ring = m->ring_a * exp(-t / 0.015) * sin(2 * M_PI * 113.0 * t);
    double x = m->stage + m->frame + ring;
    if (m->enc_q > 1) x = floor(x / m->enc_q + 0.5) * m->enc_q;     /* the scale counts in its own quantum */
    return (int32_t)floor(x + 0.5);
}

static double sim_true_position(const Sim *m) { return m->stage + m->frame; }   /* encoder frame, unquantised */

static void sim_execute(Sim *m, SettleState *s, const SettleActions *a)
{
    if (a->move)
    {
        TEST_ASSERT_TRUE_MESSAGE(a->move_to >= m->lo && a->move_to <= m->hi, "a leg was issued beyond the travel limits");
        if (m->legs < 64) { m->leg_to[m->legs] = a->move_to; m->leg_at_s[m->legs] = m->now_s; }
        m->legs++;
        m->travel += llabs((long long)a->move_to - m->xtarget);
        m->xtarget = a->move_to;
        double n = fabs((double)m->xtarget - m->xactual);
        m->ramp_end_s = m->now_s + 2 * sqrt(n / 3.2e6);          /* 300 mm/s2 at 16 usteps/FS, triangular */
    }
    if (a->done) { m->done = true; m->rebase_to = a->rebase_to; m->xactual = m->xtarget = a->rebase_to;
                   m->frame += m->motor - a->rebase_to; m->stage -= m->motor - a->rebase_to; m->motor = a->rebase_to; }
    if (a->stopped_short) { m->stopped_short = true; m->rebase_to = a->rebase_to; }
    if (a->missed) { m->missed = true; m->rebase_to = a->rebase_to;      /* the glue re-bases the counter to where the stage is */
                     m->frame += m->motor - a->rebase_to; m->stage -= m->motor - a->rebase_to; m->motor = a->rebase_to;
                     m->xactual = m->xtarget = a->rebase_to; }
    if (a->fault) m->fault = a->fault;
    (void)s;
}

static void sim_inputs(const Sim *m, SettleInputs *in)
{
    SettleInputs z = {};
    *in = z;
    in->now_us = sim_micros(m);
    in->correct_allowed = m->correct_allowed;
    in->split_allowed = m->split_allowed;
    in->limit_lo = m->lo; in->limit_hi = m->hi;
    in->xactual = m->xactual;
}

static void sim_begin(Sim *m, SettleState *s, const SettleParams *p, int32_t target, bool trusted)
{
    SettleInputs in; SettleActions out = {};
    sim_inputs(m, &in);
    m->done = false; m->stopped_short = false; m->missed = false; m->fault = 0; m->legs = 0; m->travel = 0;
    int32_t enc_x16 = s->last_mean_valid ? s->last_mean_x16 : sim_encoder(m) * SETTLE_X16;
    settle_begin(s, p, target, enc_x16, trusted, &in, &out);
    sim_execute(m, s, &out);
}

/* run passes of 0.3 ms (one SPI read) until the policy is idle; returns the elapsed seconds */
static double sim_run(Sim *m, SettleState *s, const SettleParams *p)
{
    double start = m->now_s;
    for (int i = 0; i < 40000 && settle_busy(s); i++)
    {
        m->now_s += 0.0003;
        SettleInputs in; SettleActions out = {};
        sim_inputs(m, &in);
        if (s->state == SETTLE_MOVE)
        {
            bool running = m->now_s < m->ramp_end_s;
            if (!running && m->xactual != m->xtarget)
            {
                int32_t to = m->xtarget;
                if (m->hard_stop_at != INT32_MAX && ((m->xactual <= m->hard_stop_at && to > m->hard_stop_at) ||
                                                      (m->xactual >= m->hard_stop_at && to < m->hard_stop_at)))
                    to = m->hard_stop_at;
                sim_move_motor(m, to);
                m->xactual = to;
                if (m->kick != 0) { m->stage += m->kick; m->kick = 0; }
                m->stage += m->kick_always;
            }
            in.ramp_running = running;
            in.xactual = m->xactual;
        }
        else if (s->state == SETTLE_MEASURE)
        {
            in.enc_valid = true;
            in.enc = sim_encoder(m);
        }
        else if (s->state == SETTLE_MOVE_A && m->now_s >= m->ramp_end_s && m->xactual != m->xtarget)
        {
            sim_move_motor(m, m->xtarget);
            m->xactual = m->xtarget;
        }
        settle_step(s, p, &in, &out);
        sim_execute(m, s, &out);
    }
    TEST_ASSERT_FALSE_MESSAGE(settle_busy(s), "the move-and-settle never finished");
    return m->now_s - start;
}

/* usteps the stage must travel to `target` according to the mean the next plan starts from: plans
   use the sub-ustep window mean, so the expected leg is not simply target - previous target */
static int32_t stage_travel_needed(const SettleState *s, int32_t target)
{
    return (int32_t)settle_div_round((int64_t)target * SETTLE_X16 - s->last_mean_x16, SETTLE_X16);
}

static void assert_settled(const Sim *m, const SettleParams *p, int32_t target)
{
    TEST_ASSERT_TRUE_MESSAGE(m->done, "no DONE");
    TEST_ASSERT_EQUAL_UINT8(0, m->fault);
    TEST_ASSERT_EQUAL_INT32(target, m->rebase_to);
    double e = target - sim_true_position(m);
    /* the mean of a quantised, ringing reading is good to a fraction of a ustep */
    /* e > 0 = short of a target approached going +; the band is wider on the far side. Direction is not known
       here, so the wider bound is used for both: what this guards is convergence, the band has its own tests */
    double band = p->tol_over_usteps > p->tol_usteps ? p->tol_over_usteps : p->tol_usteps;
    TEST_ASSERT_TRUE_MESSAGE(fabs(e) <= band + 0.6, "the stage is not inside the tolerance");
}

/* ---- the everyday case: a stack that climbs ---------------------------------------------------- */

void test_climbing_stack_places_every_plane_and_never_reverses(void)
{
    Sim m; sim_init(&m); m.gap = 12; m.frame = 28.4; m.ring_amp = 0.12;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    /* approach from below first, as a stack does */
    sim_begin(&m, &s, &p, 1000, true); sim_run(&m, &s, &p);
    assert_settled(&m, &p, 1000);
    double worst = 0, total = 0;
    for (int k = 1; k <= 20; k++)
    {
        int32_t target = 1000 + 11 * k;                  /* 1.03 um planes at 16 usteps/FS */
        sim_begin(&m, &s, &p, target, true);
        int32_t from = m.xactual;
        double t = sim_run(&m, &s, &p);
        assert_settled(&m, &p, target);
        for (int i = 0; i < m.legs && i < 64; i++)
            TEST_ASSERT_TRUE_MESSAGE(m.leg_to[i] >= from, "a climbing plane must never issue a downward leg");
        TEST_ASSERT_EQUAL_UINT8(0, s.rep_reapproaches);
        total += t; if (t > worst) worst = t;
    }
    /* one ramp, the settle time and one window when the landing is good: ~4 + 5 + 9 ms */
    TEST_ASSERT_TRUE_MESSAGE(total / 20 < 0.022, "a clean climbing plane should cost one ramp, one settle and one window");
    TEST_ASSERT_TRUE(worst < 0.060);
}

void test_the_plan_starts_from_the_encoder_not_the_counter(void)
{
    /* The counter says 500, the encoder 470: the stage is 30 usteps lower than the counter believes
       (lost steps, thermal growth, a wheel move that wound the screw). The leg must carry the stage
       the 41 usteps it actually needs, not the 11 the counter would give it. */
    Sim m; sim_init(&m); m.motor = m.stage = 500; m.xactual = m.xtarget = 500; m.frame = -30;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    sim_begin(&m, &s, &p, 511, true);
    TEST_ASSERT_EQUAL_INT32(541, m.leg_to[0]);
    sim_run(&m, &s, &p);
    assert_settled(&m, &p, 511);
    TEST_ASSERT_EQUAL_INT32(1, m.legs);
    /* DONE re-bases the counter: XACTUAL is the host's frame again */
    TEST_ASSERT_EQUAL_INT32(511, m.xactual);
}

/* ---- reversals and lost motion ------------------------------------------------------------------ */

void test_reversal_with_unknown_lost_motion_converges_from_the_short_side(void)
{
    Sim m; sim_init(&m); m.gap = 12; m.ring_amp = 0.1;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    sim_begin(&m, &s, &p, 2000, true); sim_run(&m, &s, &p); assert_settled(&m, &p, 2000);
    sim_begin(&m, &s, &p, 1989, true);                   /* one plane down: the whole leg is eaten by the gap */
    int32_t from = m.xactual;
    sim_run(&m, &s, &p);
    assert_settled(&m, &p, 1989);
    TEST_ASSERT_TRUE(s.rep_first_landing > p.tol_usteps);                     /* landed short, as predicted */
    TEST_ASSERT_TRUE(s.rep_trims >= 1 && s.rep_trims <= p.max_trims);
    TEST_ASSERT_EQUAL_UINT8(0, s.rep_reapproaches);                           /* ... and never overshot */
    for (int i = 0; i < m.legs && i < 64; i++) TEST_ASSERT_TRUE(m.leg_to[i] <= from);
}

void test_lost_motion_feedforward_saves_the_trims(void)
{
    Sim m; sim_init(&m); m.gap = 12;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.lost_motion_usteps = 10;     /* under the real 12, as documented */
    sim_begin(&m, &s, &p, 2000, true); sim_run(&m, &s, &p);
    int32_t need = stage_travel_needed(&s, 1989);        /* about -11 */
    sim_begin(&m, &s, &p, 1989, true);
    TEST_ASSERT_EQUAL_INT32(2000 + need - 10, m.leg_to[0]);
    sim_run(&m, &s, &p);
    assert_settled(&m, &p, 1989);
    TEST_ASSERT_EQUAL_UINT8(0, s.rep_trims);             /* 2 usteps short = inside the tolerance */
    /* continuing the same way needs no feedforward */
    need = stage_travel_needed(&s, 1978);
    sim_begin(&m, &s, &p, 1978, true);
    TEST_ASSERT_EQUAL_INT32(1989 + need, m.leg_to[0]);
    sim_run(&m, &s, &p);
    assert_settled(&m, &p, 1978);
}

void test_overshoot_backs_off_beyond_the_lost_motion_and_approaches_again(void)
{
    /* The plan starts from a reading that is 6 usteps too low (the stage moved after it was taken):
       the leg is 6 usteps too long and the first landing overshoots. */
    Sim m; sim_init(&m); m.gap = 8;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    sim_begin(&m, &s, &p, 1000, true); sim_run(&m, &s, &p);
    s.last_mean_x16 -= 6 * SETTLE_X16;
    sim_begin(&m, &s, &p, 1011, true);
    sim_run(&m, &s, &p);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(0, m.fault, "an overshoot must be recovered, not faulted");
    assert_settled(&m, &p, 1011);
    TEST_ASSERT_EQUAL_UINT8(1, s.rep_reapproaches);
    TEST_ASSERT_TRUE(s.rep_first_landing < -p.tol_usteps);
    /* leg 2 is the back-off: it must go beyond the target by at least the back-off distance */
    TEST_ASSERT_TRUE(m.legs >= 3);
    TEST_ASSERT_TRUE(m.leg_to[1] < m.leg_to[0] - p.backoff_usteps);
    TEST_ASSERT_EQUAL_INT8(1, s.last_dir);               /* and the move still ends travelling up */
}

void test_fixed_approach_direction_goes_beyond_and_comes_back(void)
{
    Sim m; sim_init(&m); m.gap = 10;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.approach = MOVE_SETTLE_APPROACH_POSITIVE;
    sim_begin(&m, &s, &p, 3000, true); sim_run(&m, &s, &p); assert_settled(&m, &p, 3000);
    int32_t need = stage_travel_needed(&s, 2900 - p.backoff_usteps);
    sim_begin(&m, &s, &p, 2900, true);                   /* down 100: must end travelling up */
    TEST_ASSERT_EQUAL_UINT8(SETTLE_LEG_BACKOFF, s.leg);
    TEST_ASSERT_EQUAL_INT32(3000 + need, m.leg_to[0]);   /* beyond the target by the back-off distance */
    sim_run(&m, &s, &p);
    assert_settled(&m, &p, 2900);
    TEST_ASSERT_EQUAL_INT8(1, s.last_dir);
    TEST_ASSERT_EQUAL_UINT8(0, s.rep_reapproaches);      /* a planned back-off is not a recovery */
    /* an upward move under the same setting is a plain approach */
    sim_begin(&m, &s, &p, 2950, true);
    TEST_ASSERT_EQUAL_UINT8(SETTLE_LEG_APPROACH, s.leg);
    sim_run(&m, &s, &p); assert_settled(&m, &p, 2950);
}

void test_undershoot_bias_applies_to_long_legs_only(void)
{
    Sim m; sim_init(&m);
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.bias_usteps = 1;
    sim_begin(&m, &s, &p, 100, true);
    TEST_ASSERT_EQUAL_INT32(99, m.leg_to[0]);
    sim_run(&m, &s, &p); assert_settled(&m, &p, 100);
    int32_t need = stage_travel_needed(&s, 103);         /* 3 or 4 usteps: a bias of 1 is too much of it - not applied */
    sim_begin(&m, &s, &p, 103, true);
    TEST_ASSERT_EQUAL_INT32(100 + need, m.leg_to[0]);
    sim_run(&m, &s, &p); assert_settled(&m, &p, 103);
}

/* ---- the lost-motion model ------------------------------------------------------------------------ */

/* Climb through planes of mixed size: the long ones (11, 16 usteps) push the stage far enough to carry
   it, the short ones (2-4 usteps: a focus nudge, an autofocus correction) leave it nearly in contact -
   so every long plane after a short one starts from the other state, which is where the bench's first
   firmware mis-landed by the carry. Returns the planes that needed any correction. */
static int climb_mixed_x(Sim *m, SettleState *s, const SettleParams *p, int rounds, int scale, int *worst_landing);
static int climb_mixed(Sim *m, SettleState *s, const SettleParams *p, int rounds, int *worst_landing)
{
    return climb_mixed_x(m, s, p, rounds, 1, worst_landing);
}

static int climb_mixed_x(Sim *m, SettleState *s, const SettleParams *p, int rounds, int scale, int *worst_landing)
{
    static const int sizes[] = {11, 11, 3, 11, 16, 2, 11, 4, 16, 11};
    int corrected = 0; *worst_landing = 0;
    int32_t target = m->xactual;
    for (int r = 0; r < rounds; r++)
        for (unsigned i = 0; i < sizeof sizes / sizeof sizes[0]; i++)
        {
            target += sizes[i] * scale;
            sim_begin(m, s, p, target, true);
            sim_run(m, s, p);
            assert_settled(m, p, target);
            if (s->rep_trims > 0 || s->rep_reapproaches > 0) corrected++;
            int l = s->rep_first_landing < 0 ? -s->rep_first_landing : s->rep_first_landing;
            if (l > *worst_landing) *worst_landing = l;
        }
    return corrected;
}

void test_with_the_carry_known_every_plane_lands_on_the_first_leg(void)
{
    /* the plant of the bench: a good push carries 5 usteps, a trim ends nearly in contact */
    Sim m; sim_init(&m); m.gap = 10; m.carry = 5;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.carry_usteps = 5; p.lost_motion_usteps = 10; p.learn_x16 = 0;
    p.fast_min_usteps = 8;                                /* the plant's own number: this test is about the bookkeeping */
    p.tol_usteps = 2;                                     /* tight enough that a mis-planned leg cannot hide inside it. Not 1 any more:
                                                             since 2026-09-21 a push ANCHORS the gap at the carry of that push instead of
                                                             booking stage travel minus motor travel, and the carry of a measured travel
                                                             is good to a ustep, not exact - the price of a gap that cannot drift */
    sim_begin(&m, &s, &p, 1000, true); sim_run(&m, &s, &p); assert_settled(&m, &p, 1000);
    int worst;
    TEST_ASSERT_EQUAL_INT(0, climb_mixed(&m, &s, &p, 3, &worst));
    TEST_ASSERT_TRUE(worst <= p.tol_usteps);
}

void test_without_the_model_the_same_plant_needs_corrections(void)
{
    /* the control: carry unknown and learning off is the firmware that ran on the bench first */
    Sim m; sim_init(&m); m.gap = 10; m.carry = 5;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.learn_x16 = 0; p.max_reapproaches = 3; p.max_trims = 15;
    p.tol_usteps = 1;
    sim_begin(&m, &s, &p, 1000, true); sim_run(&m, &s, &p); assert_settled(&m, &p, 1000);
    int worst;
    int corrected = climb_mixed(&m, &s, &p, 3, &worst);
    TEST_ASSERT_TRUE_MESSAGE(corrected >= 6, "the simulated plant does not reproduce the bench's mis-landings: the test above proves nothing");
}

void test_the_carry_is_learned_from_the_landings(void)
{
    /* nothing configured, and the policy's idea of a full push (10) is not the plant's (8): the model
       starts at zero and has to find a carry that makes the plans land */
    Sim m; sim_init(&m); m.gap = 10; m.carry = 5;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.max_reapproaches = 3;
    sim_begin(&m, &s, &p, 1000, true); sim_run(&m, &s, &p);
    int worst;
    climb_mixed(&m, &s, &p, 4, &worst);                  /* learning: corrections are expected here */
    TEST_ASSERT_INT_WITHIN(3 * SETTLE_X16 / 2, 5 * SETTLE_X16, s.carry_x16[1]);
    int corrected = climb_mixed(&m, &s, &p, 3, &worst);
    TEST_ASSERT_TRUE_MESSAGE(corrected <= 2, "with the carry learned, corrections must be the exception");
}

void test_the_same_stage_at_four_times_the_microstepping(void)
{
    /* 64 usteps/FS: every length is 4x in usteps, and the encoder now counts in 4.27-ustep quanta. What is
       learned per landing, what counts as a push and the smallest trim scale with it (learn_clip, tol) */
    Sim m; sim_init(&m); m.gap = 40; m.carry = 20; m.full_push = 32; m.enc_q = 4.27;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    p.tol_usteps = 8; p.tol_over_usteps = 12; p.fast_min_usteps = 40; p.learn_clip_usteps = 16;
    p.carry_max_usteps = 64; p.lost_max_usteps = 128; p.backoff_usteps = 128; p.min_drive_usteps = 128;
    sim_begin(&m, &s, &p, 4000, true); sim_run(&m, &s, &p);
    int worst;
    climb_mixed_x(&m, &s, &p, 4, 4, &worst);             /* learning */
    TEST_ASSERT_INT_WITHIN(8 * SETTLE_X16, 20 * SETTLE_X16, s.carry_x16[1]);
    int corrected = climb_mixed_x(&m, &s, &p, 3, 4, &worst);
    TEST_ASSERT_TRUE_MESSAGE(corrected <= 3, "with the carry learned, corrections must be the exception at 64 usteps/FS too");
    TEST_ASSERT_EQUAL_UINT8(0, m.fault);
}

void test_a_vertical_axis_carries_further_down_than_up(void)
{
    /* bench 2026-09-20: with one carry for both directions, up legs landed short and down legs long of it.
       Mixed sizes: legs of one size alone cannot tell a carry from a gap (any value is self-consistent) */
    Sim m; sim_init(&m); m.gap = 10; m.carry = 2; m.carry_neg = 8;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.fast_min_usteps = 8; p.lost_motion_usteps = 10; p.carry_usteps = 5;
    p.max_reapproaches = 3;                               /* while it learns; what is asserted below is the learned state */
    sim_begin(&m, &s, &p, 1000, true); sim_run(&m, &s, &p);
    int32_t target = 1000;
    static const int seq[] = {11, 3, 11, 16, 2, 11, -11, -3, -11, -16, -2, -11};
    for (int r = 0; r < 20; r++)
        for (unsigned i = 0; i < 12; i++) { sim_begin(&m, &s, &p, target += seq[i], true); sim_run(&m, &s, &p); }
    /* 2 usteps, not the plant's 6: the carry is taught only where it can be observed (a fast leg from contact, a trim
       with a take-up), and once the plans land there is nothing left to teach it with - it goes as far as the landings
       need (no corrections below), not to the plant's number */
    TEST_ASSERT_TRUE_MESSAGE(s.carry_x16[0] - s.carry_x16[1] >= 2 * SETTLE_X16, "the down carry must come out larger than the up carry");
    int corrected = 0;
    for (int r = 0; r < 3; r++)
        for (unsigned i = 0; i < 12; i++)
        {
            sim_begin(&m, &s, &p, target += seq[i], true); sim_run(&m, &s, &p); assert_settled(&m, &p, target);
            if (s.rep_reapproaches > 0) corrected += 10;
            if (s.rep_trims > 0) corrected++;
        }
    /* <= 12 = one back-off and two trims in 36 moves (was <= 4). KNOWN IMPERFECTION of the anchored gap (2026-09-21): in this
       plant, at a tolerance of 2 usteps, the down carry creeps up ~0.1 ustep a round until a plane lands past the band, the
       back-off resets it, and it starts over - one back-off in ~100 moves. Accepted against what the booked gap did on the
       bench (19 back-offs and 7 missed moves in 1350, against 1 and 0 in 900 with this), and to be looked at again. */
    TEST_ASSERT_TRUE_MESSAGE(corrected <= 12, "with a carry per direction, corrections must be the exception both ways");
}

void test_the_lost_motion_is_kept_per_direction_of_the_crossing(void)
{
    SettleParams p; settle_params_default(&p); p.learn_x16 = 16;
    SettleState s; settle_state_init(&s); s.model_init = true;
    s.lost_x16[1] = 20 * SETTLE_X16; s.lost_x16[0] = 10 * SETTLE_X16;
    s.side = -1; s.gap_x16 = 0;
    TEST_ASSERT_EQUAL_INT32(20 + 30, settle_plan_leg(&s, &p, 30 * SETTLE_X16, 0));      /* crossing to go positive */
    /* it lands 4 short of the plan: the positive crossing loses more than was thought - the other is untouched */
    s.leg = SETTLE_LEG_APPROACH; s.landed_once = true;
    settle_model_learn(&s, &p, (30 - 4) * SETTLE_X16);
    TEST_ASSERT_EQUAL_INT32(24 * SETTLE_X16, s.lost_x16[1]);
    TEST_ASSERT_EQUAL_INT32(10 * SETTLE_X16, s.lost_x16[0]);
    s.side = 1; s.gap_x16 = 0;
    TEST_ASSERT_EQUAL_INT32(-(10 + 30), settle_plan_leg(&s, &p, -30 * SETTLE_X16, 0)); /* crossing to go negative */
}

/* ---- motion the policy did not issue, measured afterwards (the focus wheel) ------------------------ */

static void wheel_state(SettleState *s, SettleParams *p, int8_t side, int gap)
{
    settle_state_init(s); settle_params_default(p); p->lost_motion_usteps = 10;
    settle_model_seed(s, p);
    s->side = side; s->gap_x16 = gap * SETTLE_X16;
}

void test_wheel_motion_the_same_way_keeps_the_flank_and_closes_the_gap(void)
{
    SettleState s; SettleParams p; wheel_state(&s, &p, 1, 4);
    /* wheel +20: the flank closes the 4 usteps the stage was ahead by, then pushes it 16 */
    settle_observe_external(&s, &p, 20, 16 * SETTLE_X16, 1, true);
    TEST_ASSERT_EQUAL_INT8(1, s.side);
    TEST_ASSERT_EQUAL_INT32(0, s.gap_x16);
}

void test_wheel_motion_inside_the_play_moves_nothing_and_is_booked_as_such(void)
{
    SettleState s; SettleParams p; wheel_state(&s, &p, 1, 0);
    /* wheel -6 of a play of 10: the stage stays, the flank that pushed it is now 6 behind it */
    settle_observe_external(&s, &p, -6, 0, -1, true);
    TEST_ASSERT_EQUAL_INT8(1, s.side);
    TEST_ASSERT_EQUAL_INT32(6 * SETTLE_X16, s.gap_x16);
    /* so the next move up has 6 usteps to take up first, and the next move down has 4 */
    TEST_ASSERT_EQUAL_INT32(6 + 30, settle_plan_leg(&s, &p, 30 * SETTLE_X16, 0));
    wheel_state(&s, &p, 1, 0);
    settle_observe_external(&s, &p, -6, 0, -1, true);
    TEST_ASSERT_EQUAL_INT32(-(4 + 30), settle_plan_leg(&s, &p, -30 * SETTLE_X16, 0));
}

void test_wheel_motion_across_the_play_hands_the_stage_to_the_other_flank(void)
{
    SettleState s; SettleParams p; wheel_state(&s, &p, 1, 0);
    /* wheel -25: 10 of play, then the stage follows 15 down */
    settle_observe_external(&s, &p, -25, -15 * SETTLE_X16, -1, true);
    TEST_ASSERT_EQUAL_INT8(-1, s.side);
    TEST_ASSERT_EQUAL_INT32(0, s.gap_x16);
}

void test_wheel_back_and_forth_is_judged_by_where_it_ended_not_by_how_it_got_there(void)
{
    SettleState s; SettleParams p; wheel_state(&s, &p, 1, 0);
    /* up 50, down 53: net -3 on the counter and the stage 7 above where it started (it was pushed up 50,
       the way down took 10 of play and pushed 43) - the stage is 10 ahead of the up flank: on the other one */
    settle_observe_external(&s, &p, -3, 7 * SETTLE_X16, -1, true);
    TEST_ASSERT_EQUAL_INT8(-1, s.side);
    TEST_ASSERT_EQUAL_INT32(0, s.gap_x16);
}

void test_wheel_motion_that_cannot_be_reckoned_leaves_the_state_unknown_unless_it_clearly_pushed(void)
{
    SettleState s; SettleParams p; wheel_state(&s, &p, 1, 0);
    settle_observe_external(&s, &p, 8, 8 * SETTLE_X16, 1, false);        /* start not measured, short: unknown */
    TEST_ASSERT_EQUAL_INT8(0, s.side);
    wheel_state(&s, &p, 0, 0);
    settle_observe_external(&s, &p, 5000, 4990 * SETTLE_X16, 1, false);  /* long, and the stage followed: in contact */
    TEST_ASSERT_EQUAL_INT8(1, s.side);
    TEST_ASSERT_EQUAL_INT32(0, s.gap_x16);
    /* a disagreement no play can explain (lost steps, a slipped frame) is not turned into a gap */
    wheel_state(&s, &p, 1, 0);
    settle_observe_external(&s, &p, 100, 20 * SETTLE_X16, 1, true);
    TEST_ASSERT_EQUAL_INT8(0, s.side);
}

void test_wheel_motion_is_seen_through_the_screw_scale(void)
{
    SettleState s; SettleParams p; wheel_state(&s, &p, 1, 0); s.scale_ppm_x16 = -1100 * 16;
    /* 20000 usteps up on a screw that is 1100 ppm short: the stage went 19978 and is still in contact */
    settle_observe_external(&s, &p, 20000, 19978 * SETTLE_X16, 1, true);
    TEST_ASSERT_EQUAL_INT8(1, s.side);
    TEST_ASSERT_INT_WITHIN(SETTLE_X16, 0, s.gap_x16);
}

/* ---- the screw's lead against the encoder's scale ------------------------------------------------- */

/* bench 2026-09-20: 1100 ppm. 200 um continuing legs landed 0.4-0.6 um short, every time: the leg falls
   short by the scale, AND the observed gap books that shortfall as "closer to the flank", which the
   next continuing leg pays again */
static void long_plant(Sim *m, SettleState *s, SettleParams *p)
{
    sim_init(m); m->gap = 10; m->carry = 5; m->lead_ppm = -1100;
    settle_state_init(s);
    settle_params_default(p); p->carry_usteps = 5; p->lost_motion_usteps = 10; p->fast_min_usteps = 8;
}

void test_the_screw_scale_is_learned_from_long_continuing_legs(void)
{
    Sim m; SettleState s; SettleParams p; long_plant(&m, &s, &p);
    sim_begin(&m, &s, &p, 1000, true); sim_run(&m, &s, &p);
    int32_t target = 1000;
    sim_begin(&m, &s, &p, target += 10000, true); sim_run(&m, &s, &p);
    TEST_ASSERT_TRUE_MESSAGE(s.rep_first_landing >= 8, "the simulated screw does not fall short: the test proves nothing");
    for (int k = 0; k < 14; k++) { sim_begin(&m, &s, &p, target += 10000, true); sim_run(&m, &s, &p); assert_settled(&m, &p, target); }
    TEST_ASSERT_INT_WITHIN(250 * 16, -1100 * 16, s.scale_ppm_x16);
    /* ... and then long legs land, continuing AND reversing, and so does the short plane after one */
    sim_begin(&m, &s, &p, target += 10000, true); sim_run(&m, &s, &p); assert_settled(&m, &p, target);
    TEST_ASSERT_EQUAL_UINT8(0, s.rep_trims); TEST_ASSERT_EQUAL_UINT8(0, s.rep_reapproaches);
    sim_begin(&m, &s, &p, target += 11, true); sim_run(&m, &s, &p); assert_settled(&m, &p, target);
    TEST_ASSERT_EQUAL_UINT8(0, s.rep_trims); TEST_ASSERT_EQUAL_UINT8(0, s.rep_reapproaches);
    sim_begin(&m, &s, &p, target -= 10000, true); sim_run(&m, &s, &p); assert_settled(&m, &p, target);
    TEST_ASSERT_EQUAL_UINT8(0, s.rep_reapproaches);
    TEST_ASSERT_EQUAL_UINT8(0, m.fault);
}

void test_a_seeded_screw_scale_lands_the_first_long_leg(void)
{
    Sim m; SettleState s; SettleParams p; long_plant(&m, &s, &p); p.scale_ppm = -1100;
    sim_begin(&m, &s, &p, 1000, true); sim_run(&m, &s, &p);
    sim_begin(&m, &s, &p, 21000, true); sim_run(&m, &s, &p); assert_settled(&m, &p, 21000);
    TEST_ASSERT_TRUE(s.rep_first_landing >= -p.tol_over_usteps && s.rep_first_landing <= p.tol_usteps);
    TEST_ASSERT_EQUAL_UINT8(0, s.rep_trims);
    /* the gap after it was observed through the same scale: the next plane is not paid twice */
    sim_begin(&m, &s, &p, 21011, true); sim_run(&m, &s, &p); assert_settled(&m, &p, 21011);
    TEST_ASSERT_EQUAL_UINT8(0, s.rep_trims);
}

void test_short_legs_do_not_teach_the_scale_and_it_stays_bounded(void)
{
    Sim m; SettleState s; SettleParams p; long_plant(&m, &s, &p); m.lead_ppm = 0;
    sim_begin(&m, &s, &p, 1000, true); sim_run(&m, &s, &p);
    int worst; climb_mixed(&m, &s, &p, 3, &worst);
    TEST_ASSERT_EQUAL_INT32(0, s.scale_ppm_x16);
    /* a screw that is wildly off (or an encoder that lies): the learned scale stops at its bound */
    m.lead_ppm = -30000;
    int32_t target = m.xactual;
    for (int k = 0; k < 40; k++) { sim_begin(&m, &s, &p, target += 10000, true); sim_run(&m, &s, &p); }
    TEST_ASSERT_TRUE(s.scale_ppm_x16 >= -p.scale_max_ppm * 16 && s.scale_ppm_x16 <= p.scale_max_ppm * 16);
    TEST_ASSERT_EQUAL_UINT8(0, m.fault);
}

/* ---- a leg that crosses the play scatters wider ---------------------------------------------------- */

void test_the_approach_after_a_back_off_aims_short(void)
{
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.learn_x16 = 0;
    SettleInputs in = {}; in.correct_allowed = true; in.limit_lo = -100000; in.limit_hi = 100000; in.now_us = 5000;
    SettleActions out = {};
    s.state = SETTLE_MEASURE; s.leg = SETTLE_LEG_BACKOFF; s.dir = 1; s.target = 100; s.goal = 68; s.x_leg = 60;
    s.stopped_once = true; s.t_first_stop_us = 5000; s.model_init = true; s.reapproaches = 1; s.landed_once = true;
    settle_decide(&s, &p, &in, 60 * SETTLE_X16, 0, &out);              /* backed off to 40 below the target */
    TEST_ASSERT_TRUE(out.move);
    TEST_ASSERT_EQUAL_INT32(60 + 40 - p.tol_usteps, out.move_to);       /* nothing learned yet: one tolerance short */
    /* with a reversal scatter of 4 usteps mean absolute error: two sigmas = 10, bounded by four tolerances = 8 */
    SettleState s2; settle_state_init(&s2);
    s2.state = SETTLE_MEASURE; s2.leg = SETTLE_LEG_BACKOFF; s2.dir = 1; s2.target = 100; s2.goal = 68; s2.x_leg = 60;
    s2.stopped_once = true; s2.t_first_stop_us = 5000; s2.model_init = true; s2.reapproaches = 1; s2.landed_once = true;
    s2.rev_mad_x256 = 4 * 256;
    SettleActions out2 = {};
    settle_decide(&s2, &p, &in, 60 * SETTLE_X16, 0, &out2);
    TEST_ASSERT_EQUAL_INT32(60 + 40 - 4 * p.tol_usteps, out2.move_to);
}

void test_a_first_leg_across_the_play_aims_short_by_its_own_scatter(void)
{
    SettleParams p; settle_params_default(&p);
    SettleInputs in = {}; in.correct_allowed = true; in.limit_lo = -100000; in.limit_hi = 100000; in.xactual = 0;
    /* continuing, nothing learned: aims at the target */
    SettleState s; settle_state_init(&s); s.model_init = true; s.side = 1; s.last_dir = 1;
    s.rev_mad_x256 = 4 * 256;                                           /* reversals scatter; continuing legs do not (yet) */
    SettleActions out = {};
    settle_begin(&s, &p, 100, 0, true, &in, &out);
    TEST_ASSERT_EQUAL_INT32(100, out.move_to);
    /* the same move against the last push: half a sigma of 5 usteps = 2.5, bounded by the tolerance = 2 */
    SettleState r; settle_state_init(&r); r.model_init = true; r.side = -1; r.last_dir = -1;
    r.rev_mad_x256 = 4 * 256;
    SettleActions outr = {};
    settle_begin(&r, &p, 100, 0, true, &in, &outr);
    TEST_ASSERT_EQUAL_INT32(100 - p.tol_usteps, outr.move_to);
}

void test_the_reversal_scatter_is_learned_separately(void)
{
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    s.model_init = true;
    for (int k = 0; k < 40; k++)
    {
        s.side = -1; s.leg_dir = 1; s.leg_learn = true; s.leg_observed = true; s.leg_against = true; s.landed_once = false;
        s.leg = SETTLE_LEG_APPROACH; s.limited = false; s.gap_x16 = 0; s.lost_x16[0] = s.lost_x16[1] = 0;
        s.leg_n_x16 = 30 * SETTLE_X16; s.leg_from_x16 = 0; s.leg_free_x16 = 0; s.leg_push_x16 = 30 * SETTLE_X16;
        s.leg_expect_x16 = 30 * SETTLE_X16;
        settle_model_learn(&s, &p, (30 + ((k & 1) ? 6 : -6)) * SETTLE_X16);
    }
    TEST_ASSERT_INT_WITHIN(256, 6 * 256, s.rev_mad_x256);
    TEST_ASSERT_EQUAL_INT32(0, s.land_mad_x256);                        /* the continuing legs' scatter is its own number */
}

void test_the_lost_motion_is_learned_from_reversals(void)
{
    Sim m; sim_init(&m); m.gap = 12;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    sim_begin(&m, &s, &p, 2000, true); sim_run(&m, &s, &p);
    int32_t target = 2000;
    for (int k = 0; k < 24; k++)                         /* up and down: every move is a reversal, a dozen each way */
    {
        target += (k % 2) ? 40 : -40;
        sim_begin(&m, &s, &p, target, true); sim_run(&m, &s, &p); assert_settled(&m, &p, target);
    }
    TEST_ASSERT_INT_WITHIN(2 * SETTLE_X16, 12 * SETTLE_X16, s.lost_x16[0]);
    TEST_ASSERT_INT_WITHIN(2 * SETTLE_X16, 12 * SETTLE_X16, s.lost_x16[1]);
    target -= 40;
    sim_begin(&m, &s, &p, target, true); sim_run(&m, &s, &p); assert_settled(&m, &p, target);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(0, s.rep_trims, "a reversal with the lost motion learned should land without a trim");
}

void test_the_model_stays_inside_its_bounds_whatever_the_landings_say(void)
{
    Sim m; sim_init(&m); m.gap = 10; m.gain = 2.0;       /* a stage that goes twice as far as asked: nonsense to the model */
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.max_trims = 15; p.max_reapproaches = 3; p.tol_usteps = 6;
    int32_t target = 0;
    for (int k = 0; k < 12; k++)
    {
        target += 20;
        sim_begin(&m, &s, &p, target, true); sim_run(&m, &s, &p);
        m.fault = 0;
        for (int d = 0; d < 2; d++)
            TEST_ASSERT_TRUE(s.carry_x16[d] >= 0 && s.carry_x16[d] <= p.carry_max_usteps * SETTLE_X16);
        for (int d = 0; d < 2; d++)
            TEST_ASSERT_TRUE(s.lost_x16[d] >= 0 && s.lost_x16[d] <= p.lost_max_usteps * SETTLE_X16);
    }
}

void test_a_trim_closes_the_gap_a_fast_leg_left(void)
{
    /* after a fast leg the stage is 5 ahead of the flank: a trim of the bare shortfall would move nothing */
    Sim m; sim_init(&m); m.gap = 10; m.carry = 5;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.carry_usteps = 5; p.lost_motion_usteps = 10; p.learn_x16 = 0;
    p.fast_min_usteps = 8; p.gain_x16 = 16; p.bias_sigma_x16 = 0;
    sim_begin(&m, &s, &p, 1000, true); sim_run(&m, &s, &p);
    sim_begin(&m, &s, &p, 1011, true); sim_run(&m, &s, &p); assert_settled(&m, &p, 1011);   /* chain state: carried */
    sim_begin(&m, &s, &p, 1022, true);
    m.kick = -4;                                         /* lands 4 short */
    sim_run(&m, &s, &p);
    assert_settled(&m, &p, 1022);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(1, s.rep_trims, "one trim must do it: shortfall plus the gap");
}

/* ---- bounded: what must never run away ---------------------------------------------------------- */

void test_frozen_encoder_on_a_long_move_is_no_response(void)
{
    Sim m; sim_init(&m); m.frozen = true; m.frozen_value = 0;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.max_dev_usteps = 2000;
    sim_begin(&m, &s, &p, 500, true);
    sim_run(&m, &s, &p);
    TEST_ASSERT_EQUAL_UINT8(PID_FAULT_NO_RESPONSE, m.fault);
    TEST_ASSERT_FALSE(m.done);
    TEST_ASSERT_EQUAL_INT32(1, m.legs);                  /* the move itself and nothing after it */
}

void test_frozen_encoder_on_a_small_move_runs_out_of_trims_not_out_of_travel(void)
{
    Sim m; sim_init(&m); m.frozen = true; m.frozen_value = 0;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    sim_begin(&m, &s, &p, 5, true);
    sim_run(&m, &s, &p);
    /* too little drive to call the encoder dead: the trims run out and the move is MISSED, not faulted */
    TEST_ASSERT_TRUE(m.missed || m.fault == PID_FAULT_NO_RESPONSE);
    TEST_ASSERT_FALSE(m.done);
    /* 5 usteps asked, six trims - unanswered ones grow, but none beyond the shortfall plus half the accepted
       overshoot (3 usteps here): the drive is bounded by the budget */
    TEST_ASSERT_TRUE(m.travel <= 5 + 6 * (5 + 2));
}

void test_encoder_beyond_the_watchdog_is_a_fault_not_a_correction(void)
{
    Sim m; sim_init(&m);
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.max_dev_usteps = 200;
    sim_begin(&m, &s, &p, 1000, true);
    m.frame = -500;                                      /* the stage lets go mid-move */
    sim_run(&m, &s, &p);
    TEST_ASSERT_EQUAL_UINT8(PID_FAULT_WATCHDOG, m.fault);
    TEST_ASSERT_EQUAL_INT32(1, m.legs);
}

void test_frames_that_disagree_beyond_the_watchdog_fault_before_anything_moves(void)
{
    /* counter 500, encoder 100 (a chip reset mid-travel zeroed one frame): planning from the encoder
       would carry the stage 400 usteps further than the host asked. Not one ustep may be issued. */
    Sim m; sim_init(&m); m.motor = m.stage = 500; m.xactual = m.xtarget = 500; m.frame = -400;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.max_dev_usteps = 200;
    sim_begin(&m, &s, &p, 511, true);
    TEST_ASSERT_EQUAL_UINT8(PID_FAULT_WATCHDOG, m.fault);
    TEST_ASSERT_EQUAL_INT32(0, m.legs);
    TEST_ASSERT_FALSE(settle_busy(&s));
    /* inside the watchdog the same disagreement is simply where the stage is */
    m.frame = -150; m.fault = 0;
    sim_begin(&m, &s, &p, 511, true);
    TEST_ASSERT_EQUAL_UINT8(0, m.fault);
    TEST_ASSERT_EQUAL_INT32(500 + 11 + 150, m.leg_to[0]);
    sim_run(&m, &s, &p);
    assert_settled(&m, &p, 511);
}

void test_correction_budget_is_a_timeout(void)
{
    /* a stage that answers every trim with a third of it: converging, but slower than the budget */
    Sim m; sim_init(&m); m.gain = 0.2;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.max_trims = 15; p.correct_budget_us = 30000u;
    sim_begin(&m, &s, &p, 400, true);
    sim_run(&m, &s, &p);
    /* the encoder answers, the stage is merely slow: a missed move, no latch, and the counter tells the truth */
    TEST_ASSERT_TRUE(m.missed);
    TEST_ASSERT_EQUAL_UINT8(0, m.fault);
    TEST_ASSERT_FALSE(m.done);
    TEST_ASSERT_TRUE(s.rep_missed);
    TEST_ASSERT_INT_WITHIN(1, (int32_t)floor(sim_true_position(&m) + 0.5), m.rebase_to);
    /* ... and the axis is usable: the next move settles */
    m.gain = 1.0; p.correct_budget_us = 1000000u; settle_state_init(&s);
    sim_begin(&m, &s, &p, 450, true); sim_run(&m, &s, &p);
    assert_settled(&m, &p, 450);
}

void test_an_overshoot_inside_the_band_is_accepted_and_does_not_accumulate(void)
{
    Sim m; sim_init(&m); m.gap = 8;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.bias_sigma_x16 = 0;      /* tol 2 short, 3 past */
    sim_begin(&m, &s, &p, 1000, true); sim_run(&m, &s, &p);
    sim_begin(&m, &s, &p, 1011, true);
    m.kick = 3;                                          /* lands 3 past: inside the band */
    sim_run(&m, &s, &p);
    TEST_ASSERT_TRUE(m.done);
    TEST_ASSERT_EQUAL_UINT8(0, s.rep_reapproaches);
    TEST_ASSERT_EQUAL_UINT8(0, s.rep_trims);
    TEST_ASSERT_EQUAL_INT32(1, m.legs);
    /* the next plane is planned from where the stage is, so the overshoot is not carried into ITS target. What the
       policy no longer assumes is that a stage found further along than planned is that much further ahead of the
       flank (bench 2026-09-21: it was the play that had been smaller, and the plane planned on the booked gap went
       17 usteps too far): here, where the kick really did move the stage off the flank, that costs a trim. */
    sim_begin(&m, &s, &p, 1022, true); sim_run(&m, &s, &p);
    assert_settled(&m, &p, 1022);
    TEST_ASSERT_EQUAL_UINT8(0, s.rep_reapproaches);
}

void test_beyond_the_band_backs_off_once_and_then_misses(void)
{
    Sim m; sim_init(&m); m.gap = 8;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    sim_begin(&m, &s, &p, 1000, true); sim_run(&m, &s, &p);
    sim_begin(&m, &s, &p, 1011, true);
    m.kick = 6;                                          /* 6 past: a disturbance, not landing scatter */
    sim_run(&m, &s, &p);
    assert_settled(&m, &p, 1011);
    TEST_ASSERT_EQUAL_UINT8(1, s.rep_reapproaches);
    /* past the band again with the one back-off already spent: the move is MISSED - no second back-off,
       no fault, and the counter is handed the encoder's reading */
    SettleInputs in = {}; in.correct_allowed = true; in.limit_lo = -100000; in.limit_hi = 100000; in.now_us = 9000;
    SettleActions out = {};
    s.state = SETTLE_MEASURE; s.leg = SETTLE_LEG_APPROACH; s.dir = 1; s.target = 2000; s.goal = 2000; s.x_leg = 2000;
    s.reapproaches = 1; s.trims = 0; s.stopped_once = true; s.t_first_stop_us = 9000; s.leg_dir = 0;
    settle_decide(&s, &p, &in, 2006 * SETTLE_X16 + 5, 0, &out);
    TEST_ASSERT_TRUE(out.missed);
    TEST_ASSERT_FALSE(out.move);
    TEST_ASSERT_EQUAL_UINT8(0, out.fault);
    TEST_ASSERT_EQUAL_INT32(2006, out.rebase_to);
    TEST_ASSERT_FALSE(settle_busy(&s));
    TEST_ASSERT_TRUE(s.rep_missed);
}

void test_a_trim_aims_at_the_near_side_of_the_band(void)
{
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.gain_x16 = 16; p.learn_x16 = 0;
    SettleInputs in = {}; in.correct_allowed = true; in.limit_lo = -100000; in.limit_hi = 100000; in.now_us = 5000;
    SettleActions out = {};
    s.state = SETTLE_MEASURE; s.leg = SETTLE_LEG_APPROACH; s.dir = 1; s.target = 100; s.goal = 100; s.x_leg = 95;
    s.stopped_once = true; s.t_first_stop_us = 5000; s.model_init = true;
    settle_decide(&s, &p, &in, 95 * SETTLE_X16, 0, &out);              /* 5 short, tol 2 */
    TEST_ASSERT_TRUE(out.move);
    TEST_ASSERT_EQUAL_INT32(95 + 4, out.move_to);                       /* aims 1 short of the target, not at it */
}

void test_the_undershoot_bias_is_learned_from_the_landing_scatter(void)
{
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    TEST_ASSERT_EQUAL_INT32(0, s.land_mad_x256);
    /* feed first landings that miss the plan by +-2 usteps */
    s.model_init = true; s.side = 1;
    for (int k = 0; k < 40; k++)
    {
        s.leg_dir = 1; s.leg_learn = true; s.leg_observed = true; s.leg_against = false; s.landed_once = false;
        s.leg = SETTLE_LEG_APPROACH; s.limited = false;
        s.leg_n_x16 = 11 * SETTLE_X16; s.leg_from_x16 = 0; s.leg_push_x16 = 11 * SETTLE_X16; s.leg_expect_x16 = 11 * SETTLE_X16;
        settle_model_learn(&s, &p, (11 + ((k & 1) ? 2 : -2)) * SETTLE_X16);
    }
    TEST_ASSERT_INT_WITHIN(16, 2 * 256, s.land_mad_x256);
    /* half a sigma of 2.5 usteps would be 1.25; the cap is half the tolerance = 1 ustep */
    SettleInputs in = {}; in.correct_allowed = true; in.limit_lo = -100000; in.limit_hi = 100000; in.xactual = 0;
    SettleActions out = {};
    s.last_dir = 1; s.side = 1; s.gap_x16 = 0; s.carry_x16[0] = s.carry_x16[1] = 0;
    settle_begin(&s, &p, 100, 0, true, &in, &out);
    TEST_ASSERT_EQUAL_INT32(99, out.move_to);
}

void test_legs_never_leave_the_travel_limits_and_a_pinned_target_is_not_a_fault(void)
{
    /* the stage is 20 usteps lower than the counter believes and the target is the upper limit:
       reaching it on the encoder would take the counter past the limit. Accept what is reachable. */
    Sim m; sim_init(&m); m.motor = m.stage = 9950; m.xactual = m.xtarget = 9950; m.frame = -20; m.hi = 10000;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    sim_begin(&m, &s, &p, 10000, true);
    TEST_ASSERT_EQUAL_INT32(10000, m.leg_to[0]);         /* clamped (the harness asserts every leg, too) */
    sim_run(&m, &s, &p);
    TEST_ASSERT_TRUE(m.done);
    TEST_ASSERT_EQUAL_UINT8(0, m.fault);
    TEST_ASSERT_TRUE(s.rep_limited);
}

void test_ramp_stopped_short_by_the_chip_hands_back_the_frame(void)
{
    Sim m; sim_init(&m); m.motor = m.stage = 500; m.xactual = m.xtarget = 500; m.frame = -30; m.hard_stop_at = 520;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    sim_begin(&m, &s, &p, 600, true);                    /* counter leg 500 -> 630, stopped at 520 */
    sim_run(&m, &s, &p);
    TEST_ASSERT_TRUE(m.stopped_short);
    TEST_ASSERT_FALSE(m.done);
    TEST_ASSERT_EQUAL_INT32(520 - 30, m.rebase_to);      /* counter 520 is host 490 */
}

/* ---- where the encoder is not evidence ---------------------------------------------------------- */

void test_inside_the_home_zone_completion_is_on_the_counter(void)
{
    /* gap stage resting on its stop: the encoder stands still while the actuator moves. Nothing is
       measured, nothing corrected, nothing faulted - even with a watchdog a tenth of the "error". */
    Sim m; sim_init(&m); m.frozen = true; m.frozen_value = 0; m.correct_allowed = false;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.max_dev_usteps = 100;
    sim_begin(&m, &s, &p, 3000, false);
    TEST_ASSERT_EQUAL_INT32(3000, m.leg_to[0]);
    double t = sim_run(&m, &s, &p);
    TEST_ASSERT_TRUE(m.done);
    TEST_ASSERT_EQUAL_UINT8(0, m.fault);
    TEST_ASSERT_EQUAL_INT32(1, m.legs);
    TEST_ASSERT_TRUE(t < 2 * sqrt(3000 / 3.2e6) + 0.002);   /* no window was waited for */
    TEST_ASSERT_FALSE(s.last_mean_valid);
}

void test_counter_planned_move_that_ends_where_the_encoder_counts_is_corrected(void)
{
    Sim m; sim_init(&m); m.gap = 10; m.frame = 0;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    m.correct_allowed = false;
    sim_begin(&m, &s, &p, 4000, false);                  /* leaves the zone during the move ... */
    m.correct_allowed = true;                            /* ... and the frames were aligned at rest */
    m.frame = -7;                                        /* the counter's landing is 7 usteps low on the encoder */
    sim_run(&m, &s, &p);
    assert_settled(&m, &p, 4000);
    TEST_ASSERT_TRUE(s.rep_trims >= 1);
}

void test_a_command_during_a_move_settle_is_planned_in_the_counter_frame(void)
{
    Sim m; sim_init(&m); m.motor = m.stage = 500; m.xactual = m.xtarget = 500; m.frame = -30;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    sim_begin(&m, &s, &p, 600, true);                    /* counter leg to 630: offset 30 */
    TEST_ASSERT_EQUAL_INT32(30, settle_offset(&s));
    sim_begin(&m, &s, &p, 700, true);                    /* a second command before the first finished */
    TEST_ASSERT_EQUAL_INT32(730, m.leg_to[0]);
    sim_run(&m, &s, &p);
    assert_settled(&m, &p, 700);
    TEST_ASSERT_EQUAL_INT32(0, settle_offset(&s));
}

/* ---- measuring ---------------------------------------------------------------------------------- */

void test_acceptance_is_inclusive_at_the_tolerance_and_not_beyond(void)
{
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    SettleInputs in = {}; in.correct_allowed = true; in.limit_lo = -100000; in.limit_hi = 100000; in.now_us = 5000;
    SettleActions out = {};
    /* exactly tol short: done */
    s.state = SETTLE_MEASURE; s.leg = SETTLE_LEG_APPROACH; s.dir = 1; s.target = 100; s.goal = 100; s.x_leg = 100;
    s.stopped_once = true; s.t_first_stop_us = 5000;
    settle_decide(&s, &p, &in, (100 - p.tol_usteps) * SETTLE_X16, 0, &out);
    TEST_ASSERT_TRUE(out.done);
    /* one sixteenth more: a trim, upward, of at least one ustep */
    SettleActions out2 = {};
    s.state = SETTLE_MEASURE; s.leg = SETTLE_LEG_APPROACH; s.trims = 0;
    settle_decide(&s, &p, &in, (100 - p.tol_usteps) * SETTLE_X16 - 1, 0, &out2);
    TEST_ASSERT_FALSE(out2.done);
    TEST_ASSERT_TRUE(out2.move);
    TEST_ASSERT_TRUE(out2.move_to > 100);
    /* an accepted overshoot wider than the tolerance */
    SettleActions out3 = {};
    p.tol_over_usteps = 4;
    s.state = SETTLE_MEASURE; s.leg = SETTLE_LEG_APPROACH; s.trims = 0; s.x_leg = 100;
    settle_decide(&s, &p, &in, (100 + 4) * SETTLE_X16, 0, &out3);
    TEST_ASSERT_TRUE(out3.done);
}

void test_the_window_mean_beats_the_quantisation(void)
{
    /* the stage sits 0.4 ustep above a count boundary and rings: single reads flicker between two
       counts, the window mean must land within a quarter ustep of the truth */
    Sim m; sim_init(&m); m.ring_amp = 0.15;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    m.frame = 0.4;
    sim_begin(&m, &s, &p, 220, true);
    sim_run(&m, &s, &p);
    assert_settled(&m, &p, 220);
    TEST_ASSERT_TRUE(s.last_mean_valid);
    TEST_ASSERT_TRUE(fabs(s.last_mean_x16 / 16.0 - sim_true_position(&m)) < 0.3);
}

void test_window_waits_for_its_minimum_of_samples(void)
{
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.min_samples = 4;
    SettleInputs in = {}; in.correct_allowed = true; in.limit_lo = -1000; in.limit_hi = 1000;
    s.target = 10; s.goal = 10; s.dir = 1; s.x_leg = 10; s.stopped_once = true; s.t_first_stop_us = 0;
    settle_start_window(&s, 0, 0);
    for (int i = 1; i <= 3; i++)
    {
        SettleActions out = {};
        in.now_us = 10000u * i; in.enc_valid = true; in.enc = 10;   /* long past the window, too few samples */
        settle_step(&s, &p, &in, &out);
        TEST_ASSERT_FALSE(out.done);
    }
    SettleActions out = {};
    in.now_us = 40000u; in.enc_valid = true; in.enc = 10;
    settle_step(&s, &p, &in, &out);
    TEST_ASSERT_TRUE(out.done);
}

void test_settle_requirement_delays_done_but_never_faults(void)
{
    Sim m; sim_init(&m); m.ring_amp = 0.25;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    sim_begin(&m, &s, &p, 110, true); double t_plain = sim_run(&m, &s, &p); assert_settled(&m, &p, 110);
    p.quiet_pp_usteps = 1; p.max_quiet_windows = 5;
    sim_begin(&m, &s, &p, 220, true); double t_settled = sim_run(&m, &s, &p); assert_settled(&m, &p, 220);
    TEST_ASSERT_TRUE(t_settled > t_plain + 0.008);       /* at least one more window */
    TEST_ASSERT_TRUE(t_settled < t_plain + 6 * 0.0095);  /* and never more than it may wait for */
}

void test_split_leg_is_two_parts_half_a_period_apart(void)
{
    Sim m; sim_init(&m);
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    p.split_half_period_us = 4420; p.split_first_x256 = 144; p.split_max_usteps = 64;
    sim_begin(&m, &s, &p, 11, true);
    TEST_ASSERT_EQUAL_UINT8(SETTLE_MOVE_A, s.state);
    TEST_ASSERT_EQUAL_INT32(6, m.leg_to[0]);             /* 11 x 144/256 = 6.2 */
    sim_run(&m, &s, &p);
    assert_settled(&m, &p, 11);
    TEST_ASSERT_EQUAL_INT32(2, m.legs);
    TEST_ASSERT_EQUAL_INT32(11, m.leg_to[1]);
    double gap_s = m.leg_at_s[1] - m.leg_at_s[0];
    TEST_ASSERT_TRUE(gap_s >= 0.00442 && gap_s < 0.00442 + 0.0004);
    /* a long leg is one ramp: its deceleration, not its length, is what rings */
    sim_begin(&m, &s, &p, 1000, true);
    TEST_ASSERT_EQUAL_UINT8(SETTLE_MOVE, s.state);
    sim_run(&m, &s, &p); assert_settled(&m, &p, 1000);
}

void test_time_arithmetic_survives_the_micros_wrap(void)
{
    Sim m; sim_init(&m); m.gap = 12; m.ring_amp = 0.1;
    m.t0_us = 0xFFFFFFFFu - 6000u;                        /* micros() wraps during the first window */
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);
    p.split_half_period_us = 4420; p.split_max_usteps = 64;
    sim_begin(&m, &s, &p, 11, true);
    double t = sim_run(&m, &s, &p);
    assert_settled(&m, &p, 11);
    TEST_ASSERT_TRUE(t < 0.080);   /* a timer lost in the wrap runs to the harness limit (12 s), not to tens of ms */
}

void test_the_window_opens_only_after_the_settle_time(void)
{
    /* the arrival transient must not be averaged: no sample before wait_us has passed */
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.wait_us = 5000u; p.window_us = 9000u;
    SettleInputs in = {}; in.correct_allowed = true; in.limit_lo = -1000; in.limit_hi = 1000;
    s.target = 10; s.goal = 10; s.dir = 1; s.x_leg = 10; s.x_cmd = 10; s.state = SETTLE_MOVE; s.leg_from_encoder = true;
    SettleActions out = {};
    in.now_us = 1000; in.ramp_running = false; in.xactual = 10;
    settle_step(&s, &p, &in, &out);                       /* ramp idle: settle starts */
    TEST_ASSERT_EQUAL_UINT8(SETTLE_MEASURE, s.state);
    for (uint32_t t = 1300; t < 6000; t += 300)
    {
        SettleActions o = {}; in.now_us = t; in.enc_valid = true; in.enc = 99;   /* a transient far from the truth */
        settle_step(&s, &p, &in, &o);
        TEST_ASSERT_EQUAL_UINT16(0, s.n);
    }
    bool done = false;
    for (uint32_t t = 6000; t < 16000 && !done; t += 300)
    {
        SettleActions o = {}; in.now_us = t; in.enc_valid = true; in.enc = 10;
        settle_step(&s, &p, &in, &o);
        done = o.done;
    }
    TEST_ASSERT_TRUE(done);
    TEST_ASSERT_EQUAL_INT32(10 * SETTLE_X16, s.last_mean_x16);
}

void test_offset_is_zero_at_rest(void)
{
    SettleState s; settle_state_init(&s);
    TEST_ASSERT_FALSE(settle_busy(&s));
    TEST_ASSERT_EQUAL_INT32(0, settle_offset(&s));
}

/* ---- the counter at rest: what every wait on the ramp generator is bounded with ---- */

void test_rest_watch_stays_quiet_while_the_counter_moves(void)
{
    SettleRestWatch w = {};
    uint32_t t = 1000;
    for (int32_t xa = 0; xa < 40; xa++, t += 25000u)
        TEST_ASSERT_FALSE(settle_rest_step(&w, xa, t, 50000u));      /* one ustep per sample is motion */
}

void test_rest_watch_fires_once_the_counter_has_stood_for_the_limit(void)
{
    SettleRestWatch w = {};
    TEST_ASSERT_FALSE(settle_rest_step(&w, 500, 0u, 50000u));        /* arms */
    TEST_ASSERT_FALSE(settle_rest_step(&w, 500, 25000u, 50000u));
    TEST_ASSERT_TRUE(settle_rest_step(&w, 500, 50000u, 50000u));
    /* it says where, whether or not that is the target: a ramp whose flags never clear AT the target
       (the old watch required xactual != x_cmd and VACTUAL == 0, and waited for ever otherwise) */
    TEST_ASSERT_TRUE(settle_rest_step(&w, 500, 75000u, 50000u));
}

void test_rest_watch_starts_over_when_the_counter_moves_or_a_new_wait_begins(void)
{
    SettleRestWatch w = {};
    settle_rest_step(&w, 500, 0u, 50000u);
    settle_rest_step(&w, 500, 40000u, 50000u);
    TEST_ASSERT_FALSE(settle_rest_step(&w, 501, 50000u, 50000u));    /* moved: the time counts from here */
    TEST_ASSERT_FALSE(settle_rest_step(&w, 501, 90000u, 50000u));
    TEST_ASSERT_TRUE(settle_rest_step(&w, 501, 100000u, 50000u));
    /* a new leg from the same place, long after: a watch that was not reset would fire on its first
       sample, before the ramp has made its first ustep */
    settle_rest_reset(&w);
    TEST_ASSERT_FALSE(settle_rest_step(&w, 501, 9000000u, 50000u));
}

void test_rest_watch_survives_the_micros_wrap(void)
{
    SettleRestWatch w = {};
    TEST_ASSERT_FALSE(settle_rest_step(&w, 7, 0xFFFFF000u, 50000u));
    TEST_ASSERT_FALSE(settle_rest_step(&w, 7, 0xFFFFF000u + 25000u, 50000u));   /* wrapped */
    TEST_ASSERT_TRUE(settle_rest_step(&w, 7, 0xFFFFF000u + 50000u, 50000u));
}

/* ---- motion the policy did not issue: the next move is planned on the reckoned state and teaches nothing ---- */

static void reversals_learned(Sim *m, SettleState *s, SettleParams *p, int32_t *target)
{
    sim_init(m); m->gap = 12;
    settle_state_init(s); settle_params_default(p);
    *target = 2000;
    sim_begin(m, s, p, *target, true); sim_run(m, s, p);
    for (int k = 0; k < 24; k++)
    {
        *target += (k % 2) ? 40 : -40;
        sim_begin(m, s, p, *target, true); sim_run(m, s, p);
    }
}

void test_the_first_move_after_motion_of_others_teaches_nothing(void)
{
    Sim m; SettleState s; SettleParams p; int32_t target;
    reversals_learned(&m, &s, &p, &target);              /* the last move went +40: the + flank has the stage */
    int32_t lost[2] = {s.lost_x16[0], s.lost_x16[1]}, carry[2] = {s.carry_x16[0], s.carry_x16[1]};
    int32_t mad = s.land_mad_x256, rev = s.rev_mad_x256;
    /* booked wrongly (bench 2026-09-20: a slow wheel reversal, an open-loop approach before an ENABLE that
       adopts the encoder): "carried on the - flank of a long ramp", while the plant has not moved */
    settle_note_external_motion(&s, &p, -100000);
    TEST_ASSERT_TRUE(s.teach_hold);
    target -= 40;                                        /* planned as a continuing leg, it is a reversal: lands the play short */
    sim_begin(&m, &s, &p, target, true); sim_run(&m, &s, &p); assert_settled(&m, &p, target);
    TEST_ASSERT_TRUE_MESSAGE(s.rep_trims > 0, "the plant should have made this plan land short");
    TEST_ASSERT_EQUAL_INT32(lost[0], s.lost_x16[0]); TEST_ASSERT_EQUAL_INT32(lost[1], s.lost_x16[1]);
    TEST_ASSERT_EQUAL_INT32(carry[0], s.carry_x16[0]); TEST_ASSERT_EQUAL_INT32(carry[1], s.carry_x16[1]);
    TEST_ASSERT_EQUAL_INT32(mad, s.land_mad_x256); TEST_ASSERT_EQUAL_INT32(rev, s.rev_mad_x256);
    TEST_ASSERT_FALSE_MESSAGE(s.teach_hold, "a measured landing of the policy's own lifts the hold");
    target += 40;                                        /* and the model it kept still fits: the reversal lands as before */
    sim_begin(&m, &s, &p, target, true); sim_run(&m, &s, &p); assert_settled(&m, &p, target);
    TEST_ASSERT_EQUAL_UINT8(0, s.rep_trims);
}

/* ---- a trim the stage does not answer: the flank is further away than the model believed ---- */

/* The plant of the bench's six-trim miss: the model believes the + flank has the stage (gap 0), while the motor
   stands `back` usteps inside the play - what a misreckoned wheel episode leaves. */
static void flank_further_than_believed(Sim *m, SettleState *s, SettleParams *p, int back)
{
    sim_init(m); m->gap = 120;
    settle_state_init(s); settle_params_default(p);
    p->tol_usteps = 8; p->tol_over_usteps = 12; p->learn_x16 = 0; p->min_drive_usteps = 128;   /* as at 64 usteps/FS */
    sim_begin(m, s, p, 2000, true); sim_run(m, s, p); assert_settled(m, p, 2000);
    m->xactual -= back; m->xtarget = m->xactual;
    sim_move_motor(m, m->xactual);                       /* inside the play: the stage stays */
}

void test_unanswered_trims_grow_until_the_flank_reaches_the_stage(void)
{
    Sim m; SettleState s; SettleParams p;
    flank_further_than_believed(&m, &s, &p, 50);
    /* 10 usteps from where the stage is: the first leg moves nothing, trims of (10 - 4) x 0.75 = 4.5 usteps would need nine */
    int32_t target = sim_encoder(&m) + 10;
    sim_begin(&m, &s, &p, target, true); sim_run(&m, &s, &p);
    TEST_ASSERT_FALSE_MESSAGE(m.missed, "six equal trims never reach the stage: the unanswered ones have to grow");
    TEST_ASSERT_EQUAL_UINT8(0, m.fault);
    assert_settled(&m, &p, target);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(0, s.rep_reapproaches, "a grown trim must not land past the band");
}

void test_a_grown_trim_cannot_land_past_the_band_wherever_the_flank_meets_the_stage(void)
{
    for (int back = 11; back <= 50; back++)              /* every place the flank can be when the trims start */
    {
        Sim m; SettleState s; SettleParams p;
        flank_further_than_believed(&m, &s, &p, back);
        int32_t target = sim_encoder(&m) + 10;
        sim_begin(&m, &s, &p, target, true); sim_run(&m, &s, &p);
        TEST_ASSERT_FALSE(m.missed);
        assert_settled(&m, &p, target);
        TEST_ASSERT_EQUAL_UINT8(0, s.rep_reapproaches);
    }
}

void test_answered_trims_are_sized_as_before(void)
{
    Sim m; sim_init(&m); m.gap = 12;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.tol_usteps = 8; p.tol_over_usteps = 12; p.learn_x16 = 0;
    sim_begin(&m, &s, &p, 2000, true); sim_run(&m, &s, &p);
    m.kick = -14;                                        /* lands, then sits 14 short: in contact, every trim is answered */
    sim_begin(&m, &s, &p, 2100, true); sim_run(&m, &s, &p); assert_settled(&m, &p, 2100);
    /* legs: the approach, then (14 - 4) x 0.75 = 7.5 -> one trim of 8 usteps, nothing doubled */
    TEST_ASSERT_EQUAL_INT(2, m.legs);
    TEST_ASSERT_INT_WITHIN(1, 8, m.leg_to[1] - m.leg_to[0]);
}

/* ---- long legs end with a short finishing leg (bench 2026-09-26) ---- */

/* the split is a property of the first leg's plan: in contact on the + flank, nothing learned, planning from 0.
   50 um finishing leg from 1 mm, at 64 usteps/FS on a 0.3 mm screw (42.667 usteps/um) */
static void finish_state(SettleState *s, SettleParams *p, SettleInputs *in)
{
    settle_state_init(s); settle_params_default(p); p->learn_x16 = 0;
    p->finish_usteps = 2133; p->finish_from_usteps = 42667;
    s->model_init = true; s->side = 1; s->last_dir = 1;
    SettleInputs z = {}; *in = z;
    in->correct_allowed = true; in->limit_lo = -1000000; in->limit_hi = 1000000; in->now_us = 5000;
}

void test_with_the_finishing_leg_off_a_long_move_is_one_leg_to_the_target(void)
{
    Sim m; sim_init(&m);
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p);                    /* finish 0: as before */
    sim_begin(&m, &s, &p, 85333, true);                            /* 2 mm */
    TEST_ASSERT_FALSE(s.finish_pending);
    TEST_ASSERT_EQUAL_INT32(85333, m.leg_to[0]);
    sim_run(&m, &s, &p);
    assert_settled(&m, &p, 85333);
    TEST_ASSERT_EQUAL_INT32(1, m.legs);
}

void test_a_long_move_ends_with_a_short_finishing_leg(void)
{
    SettleState s; SettleParams p; SettleInputs in; finish_state(&s, &p, &in);
    SettleActions out = {};
    settle_begin(&s, &p, 85333, 0, true, &in, &out);               /* 2 mm: the long leg aims 50 um short */
    TEST_ASSERT_TRUE(out.move);
    TEST_ASSERT_EQUAL_INT32(85333 - 2133, out.move_to);
    TEST_ASSERT_EQUAL_INT32((85333 - 2133) * SETTLE_X16, s.leg_expect_x16);
    TEST_ASSERT_TRUE(s.finish_pending);
    TEST_ASSERT_EQUAL_UINT8(SETTLE_LEG_APPROACH, s.leg);
    TEST_ASSERT_TRUE(s.leg_from_encoder);
    /* it lands 40 usteps (~1 um) short of its aim: neither judged nor reported - the finishing leg goes from there */
    s.stopped_once = true; s.t_first_stop_us = in.now_us;
    SettleActions out2 = {};
    settle_decide(&s, &p, &in, (85333 - 2133 - 40) * SETTLE_X16, 0, &out2);
    TEST_ASSERT_TRUE(out2.move);
    TEST_ASSERT_FALSE(out2.done); TEST_ASSERT_FALSE(out2.missed); TEST_ASSERT_EQUAL_UINT8(0, out2.fault);
    TEST_ASSERT_EQUAL_INT32(85333 + 40, out2.move_to);            /* the rest, from the measured landing: the counter goes 40 past */
    TEST_ASSERT_EQUAL_UINT8(SETTLE_LEG_APPROACH, s.leg);
    TEST_ASSERT_FALSE(s.finish_pending);
    TEST_ASSERT_FALSE(s.landed_once);
    TEST_ASSERT_EQUAL_INT8(0, s.rep_first_landing);
    TEST_ASSERT_EQUAL_UINT8(0, s.trims); TEST_ASSERT_EQUAL_UINT8(0, s.reapproaches);
    TEST_ASSERT_EQUAL_INT32(0, s.land_mad_x256);                   /* the long leg's miss is not first-landing scatter */
    /* the finishing leg lands one ustep short: inside the band, DONE, and THAT is the first landing reported */
    SettleActions out3 = {};
    settle_decide(&s, &p, &in, (85333 - 1) * SETTLE_X16, 0, &out3);
    TEST_ASSERT_TRUE(out3.done);
    TEST_ASSERT_TRUE(s.landed_once);
    TEST_ASSERT_EQUAL_INT8(1, s.rep_first_landing);
    TEST_ASSERT_EQUAL_UINT8(0, s.rep_trims);
    TEST_ASSERT_FALSE(settle_busy(&s));
    /* on the simulated plant, with lost motion, carry and a screw lead, from the chain state: the long leg, the
       finishing leg, DONE - no trim, no back-off */
    Sim m; SettleState t; SettleParams q; long_plant(&m, &t, &q); q.scale_ppm = -1100;
    q.finish_usteps = 2133; q.finish_from_usteps = 42667;
    sim_begin(&m, &t, &q, 1000, true); sim_run(&m, &t, &q);
    sim_begin(&m, &t, &q, 1011, true); sim_run(&m, &t, &q); assert_settled(&m, &q, 1011);
    sim_begin(&m, &t, &q, 86344, true);                            /* 2 mm up */
    TEST_ASSERT_TRUE(t.finish_pending);
    TEST_ASSERT_INT_WITHIN(200, 86344 - 2133, m.leg_to[0]);        /* the screw's lead and the take-up are in the plan */
    sim_run(&m, &t, &q); assert_settled(&m, &q, 86344);
    TEST_ASSERT_EQUAL_INT(2, m.legs);
    TEST_ASSERT_EQUAL_UINT8(0, t.rep_trims); TEST_ASSERT_EQUAL_UINT8(0, t.rep_reapproaches);
    TEST_ASSERT_TRUE(t.rep_first_landing >= -q.tol_over_usteps && t.rep_first_landing <= q.tol_usteps);
    TEST_ASSERT_FALSE(t.finish_pending);
}

void test_a_move_inside_the_threshold_is_not_split(void)
{
    SettleState s; SettleParams p; SettleInputs in; finish_state(&s, &p, &in);
    SettleActions out = {};
    settle_begin(&s, &p, 42667, 0, true, &in, &out);               /* exactly the threshold: one leg to the target */
    TEST_ASSERT_EQUAL_INT32(42667, out.move_to);
    TEST_ASSERT_FALSE(s.finish_pending);
    finish_state(&s, &p, &in); SettleActions out2 = {};
    settle_begin(&s, &p, 42668, 0, true, &in, &out2);              /* one more: split */
    TEST_ASSERT_EQUAL_INT32(42668 - 2133, out2.move_to);
    TEST_ASSERT_TRUE(s.finish_pending);
    finish_state(&s, &p, &in); s.side = -1; s.last_dir = -1; SettleActions out3 = {};
    settle_begin(&s, &p, -50000, 0, true, &in, &out3);             /* the same going negative */
    TEST_ASSERT_EQUAL_INT32(-50000 + 2133, out3.move_to);
    TEST_ASSERT_TRUE(s.finish_pending);
    /* threshold 0: every leg longer than the finishing leg itself is split; the finishing leg alone is not */
    finish_state(&s, &p, &in); p.finish_from_usteps = 0; SettleActions out4 = {};
    settle_begin(&s, &p, 2133, 0, true, &in, &out4);
    TEST_ASSERT_EQUAL_INT32(2133, out4.move_to);
    TEST_ASSERT_FALSE(s.finish_pending);
    finish_state(&s, &p, &in); p.finish_from_usteps = 0; SettleActions out5 = {};
    settle_begin(&s, &p, 2140, 0, true, &in, &out5);
    TEST_ASSERT_EQUAL_INT32(7, out5.move_to);
    TEST_ASSERT_TRUE(s.finish_pending);
    /* a landing the watchdog would fault is no place to stop: unsplit */
    finish_state(&s, &p, &in); p.max_dev_usteps = 2000; SettleActions out6 = {};
    settle_begin(&s, &p, 85333, 0, true, &in, &out6);
    TEST_ASSERT_EQUAL_INT32(85333, out6.move_to);
    TEST_ASSERT_FALSE(s.finish_pending);
}

void test_a_miss_or_abort_during_the_long_leg_clears_the_finishing_leg(void)
{
    SettleState s; SettleParams p; SettleInputs in; finish_state(&s, &p, &in);
    SettleActions out = {};
    settle_begin(&s, &p, 85333, 0, true, &in, &out);
    TEST_ASSERT_TRUE(s.finish_pending);
    settle_abort(&s);                                              /* homing, RESET, a fault raised elsewhere */
    TEST_ASSERT_FALSE(s.finish_pending);
    /* the budget spent on the long leg: MISSED, nothing pending */
    finish_state(&s, &p, &in); SettleActions out2 = {};
    settle_begin(&s, &p, 85333, 0, true, &in, &out2);
    s.stopped_once = true; s.t_first_stop_us = in.now_us;
    in.now_us += p.correct_budget_us + 1;
    SettleActions out3 = {};
    settle_decide(&s, &p, &in, (85333 - 2133 - 40) * SETTLE_X16, 0, &out3);
    TEST_ASSERT_TRUE(out3.missed);
    TEST_ASSERT_FALSE(out3.move);
    TEST_ASSERT_FALSE(s.finish_pending);
    /* the watchdog tripped by the long leg's landing: a fault, nothing pending */
    finish_state(&s, &p, &in); p.max_dev_usteps = 4000; SettleActions out4 = {};
    settle_begin(&s, &p, 85333, 0, true, &in, &out4);
    TEST_ASSERT_TRUE(s.finish_pending);
    s.stopped_once = true; s.t_first_stop_us = in.now_us;
    SettleActions out5 = {};
    settle_decide(&s, &p, &in, (85333 - 6000) * SETTLE_X16, 0, &out5);
    TEST_ASSERT_EQUAL_UINT8(PID_FAULT_WATCHDOG, out5.fault);
    TEST_ASSERT_FALSE(s.finish_pending);
    TEST_ASSERT_FALSE(settle_busy(&s));
}

/* ---- a counter-planned leg whose landing will be judged ends with a finishing leg too (bench 2026-09-26, x9_home_a) ---- */

/* out of the home zone: 150 um -> 250 um (4267 usteps), the encoder 60 usteps (1.4 um) above the counter */
static void drifted_plant(Sim *m, SettleState *s, SettleParams *p)
{
    sim_init(m); m->gap = 10; m->carry = 5; m->motor = m->stage = 6400; m->xactual = m->xtarget = 6400; m->frame = 60;
    settle_state_init(s);
    settle_params_default(p); p->carry_usteps = 5; p->lost_motion_usteps = 10; p->fast_min_usteps = 8;
    p->finish_usteps = 2133; p->finish_from_usteps = 42667;        /* 1 mm: not what decides here */
}

void test_a_counter_planned_move_that_will_be_judged_is_finished_from_the_encoder(void)
{
    SettleState s; SettleParams p; SettleInputs in; finish_state(&s, &p, &in);
    in.xactual = 6400; in.split_allowed = true;
    SettleActions out = {};
    settle_begin(&s, &p, 10667, 0, false, &in, &out);              /* far below finish_from: the counter's drift is the reason */
    TEST_ASSERT_TRUE(out.move);
    TEST_ASSERT_EQUAL_INT32(10667 - 2133, out.move_to);
    TEST_ASSERT_TRUE(s.finish_pending);
    TEST_ASSERT_EQUAL_UINT8(SETTLE_LEG_APPROACH, s.leg);
    TEST_ASSERT_EQUAL_INT8(1, s.dir);
    TEST_ASSERT_FALSE(s.leg_from_encoder);                         /* a counter-planned ramp like any other: nothing learned from it */
    TEST_ASSERT_FALSE(s.leg_observed);
    TEST_ASSERT_EQUAL_INT32((4267 - 2133) * SETTLE_X16, s.leg_n_x16);
    TEST_ASSERT_EQUAL_INT32(0, settle_offset(&s));                 /* the counter is the host's frame all along */
    /* the ramp arrives and is measured: the target is where the encoder is evidence */
    in.now_us += 100000; in.xactual = 10667 - 2133;
    SettleActions out1 = {};
    settle_step(&s, &p, &in, &out1);
    TEST_ASSERT_EQUAL_UINT8(SETTLE_MEASURE, s.state);
    TEST_ASSERT_FALSE(out1.done);
    /* the stage is 60 usteps PAST the leg's aim, the counter's drift: not judged - the rest is planned from there */
    SettleActions out2 = {};
    settle_decide(&s, &p, &in, (10667 - 2133 + 60) * SETTLE_X16, 0, &out2);
    TEST_ASSERT_TRUE(out2.move);
    TEST_ASSERT_FALSE(out2.done); TEST_ASSERT_FALSE(out2.missed); TEST_ASSERT_EQUAL_UINT8(0, out2.fault);
    TEST_ASSERT_EQUAL_INT32(10667 - 60, out2.move_to);             /* 2133 - 60 from where the counter stopped */
    TEST_ASSERT_EQUAL_INT32((2133 - 60) * SETTLE_X16, s.leg_n_x16);
    TEST_ASSERT_EQUAL_UINT8(SETTLE_LEG_APPROACH, s.leg);
    TEST_ASSERT_TRUE(s.leg_from_encoder);
    TEST_ASSERT_TRUE(s.leg_observed);
    TEST_ASSERT_FALSE(s.finish_pending);
    TEST_ASSERT_FALSE(s.landed_once);
    TEST_ASSERT_EQUAL_INT8(0, s.rep_first_landing);
    TEST_ASSERT_EQUAL_UINT8(0, s.trims); TEST_ASSERT_EQUAL_UINT8(0, s.reapproaches);
    TEST_ASSERT_EQUAL_INT32(-60, settle_offset(&s));               /* this leg ends at the target: the drift is the offset now */
    /* the finishing leg lands one ustep short: DONE, and that is the first landing reported */
    SettleActions out3 = {};
    settle_decide(&s, &p, &in, (10667 - 1) * SETTLE_X16, 0, &out3);
    TEST_ASSERT_TRUE(out3.done);
    TEST_ASSERT_EQUAL_INT32(10667, out3.rebase_to);
    TEST_ASSERT_EQUAL_INT8(1, s.rep_first_landing);
    TEST_ASSERT_EQUAL_UINT8(0, s.rep_trims); TEST_ASSERT_EQUAL_UINT8(0, s.rep_reapproaches);
    TEST_ASSERT_FALSE(settle_busy(&s));
    /* the finishing leg is biased like any first landing */
    finish_state(&s, &p, &in); p.bias_usteps = 1; in.xactual = 6400; in.split_allowed = true;
    SettleActions out4 = {};
    settle_begin(&s, &p, 10667, 0, false, &in, &out4);
    TEST_ASSERT_EQUAL_INT32(10667 - 2133, out4.move_to);           /* the first leg is not: it is not aimed at the target */
    s.stopped_once = true; s.t_first_stop_us = in.now_us;
    SettleActions out5 = {};
    settle_decide(&s, &p, &in, (10667 - 2133 + 60) * SETTLE_X16, 0, &out5);
    TEST_ASSERT_EQUAL_INT32(10667 - 60 - 1, out5.move_to);
    /* going negative */
    finish_state(&s, &p, &in); s.side = -1; s.last_dir = -1; in.xactual = 14934; in.split_allowed = true;
    SettleActions out6 = {};
    settle_begin(&s, &p, 10667, 0, false, &in, &out6);
    TEST_ASSERT_EQUAL_INT32(10667 + 2133, out6.move_to);
    TEST_ASSERT_EQUAL_INT8(-1, s.dir);
    TEST_ASSERT_TRUE(s.finish_pending);
    TEST_ASSERT_EQUAL_INT32(0, settle_offset(&s));
    /* on the simulated plant, lost motion and carry: two legs, DONE, no trim and no back-off */
    Sim m; SettleState t; SettleParams q; drifted_plant(&m, &t, &q); m.split_allowed = true;
    sim_begin(&m, &t, &q, 10667, false);
    TEST_ASSERT_TRUE(t.finish_pending);
    TEST_ASSERT_EQUAL_INT32(10667 - 2133, m.leg_to[0]);
    sim_run(&m, &t, &q); assert_settled(&m, &q, 10667);
    TEST_ASSERT_EQUAL_INT(2, m.legs);
    TEST_ASSERT_INT_WITHIN(2, 10667 - 60, m.leg_to[1]);
    TEST_ASSERT_EQUAL_UINT8(0, t.rep_trims); TEST_ASSERT_EQUAL_UINT8(0, t.rep_reapproaches);
    TEST_ASSERT_TRUE(t.rep_first_landing >= -q.tol_over_usteps && t.rep_first_landing <= q.tol_usteps);
    TEST_ASSERT_FALSE(t.finish_pending);
}

void test_a_counter_planned_move_without_a_split_point_is_one_leg_as_before(void)
{
    SettleState s; SettleParams p; SettleInputs in; finish_state(&s, &p, &in);
    in.xactual = 6400;                                             /* split_allowed false: the split point is in the zone */
    SettleActions out = {};
    settle_begin(&s, &p, 10667, 0, false, &in, &out);
    TEST_ASSERT_EQUAL_INT32(10667, out.move_to);
    TEST_ASSERT_FALSE(s.finish_pending);
    TEST_ASSERT_FALSE(s.leg_from_encoder);
    TEST_ASSERT_EQUAL_INT32(4267 * SETTLE_X16, s.leg_n_x16);
    TEST_ASSERT_EQUAL_INT32(0, settle_offset(&s));
    /* the finishing leg off: the same, whatever the caller says of the split point */
    finish_state(&s, &p, &in); p.finish_usteps = 0; in.xactual = 6400; in.split_allowed = true;
    SettleActions out2 = {};
    settle_begin(&s, &p, 10667, 0, false, &in, &out2);
    TEST_ASSERT_EQUAL_INT32(10667, out2.move_to);
    TEST_ASSERT_FALSE(s.finish_pending);
    /* and what that costs on the plant (x9_home_a): the landing is past by the drift, one back-off */
    Sim m; SettleState t; SettleParams q; drifted_plant(&m, &t, &q);
    sim_begin(&m, &t, &q, 10667, false);
    TEST_ASSERT_FALSE(t.finish_pending);
    TEST_ASSERT_EQUAL_INT32(10667, m.leg_to[0]);
    sim_run(&m, &t, &q); assert_settled(&m, &q, 10667);
    TEST_ASSERT_INT_WITHIN(2, -60, t.rep_first_landing);
    TEST_ASSERT_EQUAL_UINT8(1, t.rep_reapproaches);
}

void test_a_counter_planned_move_that_ends_in_the_zone_is_not_split(void)
{
    SettleState s; SettleParams p; SettleInputs in; finish_state(&s, &p, &in);
    in.xactual = 6400; in.split_allowed = true; in.correct_allowed = false;   /* its landing will not be judged */
    SettleActions out = {};
    settle_begin(&s, &p, 10667, 0, false, &in, &out);
    TEST_ASSERT_EQUAL_INT32(10667, out.move_to);
    TEST_ASSERT_FALSE(s.finish_pending);
    /* ... and completes on the counter, at the target */
    in.now_us += 100000; in.xactual = 10667;
    SettleActions out2 = {};
    settle_step(&s, &p, &in, &out2);
    TEST_ASSERT_TRUE(out2.done);
    TEST_ASSERT_EQUAL_INT32(10667, out2.rebase_to);
    TEST_ASSERT_EQUAL_INT32(10667, s.x_leg);
}

void test_a_counter_planned_move_no_longer_than_the_finishing_leg_is_not_split(void)
{
    SettleState s; SettleParams p; SettleInputs in; finish_state(&s, &p, &in);
    in.xactual = 6400; in.split_allowed = true;
    SettleActions out = {};
    settle_begin(&s, &p, 6400 + 2133, 0, false, &in, &out);        /* exactly the finishing leg: one leg to the target */
    TEST_ASSERT_EQUAL_INT32(6400 + 2133, out.move_to);
    TEST_ASSERT_FALSE(s.finish_pending);
    finish_state(&s, &p, &in); in.xactual = 6400; in.split_allowed = true; SettleActions out2 = {};
    settle_begin(&s, &p, 6400 + 2134, 0, false, &in, &out2);       /* one more: split */
    TEST_ASSERT_EQUAL_INT32(6401, out2.move_to);
    TEST_ASSERT_TRUE(s.finish_pending);
    finish_state(&s, &p, &in); in.xactual = 6400; in.split_allowed = true; SettleActions out3 = {};
    settle_begin(&s, &p, 6400 - 2133, 0, false, &in, &out3);       /* the same going negative */
    TEST_ASSERT_EQUAL_INT32(6400 - 2133, out3.move_to);
    TEST_ASSERT_FALSE(s.finish_pending);
    /* a landing the watchdog would fault is no place to stop: unsplit */
    finish_state(&s, &p, &in); p.max_dev_usteps = 2000; in.xactual = 6400; in.split_allowed = true; SettleActions out4 = {};
    settle_begin(&s, &p, 10667, 0, false, &in, &out4);
    TEST_ASSERT_EQUAL_INT32(10667, out4.move_to);
    TEST_ASSERT_FALSE(s.finish_pending);
}

void test_a_command_during_a_move_settle_is_not_split(void)
{
    /* its frame is that of the move it interrupts, settled only when it ends: one leg on the counter, as before */
    Sim m; sim_init(&m); m.motor = m.stage = 500; m.xactual = m.xtarget = 500; m.frame = -30; m.split_allowed = true;
    SettleState s; settle_state_init(&s);
    SettleParams p; settle_params_default(&p); p.finish_usteps = 2133; p.finish_from_usteps = 42667;
    sim_begin(&m, &s, &p, 600, true);                              /* counter leg to 630: offset 30 */
    TEST_ASSERT_EQUAL_INT32(30, settle_offset(&s));
    sim_begin(&m, &s, &p, 10667, false);                           /* a second command before the first finished */
    TEST_ASSERT_EQUAL_INT32(10667 + 30, m.leg_to[0]);
    TEST_ASSERT_FALSE(s.finish_pending);
    sim_run(&m, &s, &p);
    assert_settled(&m, &p, 10667);
    /* the same while the first leg of a split move runs: nothing of it is left pending */
    SettleParams q; SettleInputs in; finish_state(&s, &q, &in);
    in.xactual = 6400; in.split_allowed = true;
    SettleActions out = {};
    settle_begin(&s, &q, 10667, 0, false, &in, &out);
    TEST_ASSERT_TRUE(s.finish_pending);
    in.xactual = 7000; SettleActions out2 = {};
    settle_begin(&s, &q, 12800, 0, false, &in, &out2);
    TEST_ASSERT_EQUAL_INT32(12800, out2.move_to);
    TEST_ASSERT_FALSE(s.finish_pending);
}

/* settle_offset() is where the leg in flight ends, counter frame minus host frame: the first leg of a split move
   ends at the split point, not at the target */
void test_the_first_leg_of_a_split_move_keeps_the_frame_offset_true(void)
{
    SettleState s; SettleParams p; SettleInputs in; finish_state(&s, &p, &in);
    in.xactual = 30;                                               /* the counter 30 above the encoder */
    SettleActions out = {};
    settle_begin(&s, &p, 85333, 0, true, &in, &out);               /* 2 mm, planned from the encoder: split */
    TEST_ASSERT_TRUE(s.finish_pending);
    TEST_ASSERT_EQUAL_INT32(85333 - 2133 + 30, out.move_to);
    TEST_ASSERT_EQUAL_INT32(30, settle_offset(&s));
    /* the chip stops the long leg short at counter 40000: that is host 39970 */
    in.now_us += 100000; in.xactual = 40000;
    SettleActions out2 = {};
    settle_step(&s, &p, &in, &out2);
    TEST_ASSERT_TRUE(out2.stopped_short);
    TEST_ASSERT_EQUAL_INT32(40000 - 30, out2.rebase_to);
    TEST_ASSERT_FALSE(s.finish_pending);
    /* the finishing leg ends at the target */
    finish_state(&s, &p, &in); in.xactual = 30; SettleActions out3 = {};
    settle_begin(&s, &p, 85333, 0, true, &in, &out3);
    s.stopped_once = true; s.t_first_stop_us = in.now_us;
    SettleActions out4 = {};
    settle_decide(&s, &p, &in, (85333 - 2133 - 40) * SETTLE_X16, 0, &out4);
    TEST_ASSERT_EQUAL_INT32(85333 + 30 + 40, out4.move_to);
    TEST_ASSERT_EQUAL_INT32(30 + 40, settle_offset(&s));
    /* a command during the long leg is planned in the counter's frame and lands on ITS target */
    Sim m; sim_init(&m); m.motor = m.stage = 500; m.xactual = m.xtarget = 500; m.frame = -30;
    SettleState t; settle_state_init(&t);
    SettleParams q; settle_params_default(&q); q.finish_usteps = 2133; q.finish_from_usteps = 42667;
    sim_begin(&m, &t, &q, 90000, true);
    TEST_ASSERT_TRUE(t.finish_pending);
    TEST_ASSERT_EQUAL_INT32(30, settle_offset(&t));
    sim_begin(&m, &t, &q, 1000, true);
    TEST_ASSERT_EQUAL_INT32(1000 + 30, m.leg_to[0]);
    sim_run(&m, &t, &q);
    assert_settled(&m, &q, 1000);
}

int main(int, char **)
{
    UNITY_BEGIN();
    RUN_TEST(test_climbing_stack_places_every_plane_and_never_reverses);
    RUN_TEST(test_the_plan_starts_from_the_encoder_not_the_counter);
    RUN_TEST(test_reversal_with_unknown_lost_motion_converges_from_the_short_side);
    RUN_TEST(test_lost_motion_feedforward_saves_the_trims);
    RUN_TEST(test_overshoot_backs_off_beyond_the_lost_motion_and_approaches_again);
    RUN_TEST(test_fixed_approach_direction_goes_beyond_and_comes_back);
    RUN_TEST(test_undershoot_bias_applies_to_long_legs_only);
    RUN_TEST(test_with_the_carry_known_every_plane_lands_on_the_first_leg);
    RUN_TEST(test_without_the_model_the_same_plant_needs_corrections);
    RUN_TEST(test_the_carry_is_learned_from_the_landings);
    RUN_TEST(test_the_same_stage_at_four_times_the_microstepping);
    RUN_TEST(test_wheel_motion_the_same_way_keeps_the_flank_and_closes_the_gap);
    RUN_TEST(test_wheel_motion_inside_the_play_moves_nothing_and_is_booked_as_such);
    RUN_TEST(test_wheel_motion_across_the_play_hands_the_stage_to_the_other_flank);
    RUN_TEST(test_wheel_back_and_forth_is_judged_by_where_it_ended_not_by_how_it_got_there);
    RUN_TEST(test_wheel_motion_that_cannot_be_reckoned_leaves_the_state_unknown_unless_it_clearly_pushed);
    RUN_TEST(test_wheel_motion_is_seen_through_the_screw_scale);
    RUN_TEST(test_a_vertical_axis_carries_further_down_than_up);
    RUN_TEST(test_the_lost_motion_is_kept_per_direction_of_the_crossing);
    RUN_TEST(test_the_screw_scale_is_learned_from_long_continuing_legs);
    RUN_TEST(test_a_seeded_screw_scale_lands_the_first_long_leg);
    RUN_TEST(test_short_legs_do_not_teach_the_scale_and_it_stays_bounded);
    RUN_TEST(test_the_approach_after_a_back_off_aims_short);
    RUN_TEST(test_a_first_leg_across_the_play_aims_short_by_its_own_scatter);
    RUN_TEST(test_the_reversal_scatter_is_learned_separately);
    RUN_TEST(test_the_lost_motion_is_learned_from_reversals);
    RUN_TEST(test_the_model_stays_inside_its_bounds_whatever_the_landings_say);
    RUN_TEST(test_a_trim_closes_the_gap_a_fast_leg_left);
    RUN_TEST(test_frozen_encoder_on_a_long_move_is_no_response);
    RUN_TEST(test_frozen_encoder_on_a_small_move_runs_out_of_trims_not_out_of_travel);
    RUN_TEST(test_encoder_beyond_the_watchdog_is_a_fault_not_a_correction);
    RUN_TEST(test_frames_that_disagree_beyond_the_watchdog_fault_before_anything_moves);
    RUN_TEST(test_correction_budget_is_a_timeout);
    RUN_TEST(test_an_overshoot_inside_the_band_is_accepted_and_does_not_accumulate);
    RUN_TEST(test_beyond_the_band_backs_off_once_and_then_misses);
    RUN_TEST(test_a_trim_aims_at_the_near_side_of_the_band);
    RUN_TEST(test_the_undershoot_bias_is_learned_from_the_landing_scatter);
    RUN_TEST(test_legs_never_leave_the_travel_limits_and_a_pinned_target_is_not_a_fault);
    RUN_TEST(test_ramp_stopped_short_by_the_chip_hands_back_the_frame);
    RUN_TEST(test_inside_the_home_zone_completion_is_on_the_counter);
    RUN_TEST(test_counter_planned_move_that_ends_where_the_encoder_counts_is_corrected);
    RUN_TEST(test_a_command_during_a_move_settle_is_planned_in_the_counter_frame);
    RUN_TEST(test_acceptance_is_inclusive_at_the_tolerance_and_not_beyond);
    RUN_TEST(test_the_window_mean_beats_the_quantisation);
    RUN_TEST(test_window_waits_for_its_minimum_of_samples);
    RUN_TEST(test_settle_requirement_delays_done_but_never_faults);
    RUN_TEST(test_split_leg_is_two_parts_half_a_period_apart);
    RUN_TEST(test_time_arithmetic_survives_the_micros_wrap);
    RUN_TEST(test_the_window_opens_only_after_the_settle_time);
    RUN_TEST(test_offset_is_zero_at_rest);
    RUN_TEST(test_rest_watch_stays_quiet_while_the_counter_moves);
    RUN_TEST(test_rest_watch_fires_once_the_counter_has_stood_for_the_limit);
    RUN_TEST(test_rest_watch_starts_over_when_the_counter_moves_or_a_new_wait_begins);
    RUN_TEST(test_rest_watch_survives_the_micros_wrap);
    RUN_TEST(test_the_first_move_after_motion_of_others_teaches_nothing);
    RUN_TEST(test_unanswered_trims_grow_until_the_flank_reaches_the_stage);
    RUN_TEST(test_a_grown_trim_cannot_land_past_the_band_wherever_the_flank_meets_the_stage);
    RUN_TEST(test_answered_trims_are_sized_as_before);
    RUN_TEST(test_with_the_finishing_leg_off_a_long_move_is_one_leg_to_the_target);
    RUN_TEST(test_a_long_move_ends_with_a_short_finishing_leg);
    RUN_TEST(test_a_move_inside_the_threshold_is_not_split);
    RUN_TEST(test_a_miss_or_abort_during_the_long_leg_clears_the_finishing_leg);
    RUN_TEST(test_a_counter_planned_move_that_will_be_judged_is_finished_from_the_encoder);
    RUN_TEST(test_a_counter_planned_move_without_a_split_point_is_one_leg_as_before);
    RUN_TEST(test_a_counter_planned_move_that_ends_in_the_zone_is_not_split);
    RUN_TEST(test_a_counter_planned_move_no_longer_than_the_finishing_leg_is_not_split);
    RUN_TEST(test_a_command_during_a_move_settle_is_not_split);
    RUN_TEST(test_the_first_leg_of_a_split_move_keeps_the_frame_offset_true);
    return UNITY_END();
}
