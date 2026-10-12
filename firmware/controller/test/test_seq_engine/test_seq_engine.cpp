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
    c.filter_target = 0;
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

static bool saw(const FakeHal& hal, const char* what) {
    for (size_t i = 0; i < hal.calls.size(); i++)
        if (hal.calls[i].what == what) return true;
    return false;
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
                            (uint8_t)e.load(l, ch, cams, 1).error);
    TEST_ASSERT_TRUE(e.start(hal.now_us, /*wait_timeout_us=*/5000000, 40000));
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
    e.load(l, ch, cams, 1);
    e.start(hal.now_us, 5000000, 40000);
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
    e.load(l, ch, cams, 1);
    e.start(0, 5000000, 100000);
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
    ch[0].filter_target = 5;
    hal.move_duration_us[3] = 50000;
    SeqCameraConfig cams[1] = {cam_level()};
    e.load(l, ch, cams, 1);
    e.start(0, 5000000, 40000);
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
    e.load(l, ch, cams, 1);
    e.start(0, 5000000, 40000);
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
    ch[0].filter_target = 1;
    ch[1].filter_wheel = 3;
    ch[1].filter_target = 2;
    hal.move_duration_us[3] = 15000;
    SeqCameraConfig cams[1] = {cam_level()};  // strobe 500, exposure 10000, readout 20000
    e.load(l, ch, cams, 1);
    e.start(0, 5000000, 40000);
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
    e.load(l, ch, cams, 1);
    e.start(0, 5000000, 40000);
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
    e.load(l, ch, cams, 1);
    e.start(0, 5000000, 40000);
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
    e.load(l, ch, cams, 1);
    e.start(0, 5000000, 40000);
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
    e.load(l, ch, cams, 2);
    e.start(0, 5000000, 40000);
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
    e.load(l, ch, cams, 1);
    e.start(0, 5000000, 40000);
    run_until(e, hal, 30000);
    TEST_ASSERT_EQUAL(0, (int)hal.plans.size());  // still gated
    hal.ready_lines[0] = true;
    run_until(e, hal, 60000);
    TEST_ASSERT_EQUAL(1, (int)hal.plans.size());
    TEST_ASSERT_TRUE(hal.plans[0].t_assert_us >= 30000);
}

// Stuck ready-line check: a camera with a ready line is BUSY from its exposure until its readout ends. A line
// that still reads "ready" and was never seen busy since the trigger is not a fast camera - it is
// a stuck line (shorted cable, an output that was never configured): gating on it would fire the
// next trigger without waiting. Bench 2026-09-20: an unconfigured Fusion BT output sits at the
// level that means READY.
static void two_channel_ready_line_program(SeqLoop* l, SeqChannel* ch, SeqCameraConfig* cams) {
    *l = good_loop();
    l->n_layers = 1;
    l->n_channels = 2;
    l->z_settle_us = 0;
    ch[0] = good_channel();
    ch[1] = good_channel();
    cams[0] = cam_level();
    cams[0].ready_line = 0;
    cams[0].ready_active_high = 1;
}

void test_ready_line_stuck_at_ready_fails_after_the_first_frame(void) {
    FakeHal hal;  // ready_lines[0] stays true: never busy
    SeqEngine e(hal);
    SeqLoop l;
    SeqChannel ch[2];
    SeqCameraConfig cams[1];
    two_channel_ready_line_program(&l, ch, cams);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None, (uint8_t)e.load(l, ch, cams, 1).error);
    TEST_ASSERT_TRUE(e.start(0, 5000000, 40000));
    run_until(e, hal, 200000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Failed, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::ReadyLineStuck, e.progress().abort_error);
    TEST_ASSERT_EQUAL_UINT8(0, e.progress().abort_detail);  // the camera whose line is stuck
    TEST_ASSERT_EQUAL_UINT32(1, e.progress().frames_fired);  // the second trigger never fired
    TEST_ASSERT_EQUAL(1, (int)hal.plans.size());
    TEST_ASSERT_TRUE(saw(hal, "all_off"));
}

