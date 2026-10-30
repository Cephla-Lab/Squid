#include <string.h>
#include <unity.h>

// Include sources directly for native tests (same convention as test_crc8)
#include "protocol/crc16.cpp"
#include "sequencer/seq_staging.cpp"
#include "sequencer/seq_types.cpp"
#include "sequencer/seq_wire.cpp"

#include "constants_protocol.h"

using namespace seq;
using namespace seq::wire;

// A 2-channel, 1-camera program laid out the way the host stages it.
static uint16_t build(uint8_t* buf) {
    memset(buf, 0, kStagingBytes);
    WireHeader h{};
    h.version = kWireVersion;
    h.n_cameras = 1;
    h.wait_timeout_us = 5000000;
    memcpy(buf, &h, sizeof h);
    SeqLoop l{};
    l.stack_axis_type = (uint8_t)StackAxisType::Piezo;
    l.stack_axis_id = 7;
    l.dz = 218;
    l.n_layers = 5;
    l.z_settle_us = 20000;
    l.return_to_start = 1;
    l.n_channels = 2;
    memcpy(buf + kLoopOffset, &l, sizeof l);
    for (uint8_t i = 0; i < 2; i++) {
        SeqChannel c{};
        c.filter_wheel = kNone;
        c.illum_ttl_mask = (uint8_t)(1u << i);
        c.led_pattern = kNone;
        c.intensity_dac = i;
        c.intensity = (uint16_t)(2000 + i);
        c.exposure_us = 20000u * (i + 1);
        c.camera_mask = 1;
        memcpy(buf + kChannelsOffset + i * sizeof(SeqChannel), &c, sizeof c);
    }
    WireCamera w{};
    w.trigger_mode = (uint8_t)TriggerMode::Level;
    w.ready_line = kNone;
    w.ready_active_high = 1;
    w.readout_overlap_safe = 1;
    w.strobe_delay_us = 300;
    w.readout_time_us = 25000;
    memcpy(buf + kChannelsOffset + 2 * sizeof(SeqChannel), &w, sizeof w);
    return program_bytes(2, 1);
}

// Send the program the way SEQ_WRITE does: 4 bytes per command, by word index.
static void write_all(Staging& s, const uint8_t* src, uint16_t len) {
    for (uint16_t off = 0; off < len; off += kWordBytes)
        TEST_ASSERT_TRUE(s.write_word((uint8_t)(off / kWordBytes), src + off));
}

void setUp(void) {}
void tearDown(void) {}

void test_write_words_then_commit_parses_the_program(void) {
    uint8_t src[kStagingBytes];
    const uint16_t len = build(src);
    Staging s;
    write_all(s, src, len);
    ParsedProgram p;
    const ValidationResult r = s.commit(len, protocol::crc16_ccitt(src, len), &p);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None, (uint8_t)r.error);
    TEST_ASSERT_EQUAL_UINT16(5, p.loop.n_layers);
    TEST_ASSERT_EQUAL_UINT32(40000, p.channels[1].exposure_us);
    TEST_ASSERT_EQUAL_UINT32(300, p.cams[0].strobe_delay_us);
}

// v1 resends a command blindly when an ack is lost: a repeated write must be harmless.
void test_a_resent_write_is_idempotent(void) {
    uint8_t src[kStagingBytes];
    const uint16_t len = build(src);
    Staging s;
    write_all(s, src, len);
    TEST_ASSERT_TRUE(s.write_word(3, src + 12));
    TEST_ASSERT_TRUE(s.write_word(3, src + 12));
    ParsedProgram p;
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None,
                            (uint8_t)s.commit(len, protocol::crc16_ccitt(src, len), &p).error);
}

