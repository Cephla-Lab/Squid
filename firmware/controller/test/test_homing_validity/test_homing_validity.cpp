#include <unity.h>
#include <stdint.h>
#include "constants_protocol.h"
#include "utils/homing_validity.h"

// Include sources directly for native tests (env:native builds no src/ code into tests)
#include "utils/homing_validity.cpp"

static const uint32_t SIG = 0xA1B2C3D4u;
static const uint8_t FMT = 1;
static const uint8_t ALL = 0b111;

static HomingValidity hv;

static void home_all(bool pg) {
    for (uint8_t a = 0; a < 3; a++) {
        hv_home_start(&hv, a, pg);
        hv_home_complete(&hv, a, pg);
    }
}
static uint8_t verdict(uint8_t mask = ALL, uint8_t rest = 0b111, bool pg = true, bool tmc = true) {
    return hv_verdict(&hv, SIG, FMT, mask, rest, pg, tmc);
}
static void warm(void) {
    hv_power_up(&hv);
    hv_store_signature(&hv, SIG, FMT);
    home_all(true);
}

void setUp(void) { hv_power_up(&hv); }
void tearDown(void) {}

void test_power_up_has_no_signature(void) {
    TEST_ASSERT_EQUAL_UINT8(STARTUP_VERDICT_NO_SIGNATURE, verdict());
}
void test_store_and_home_all_is_warm(void) {
    warm();
    TEST_ASSERT_EQUAL_UINT8(STARTUP_VERDICT_WARM, verdict());
}
void test_wrong_signature_or_format_mismatches(void) {
    warm();
    TEST_ASSERT_EQUAL_UINT8(STARTUP_VERDICT_SIGNATURE_MISMATCH, hv_verdict(&hv, SIG + 1, FMT, ALL, ALL, true, true));
    TEST_ASSERT_EQUAL_UINT8(STARTUP_VERDICT_SIGNATURE_MISMATCH, hv_verdict(&hv, SIG, FMT + 1, ALL, ALL, true, true));
}
void test_unhealthy_cases(void) {
    warm();
    TEST_ASSERT_EQUAL_UINT8(STARTUP_VERDICT_UNHEALTHY, verdict(ALL, ALL, false, true));
    TEST_ASSERT_EQUAL_UINT8(STARTUP_VERDICT_UNHEALTHY, verdict(ALL, ALL, true, false));
    hv_driver_enable(&hv, 1, false);
    TEST_ASSERT_EQUAL_UINT8(STARTUP_VERDICT_UNHEALTHY, verdict());
}
void test_moving(void) {
    warm();
    TEST_ASSERT_EQUAL_UINT8(STARTUP_VERDICT_MOVING, verdict(ALL, 0b011));
}
void test_not_homed_required_axis(void) {
    hv_store_signature(&hv, SIG, FMT);
    hv_home_start(&hv, 0, true); hv_home_complete(&hv, 0, true);
    hv_home_start(&hv, 1, true); hv_home_complete(&hv, 1, true);
    TEST_ASSERT_EQUAL_UINT8(STARTUP_VERDICT_NOT_HOMED, verdict(0b111));
    TEST_ASSERT_EQUAL_UINT8(STARTUP_VERDICT_WARM, verdict(0b011));
}
void test_home_started_pg_low_completed_pg_high(void) {
    hv_store_signature(&hv, SIG, FMT);
    hv_home_start(&hv, 0, false);
    hv_home_complete(&hv, 0, true);
    TEST_ASSERT_FALSE(hv.homed[0]);
}
void test_home_started_on_disabled_driver(void) {
    hv_store_signature(&hv, SIG, FMT);
    hv_driver_enable(&hv, 0, false);
    hv_home_start(&hv, 0, true);
    hv_driver_enable(&hv, 0, true);
    hv_home_complete(&hv, 0, true);
    TEST_ASSERT_FALSE(hv.homed[0]);
}
void test_power_good_blip_disarms(void) {
    hv_store_signature(&hv, SIG, FMT);
    hv_home_start(&hv, 0, true);
    hv_power_good(&hv, false);
    hv_power_good(&hv, true);
    hv_home_complete(&hv, 0, true);
    TEST_ASSERT_FALSE(hv.homed[0]);
}
void test_complete_with_power_bad(void) {
    hv_home_start(&hv, 0, true);
    hv_home_complete(&hv, 0, false);
    TEST_ASSERT_FALSE(hv.homed[0]);
}
void test_complete_without_start_does_nothing(void) {
    hv_home_complete(&hv, 0, true);
    TEST_ASSERT_FALSE(hv.homed[0]);
}
void test_zero_clears_that_axis_only(void) {
    warm();
    hv_invalidate_axis(&hv, 0);
    TEST_ASSERT_FALSE(hv.homed[0]);
    TEST_ASSERT_TRUE(hv.homed[1]);
    TEST_ASSERT_TRUE(hv.homed[2]);
}
void test_initialize_clears_everything(void) {
    warm();
    hv_initialize(&hv);
    TEST_ASSERT_EQUAL_UINT8(STARTUP_VERDICT_NO_SIGNATURE, verdict());
    for (int a = 0; a < 3; a++) {
        TEST_ASSERT_FALSE(hv.homed[a]);
        TEST_ASSERT_FALSE(hv.armed[a]);
        TEST_ASSERT_TRUE(hv.driver_enabled[a]);
    }
}
void test_store_different_clears_identical_keeps(void) {
    warm();
    hv_store_signature(&hv, SIG, FMT);
    TEST_ASSERT_EQUAL_UINT8(STARTUP_VERDICT_WARM, verdict());
    hv_store_signature(&hv, SIG + 1, FMT);
    TEST_ASSERT_FALSE(hv.homed[0]);
    TEST_ASSERT_FALSE(hv.homed[1]);
    TEST_ASSERT_FALSE(hv.homed[2]);
}
void test_store_different_format_clears(void) {
    warm();
    hv_store_signature(&hv, SIG, FMT + 1);
    TEST_ASSERT_FALSE(hv.homed[2]);
}
void test_a_b_a_leaves_flags_clear(void) {
    warm();
    hv_store_signature(&hv, SIG + 1, FMT);
    hv_store_signature(&hv, SIG, FMT);
    for (int a = 0; a < 3; a++) TEST_ASSERT_FALSE(hv.homed[a]);
    TEST_ASSERT_EQUAL_UINT8(STARTUP_VERDICT_NOT_HOMED, verdict());
}
void test_disarm_on_driver_disable(void) {
    hv_home_start(&hv, 1, true);
    hv_driver_enable(&hv, 1, false);
    TEST_ASSERT_FALSE(hv.armed[1]);
    hv_driver_enable(&hv, 1, true);
    TEST_ASSERT_FALSE(hv.armed[1]);
}
void test_config_first_bytes_empty_signature(void) {
    const uint8_t b[4] = {1, 2, 3, 4};
    hv_store_signature(&hv, SIG, FMT);
    hv_config_received(&hv, HV_CFG_PITCH, 0, b);
    TEST_ASSERT_FALSE(hv.signature_stored);
    TEST_ASSERT_EQUAL_UINT8(STARTUP_VERDICT_NO_SIGNATURE, verdict());
}
void test_config_identical_keeps_changed_empties(void) {
    const uint8_t b[4] = {1, 2, 3, 4};
    const uint8_t c[4] = {1, 2, 3, 5};
    hv_config_received(&hv, HV_CFG_DRIVER, 1, b);
    hv_store_signature(&hv, SIG, FMT);
    hv_config_received(&hv, HV_CFG_DRIVER, 1, b);
    TEST_ASSERT_TRUE(hv.signature_stored);
    hv_config_received(&hv, HV_CFG_DRIVER, 1, c);
    TEST_ASSERT_FALSE(hv.signature_stored);
    TEST_ASSERT_EQUAL_UINT8(5, hv.cfg_bytes[HV_CFG_DRIVER][1][3]);
}
void test_config_other_axis_independent(void) {
    const uint8_t b[4] = {1, 2, 3, 4};
    hv_config_received(&hv, HV_CFG_VEL_ACC, 0, b);
    hv_store_signature(&hv, SIG, FMT);
    hv_config_received(&hv, HV_CFG_VEL_ACC, 0, b);
    TEST_ASSERT_TRUE(hv.signature_stored);
    hv_config_received(&hv, HV_CFG_VEL_ACC, 1, b);  // first time for axis 1
    TEST_ASSERT_FALSE(hv.signature_stored);
}
void test_restore_after_config_change_clears_flags(void) {
    const uint8_t b[4] = {1, 2, 3, 4};
    const uint8_t c[4] = {9, 9, 9, 9};
    hv_config_received(&hv, HV_CFG_PITCH, 0, b);
    warm();  // power-up resets cfg_seen too
    hv_config_received(&hv, HV_CFG_PITCH, 0, b);
    hv_store_signature(&hv, SIG, FMT);
    home_all(true);
    hv_config_received(&hv, HV_CFG_PITCH, 0, c);
    hv_store_signature(&hv, SIG, FMT);
    TEST_ASSERT_FALSE(hv.homed[0]);
}