void test_a_run_after_a_stuck_ready_line_starts_clean(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l;
    SeqChannel ch[2];
    SeqCameraConfig cams[1];
    two_channel_ready_line_program(&l, ch, cams);
    e.load(l, ch, cams, 1);
    TEST_ASSERT_TRUE(e.start(0, 5000000, 40000));
    run_until(e, hal, 200000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::ReadyLineStuck, e.progress().abort_error);
    // the cable is fixed: the camera now goes busy as it should
    hal.model_camera_busy = true;
    hal.busy_latency_us = 50;
    hal.busy_readout_us = 11000;
    TEST_ASSERT_TRUE(e.start(hal.now_us, 5000000, 40000));
    run_until(e, hal, 600000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT32(2, e.progress().frames_fired);
}

void test_ready_line_that_goes_busy_after_each_trigger_runs_to_done(void) {
    FakeHal hal;
    hal.model_camera_busy = true;
    hal.busy_latency_us = 50;
    hal.busy_readout_us = 11000;
    SeqEngine e(hal);
    SeqLoop l;
    SeqChannel ch[2];
    SeqCameraConfig cams[1];
    two_channel_ready_line_program(&l, ch, cams);
    e.load(l, ch, cams, 1);
    TEST_ASSERT_TRUE(e.start(0, 5000000, 40000));
    run_until(e, hal, 400000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None, e.progress().abort_error);
    TEST_ASSERT_EQUAL_UINT32(2, e.progress().frames_fired);
    // the second trigger waited for the camera, not for a model
    TEST_ASSERT_TRUE(hal.plans[1].t_assert_us >= hal.plans[0].t_deassert_us + 11000);
}

void test_a_slow_ready_line_gets_time_to_go_busy_before_it_is_called_stuck(void) {
    // A very short exposure and a line that only goes busy 500 us after the trigger: when the
    // exposure ends the line still reads ready. That is latency, not a stuck line.
    FakeHal hal;
    hal.model_camera_busy = true;
    hal.busy_latency_us = 500;
    hal.busy_readout_us = 5000;
    SeqEngine e(hal);
    SeqLoop l;
    SeqChannel ch[2];
    SeqCameraConfig cams[1];
    two_channel_ready_line_program(&l, ch, cams);
    ch[0].exposure_us = 100;
    ch[1].exposure_us = 100;
    cams[0].strobe_delay_us = 0;
    e.load(l, ch, cams, 1);
    TEST_ASSERT_TRUE(e.start(0, 5000000, 40000));
    run_until(e, hal, 100000, /*step_us=*/20);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT32(2, e.progress().frames_fired);
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
    e.load(l, ch, cams, 1);
    e.start(0, /*wait_timeout_us=*/100000, 40000);
    run_until(e, hal, 300000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Failed, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::WaitTimeout, e.progress().abort_error);
    bool all_off_called = false;
    for (auto& c : hal.calls) {
        if (c.what == "all_off") all_off_called = true;
    }
    TEST_ASSERT_TRUE(all_off_called);
    TEST_ASSERT_TRUE(saw(hal, "stop_motion"));  // E4: failure stops motion too
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
    ch[1].filter_target = 2;
    SeqCameraConfig cams[1] = {cam_level()};  // readout 20000
    cams[0].readout_overlap_safe = 0;
    e.load(l, ch, cams, 1);
    e.start(0, 5000000, 40000);
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
    e.load(l, ch, cams, 1);
    e.start(0, 5000000, 40000);
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
    e.load(l, ch, cams, 1);
    e.start(0, 5000000, 40000);
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
    ch[1].filter_target = 2;
    ch[2].filter_wheel = 3;
    ch[2].filter_target = 4;
    hal.move_duration_us[3] = 7000;
    SeqCameraConfig cams[1] = {cam_level()};
    e.load(l, ch, cams, 1);
    e.start(0, 5000000, 40000);
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

// E7: the wheel target is absolute microsteps supplied by the host (slot->ustep mapping is
// host-side); a u8 slot index cannot carry it.
void test_filter_target_is_passed_as_absolute_usteps(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 1;
    l.n_channels = 1;
    SeqChannel ch[1] = {good_channel()};
    ch[0].filter_wheel = 3;
    ch[0].filter_target = 123456;
    SeqCameraConfig cams[1] = {cam_level()};
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None, (uint8_t)e.load(l, ch, cams, 1).error);
    TEST_ASSERT_TRUE(e.start(hal.now_us, 5000000, 40000));
    bool saw_move = false;
    for (size_t i = 0; i < hal.calls.size(); i++)
        if (hal.calls[i].what == "move" && hal.calls[i].a == 3) {
            TEST_ASSERT_EQUAL_INT32(123456, (int32_t)hal.calls[i].b);
            saw_move = true;
        }
    TEST_ASSERT_TRUE(saw_move);
}

// S10: upload once per acquisition, run once per FOV with that FOV's piezo start.
void test_loaded_program_runs_again_with_a_new_stack_start(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 1;
    l.n_channels = 1;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    TEST_ASSERT_FALSE(e.start(hal.now_us, 5000000, 40000));  // nothing loaded
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None, (uint8_t)e.load(l, ch, cams, 1).error);
    TEST_ASSERT_TRUE(e.start(hal.now_us, 5000000, 40000));
    TEST_ASSERT_TRUE(e.running());
    TEST_ASSERT_FALSE(e.start(hal.now_us, 5000000, 40000));  // already running
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::Busy, (uint8_t)e.load(l, ch, cams, 1).error);
    run_until(e, hal, 2000000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_FALSE(e.running());
    size_t n_before = hal.calls.size();
    TEST_ASSERT_TRUE(e.start(hal.now_us, 5000000, 41000));  // from Done, no reload
    TEST_ASSERT_EQUAL_STRING("dac", hal.calls[n_before].what.c_str());
    TEST_ASSERT_EQUAL(41000, (int)hal.calls[n_before].b);
    run_until(e, hal, 4000000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT32(1, e.progress().frames_fired);
}

// A failed FOV must not poison the next one: the host retries or moves on and runs again.
void test_program_runs_again_after_a_failed_run(void) {
    FakeHal hal;
    hal.ready_lines[0] = false;  // camera never ready -> WAIT timeout
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 1;
    l.n_channels = 1;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    cams[0].ready_line = 0;
    e.load(l, ch, cams, 1);
    TEST_ASSERT_TRUE(e.start(hal.now_us, /*wait_timeout_us=*/100000, 40000));
    run_until(e, hal, 300000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Failed, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::WaitTimeout, e.progress().abort_error);
    hal.ready_lines[0] = true;
    TEST_ASSERT_TRUE(e.start(hal.now_us, 100000, 40000));
    TEST_ASSERT_TRUE(e.running());  // not the stale Failed from the previous run
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None, e.progress().abort_error);
    run_until(e, hal, 600000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT32(1, e.progress().frames_fired);
}

// E1: micros() wraps every 71.6 min. A stack straddling the wrap must neither skip a wait
// nor time out falsely.
void test_timer_wrap_keeps_settle_readout_and_timeout_correct(void) {
    FakeHal hal;
    const uint32_t t0 = 0xFFFFF000u;  // 4096 us before the wrap
    hal.now_us = t0;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 3;
    l.n_channels = 1;
    l.z_settle_us = 3000;
    SeqChannel ch[1] = {good_channel()};      // exposure 10000
    SeqCameraConfig cams[1] = {cam_level()};  // strobe 500, readout 20000
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None, (uint8_t)e.load(l, ch, cams, 1).error);
    TEST_ASSERT_TRUE(e.start(hal.now_us, 5000000, 40000));
    run_for(e, hal, 200000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_EQUAL(3, (int)hal.plans.size());
    TEST_ASSERT_TRUE((uint32_t)(hal.plans[0].t_assert_us - t0) >= 3000);  // settle honoured
    for (size_t i = 0; i + 1 < hal.plans.size(); i++)  // strobe + exposure + readout
        TEST_ASSERT_TRUE((uint32_t)(hal.plans[i + 1].t_assert_us - hal.plans[i].t_assert_us) >= 30500);
}

// The WAIT deadline itself may land beyond the wrap: it must not fire early.
void test_wait_deadline_beyond_the_wrap_does_not_fire_early(void) {
    FakeHal hal;
    hal.now_us = 0xFFFFF000u;
    hal.ready_lines[0] = false;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 1;
    l.n_channels = 1;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    cams[0].ready_line = 0;
    e.load(l, ch, cams, 1);
    TEST_ASSERT_TRUE(e.start(hal.now_us, /*wait_timeout_us=*/100000, 40000));  // deadline wraps
    run_for(e, hal, 50000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::WaitHw, (uint8_t)e.state());  // still waiting
    run_for(e, hal, 60000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Failed, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::WaitTimeout, e.progress().abort_error);
}

void test_durations_beyond_the_wrap_safe_range_are_rejected(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_channels = 1;
    SeqChannel ch[1] = {good_channel()};
    ch[0].exposure_us = kMaxDurationUs + 1;
    SeqCameraConfig cams[1] = {cam_level()};
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadExposure, (uint8_t)e.load(l, ch, cams, 1).error);
    ch[0].exposure_us = 10000;
    l.z_settle_us = kMaxDurationUs + 1;
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadDuration, (uint8_t)e.load(l, ch, cams, 1).error);
    l.z_settle_us = 2000;
    cams[0].readout_time_us = kMaxDurationUs + 1;
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadDuration, (uint8_t)e.load(l, ch, cams, 1).error);
    cams[0].readout_time_us = 20000;
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None, (uint8_t)e.load(l, ch, cams, 1).error);
}

