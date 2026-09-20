#include <unity.h>

#include "controller_profile.h"

using namespace controller;

void setUp(void) {}
void tearDown(void) {}

void test_legacy_profile_matches_the_shipped_pin_map(void) {
    TEST_ASSERT_EQUAL_UINT8(4, kLegacy.n_triggers);
    const uint8_t pins[4] = {29, 30, 31, 32};
    TEST_ASSERT_EQUAL_UINT8_ARRAY(pins, kLegacy.trigger_pins, 4);
    TEST_ASSERT_EQUAL_UINT8(0, kLegacy.trigger_assert_level);  // inverting 3904 stage: pin LOW = asserted
    TEST_ASSERT_EQUAL_INT8(-1, kLegacy.ready_pin);
}

void test_new_controller_profile(void) {
    TEST_ASSERT_EQUAL_UINT8(1, kNewCtrl.n_triggers);
    TEST_ASSERT_EQUAL_UINT8(19, kNewCtrl.trigger_pins[0]);
    TEST_ASSERT_EQUAL_UINT8(1, kNewCtrl.trigger_assert_level);  // direct GPIO: pin HIGH = asserted
    TEST_ASSERT_EQUAL_INT8(18, kNewCtrl.ready_pin);
}

void test_channel_bounds_come_from_the_profile(void) {
    TEST_ASSERT_TRUE(trigger_channel_valid(kLegacy, 3));
    TEST_ASSERT_FALSE(trigger_channel_valid(kLegacy, 4));
    TEST_ASSERT_FALSE(trigger_channel_valid(kLegacy, 15));   // the host nibble can carry 0-15
    TEST_ASSERT_TRUE(trigger_channel_valid(kNewCtrl, 0));
    TEST_ASSERT_FALSE(trigger_channel_valid(kNewCtrl, 1));   // would index past a 1-entry pin map
    TEST_ASSERT_FALSE(trigger_channel_valid(kNewCtrl, 255)); // SET_STROBE_DELAY carries a raw byte
}

void test_default_build_selects_legacy(void) {
    TEST_ASSERT_EQUAL_UINT8(4, kActive.n_triggers);  // native env defines no controller flag
    TEST_ASSERT_TRUE(kMaxTriggers >= kLegacy.n_triggers);
}

int main(void) {
    UNITY_BEGIN();
    RUN_TEST(test_legacy_profile_matches_the_shipped_pin_map);
    RUN_TEST(test_new_controller_profile);
    RUN_TEST(test_channel_bounds_come_from_the_profile);
    RUN_TEST(test_default_build_selects_legacy);
    return UNITY_END();
}
