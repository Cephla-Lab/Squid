#include <unity.h>
#include "pid_clamp.h"

/*
  pid_clamp.h is the ONE path from a requested correction clamp to the chip's PID_DV_CLIP and to
  the correction watch's cached copy. Both command handlers (CONFIGURE_STAGE_PID, SET_PID_LIMITS)
  call it with decoded inputs and a register-write function; here the write is recorded. This
  exists because the handler wrote VMAX's 24.8 format (pps x 256) into PID_DV_CLIP from the first
  closed-loop firmware through 6a8b12cd, and the helper-only test in test_pid_policy could not see
  which value the handler actually handed to the register (Codex, 2026-09-14).
*/

void setUp(void) {}
void tearDown(void) {}

/* The 2240 bench Z: 16 usteps/FS, 200 steps/rev, 0.3 mm pitch = 10,667 pps per mm/s. */
static const PidClampGeometry BENCH = {16u, 200u, 0.3f};

typedef struct {
    uint32_t writes[8];
    int n;
} Recorder;

static void record(void *ctx, uint32_t pps)
{
    Recorder *r = (Recorder *)ctx;
    if (r->n < 8) r->writes[r->n] = pps;
    r->n++;
}

void test_configure_writes_the_axis_default_in_pps(void) {
    PidClamp c = {0u, 0u};
    Recorder r = {{0}, 0};
    uint32_t written = pid_clamp_configure(&c, 1.0f, BENCH, record, &r);
    TEST_ASSERT_EQUAL_INT(1, r.n);
    TEST_ASSERT_EQUAL_UINT32(10667u, r.writes[0]);          // not 2,730,666 (the 24.8 value)
    TEST_ASSERT_EQUAL_UINT32(10667u, written);
    TEST_ASSERT_EQUAL_UINT32(10667u, c.effective_pps);       // the watch budgets against what the chip holds
    TEST_ASSERT_EQUAL_UINT32(0u, c.override_pps);            // no host override recorded
}

void test_a_limit_sent_before_the_encoder_is_configured_waits_for_configure(void) {
    PidClamp c = {0u, 0u};
    Recorder r = {{0}, 0};
    pid_clamp_set_limit(&c, 0.5f, BENCH, false, record, &r);
    TEST_ASSERT_EQUAL_INT(0, r.n);                           // the chip's registers are reset by CONFIGURE anyway
    TEST_ASSERT_EQUAL_UINT32(5333u, c.override_pps);
    TEST_ASSERT_EQUAL_UINT32(0u, c.effective_pps);           // nothing in effect yet
    pid_clamp_configure(&c, 1.0f, BENCH, record, &r);
    TEST_ASSERT_EQUAL_INT(1, r.n);
    TEST_ASSERT_EQUAL_UINT32(5333u, r.writes[0]);           // the override wins over the axis default
    TEST_ASSERT_EQUAL_UINT32(5333u, c.effective_pps);
}

void test_a_limit_with_the_encoder_configured_writes_and_takes_effect(void) {
    PidClamp c = {0u, 0u};
    Recorder r = {{0}, 0};
    pid_clamp_configure(&c, 1.0f, BENCH, record, &r);
    pid_clamp_set_limit(&c, 0.02f, BENCH, true, record, &r);   // the F7 bench setting: 213 pps
    TEST_ASSERT_EQUAL_INT(2, r.n);
    TEST_ASSERT_EQUAL_UINT32(213u, r.writes[1]);
    TEST_ASSERT_EQUAL_UINT32(213u, c.effective_pps);
    TEST_ASSERT_EQUAL_UINT32(213u, c.override_pps);
}

void test_register_and_watch_agree_after_every_step(void) {
    PidClamp c = {0u, 0u};
    Recorder r = {{0}, 0};
    pid_clamp_configure(&c, 3.0f, BENCH, record, &r);
    TEST_ASSERT_EQUAL_UINT32(r.writes[r.n - 1], c.effective_pps);
    pid_clamp_set_limit(&c, 1.0f, BENCH, true, record, &r);
    TEST_ASSERT_EQUAL_UINT32(r.writes[r.n - 1], c.effective_pps);
    pid_clamp_configure(&c, 3.0f, BENCH, record, &r);          // a later CONFIGURE re-applies the override
    TEST_ASSERT_EQUAL_UINT32(10667u, r.writes[r.n - 1]);
    TEST_ASSERT_EQUAL_UINT32(r.writes[r.n - 1], c.effective_pps);
    TEST_ASSERT_EQUAL_INT(3, r.n);
}

void test_reset_returns_to_the_firmware_default(void) {
    PidClamp c = {5333u, 5333u};
    pid_clamp_reset(&c);
    TEST_ASSERT_EQUAL_UINT32(0u, c.override_pps);
    TEST_ASSERT_EQUAL_UINT32(0u, c.effective_pps);
}

int main(int argc, char **argv) {
    (void)argc; (void)argv;
    UNITY_BEGIN();
    RUN_TEST(test_configure_writes_the_axis_default_in_pps);
    RUN_TEST(test_a_limit_sent_before_the_encoder_is_configured_waits_for_configure);
    RUN_TEST(test_a_limit_with_the_encoder_configured_writes_and_takes_effect);
    RUN_TEST(test_register_and_watch_agree_after_every_step);
    RUN_TEST(test_reset_returns_to_the_firmware_default);
    return UNITY_END();
}