// E2: the piezo target is a u16 DAC code; an unchecked cast wraps and slams the piezo to the
// opposite end of its travel.
void test_stack_leaving_the_piezo_range_fails_before_anything_moves(void) {
    SeqCameraConfig cams[1] = {cam_level()};
    SeqChannel ch[1] = {good_channel()};
    SeqLoop l = good_loop();  // dz 120, 10 layers -> span +1080
    l.n_channels = 1;
    const int32_t starts[3] = {65000, 64456, -1};  // overflows; 64456+1080 = 65536; below zero
    for (int i = 0; i < 3; i++) {
        FakeHal hal;
        SeqEngine e(hal);
        TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None, (uint8_t)e.load(l, ch, cams, 1).error);
        TEST_ASSERT_TRUE(e.start(hal.now_us, 5000000, starts[i]));
        TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Failed, (uint8_t)e.state());
        TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::StackOutOfRange, e.progress().abort_error);
        for (size_t k = 0; k < hal.calls.size(); k++) {
            TEST_ASSERT_TRUE(hal.calls[k].what != "dac");
            TEST_ASSERT_TRUE(hal.calls[k].what != "move");
            TEST_ASSERT_TRUE(hal.calls[k].what != "expose");
        }
    }
    FakeHal hal;  // exactly at the top is fine: 64455 + 1080 = 65535
    SeqEngine e(hal);
    e.load(l, ch, cams, 1);
    TEST_ASSERT_TRUE(e.start(hal.now_us, 5000000, 64455));
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::WaitHw, (uint8_t)e.state());
}