// A lost chunk leaves zeros (or stale bytes) behind: only the CRC can tell.
void test_a_missing_chunk_fails_the_crc(void) {
    uint8_t src[kStagingBytes];
    const uint16_t len = build(src);
    Staging s;
    for (uint16_t off = 0; off < len; off += kWordBytes) {
        if (off == 28) continue;  // drop one word inside channel 0
        s.write_word((uint8_t)(off / kWordBytes), src + off);
    }
    ParsedProgram p;
    const ValidationResult r = s.commit(len, protocol::crc16_ccitt(src, len), &p);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadProgram, (uint8_t)r.error);
    TEST_ASSERT_EQUAL_UINT8(kBadProgramCrc, r.detail);
}

void test_commit_rejects_a_wrong_crc_and_impossible_lengths(void) {
    uint8_t src[kStagingBytes];
    const uint16_t len = build(src);
    Staging s;
    write_all(s, src, len);
    ParsedProgram p;
    const uint16_t good = protocol::crc16_ccitt(src, len);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadProgram, (uint8_t)s.commit(len, good ^ 1, &p).error);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadProgram,
                            (uint8_t)s.commit(kStagingBytes + 4, good, &p).error);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadProgram, (uint8_t)s.commit(0, good, &p).error);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None, (uint8_t)s.commit(len, good, &p).error);
}

void test_write_beyond_the_buffer_is_rejected(void) {
    Staging s;
    const uint8_t data[4] = {1, 2, 3, 4};
    TEST_ASSERT_TRUE(s.write_word((uint8_t)(kStagingBytes / kWordBytes - 1), data));
    TEST_ASSERT_FALSE(s.write_word((uint8_t)(kStagingBytes / kWordBytes), data));
    TEST_ASSERT_FALSE(s.write_word(255, data));
}

// While a sequence runs the MCU owns the stage, the light and the camera: everything that
// would touch them is refused at ONE choke point in the dispatcher.
void test_only_safety_and_cancel_commands_are_allowed_while_running(void) {
    TEST_ASSERT_TRUE(allowed_while_running(HEARTBEAT));
    TEST_ASSERT_TRUE(allowed_while_running(SEQ_CANCEL));
    TEST_ASSERT_TRUE(allowed_while_running(TURN_OFF_ALL_PORTS));
    TEST_ASSERT_TRUE(allowed_while_running(RESET));
    TEST_ASSERT_FALSE(allowed_while_running(MOVE_Z));
    TEST_ASSERT_FALSE(allowed_while_running(MOVETO_Z));
    TEST_ASSERT_FALSE(allowed_while_running(SEND_HARDWARE_TRIGGER));
    TEST_ASSERT_FALSE(allowed_while_running(SET_ILLUMINATION));
    TEST_ASSERT_FALSE(allowed_while_running(ANALOG_WRITE_ONBOARD_DAC));
    TEST_ASSERT_FALSE(allowed_while_running(SEQ_WRITE));
    TEST_ASSERT_FALSE(allowed_while_running(SEQ_COMMIT));
    TEST_ASSERT_FALSE(allowed_while_running(SEQ_RUN));
}

// The wire contract and the v1 opcode table must never drift apart.
void test_wire_opcodes_match_the_v1_opcode_table(void) {
    TEST_ASSERT_EQUAL(SEQ_WRITE, kOpSeqWrite);
    TEST_ASSERT_EQUAL(SEQ_COMMIT, kOpSeqCommit);
    TEST_ASSERT_EQUAL(SEQ_RUN, kOpSeqRun);
    TEST_ASSERT_EQUAL(SEQ_CANCEL, kOpSeqCancel);
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_write_words_then_commit_parses_the_program);
    RUN_TEST(test_a_resent_write_is_idempotent);
    RUN_TEST(test_a_missing_chunk_fails_the_crc);
    RUN_TEST(test_commit_rejects_a_wrong_crc_and_impossible_lengths);
    RUN_TEST(test_write_beyond_the_buffer_is_rejected);
    RUN_TEST(test_only_safety_and_cancel_commands_are_allowed_while_running);
    RUN_TEST(test_wire_opcodes_match_the_v1_opcode_table);
    return UNITY_END();
}
