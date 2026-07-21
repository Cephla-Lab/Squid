#include <unity.h>

#include <cstring>
#include <stdint.h>
#include <vector>

#include "boot/boot.h"

// Include source directly for native tests.
#include "boot/boot.cpp"

using boot::Boot;
using boot::FaultRecord;

// --- Fake hardware --------------------------------------------------------

class FakeBootHal : public boot::BootHal {
public:
    uint8_t eeprom[256];
    uint8_t reset_cause_value;
    int reset_reads;
    bool reset_cleared;
    uint32_t cycle;
    uint32_t last_wdog_arm_ms;
    int wdog_kicks;
    std::vector<const char*> calls;

    FakeBootHal() {
        memset(eeprom, 0, sizeof(eeprom));
        reset_cause_value = boot::RESET_POWER_ON;
        reset_reads = 0;
        reset_cleared = false;
        cycle = 0xABCDEF01u;
        last_wdog_arm_ms = 0;
        wdog_kicks = 0;
    }
    void safe_state() override { calls.push_back("safe_state"); }
    uint8_t eeprom_read(uint16_t a) override {
        calls.push_back("ee_read");
        return eeprom[a];
    }
    void eeprom_write(uint16_t a, uint8_t v) override {
        calls.push_back("ee_write");
        eeprom[a] = v;
    }
    uint8_t read_reset_cause() override {
        ++reset_reads;
        return reset_cause_value;
    }
    void clear_reset_cause() override {
        reset_cleared = true;
        reset_cause_value = boot::RESET_UNKNOWN;
    }
    void watchdog_arm(uint32_t ms) override { last_wdog_arm_ms = ms; }
    void watchdog_kick() override { ++wdog_kicks; }
    uint32_t cycle_counter() override { return cycle; }
};

void setUp(void) {}
void tearDown(void) {}

// --- Ordering: safe_state runs before anything else -----------------------

void test_safe_state_called_first(void) {
    FakeBootHal hal;
    Boot b(hal);
    b.begin();
    TEST_ASSERT_TRUE(hal.calls.size() >= 1);
    TEST_ASSERT_EQUAL_STRING("safe_state", hal.calls[0]);
    // safe_state must precede any EEPROM access.
    for (size_t i = 1; i < hal.calls.size(); ++i) {
        TEST_ASSERT_TRUE(strcmp(hal.calls[i], "safe_state") != 0);
    }
}

// --- boot_count persists and increments -----------------------------------

void test_boot_count_increments_and_persists(void) {
    FakeBootHal hal;
    Boot b1(hal);
    b1.begin();
    TEST_ASSERT_EQUAL_UINT32(1, b1.boot_count());

    Boot b2(hal);  // "reboot": same EEPROM
    b2.begin();
    TEST_ASSERT_EQUAL_UINT32(2, b2.boot_count());

    Boot b3(hal);
    b3.begin();
    TEST_ASSERT_EQUAL_UINT32(3, b3.boot_count());
}

// --- reset cause captured once, register cleared --------------------------

void test_reset_cause_captured_once(void) {
    FakeBootHal hal;
    hal.reset_cause_value = boot::RESET_WATCHDOG;
    Boot b(hal);
    b.begin();
    TEST_ASSERT_EQUAL_UINT8(boot::RESET_WATCHDOG, b.reset_cause());
    TEST_ASSERT_EQUAL_INT(1, hal.reset_reads);   // read exactly once
    TEST_ASSERT_TRUE(hal.reset_cleared);          // register cleared for next boot
    // Cause is latched: it survives repeated reads.
    TEST_ASSERT_EQUAL_UINT8(boot::RESET_WATCHDOG, b.reset_cause());
}

// --- session nonce non-zero and differs across boots ----------------------

void test_session_nonce_nonzero_and_differs(void) {
    FakeBootHal hal;
    hal.cycle = 0x11112222u;  // identical entropy on both boots
    Boot b1(hal);
    b1.begin();
    uint32_t n1 = b1.session_nonce();

    Boot b2(hal);  // same EEPROM -> boot_count differs -> nonce must differ
    b2.begin();
    uint32_t n2 = b2.session_nonce();

    TEST_ASSERT_NOT_EQUAL(0, n1);
    TEST_ASSERT_NOT_EQUAL(0, n2);
    TEST_ASSERT_NOT_EQUAL(n1, n2);
}

// --- watchdog armed with the configured timeout ---------------------------