// A per-channel z offset can push an otherwise valid stack out of range; a negative dz
// stack is checked at its far end too. The failing channel is reported.
void test_stack_range_accounts_for_channel_offsets_and_negative_dz(void) {
    SeqCameraConfig cams[1] = {cam_level()};
    SeqChannel ch[2] = {good_channel(), good_channel()};
    ch[1].z_offset = 600;
    SeqLoop l = good_loop();
    l.n_channels = 2;
    {
        FakeHal hal;
        SeqEngine e(hal);
        e.load(l, ch, cams, 1);
        TEST_ASSERT_TRUE(e.start(hal.now_us, 5000000, 64000));  // 64000+1080+600 > 65535
        TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::StackOutOfRange, e.progress().abort_error);
        TEST_ASSERT_EQUAL_UINT8(1, e.progress().abort_detail);  // channel 1
    }
    {
        FakeHal hal;
        SeqEngine e(hal);
        l.dz = -120;
        ch[1].z_offset = 0;
        e.load(l, ch, cams, 1);
        TEST_ASSERT_TRUE(e.start(hal.now_us, 5000000, 1000));  // 1000 - 1080 < 0
        TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::StackOutOfRange, e.progress().abort_error);
    }
}

// E4: an interlock / watchdog abort must go THROUGH the engine (so the run fails visibly
// instead of silently producing dark frames), and any failure stops motion too.
void test_abort_turns_everything_off_and_stops_motion(void) {
    FakeHal hal;
    SeqEngine e(hal);
    e.abort(SeqError::InterlockOpen);  // idle: no-op
    TEST_ASSERT_EQUAL(0, (int)hal.calls.size());
    SeqLoop l = good_loop();
    l.stack_axis_type = (uint8_t)StackAxisType::Stepper;
    l.stack_axis_id = 2;
    l.n_channels = 1;
    hal.move_duration_us[2] = 50000;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    e.load(l, ch, cams, 1);
    TEST_ASSERT_TRUE(e.start(hal.now_us, 5000000, 1000));
    run_for(e, hal, 5000);  // mid-move
    e.abort(SeqError::InterlockOpen);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Failed, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::InterlockOpen, e.progress().abort_error);
    TEST_ASSERT_TRUE(saw(hal, "all_off"));
    TEST_ASSERT_TRUE(saw(hal, "stop_motion"));
    size_t n = hal.calls.size();
    e.abort(SeqError::HostAbort);  // already terminal: no second shutdown, error preserved
    TEST_ASSERT_EQUAL((int)n, (int)hal.calls.size());
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::InterlockOpen, e.progress().abort_error);
}

