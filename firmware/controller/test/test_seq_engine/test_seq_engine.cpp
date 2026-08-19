#include <unity.h>

#include "sequencer/seq_types.h"
// Include sources directly for native tests (same convention as test_crc8)
#include "sequencer/seq_engine.cpp"
#include "sequencer/seq_types.cpp"

#include "fake_hal.h"

using namespace seq;

static SeqLoop good_loop() {
    SeqLoop l{};
    l.stack_axis_type = (uint8_t)StackAxisType::Piezo;
    l.stack_axis_id = 7;  // DAC7 = piezo on current boards
    l.dz = 120;
    l.n_layers = 10;
    l.order = (uint8_t)Order::ChannelsInner;
    l.z_settle_us = 2000;
    l.return_to_start = 1;
    l.n_channels = 2;
    return l;
}

static SeqChannel good_channel() {
    SeqChannel c{};
    c.filter_wheel = kNone;
    c.filter_pos = 0;
    c.illum_ttl_mask = 0x01;
    c.led_pattern = kNone;
    c.intensity_dac = 0;
    c.intensity = 30000;
    c.exposure_us = 10000;
    c.camera_mask = 0x01;
    c.z_offset = 0;
    c.flags = 0;
    return c;
}

static SeqCameraConfig cam_level() {
    SeqCameraConfig c{};
    c.trigger_mode = (uint8_t)TriggerMode::Level;
    c.strobe_delay_us = 500;
    c.readout_time_us = 20000;
    c.min_trigger_period_us = 0;
    c.ready_line = kNone;
    c.ready_active_high = 1;
    c.readout_overlap_safe = 1;
    return c;
}

void setUp(void) {}
void tearDown(void) {}

// Simplest program: 1 layer, 1 channel, no filter, piezo stack axis, level trigger.
void test_single_frame_program_completes(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 1;
    l.n_channels = 1;
    l.z_settle_us = 1000;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None,
                            (uint8_t)e.load(l, ch, cams, 1, /*stack_start=*/40000).error);
    TEST_ASSERT_TRUE(e.start(hal.now_us, /*wait_timeout_us=*/5000000));
    run_until(e, hal, 2000000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT32(1, e.progress().frames_fired);
    TEST_ASSERT_EQUAL(1, (int)hal.plans.size());
    const ExposurePlan& p = hal.plans[0];
    // Level semantics: illum on at assert+strobe; deassert == illum_off;
    // pulse width = strobe + exposure.
    TEST_ASSERT_EQUAL_UINT32(p.t_assert_us + 500, p.t_illum_on_us);
    TEST_ASSERT_EQUAL_UINT32(p.t_illum_on_us + 10000, p.t_illum_off_us);
    TEST_ASSERT_EQUAL_UINT32(p.t_illum_off_us, p.t_deassert_us);
}

