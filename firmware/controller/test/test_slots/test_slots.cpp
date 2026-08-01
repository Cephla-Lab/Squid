#include <unity.h>

#include <stdint.h>

#include "protocol/frames.h"
#include "protocol/slots.h"

// Include sources directly for native tests (SlotManager uses claims_conflict).
#include "protocol/claims.cpp"
#include "protocol/slots.cpp"

using namespace protocol;

void setUp(void) {}
void tearDown(void) {}

// Convenience wrapper: accept with all out-params captured.
static AcceptResult accept(SlotManager& m, uint8_t id, uint8_t type, uint32_t claims,
                           bool retry, uint8_t* cres, uint8_t* holder, uint8_t* rstat,
                           uint8_t* rerr) {
    uint8_t a = 0xFF, b = 0xFF, c = 0xFF, d = 0xFF;
    AcceptResult r = m.try_accept(id, type, claims, retry, &a, &b, &c, &d);
    if (cres) *cres = a;
    if (holder) *holder = b;
    if (rstat) *rstat = c;
    if (rerr) *rerr = d;
    return r;
}

// --- Accept / capacity ----------------------------------------------------

void test_accept_five_slots_then_no_slots(void) {
    SlotManager m;
    // Five compatible (zero-claim) commands fill all slots.
    for (uint8_t i = 1; i <= 5; ++i) {
        TEST_ASSERT_EQUAL(AcceptResult::NewCommand,
                          accept(m, i, 0x01, 0, false, nullptr, nullptr, nullptr, nullptr));
    }
    // The sixth compatible command has nowhere to go.
    TEST_ASSERT_EQUAL(AcceptResult::RejectNoSlots,
                      accept(m, 6, 0x01, 0, false, nullptr, nullptr, nullptr, nullptr));
}

// --- Resource conflict ----------------------------------------------------

void test_conflict_reports_resource_and_holder(void) {
    SlotManager m;
    TEST_ASSERT_EQUAL(AcceptResult::NewCommand,
                      accept(m, 42, 0x01, res_axis(0), false, nullptr, nullptr, nullptr, nullptr));

    uint8_t cres = 0xFF, holder = 0xFF;
    AcceptResult r = accept(m, 43, 0x02, res_axis(0), false, &cres, &holder, nullptr, nullptr);
    TEST_ASSERT_EQUAL(AcceptResult::RejectBusy, r);
    TEST_ASSERT_EQUAL_UINT8(0, cres);       // resource id 0 (axis 0)
    TEST_ASSERT_EQUAL_UINT8(42, holder);    // held by cmd 42

    // A disjoint resource is accepted.
    TEST_ASSERT_EQUAL(AcceptResult::NewCommand,
                      accept(m, 44, 0x02, res_axis(1), false, nullptr, nullptr, nullptr, nullptr));
}

// --- Complete -> ring + head_seq, claims released -------------------------

void test_complete_frees_slot_and_records_ring(void) {
    SlotManager m;
    TEST_ASSERT_EQUAL_UINT8(0, m.ring_head_seq());
    accept(m, 3, 0x0A, res_axis(0), false, nullptr, nullptr, nullptr, nullptr);
    TEST_ASSERT_NOT_NULL(m.find(3));
    TEST_ASSERT_EQUAL_HEX32(res_axis(0), m.inflight_claims_union());

    m.complete(3, STATUS_FAILED, ERR_INVALID_PARAMETER);
    TEST_ASSERT_NULL(m.find(3));                              // slot freed
    TEST_ASSERT_EQUAL_HEX32(0, m.inflight_claims_union());   // claims released
    TEST_ASSERT_EQUAL_UINT8(1, m.ring_head_seq());           // head_seq advanced

    uint8_t st = 0, er = 0;
    TEST_ASSERT_TRUE(m.ring_lookup(3, &st, &er));
    TEST_ASSERT_EQUAL_UINT8(STATUS_FAILED, st);
    TEST_ASSERT_EQUAL_UINT8(ERR_INVALID_PARAMETER, er);
}