// An abort mid-exposure must not leave the run "Exposing": it is terminal immediately.
void test_abort_during_exposure_is_terminal_immediately(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_channels = 1;
    l.z_settle_us = 1000;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    e.load(l, ch, cams, 1);
    e.start(hal.now_us, 5000000, 40000);
    for (int i = 0; i < 1000 && e.state() != SeqState::Exposing; i++) run_for(e, hal, 100);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Exposing, (uint8_t)e.state());
    e.abort(SeqError::HostAbort);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Failed, (uint8_t)e.state());
    run_for(e, hal, 100000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Failed, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT32(1, e.progress().frames_fired);  // nothing fired after the abort
}

// E3: the host starts the next XY move on Done — the stack axis must be back first.
void test_done_waits_for_the_stepper_return_move(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.stack_axis_type = (uint8_t)StackAxisType::Stepper;
    l.stack_axis_id = 2;
    l.n_layers = 2;
    l.n_channels = 1;
    l.z_settle_us = 2000;
    hal.move_duration_us[2] = 5000;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    e.load(l, ch, cams, 1);
    TEST_ASSERT_TRUE(e.start(hal.now_us, 5000000, 1000));
    for (int i = 0; i < 20000 && e.state() != SeqState::Returning; i++) run_for(e, hal, 100);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Returning, (uint8_t)e.state());
    TEST_ASSERT_TRUE(e.running());
    TEST_ASSERT_EQUAL_UINT32(2, e.progress().frames_fired);
    const uint32_t t_ret = hal.now_us;
    for (int i = 0; i < 20000 && e.state() == SeqState::Returning; i++) run_for(e, hal, 100);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_TRUE(hal.now_us - t_ret >= 5000 + 2000);  // move + settle
}

