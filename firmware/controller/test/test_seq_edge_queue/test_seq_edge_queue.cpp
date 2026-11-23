#include <unity.h>

// Include sources directly for native tests (same convention as test_crc8)
#include "sequencer/seq_edge_queue.cpp"

using namespace seq;

static Edge edge(uint32_t t_us, EdgeKind kind, uint8_t camera = 0, uint8_t mask = 0) {
    Edge e{};
    e.t_us = t_us;
    e.kind = (uint8_t)kind;
    e.camera_id = camera;
    e.illum_ttl_mask = mask;
    return e;
}

void setUp(void) {}
void tearDown(void) {}

void test_edges_pop_in_time_order_regardless_of_push_order(void) {
    EdgeQueue q;
    const uint32_t now = 1000;
    TEST_ASSERT_TRUE(q.push(edge(1500, EdgeKind::IllumOff), now));
    TEST_ASSERT_TRUE(q.push(edge(1100, EdgeKind::TriggerAssert), now));
    TEST_ASSERT_TRUE(q.push(edge(1300, EdgeKind::IllumOn), now));
    TEST_ASSERT_EQUAL_UINT8(3, q.size());
    Edge e;
    TEST_ASSERT_TRUE(q.pop_due(2000, &e));
    TEST_ASSERT_EQUAL_UINT32(1100, e.t_us);
    TEST_ASSERT_TRUE(q.pop_due(2000, &e));
    TEST_ASSERT_EQUAL_UINT32(1300, e.t_us);
    TEST_ASSERT_TRUE(q.pop_due(2000, &e));
    TEST_ASSERT_EQUAL_UINT32(1500, e.t_us);
    TEST_ASSERT_FALSE(q.pop_due(2000, &e));
    TEST_ASSERT_TRUE(q.empty());
}

void test_an_edge_is_not_popped_before_its_time(void) {
    EdgeQueue q;
    q.push(edge(5000, EdgeKind::TriggerAssert), 1000);
    Edge e;
    TEST_ASSERT_FALSE(q.pop_due(4999, &e));
    TEST_ASSERT_EQUAL_UINT8(1, q.size());
    TEST_ASSERT_TRUE(q.pop_due(5000, &e));
}

// LEVEL trigger + global reset: illumination-off and trigger-release share a timestamp, and
// the light must be off no later than the exposure ends. At the start, the trigger asserts
// before the light comes on (strobe delay 0).
void test_same_time_edges_break_ties_light_off_first_and_trigger_before_light_on(void) {
    EdgeQueue q;
    const uint32_t now = 0;
    q.push(edge(900, EdgeKind::TriggerRelease), now);
    q.push(edge(900, EdgeKind::IllumOff), now);
    q.push(edge(100, EdgeKind::IllumOn), now);
    q.push(edge(100, EdgeKind::TriggerAssert), now);
    Edge e;
    q.pop_due(1000, &e);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)EdgeKind::TriggerAssert, e.kind);
    q.pop_due(1000, &e);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)EdgeKind::IllumOn, e.kind);
    q.pop_due(1000, &e);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)EdgeKind::IllumOff, e.kind);
    q.pop_due(1000, &e);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)EdgeKind::TriggerRelease, e.kind);
}

// micros() wraps every 71.6 min: an edge after the wrap has a SMALLER absolute timestamp
// than one before it, and must still come later.
void test_order_and_due_checks_survive_the_micros_wrap(void) {
    EdgeQueue q;
    const uint32_t now = 0xFFFFFF00u;
    q.push(edge(now + 1000, EdgeKind::IllumOff), now);      // wraps to 0x000002E8
    q.push(edge(now + 100, EdgeKind::TriggerAssert), now);  // still before the wrap
    Edge e;
    TEST_ASSERT_FALSE(q.pop_due(now + 50, &e));
    TEST_ASSERT_TRUE(q.pop_due(now + 100, &e));
    TEST_ASSERT_EQUAL_UINT8((uint8_t)EdgeKind::TriggerAssert, e.kind);
    TEST_ASSERT_FALSE(q.pop_due(now + 500, &e));   // now + 500 has wrapped; the edge has not come
    TEST_ASSERT_TRUE(q.pop_due(now + 1000, &e));
    TEST_ASSERT_EQUAL_UINT8((uint8_t)EdgeKind::IllumOff, e.kind);
}