// --- Ring wraps at 8 keeping the newest -----------------------------------

void test_ring_wraps_keeping_newest(void) {
    SlotManager m;
    for (uint8_t i = 1; i <= 10; ++i) {
        accept(m, i, 0x01, 0, false, nullptr, nullptr, nullptr, nullptr);
        m.complete(i, STATUS_OK, ERR_NONE);
    }
    TEST_ASSERT_EQUAL_UINT8(10, m.ring_head_seq());

    uint8_t st = 0, er = 0;
    // The two oldest (1, 2) were evicted; 3..10 remain.
    TEST_ASSERT_FALSE(m.ring_lookup(1, &st, &er));
    TEST_ASSERT_FALSE(m.ring_lookup(2, &st, &er));
    TEST_ASSERT_TRUE(m.ring_lookup(3, &st, &er));
    TEST_ASSERT_TRUE(m.ring_lookup(10, &st, &er));
    TEST_ASSERT_EQUAL_UINT8(STATUS_OK, st);
}

// --- find() ---------------------------------------------------------------

void test_find_returns_active_slot(void) {
    SlotManager m;
    accept(m, 77, 0x30, res_axis(2), false, nullptr, nullptr, nullptr, nullptr);
    const SlotInfo* s = m.find(77);
    TEST_ASSERT_NOT_NULL(s);
    TEST_ASSERT_EQUAL_UINT8(77, s->cmd_id);
    TEST_ASSERT_EQUAL_UINT8(0x30, s->cmd_type);
    TEST_ASSERT_EQUAL_UINT8(SLOT_ACTIVE, s->state);
    TEST_ASSERT_EQUAL_HEX32(res_axis(2), s->claims);
    TEST_ASSERT_NULL(m.find(78));  // never accepted
}

// --- RETRY semantics ------------------------------------------------------

void test_retry_of_active_is_active_duplicate(void) {
    SlotManager m;
    accept(m, 9, 0x01, res_axis(0), false, nullptr, nullptr, nullptr, nullptr);
    // A retry of an in-flight command returns its live state, no new slot.
    TEST_ASSERT_EQUAL(AcceptResult::ActiveDuplicate,
                      accept(m, 9, 0x01, res_axis(0), true, nullptr, nullptr, nullptr, nullptr));
    // Still exactly one active claim.
    TEST_ASSERT_EQUAL_HEX32(res_axis(0), m.inflight_claims_union());
    // A non-retry duplicate of an active cmd_id is also deduped (never re-run).
    TEST_ASSERT_EQUAL(AcceptResult::ActiveDuplicate,
                      accept(m, 9, 0x01, res_axis(0), false, nullptr, nullptr, nullptr, nullptr));
}

void test_retry_of_completed_replays_ring_without_reexec(void) {
    SlotManager m;
    accept(m, 5, 0x0A, 0, false, nullptr, nullptr, nullptr, nullptr);
    m.complete(5, STATUS_FAILED, ERR_RESOURCE_BUSY);

    uint8_t rstat = 0, rerr = 0;
    AcceptResult r = accept(m, 5, 0x0A, 0, true, nullptr, nullptr, &rstat, &rerr);
    TEST_ASSERT_EQUAL(AcceptResult::CompletedDuplicate, r);
    TEST_ASSERT_EQUAL_UINT8(STATUS_FAILED, rstat);
    TEST_ASSERT_EQUAL_UINT8(ERR_RESOURCE_BUSY, rerr);
    // No re-execution: no new slot was reserved.
    TEST_ASSERT_NULL(m.find(5));
    TEST_ASSERT_EQUAL_HEX32(0, m.inflight_claims_union());
}

void test_retry_of_unknown_is_treated_as_new(void) {
    SlotManager m;
    // Never seen cmd 200; a retry is treated as a fresh command.
    TEST_ASSERT_EQUAL(AcceptResult::NewCommand,
                      accept(m, 200, 0x01, res_axis(0), true, nullptr, nullptr, nullptr, nullptr));
    TEST_ASSERT_NOT_NULL(m.find(200));
}