void test_done_waits_for_the_piezo_return_settle(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 2;
    l.n_channels = 1;
    l.z_settle_us = 4000;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    e.load(l, ch, cams, 1);
    e.start(hal.now_us, 5000000, 40000);
    for (int i = 0; i < 20000 && e.state() != SeqState::Returning; i++) run_for(e, hal, 100);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Returning, (uint8_t)e.state());
    const uint32_t t_ret = hal.now_us;
    for (int i = 0; i < 20000 && e.state() == SeqState::Returning; i++) run_for(e, hal, 100);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_TRUE(hal.now_us - t_ret >= 4000);
    TEST_ASSERT_EQUAL_STRING("dac", hal.calls.back().what.c_str());  // the return write
    TEST_ASSERT_EQUAL(40000, (int)hal.calls.back().b);
}

// Without return_to_start there is nothing to wait for.
void test_no_return_means_done_at_the_last_exposure_end(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 1;
    l.n_channels = 1;
    l.return_to_start = 0;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    e.load(l, ch, cams, 1);
    e.start(hal.now_us, 5000000, 40000);
    bool saw_returning = false;
    for (int i = 0; i < 20000 && e.state() != SeqState::Done; i++) {
        run_for(e, hal, 100);
        if (e.state() == SeqState::Returning) saw_returning = true;
    }
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_FALSE(saw_returning);
}

// A return move that never completes must not hang the host forever.
void test_return_move_that_never_completes_times_out(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.stack_axis_type = (uint8_t)StackAxisType::Stepper;
    l.stack_axis_id = 2;
    l.n_layers = 1;
    l.n_channels = 1;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    e.load(l, ch, cams, 1);
    e.start(hal.now_us, /*wait_timeout_us=*/200000, 1000);
    for (int i = 0; i < 20000 && e.state() != SeqState::Returning; i++) run_for(e, hal, 100);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Returning, (uint8_t)e.state());
    hal.move_done_at_us[2] = hal.now_us + 10000000;  // stalled
    hal.moving[2] = true;
    run_for(e, hal, 300000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Failed, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::WaitTimeout, e.progress().abort_error);
    TEST_ASSERT_EQUAL_UINT8(2, e.progress().abort_detail);  // the stack axis
}

// E6: a cancel during WAIT must not fire another frame, nor wait out a stuck ready line.
void test_cancel_during_wait_fires_nothing_more(void) {
    FakeHal hal;
    hal.ready_lines[0] = false;  // camera never becomes ready
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_channels = 1;
    l.z_settle_us = 1000;
    SeqChannel ch[1] = {good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    cams[0].ready_line = 0;
    e.load(l, ch, cams, 1);
    TEST_ASSERT_TRUE(e.start(hal.now_us, 5000000, 40000));
    run_for(e, hal, 10000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::WaitHw, (uint8_t)e.state());
    e.cancel();
    run_for(e, hal, 5000);  // far less than the 5 s WAIT timeout
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::Canceled, e.progress().abort_error);
    TEST_ASSERT_EQUAL_UINT32(0, e.progress().frames_fired);
    TEST_ASSERT_FALSE(saw(hal, "expose"));
    TEST_ASSERT_EQUAL_STRING("dac", hal.calls.back().what.c_str());  // piezo returned to start
    TEST_ASSERT_EQUAL(40000, (int)hal.calls.back().b);
}

