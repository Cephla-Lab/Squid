#include <unity.h>
#include "tmc/drivers/driver_math.h"

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// TMC2660 — these values pin MASTER's behavior (design M5).
// If one of these changes, someone altered the fielded current on every
// deployed machine. That must be a deliberate, separate PR.
// ---------------------------------------------------------------------------
void test_tmc2660_shipped_xy(void) {
    // configuration_Squid+.ini: x/y_motor_rms_current_ma = 1000, R_sense_xy = 0.22
    TEST_ASSERT_EQUAL_UINT8(29, tmc2660_current_scale(1000.0f, 0.22f));
}

void test_tmc2660_shipped_z(void) {
    // configuration_Squid+.ini: z_motor_rms_current_ma = 500, R_sense_z = 0.43
    TEST_ASSERT_EQUAL_UINT8(29, tmc2660_current_scale(500.0f, 0.43f));
}

void test_tmc2660_shipped_w(void) {
    // _def.py: W_MOTOR_RMS_CURRENT_mA = 1900, R_sense_w = 0.105
    TEST_ASSERT_EQUAL_UINT8(26, tmc2660_current_scale(1900.0f, 0.105f));
}

void test_tmc2660_zero_current(void) {
    TEST_ASSERT_EQUAL_UINT8(0, tmc2660_current_scale(0.0f, 0.22f));
}

void test_tmc2660_rejects_out_of_range(void) {
    // cmd 21 CONFIGURE_STEPPER_DRIVER accepts a u16, so the host can request more
    // current than the formula can express. Master's uint8_t result then wrapped
    // mod 32 inside SGCSCONF's 5-bit CS field — arbitrarily higher or lower than
    // asked for. Report it instead, matching tmc2240_irun.
    TEST_ASSERT_EQUAL_UINT8(TMC_CURRENT_OUT_OF_RANGE, tmc2660_current_scale(65535.0f, 0.22f));
    // 1100 mA on X (R = 0.22): master's raw value was 32.6, outside the 5-bit CS
    // field, where it wrapped to CS = 0 — minimum current for an over-request.
    TEST_ASSERT_EQUAL_UINT8(TMC_CURRENT_OUT_OF_RANGE, tmc2660_current_scale(1100.0f, 0.22f));
}

void test_tmc2660_full_scale_stays_reachable(void) {
    // Master saturated at CS = 31 for a band above the formula's nominal ceiling
    // (1045-1078 mA on X) WITHOUT wrapping. That is legitimate full-scale current
    // and must keep working — a guard at cscale > 1.0 would make CS = 31
    // unreachable, so raising Z from 500 to 550 mA in the INI would silently
    // change nothing instead of going to maximum.
    TEST_ASSERT_EQUAL_UINT8(31, tmc2660_current_scale(1060.0f, 0.22f));
    TEST_ASSERT_EQUAL_UINT8(31, tmc2660_current_scale(545.0f, 0.43f));
    // 1044 mA is the largest request below the ceiling; master gives 30, not 31.
    TEST_ASSERT_EQUAL_UINT8(30, tmc2660_current_scale(1044.0f, 0.22f));
}

// ---------------------------------------------------------------------------
// TMC2240 — datasheet form, /sqrt(2) included (design M8).
// A regression to 15/15/19 means someone dropped the sqrt(2) and reintroduced
// the octoaxes defect.
// ---------------------------------------------------------------------------
void test_tmc2240_irun_xy_range1(void) {
    TEST_ASSERT_EQUAL_UINT8(21, tmc2240_irun(1000.0f, 1));
}

void test_tmc2240_irun_z_range0(void) {
    TEST_ASSERT_EQUAL_UINT8(22, tmc2240_irun(500.0f, 0));
}

void test_tmc2240_irun_w_range2(void) {
    TEST_ASSERT_EQUAL_UINT8(27, tmc2240_irun(1900.0f, 2));
}

void test_tmc2240_irun_rejects_out_of_range(void) {
    // 1900 mA RMS exceeds range 1's 1414 mA RMS ceiling. Must be reported,
    // not silently clamped: a quietly under-currented filter wheel stalls.
    TEST_ASSERT_EQUAL_UINT8(TMC_CURRENT_OUT_OF_RANGE, tmc2240_irun(1900.0f, 1));
    TEST_ASSERT_EQUAL_UINT8(TMC_CURRENT_OUT_OF_RANGE, tmc2240_irun(1000.0f, 0));
}