int main(int argc, char **argv) {
    UNITY_BEGIN();
    RUN_TEST(test_power_up_has_no_signature);
    RUN_TEST(test_store_and_home_all_is_warm);
    RUN_TEST(test_wrong_signature_or_format_mismatches);
    RUN_TEST(test_unhealthy_cases);
    RUN_TEST(test_moving);
    RUN_TEST(test_not_homed_required_axis);
    RUN_TEST(test_home_started_pg_low_completed_pg_high);
    RUN_TEST(test_home_started_on_disabled_driver);
    RUN_TEST(test_power_good_blip_disarms);
    RUN_TEST(test_complete_with_power_bad);
    RUN_TEST(test_complete_without_start_does_nothing);
    RUN_TEST(test_zero_clears_that_axis_only);
    RUN_TEST(test_initialize_clears_everything);
    RUN_TEST(test_store_different_clears_identical_keeps);
    RUN_TEST(test_store_different_format_clears);
    RUN_TEST(test_a_b_a_leaves_flags_clear);
    RUN_TEST(test_disarm_on_driver_disable);
    RUN_TEST(test_config_first_bytes_empty_signature);
    RUN_TEST(test_config_identical_keeps_changed_empties);
    RUN_TEST(test_config_other_axis_independent);
    RUN_TEST(test_restore_after_config_change_clears_flags);
    return UNITY_END();
}
