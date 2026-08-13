#include <unity.h>
#include "tmc/drivers/tmc2660_regs.h"
#include "tmc/drivers/tmc2240_regs.h"

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// TMC2660 — these four datagrams are byte-for-byte what master writes in
// tmc4361A_tmc2660_init (TMC4361A_TMC2660_Utils.cpp:462-465). Any change here
// changes the behavior of every fielded 2660 board (design M5).
// ---------------------------------------------------------------------------
void test_tmc2660_init_datagrams_match_master(void) {
    TEST_ASSERT_EQUAL_HEX32(0x000900C3, tmc2660_chopconf_datagram(3));
    TEST_ASSERT_EQUAL_HEX32(0x000A0000, tmc2660_smarten_datagram());
    TEST_ASSERT_EQUAL_HEX32(0x000C000A, tmc2660_sgcsconf_datagram(10, 0, false));
    TEST_ASSERT_EQUAL_HEX32(0x000E00A1, tmc2660_drvconf_datagram());
}

void test_tmc2660_disable_datagram_matches_master(void) {
    // tmc4361A_tmc2660_disable_driver writes CHOPCONF with TOFF = 0.
    TEST_ASSERT_EQUAL_HEX32(0x000900C0, tmc2660_chopconf_datagram(0));
}

void test_tmc2660_cscale_datagram_matches_master(void) {
    // tmc4361A_cScaleInit writes SGCSCONF | SFILT | cs.
    // Master's SGCSCONF = 0x0C0000, SFILT = 0x010000.
    // With the shipped X/Y current scale of 29:
    TEST_ASSERT_EQUAL_HEX32(0x000C001D | 0x00010000, tmc2660_sgcsconf_datagram(29, 0, true));
}

void test_tmc2660_sgcsconf_carries_stallguard_threshold(void) {
    // init.cpp:187 configures stallGuard with sensitivity 12, filter on.
    // SGT occupies bits [14:8] — 7 signed bits, which is why master writes
    // (sensitivity & 0x7F) << 8.
    uint32_t d = tmc2660_sgcsconf_datagram(29, 12, true);
    TEST_ASSERT_EQUAL_UINT32(29u, d & 0x1Fu);
    TEST_ASSERT_EQUAL_UINT32(12u, (d >> 8) & 0x7Fu);
    TEST_ASSERT_EQUAL_UINT32(1u,  (d >> 16) & 0x1u);
}

void test_tmc2660_sgcsconf_encodes_negative_sgt_as_twos_complement(void) {
    // SGT is a signed 7-bit field, -64..63.
    uint32_t d = tmc2660_sgcsconf_datagram(0, -1, false);
    TEST_ASSERT_EQUAL_UINT32(0x7Fu, (d >> 8) & 0x7Fu);
}

// ---------------------------------------------------------------------------
// TMC2240
// ---------------------------------------------------------------------------
void test_tmc2240_ihold_irun_packing(void) {
    // IHOLD [4:0], IRUN [12:8], IHOLDDELAY [19:16]
    uint32_t v = tmc2240_ihold_irun_value(10, 21, 6);
    TEST_ASSERT_EQUAL_UINT32(10u, v & 0x1Fu);
    TEST_ASSERT_EQUAL_UINT32(21u, (v >> 8) & 0x1Fu);
    TEST_ASSERT_EQUAL_UINT32(6u,  (v >> 16) & 0x0Fu);
}

void test_tmc2240_drv_conf_carries_current_range_and_slope(void) {
    uint32_t v = tmc2240_drv_conf_value(2);
    TEST_ASSERT_EQUAL_UINT32(2u, v & 0x03u);
    TEST_ASSERT_EQUAL_UINT32(1u, (v >> 4) & 0x0Fu);  // SLOPE_CONTROL = 1 (200 V/us)
}

void test_tmc2240_gconf_sets_direct_mode(void) {
    // direct_mode (bit 16) is mandatory: without it the TMC2240 waits for
    // step/dir and ignores the TMC4361A's SPI coil-current commands.
    TEST_ASSERT_EQUAL_UINT32(1u, (tmc2240_gconf_value(false) >> 16) & 0x1u);
    TEST_ASSERT_EQUAL_UINT32(0u, (tmc2240_gconf_value(false) >> 2) & 0x1u);
    TEST_ASSERT_EQUAL_UINT32(1u, (tmc2240_gconf_value(true) >> 2) & 0x1u);   // en_pwm_mode
}

void test_tmc2240_chopconf_field_packing(void) {
    uint32_t v = tmc2240_chopconf_value(3, 4, 0, 2, 0, false);
    TEST_ASSERT_EQUAL_UINT32(3u, v & 0x0Fu);              // TOFF [3:0]
    TEST_ASSERT_EQUAL_UINT32(4u, (v >> 4) & 0x07u);       // HSTRT [6:4]
    TEST_ASSERT_EQUAL_UINT32(3u, (v >> 7) & 0x0Fu);       // HEND [10:7], offset by 3
    TEST_ASSERT_EQUAL_UINT32(2u, (v >> 15) & 0x03u);      // TBL [16:15]
    TEST_ASSERT_EQUAL_UINT32(0u, (v >> 24) & 0x0Fu);      // MRES [27:24]
}

void test_tmc2240_chopconf_with_mres_preserves_other_fields(void) {
    // This is the shadow-register read-modify-write used by set_microsteps.
    // Everything except MRES must survive, or the driver silently changes
    // chopper behavior.
    uint32_t base = tmc2240_chopconf_value(3, 4, 0, 2, 0, true);
    uint32_t v    = tmc2240_chopconf_with_mres(base, 5);
    TEST_ASSERT_EQUAL_UINT32(5u, (v >> 24) & 0x0Fu);
    TEST_ASSERT_EQUAL_UINT32(base & ~(0x0Fu << 24), v & ~(0x0Fu << 24));
}

void test_tmc2240_chopconf_with_toff_preserves_other_fields(void) {
    // Used by enable/disable. TOFF = 0 disables the driver.
    uint32_t base = tmc2240_chopconf_value(3, 4, 0, 2, 0, true);
    uint32_t off  = tmc2240_chopconf_with_toff(base, 0);
    uint32_t on   = tmc2240_chopconf_with_toff(off, 3);
    TEST_ASSERT_EQUAL_UINT32(0u, off & 0x0Fu);
    TEST_ASSERT_EQUAL_UINT32(base, on);
}

int main(int argc, char **argv) {
    UNITY_BEGIN();
    RUN_TEST(test_tmc2660_init_datagrams_match_master);
    RUN_TEST(test_tmc2660_disable_datagram_matches_master);
    RUN_TEST(test_tmc2660_cscale_datagram_matches_master);
    RUN_TEST(test_tmc2660_sgcsconf_carries_stallguard_threshold);
    RUN_TEST(test_tmc2660_sgcsconf_encodes_negative_sgt_as_twos_complement);
    RUN_TEST(test_tmc2240_ihold_irun_packing);
    RUN_TEST(test_tmc2240_drv_conf_carries_current_range_and_slope);
    RUN_TEST(test_tmc2240_gconf_sets_direct_mode);
    RUN_TEST(test_tmc2240_chopconf_field_packing);
    RUN_TEST(test_tmc2240_chopconf_with_mres_preserves_other_fields);
    RUN_TEST(test_tmc2240_chopconf_with_toff_preserves_other_fields);
    return UNITY_END();
}