void test_watchdog_armed_on_begin(void) {
    FakeBootHal hal;
    Boot b(hal);
    b.begin();
    TEST_ASSERT_EQUAL_UINT32(Boot::kWatchdogTimeoutMs, hal.last_wdog_arm_ms);
    b.kick_watchdog();
    b.kick_watchdog();
    TEST_ASSERT_EQUAL_INT(2, hal.wdog_kicks);
}

// --- fault ring: append + newest-first read -------------------------------

void test_fault_ring_append_and_read(void) {
    FakeBootHal hal;
    Boot b(hal);
    b.begin();

    b.fault_append(0x41, 0x01, 100);
    b.fault_append(0x42, 0x02, 200);
    b.fault_append(0x43, 0x03, 300);
    TEST_ASSERT_EQUAL_UINT8(3, b.fault_count());

    FaultRecord r;
    TEST_ASSERT_TRUE(b.fault_get(0, r));  // newest
    TEST_ASSERT_EQUAL_UINT8(0x43, r.code);
    TEST_ASSERT_EQUAL_UINT32(300, r.uptime_ms);
    TEST_ASSERT_TRUE(b.fault_get(2, r));  // oldest
    TEST_ASSERT_EQUAL_UINT8(0x41, r.code);
    TEST_ASSERT_FALSE(b.fault_get(3, r));  // out of range
}

// --- fault ring wraps at 16 keeping the newest ----------------------------

void test_fault_ring_wraps_at_16(void) {
    FakeBootHal hal;
    Boot b(hal);
    b.begin();

    for (int i = 1; i <= 20; ++i) {
        b.fault_append((uint8_t)i, 0, (uint32_t)(i * 10));
    }
    TEST_ASSERT_EQUAL_UINT8(20, b.fault_count());  // total (saturating < 255)

    FaultRecord r;
    TEST_ASSERT_TRUE(b.fault_get(0, r));   // newest = the 20th
    TEST_ASSERT_EQUAL_UINT8(20, r.code);
    TEST_ASSERT_TRUE(b.fault_get(15, r));  // oldest kept = the 5th
    TEST_ASSERT_EQUAL_UINT8(5, r.code);
    TEST_ASSERT_FALSE(b.fault_get(16, r));  // only 16 retained
}

// --- fault ring survives a reboot -----------------------------------------

void test_fault_ring_survives_reboot(void) {
    FakeBootHal hal;
    Boot b1(hal);
    b1.begin();
    b1.fault_append(0x50, 0x11, 111);
    b1.fault_append(0x51, 0x22, 222);

    Boot b2(hal);  // "reboot": same EEPROM
    b2.begin();
    TEST_ASSERT_EQUAL_UINT32(2, b2.boot_count());     // booted again
    TEST_ASSERT_EQUAL_UINT8(2, b2.fault_count());     // faults preserved

    FaultRecord r;
    TEST_ASSERT_TRUE(b2.fault_get(0, r));
    TEST_ASSERT_EQUAL_UINT8(0x51, r.code);
    TEST_ASSERT_EQUAL_UINT32(222, r.uptime_ms);
    TEST_ASSERT_TRUE(b2.fault_get(1, r));
    TEST_ASSERT_EQUAL_UINT8(0x50, r.code);

    // A fault appended after the reboot continues the same ring.
    b2.fault_append(0x52, 0x33, 333);
    TEST_ASSERT_EQUAL_UINT8(3, b2.fault_count());
    TEST_ASSERT_TRUE(b2.fault_get(0, r));
    TEST_ASSERT_EQUAL_UINT8(0x52, r.code);
}

// --- loop/ISR watermarks update max-only ----------------------------------

void test_watermarks_update_max_only(void) {
    FakeBootHal hal;
    Boot b(hal);
    b.begin();

    b.note_loop_us(100);
    b.note_loop_us(50);   // lower — ignored
    b.note_loop_us(200);
    b.note_loop_us(150);  // lower — ignored
    TEST_ASSERT_EQUAL_UINT32(200, b.loop_max_us());

    b.note_isr_us(10);
    b.note_isr_us(5);
    TEST_ASSERT_EQUAL_UINT32(10, b.isr_max_us());
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_safe_state_called_first);
    RUN_TEST(test_boot_count_increments_and_persists);
    RUN_TEST(test_reset_cause_captured_once);
    RUN_TEST(test_session_nonce_nonzero_and_differs);
    RUN_TEST(test_watchdog_armed_on_begin);
    RUN_TEST(test_fault_ring_append_and_read);
    RUN_TEST(test_fault_ring_wraps_at_16);
    RUN_TEST(test_fault_ring_survives_reboot);
    RUN_TEST(test_watermarks_update_max_only);
    return UNITY_END();
}