void test_non_retry_reuse_of_completed_id_is_new(void) {
    SlotManager m;
    accept(m, 5, 0x0A, 0, false, nullptr, nullptr, nullptr, nullptr);
    m.complete(5, STATUS_OK, ERR_NONE);
    // A fresh (non-retry) command reusing a recently-completed id is NEW, not
    // a ring replay — cmd_ids recycle faster than the 8-entry ring forgets.
    TEST_ASSERT_EQUAL(AcceptResult::NewCommand,
                      accept(m, 5, 0x0A, 0, false, nullptr, nullptr, nullptr, nullptr));
    TEST_ASSERT_NOT_NULL(m.find(5));
}

// --- set_progress + fill_response + reset ---------------------------------

void test_set_progress_and_fill_response(void) {
    SlotManager m;
    accept(m, 11, 0x0A, res_axis(0), false, nullptr, nullptr, nullptr, nullptr);
    accept(m, 12, 0x0B, res_axis(1), false, nullptr, nullptr, nullptr, nullptr);
    m.set_progress(11, 55);
    m.set_progress(11, 200);  // clamps to 100
    m.complete(12, STATUS_OK, ERR_NONE);

    StandardResponse resp;
    m.fill_response(resp);
    // Slot holding cmd 11 reflects progress 100 and ACTIVE state.
    bool found11 = false;
    for (size_t i = 0; i < SlotManager::kNumSlots; ++i) {
        if (resp.slots[i].cmd_id == 11 && resp.slots[i].state == SLOT_ACTIVE) {
            TEST_ASSERT_EQUAL_UINT8(100, resp.slots[i].progress);
            found11 = true;
        }
    }
    TEST_ASSERT_TRUE(found11);
    TEST_ASSERT_EQUAL_UINT8(1, resp.ring_head_seq);  // one completion (cmd 12)

    // The ring section carries cmd 12's outcome.
    bool found12 = false;
    for (size_t i = 0; i < SlotManager::kRingSize; ++i) {
        if (resp.ring[i].cmd_id == 12) {
            TEST_ASSERT_EQUAL_UINT8(STATUS_OK, resp.ring[i].final_status);
            found12 = true;
        }
    }
    TEST_ASSERT_TRUE(found12);
}

void test_reset_clears_everything(void) {
    SlotManager m;
    accept(m, 1, 0x01, res_axis(0), false, nullptr, nullptr, nullptr, nullptr);
    m.complete(1, STATUS_OK, ERR_NONE);
    accept(m, 2, 0x01, res_axis(1), false, nullptr, nullptr, nullptr, nullptr);

    m.reset();
    TEST_ASSERT_NULL(m.find(2));
    TEST_ASSERT_EQUAL_HEX32(0, m.inflight_claims_union());
    TEST_ASSERT_EQUAL_UINT8(0, m.ring_head_seq());
    uint8_t st = 0, er = 0;
    TEST_ASSERT_FALSE(m.ring_lookup(1, &st, &er));  // ring cleared too
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_accept_five_slots_then_no_slots);
    RUN_TEST(test_conflict_reports_resource_and_holder);
    RUN_TEST(test_complete_frees_slot_and_records_ring);
    RUN_TEST(test_ring_wraps_keeping_newest);
    RUN_TEST(test_find_returns_active_slot);
    RUN_TEST(test_retry_of_active_is_active_duplicate);
    RUN_TEST(test_retry_of_completed_replays_ring_without_reexec);
    RUN_TEST(test_retry_of_unknown_is_treated_as_new);
    RUN_TEST(test_non_retry_reuse_of_completed_id_is_new);
    RUN_TEST(test_set_progress_and_fill_response);
    RUN_TEST(test_reset_clears_everything);
    return UNITY_END();
}