// E5: brightfield (LED matrix) then fluorescence — the matrix must be dark for the laser frame.
void test_led_matrix_is_turned_off_for_a_following_ttl_channel(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 1;
    l.n_channels = 2;
    l.return_to_start = 0;
    SeqChannel ch[2] = {good_channel(), good_channel()};
    ch[0].led_pattern = 3;
    ch[0].illum_ttl_mask = 0;
    SeqCameraConfig cams[1] = {cam_level()};
    e.load(l, ch, cams, 1);
    TEST_ASSERT_TRUE(e.start(hal.now_us, 5000000, 40000));
    run_for(e, hal, 200000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    int led_off_at = -1, second_expose_at = -1, exposes = 0;
    for (size_t i = 0; i < hal.calls.size(); i++) {
        if (hal.calls[i].what == "led" && hal.calls[i].a == kNone) led_off_at = (int)i;
        if (hal.calls[i].what == "expose" && ++exposes == 2) second_expose_at = (int)i;
    }
    TEST_ASSERT_TRUE(led_off_at >= 0);
    TEST_ASSERT_TRUE(second_expose_at >= 0);
    TEST_ASSERT_TRUE(led_off_at < second_expose_at);
}

void test_led_matrix_is_off_when_the_run_ends(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 1;
    l.n_channels = 1;
    SeqChannel ch[1] = {good_channel()};
    ch[0].led_pattern = 3;
    SeqCameraConfig cams[1] = {cam_level()};
    e.load(l, ch, cams, 1);
    e.start(hal.now_us, 5000000, 40000);
    run_for(e, hal, 200000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    long last_led = -1;
    for (size_t i = 0; i < hal.calls.size(); i++)
        if (hal.calls[i].what == "led") last_led = hal.calls[i].a;
    TEST_ASSERT_EQUAL(kNone, (int)last_led);
}

// A fluorescence-only program must never touch the LED matrix (FastLED is slow).
void test_ttl_only_program_never_touches_the_led_matrix(void) {
    FakeHal hal;
    SeqEngine e(hal);
    SeqLoop l = good_loop();
    l.n_layers = 2;
    l.n_channels = 2;
    SeqChannel ch[2] = {good_channel(), good_channel()};
    SeqCameraConfig cams[1] = {cam_level()};
    e.load(l, ch, cams, 1);
    e.start(hal.now_us, 5000000, 40000);
    run_for(e, hal, 400000);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqState::Done, (uint8_t)e.state());
    TEST_ASSERT_FALSE(saw(hal, "led"));
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
    RUN_TEST(test_ready_line_stuck_at_ready_fails_after_the_first_frame);
    RUN_TEST(test_a_run_after_a_stuck_ready_line_starts_clean);
    RUN_TEST(test_ready_line_that_goes_busy_after_each_trigger_runs_to_done);
    RUN_TEST(test_a_slow_ready_line_gets_time_to_go_busy_before_it_is_called_stuck);
    RUN_TEST(test_wait_timeout_aborts_with_all_off);
    RUN_TEST(test_no_overlap_when_readout_unsafe);
    RUN_TEST(test_cancel_finishes_current_exposure_then_stops);
    RUN_TEST(test_min_trigger_period_enforced);
    RUN_TEST(test_run_invariants);
    RUN_TEST(test_filter_target_is_passed_as_absolute_usteps);
    RUN_TEST(test_loaded_program_runs_again_with_a_new_stack_start);
    RUN_TEST(test_program_runs_again_after_a_failed_run);
    RUN_TEST(test_timer_wrap_keeps_settle_readout_and_timeout_correct);
    RUN_TEST(test_wait_deadline_beyond_the_wrap_does_not_fire_early);
    RUN_TEST(test_durations_beyond_the_wrap_safe_range_are_rejected);
    RUN_TEST(test_stack_leaving_the_piezo_range_fails_before_anything_moves);
    RUN_TEST(test_stack_range_accounts_for_channel_offsets_and_negative_dz);
    RUN_TEST(test_abort_turns_everything_off_and_stops_motion);
    RUN_TEST(test_abort_during_exposure_is_terminal_immediately);
    RUN_TEST(test_done_waits_for_the_stepper_return_move);
    RUN_TEST(test_done_waits_for_the_piezo_return_settle);
    RUN_TEST(test_no_return_means_done_at_the_last_exposure_end);
    RUN_TEST(test_return_move_that_never_completes_times_out);
    RUN_TEST(test_cancel_during_wait_fires_nothing_more);
    RUN_TEST(test_led_matrix_is_turned_off_for_a_following_ttl_channel);
    RUN_TEST(test_led_matrix_is_off_when_the_run_ends);
    RUN_TEST(test_ttl_only_program_never_touches_the_led_matrix);
    return UNITY_END();
}