void test_tmc2240_irun_zero_current(void) {
    TEST_ASSERT_EQUAL_UINT8(0, tmc2240_irun(0.0f, 1));
}

void test_tmc2240_ifs_values(void) {
    // KIFS / R_ref = peak; /sqrt(2) = rms
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.979f, tmc2240_ifs_peak_a(0));
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 2.000f, tmc2240_ifs_peak_a(1));
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 3.000f, tmc2240_ifs_peak_a(2));
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 3.000f, tmc2240_ifs_peak_a(3));
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 2.121f, tmc2240_ifs_rms_a(2));
}

void test_tmc2240_selected_ranges_land_in_recommended_band(void) {
    // ADI advises IRUN in 16..31; below that the microstep waveform is
    // quantized coarsely. The octoaxes values (15/15/19) did not satisfy this.
    TEST_ASSERT_GREATER_OR_EQUAL_UINT8(16, tmc2240_irun(1000.0f, 1));
    TEST_ASSERT_GREATER_OR_EQUAL_UINT8(16, tmc2240_irun(500.0f, 0));
    TEST_ASSERT_GREATER_OR_EQUAL_UINT8(16, tmc2240_irun(1900.0f, 2));
}

// ---------------------------------------------------------------------------
// Microstep resolution code
// ---------------------------------------------------------------------------
void test_mres_all_legal_values(void) {
    TEST_ASSERT_EQUAL_UINT8(0, tmc_microsteps_to_mres(256));
    TEST_ASSERT_EQUAL_UINT8(1, tmc_microsteps_to_mres(128));
    TEST_ASSERT_EQUAL_UINT8(2, tmc_microsteps_to_mres(64));
    TEST_ASSERT_EQUAL_UINT8(3, tmc_microsteps_to_mres(32));
    TEST_ASSERT_EQUAL_UINT8(4, tmc_microsteps_to_mres(16));
    TEST_ASSERT_EQUAL_UINT8(5, tmc_microsteps_to_mres(8));
    TEST_ASSERT_EQUAL_UINT8(6, tmc_microsteps_to_mres(4));
    TEST_ASSERT_EQUAL_UINT8(7, tmc_microsteps_to_mres(2));
    TEST_ASSERT_EQUAL_UINT8(8, tmc_microsteps_to_mres(1));
}

void test_mres_rejects_illegal_values(void) {
    TEST_ASSERT_EQUAL_UINT8(TMC_MRES_INVALID, tmc_microsteps_to_mres(0));
    TEST_ASSERT_EQUAL_UINT8(TMC_MRES_INVALID, tmc_microsteps_to_mres(3));
    TEST_ASSERT_EQUAL_UINT8(TMC_MRES_INVALID, tmc_microsteps_to_mres(100));
    TEST_ASSERT_EQUAL_UINT8(TMC_MRES_INVALID, tmc_microsteps_to_mres(512));
}

int main(int argc, char **argv) {
    UNITY_BEGIN();
    RUN_TEST(test_tmc2660_shipped_xy);
    RUN_TEST(test_tmc2660_shipped_z);
    RUN_TEST(test_tmc2660_shipped_w);
    RUN_TEST(test_tmc2660_zero_current);
    RUN_TEST(test_tmc2660_rejects_out_of_range);
    RUN_TEST(test_tmc2660_full_scale_stays_reachable);
    RUN_TEST(test_tmc2240_irun_xy_range1);
    RUN_TEST(test_tmc2240_irun_z_range0);
    RUN_TEST(test_tmc2240_irun_w_range2);
    RUN_TEST(test_tmc2240_irun_rejects_out_of_range);
    RUN_TEST(test_tmc2240_irun_zero_current);
    RUN_TEST(test_tmc2240_ifs_values);
    RUN_TEST(test_tmc2240_selected_ranges_land_in_recommended_band);
    RUN_TEST(test_mres_all_legal_values);
    RUN_TEST(test_mres_rejects_illegal_values);
    return UNITY_END();
}
