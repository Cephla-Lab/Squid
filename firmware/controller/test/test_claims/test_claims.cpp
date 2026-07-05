#include <unity.h>

#include <stdint.h>

#include "protocol/claims.h"
#include "protocol/frames.h"

// Include source directly for native tests.
#include "protocol/claims.cpp"

using namespace protocol;

void setUp(void) {}
void tearDown(void) {}

// A computed hook that ignores static_claims and returns a fixed mask.
static uint32_t computed_axes_2_3(const uint8_t* payload, size_t len) {
    (void)payload;
    (void)len;
    return res_axis(2) | res_axis(3);
}

// A computed hook that echoes the payload length (proves args are passed).
static uint32_t computed_echo_len(const uint8_t* payload, size_t len) {
    (void)payload;
    return (uint32_t)len;
}

// --- Static lookup against the production table ---------------------------

void test_system_commands_claim_nothing(void) {
    TEST_ASSERT_EQUAL_HEX32(0, claims_for(GET_STATE, nullptr, 0));
    TEST_ASSERT_EQUAL_HEX32(0, claims_for(HELLO, nullptr, 0));
    TEST_ASSERT_EQUAL_HEX32(0, claims_for(GET_INFO, nullptr, 0));
    TEST_ASSERT_EQUAL_HEX32(0, claims_for(DIAG, nullptr, 0));
}

void test_command_absent_from_table_claims_nothing(void) {
    // 0x07 is not in the Phase-B production table.
    TEST_ASSERT_EQUAL_HEX32(0, claims_for(0x07, nullptr, 0));
}

// --- Conflict overlap math ------------------------------------------------

void test_conflict_reports_lowest_resource_plus_one(void) {
    // MOVE_X-style wanted {axis0} vs in-flight {axis0}: conflict on resource 0.
    TEST_ASSERT_EQUAL_UINT8(1, claims_conflict(res_axis(0), res_axis(0)));
    // vs in-flight {axis1}: compatible.
    TEST_ASSERT_EQUAL_UINT8(0, claims_conflict(res_axis(0), res_axis(1)));
    // A higher resource: ILLUM_TTL is bit 16 -> reported as 17.
    TEST_ASSERT_EQUAL_UINT8(17, claims_conflict(RES_ILLUM_TTL, RES_ILLUM_TTL | res_axis(5)));
    // Lowest set bit wins when several overlap.
    TEST_ASSERT_EQUAL_UINT8(
        1, claims_conflict(res_axis(0) | res_axis(3), res_axis(0) | res_axis(3)));
    // Disjoint masks never conflict.
    TEST_ASSERT_EQUAL_UINT8(
        0, claims_conflict(res_axis(0) | res_axis(1), res_axis(2) | res_dac(0)));
    // Nothing wanted -> nothing conflicts.
    TEST_ASSERT_EQUAL_UINT8(0, claims_conflict(0, 0xFFFFFFFFu));
}

// --- Table-driven lookup (tests + Phase D) --------------------------------

void test_static_claims_lookup_in_explicit_table(void) {
    const ClaimsRow table[] = {
        {0x01, res_axis(0), nullptr},  // MOVE_X-style
        {0x02, res_axis(1), nullptr},  // MOVE_Y-style
    };
    TEST_ASSERT_EQUAL_HEX32(res_axis(0), claims_for_in(table, 2, 0x01, nullptr, 0));
    TEST_ASSERT_EQUAL_HEX32(res_axis(1), claims_for_in(table, 2, 0x02, nullptr, 0));
    TEST_ASSERT_EQUAL_HEX32(0, claims_for_in(table, 2, 0x03, nullptr, 0));  // absent
}

void test_computed_hook_overrides_static_claims(void) {
    const ClaimsRow table[] = {
        {0x50, res_axis(0), computed_axes_2_3},  // static_claims must be ignored
    };
    uint32_t got = claims_for_in(table, 1, 0x50, nullptr, 0);
    TEST_ASSERT_EQUAL_HEX32(res_axis(2) | res_axis(3), got);
    TEST_ASSERT_EQUAL_HEX32(0, got & res_axis(0));  // static did not leak
}

void test_computed_hook_receives_payload_len(void) {
    const ClaimsRow table[] = {{0x51, 0, computed_echo_len}};
    uint8_t payload[3] = {1, 2, 3};
    TEST_ASSERT_EQUAL_HEX32(3, claims_for_in(table, 1, 0x51, payload, 3));
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_system_commands_claim_nothing);
    RUN_TEST(test_command_absent_from_table_claims_nothing);
    RUN_TEST(test_conflict_reports_lowest_resource_plus_one);
    RUN_TEST(test_static_claims_lookup_in_explicit_table);
    RUN_TEST(test_computed_hook_overrides_static_claims);
    RUN_TEST(test_computed_hook_receives_payload_len);
    return UNITY_END();
}