// Piezo stack axis: layer z = DAC steps of dz; settle honored before exposure.
void test_piezo_step_and_settle_gate_exposure(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 2;
    l.n_channels = 1;
    l.dz = 120;
    l.z_settle_us = 3000;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    cams[0].readout_time_us = 5000;
    e.load(l, ch, cams, 1, 40000);
    e.start(hal.now_us, 5000000);
    run_until(e, hal, 3000000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT32(2, e.progress().frames_fired);
    // DAC writes to the piezo (dac 7): layer0 = 40000, layer1 = 40120,
    // + return_to_start = 40000 at the end.
    int dac_writes = 0;
    uint16_t last = 0;
    for (auto& c : hal.calls) {
        if (c.what == "dac" && c.a == 7) {
            dac_writes++;
            last = (uint16_t)c.b;
        }
    }
    TEST_ASSERT_EQUAL(3, dac_writes);
    TEST_ASSERT_EQUAL_UINT16(40000, last);
    // Second exposure must start >= settle after the layer-1 DAC step.
    uint32_t t_dac1 = 0;
    for (auto& c : hal.calls) {
        if (c.what == "dac" && c.a == 7 && (uint16_t)c.b == 40120) t_dac1 = c.t_us;
    }
    TEST_ASSERT_TRUE(hal.plans[1].t_assert_us >= t_dac1 + 3000);
}

// Stepper stack axis: exposure gated on in_position + settle.
void test_stepper_settle_gates_exposure(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.stack_axis_type = (uint8_t)StackAxisType::Stepper;
    l.stack_axis_id = 2;  // Z
    l.n_layers = 1;
    l.n_channels = 1;
    l.z_settle_us = 4000;
    hal.move_duration_us[2] = 8000;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    e.load(l, ch, cams, 1, 100000);
    e.start(0, 5000000);
    run_until(e, hal, 1000000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    // assert >= move done (8000) + settle (4000); 100 µs tick quantum tolerance
    TEST_ASSERT_TRUE(hal.plans[0].t_assert_us >= 12000);
    TEST_ASSERT_TRUE(hal.plans[0].t_assert_us <= 12300);
}

// Filter-wheel move longer than z move dominates the WAIT.
void test_filter_wheel_gates_exposure(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 1;
    l.n_channels = 1;
    l.z_settle_us = 0;
    SeqChannel ch[1] = {good_channel()};
    ch[0].filter_wheel = 3;  // FILTER1 axis id
    ch[0].filter_pos = 5;
    hal.move_duration_us[3] = 50000;
    SeqCameraConfig cams[1] = {cam_level()};
    e.load(l, ch, cams, 1, 40000);
    e.start(0, 5000000);
    run_until(e, hal, 1000000);
    TEST_ASSERT_TRUE(hal.plans[0].t_assert_us >= 50000);
    // Filter move command must have been issued at PREP time (t=0), not lazily:
    // calls[0] = dac (piezo target), calls[1] = move (filter wheel).
    TEST_ASSERT_EQUAL_STRING("move", hal.calls[1].what.c_str());
    TEST_ASSERT_EQUAL_UINT32(0, hal.calls[1].t_us);
}

// Model-based readiness: second frame waits out readout_time even though motion is
// instant.
void test_model_readiness_spaces_triggers(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 2;
    l.n_channels = 1;
    l.dz = 0;
    l.z_settle_us = 0;
    SeqChannel ch[1] = {good_channel()};      // exposure 10000
    SeqCameraConfig cams[1] = {cam_level()};  // strobe 500, readout 20000
    e.load(l, ch, cams, 1, 40000);
    e.start(0, 5000000);
    run_until(e, hal, 2000000);
    TEST_ASSERT_EQUAL(2, (int)hal.plans.size());
    uint32_t end0 = hal.plans[0].t_deassert_us;  // = assert0 + 10500
    TEST_ASSERT_TRUE(hal.plans[1].t_assert_us >= end0 + 20000);
}

// THE core feature: next channel's filter move starts at exposure end (readout
// begins), NOT after readout completes. Saves (filter_move ∥ readout) per frame.
void test_filter_move_overlaps_readout(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 1;
    l.n_channels = 2;
    l.z_settle_us = 0;
    l.dz = 0;
    SeqChannel ch[2] = {good_channel(), good_channel()};
    ch[0].filter_wheel = 3;
    ch[0].filter_pos = 1;
    ch[1].filter_wheel = 3;
    ch[1].filter_pos = 2;
    hal.move_duration_us[3] = 15000;
    SeqCameraConfig cams[1] = {cam_level()};  // strobe 500, exposure 10000, readout 20000
    e.load(l, ch, cams, 1, 40000);
    e.start(0, 5000000);
    run_until(e, hal, 2000000);
    // exposure0 ends at assert0 + 10500; find the filter move to pos 2:
    uint32_t end0 = hal.plans[0].t_deassert_us;
    uint32_t t_move2 = 0;
    for (auto& c : hal.calls) {
        if (c.what == "move" && c.a == 3 && c.b == 2) t_move2 = c.t_us;
    }
    // within a tick of exposure end — i.e., DURING readout:
    TEST_ASSERT_TRUE(t_move2 >= end0 && t_move2 <= end0 + 200);
    // and frame1 fires when BOTH readout (end0+20000) and move (t_move2+15000) done:
    TEST_ASSERT_TRUE(hal.plans[1].t_assert_us >= end0 + 20000);
    TEST_ASSERT_TRUE(hal.plans[1].t_assert_us <= end0 + 20000 + 200);
}

// Z step for the next layer also overlaps the last channel's readout.
void test_z_step_overlaps_readout_between_layers(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 2;
    l.n_channels = 1;  // piezo dz = 120
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    e.load(l, ch, cams, 1, 40000);
    e.start(0, 5000000);
    run_until(e, hal, 2000000);
    uint32_t end0 = hal.plans[0].t_deassert_us;
    uint32_t t_dac1 = 0;
    for (auto& c : hal.calls) {
        if (c.what == "dac" && c.a == 7 && (uint16_t)c.b == 40120) t_dac1 = c.t_us;
    }
    TEST_ASSERT_TRUE(t_dac1 >= end0 && t_dac1 <= end0 + 200);
}

// Z_INNER order: full stack of channel 0, then channel 1; per-channel z_offset applied.
void test_z_inner_order_and_z_offset(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 2;
    l.n_channels = 2;
    l.order = (uint8_t)Order::ZInner;
    l.z_settle_us = 0;
    SeqChannel ch[2] = {good_channel(), good_channel()};
    ch[1].z_offset = 40;  // channel 1 offset
    SeqCameraConfig cams[1] = {cam_level()};
    cams[0].readout_time_us = 0;
    e.load(l, ch, cams, 1, 40000);
    e.start(0, 5000000);
    run_until(e, hal, 3000000);
    TEST_ASSERT_EQUAL_UINT32(4, e.progress().frames_fired);
    // Piezo targets in order: 40000, 40120 (ch0 L0,L1), 40040, 40160 (ch1 L0,L1),
    // then 40000 (return_to_start).
    std::vector<uint16_t> targets;
    for (auto& c : hal.calls) {
        if (c.what == "dac" && c.a == 7) targets.push_back((uint16_t)c.b);
    }
    uint16_t expect[5] = {40000, 40120, 40040, 40160, 40000};
    TEST_ASSERT_EQUAL(5, (int)targets.size());
    for (int i = 0; i < 5; i++) TEST_ASSERT_EQUAL_UINT16(expect[i], targets[i]);
}

void test_edge_mode_pulse_and_modeled_exposure_end(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 1;
    l.n_channels = 1;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    cams[0].trigger_mode = (uint8_t)TriggerMode::Edge;  // strobe 500
    e.load(l, ch, cams, 1, 40000);
    e.start(0, 5000000);
    run_until(e, hal, 1000000);
    const ExposurePlan& p = hal.plans[0];
    TEST_ASSERT_EQUAL_UINT32(p.t_assert_us + kEdgePulseUs, p.t_deassert_us);  // 50 µs
    TEST_ASSERT_EQUAL_UINT32(p.t_assert_us + 500 + 10000, p.t_illum_off_us);  // model
}

// Two cameras, different strobe delays: both scheduled at the same assert instant;
// the step is one frame event; readiness tracked per camera.
void test_two_cameras_simultaneous_exposure(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 1;
    l.n_channels = 1;
    SeqChannel ch[1] = {good_channel()};
    ch[0].camera_mask = 0x03;
    SeqCameraConfig cams[2] = {cam_level(), cam_level()};
    cams[1].strobe_delay_us = 2000;
    cams[1].readout_time_us = 40000;
    e.load(l, ch, cams, 2, 40000);
    e.start(0, 5000000);
    run_until(e, hal, 1000000);
    TEST_ASSERT_EQUAL(2, (int)hal.plans.size());
    TEST_ASSERT_EQUAL_UINT32(hal.plans[0].t_assert_us, hal.plans[1].t_assert_us);
    TEST_ASSERT_EQUAL_UINT32(1, e.progress().frames_fired);  // one step = one frame event
}

void test_ready_line_blocks_until_asserted(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 1;
    l.n_channels = 1;
    l.z_settle_us = 0;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    cams[0].ready_line = 0;
    cams[0].ready_active_high = 1;
    hal.ready_lines[0] = false;
    e.load(l, ch, cams, 1, 40000);
    e.start(0, 5000000);
    run_until(e, hal, 30000);
    TEST_ASSERT_EQUAL(0, (int)hal.plans.size());  // still gated
    hal.ready_lines[0] = true;
    run_until(e, hal, 60000);
    TEST_ASSERT_EQUAL(1, (int)hal.plans.size());
    TEST_ASSERT_TRUE(hal.plans[0].t_assert_us >= 30000);
}

void test_wait_timeout_aborts_with_all_off(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 1;
    l.n_channels = 1;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    cams[0].ready_line = 0;
    hal.ready_lines[0] = false;  // never ready
    e.load(l, ch, cams, 1, 40000);
    e.start(0, /*wait_timeout_us=*/100000);
    run_until(e, hal, 300000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Failed, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::WaitTimeout, e.progress().abort_error);
    bool all_off_called = false;
    for (auto& c : hal.calls) {
        if (c.what == "all_off") all_off_called = true;
    }
    TEST_ASSERT_TRUE(all_off_called);
}

// Rolling shutter: readout_overlap_safe=0 defers PREP(k+1) until the camera is done
// reading out — no motion during its readout.
void test_no_overlap_when_readout_unsafe(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 1;
    l.n_channels = 2;
    l.dz = 0;
    l.z_settle_us = 0;
    SeqChannel ch[2] = {good_channel(), good_channel()};
    ch[1].filter_wheel = 3;
    ch[1].filter_pos = 2;
    SeqCameraConfig cams[1] = {cam_level()};  // readout 20000
    cams[0].readout_overlap_safe = 0;
    e.load(l, ch, cams, 1, 40000);
    e.start(0, 5000000);
    run_until(e, hal, 2000000);
    uint32_t end0 = hal.plans[0].t_deassert_us;
    uint32_t t_move = 0;
    for (auto& c : hal.calls) {
        if (c.what == "move" && c.a == 3) t_move = c.t_us;
    }
    TEST_ASSERT_TRUE(t_move >= end0 + 20000);  // move waited out the readout
}

void test_cancel_finishes_current_exposure_then_stops(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 10;
    l.n_channels = 1;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    e.load(l, ch, cams, 1, 40000);
    e.start(0, 5000000);
    // run until mid-exposure of frame 2, then cancel:
    while (e.progress().frames_fired < 2) {
        hal.now_us += 100;
        e.tick(hal.now_us);
    }
    uint32_t t_cancel = hal.now_us;
    e.cancel();
    run_until(e, hal, t_cancel + 2000000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT32(2, e.progress().frames_fired);  // no frame 3
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::Canceled, e.progress().abort_error);
    // exposure 2's plan was never truncated: its deassert time stands as scheduled
    TEST_ASSERT_TRUE(hal.plans[1].t_deassert_us > t_cancel);
    // return_to_start honored: last HAL call is the piezo returning to 40000
    TEST_ASSERT_EQUAL_STRING("dac", hal.calls.back().what.c_str());
    TEST_ASSERT_EQUAL_UINT16(40000, (uint16_t)hal.calls.back().b);
}

void test_min_trigger_period_enforced(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 3;
    l.n_channels = 1;
    l.dz = 0;
    l.z_settle_us = 0;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    cams[0].readout_time_us = 0;
    cams[0].min_trigger_period_us = 50000;
    e.load(l, ch, cams, 1, 40000);
    e.start(0, 5000000);
    run_until(e, hal, 1000000);
    TEST_ASSERT_EQUAL(3, (int)hal.plans.size());
    for (int i = 1; i < 3; i++)
        TEST_ASSERT_TRUE(hal.plans[i].t_assert_us - hal.plans[i - 1].t_assert_us >= 50000);
}

// Whole-run invariants over a mixed program (property-style, deterministic inputs).
void test_run_invariants(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 5;
    l.n_channels = 3;
    l.z_settle_us = 1000;
    SeqChannel ch[3] = {good_channel(), good_channel(), good_channel()};
    ch[1].filter_wheel = 3;
    ch[1].filter_pos = 2;
    ch[2].filter_wheel = 3;
    ch[2].filter_pos = 4;
    hal.move_duration_us[3] = 7000;
    SeqCameraConfig cams[1] = {cam_level()};
    e.load(l, ch, cams, 1, 40000);
    e.start(0, 5000000);
    run_until(e, hal, 10000000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT32(15, e.progress().frames_fired);  // Nz × Nch
    // Invariant 1: exposures never overlap each other.
    for (size_t i = 1; i < hal.plans.size(); i++)
        TEST_ASSERT_TRUE(hal.plans[i].t_assert_us >= hal.plans[i - 1].t_deassert_us);
    // Invariant 2: no motion/DAC command lands inside any exposure window.
    for (auto& c : hal.calls) {
        if (c.what != "move" && c.what != "dac") continue;
        for (auto& p : hal.plans) {
            TEST_ASSERT_FALSE(c.t_us > p.t_assert_us && c.t_us < p.t_deassert_us);
        }
    }
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_single_frame_program_completes);
    RUN_TEST(test_piezo_step_and_settle_gate_exposure);
    RUN_TEST(test_stepper_settle_gates_exposure);
    RUN_TEST(test_filter_wheel_gates_exposure);
    RUN_TEST(test_model_readiness_spaces_triggers);
    RUN_TEST(test_filter_move_overlaps_readout);
    RUN_TEST(test_z_step_overlaps_readout_between_layers);
    RUN_TEST(test_z_inner_order_and_z_offset);
    RUN_TEST(test_edge_mode_pulse_and_modeled_exposure_end);
    RUN_TEST(test_two_cameras_simultaneous_exposure);
    RUN_TEST(test_ready_line_blocks_until_asserted);
    RUN_TEST(test_wait_timeout_aborts_with_all_off);
    RUN_TEST(test_no_overlap_when_readout_unsafe);
    RUN_TEST(test_cancel_finishes_current_exposure_then_stops);
    RUN_TEST(test_min_trigger_period_enforced);
    RUN_TEST(test_run_invariants);
    return UNITY_END();
}