void test_next_delay(void) {
    EdgeQueue q;
    uint32_t d = 123;
    TEST_ASSERT_FALSE(q.next_delay(1000, &d));  // empty
    q.push(edge(1800, EdgeKind::IllumOff), 1000);
    q.push(edge(1250, EdgeKind::TriggerAssert), 1000);
    TEST_ASSERT_TRUE(q.next_delay(1000, &d));
    TEST_ASSERT_EQUAL_UINT32(250, d);
    TEST_ASSERT_TRUE(q.next_delay(1300, &d));  // overdue -> service now
    TEST_ASSERT_EQUAL_UINT32(0, d);
}

void test_push_plan_level_expands_to_four_edges(void) {
    EdgeQueue q;
    ExposurePlan p{};
    p.camera_id = 2;
    p.trigger_mode = (uint8_t)TriggerMode::Level;
    p.illum_ttl_mask = 0x05;
    p.t_assert_us = 1000;
    p.t_illum_on_us = 1300;
    p.t_illum_off_us = 21300;
    p.t_deassert_us = 21300;
    TEST_ASSERT_TRUE(q.push_plan(p, 1000));
    TEST_ASSERT_EQUAL_UINT8(4, q.size());
    Edge e;
    q.pop_due(30000, &e);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)EdgeKind::TriggerAssert, e.kind);
    TEST_ASSERT_EQUAL_UINT8(2, e.camera_id);
    q.pop_due(30000, &e);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)EdgeKind::IllumOn, e.kind);
    TEST_ASSERT_EQUAL_HEX8(0x05, e.illum_ttl_mask);
    q.pop_due(30000, &e);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)EdgeKind::IllumOff, e.kind);
    TEST_ASSERT_EQUAL_HEX8(0x05, e.illum_ttl_mask);
    q.pop_due(30000, &e);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)EdgeKind::TriggerRelease, e.kind);
    TEST_ASSERT_EQUAL_UINT8(2, e.camera_id);
}

// An LED-matrix-only channel has no TTL mask: only the trigger edges are scheduled.
void test_push_plan_without_ttl_mask_schedules_trigger_edges_only(void) {
    EdgeQueue q;
    ExposurePlan p{};
    p.illum_ttl_mask = 0;
    p.t_assert_us = 1000;
    p.t_illum_on_us = 1300;
    p.t_illum_off_us = 21300;
    p.t_deassert_us = 1050;
    TEST_ASSERT_TRUE(q.push_plan(p, 1000));
    TEST_ASSERT_EQUAL_UINT8(2, q.size());
}

// A plan is all-or-nothing: a half-scheduled exposure could leave the light on.
void test_capacity_and_all_or_nothing_plans(void) {
    EdgeQueue q;
    ExposurePlan p{};
    p.illum_ttl_mask = 0x01;
    p.t_assert_us = 1000;
    p.t_illum_on_us = 1100;
    p.t_illum_off_us = 1200;
    p.t_deassert_us = 1200;
    for (uint8_t i = 0; i < EdgeQueue::kCapacity / 4; i++) TEST_ASSERT_TRUE(q.push_plan(p, 1000));
    TEST_ASSERT_EQUAL_UINT8(EdgeQueue::kCapacity, q.size());
    TEST_ASSERT_FALSE(q.push_plan(p, 1000));
    TEST_ASSERT_FALSE(q.push(edge(1000, EdgeKind::TriggerAssert), 1000));
    TEST_ASSERT_EQUAL_UINT8(EdgeQueue::kCapacity, q.size());
    Edge e;
    q.pop_due(5000, &e);
    q.pop_due(5000, &e);
    q.pop_due(5000, &e);  // 3 free slots: a 4-edge plan still does not fit
    uint8_t before = q.size();
    TEST_ASSERT_FALSE(q.push_plan(p, 1000));
    TEST_ASSERT_EQUAL_UINT8(before, q.size());
    q.clear();
    TEST_ASSERT_TRUE(q.empty());
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_edges_pop_in_time_order_regardless_of_push_order);
    RUN_TEST(test_an_edge_is_not_popped_before_its_time);
    RUN_TEST(test_same_time_edges_break_ties_light_off_first_and_trigger_before_light_on);
    RUN_TEST(test_order_and_due_checks_survive_the_micros_wrap);
    RUN_TEST(test_next_delay);
    RUN_TEST(test_push_plan_level_expands_to_four_edges);
    RUN_TEST(test_push_plan_without_ttl_mask_schedules_trigger_edges_only);
    RUN_TEST(test_capacity_and_all_or_nothing_plans);
    return UNITY_END();
}
