#include <string.h>
#include <unity.h>

// Include sources directly for native tests (same convention as test_crc8)
#include "sequencer/seq_types.cpp"
#include "sequencer/seq_wire.cpp"

using namespace seq;
using namespace seq::wire;

// Build a staged program exactly the way the host does: packed little-endian structs at
// the documented offsets. Returns the byte length the host would send in SEQ_COMMIT.
static uint16_t build(uint8_t* buf, uint8_t n_channels, uint8_t n_cameras,
                      uint8_t version = kWireVersion) {
    memset(buf, 0, kStagingBytes);
    WireHeader h{};
    h.version = version;
    h.n_cameras = n_cameras;
    h.wait_timeout_us = 5000000;
    memcpy(buf, &h, sizeof h);
    SeqLoop l{};
    l.stack_axis_type = (uint8_t)StackAxisType::Piezo;
    l.stack_axis_id = 7;
    l.dz = 120;
    l.n_layers = 10;
    l.z_settle_us = 20000;
    l.return_to_start = 1;
    l.n_channels = n_channels;
    memcpy(buf + kLoopOffset, &l, sizeof l);
    for (uint8_t i = 0; i < n_channels; i++) {
        SeqChannel c{};
        c.filter_wheel = kNone;
        c.illum_ttl_mask = (uint8_t)(1u << i);
        c.led_pattern = kNone;
        c.intensity_dac = i;
        c.intensity = (uint16_t)(1000 + i);
        c.exposure_us = 10000u * (i + 1);
        c.camera_mask = 1;
        memcpy(buf + kChannelsOffset + i * sizeof(SeqChannel), &c, sizeof c);
    }
    for (uint8_t i = 0; i < n_cameras; i++) {
        WireCamera w{};
        w.trigger_mode = (uint8_t)TriggerMode::Level;
        w.ready_line = 0;
        w.ready_active_high = 1;
        w.readout_overlap_safe = 1;
        w.strobe_delay_us = 300;
        w.readout_time_us = 25000;
        memcpy(buf + kChannelsOffset + n_channels * sizeof(SeqChannel) + i * sizeof(WireCamera),
               &w, sizeof w);
    }
    return program_bytes(n_channels, n_cameras);
}

void setUp(void) {}
void tearDown(void) {}

void test_layout_constants(void) {
    TEST_ASSERT_EQUAL(8, (int)sizeof(WireHeader));
    TEST_ASSERT_EQUAL(16, (int)sizeof(WireCamera));
    TEST_ASSERT_EQUAL(472, (int)kStagingBytes);
    TEST_ASSERT_EQUAL(24 + 2 * 20 + 16, (int)program_bytes(2, 1));
    TEST_ASSERT_TRUE(kStagingBytes / kWordBytes <= 255);  // word index fits SEQ_WRITE's u8
    TEST_ASSERT_EQUAL(0, (int)(kStagingBytes % kWordBytes));
}

void test_parse_roundtrip(void) {
    uint8_t buf[kStagingBytes];
    uint16_t len = build(buf, 2, 1);
    ParsedProgram p;
    ValidationResult r = parse_staging(buf, len, &p);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None, (uint8_t)r.error);
    TEST_ASSERT_EQUAL_UINT32(5000000, p.wait_timeout_us);
    TEST_ASSERT_EQUAL_UINT8(1, p.n_cameras);
    TEST_ASSERT_EQUAL_UINT16(10, p.loop.n_layers);
    TEST_ASSERT_EQUAL_INT32(120, p.loop.dz);
    TEST_ASSERT_EQUAL_UINT32(20000, p.channels[1].exposure_us);
    TEST_ASSERT_EQUAL_UINT8(0x02, p.channels[1].illum_ttl_mask);
    TEST_ASSERT_EQUAL_UINT16(1001, p.channels[1].intensity);
    TEST_ASSERT_EQUAL_UINT32(300, p.cams[0].strobe_delay_us);
    TEST_ASSERT_EQUAL_UINT32(25000, p.cams[0].readout_time_us);
    TEST_ASSERT_EQUAL_UINT8(0, p.cams[0].ready_line);
    TEST_ASSERT_EQUAL_UINT8(1, p.cams[0].ready_active_high);
}

// A parsed program must be loadable: parse_staging checks structure, validate() semantics.
void test_parsed_program_passes_semantic_validation(void) {
    uint8_t buf[kStagingBytes];
    uint16_t len = build(buf, 4, 1);
    ParsedProgram p;
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None, (uint8_t)parse_staging(buf, len, &p).error);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::None,
                            (uint8_t)validate(p.loop, p.channels, p.cams, p.n_cameras, 8, 8).error);
}

void test_parse_rejects_bad_version_length_and_counts(void) {
    uint8_t buf[kStagingBytes];
    ParsedProgram p;
    uint16_t len = build(buf, 2, 1, /*version=*/9);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadProgram, (uint8_t)parse_staging(buf, len, &p).error);
    len = build(buf, 2, 1);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadProgram,
                            (uint8_t)parse_staging(buf, len - 4, &p).error);  // a lost chunk
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadProgram,
                            (uint8_t)parse_staging(buf, 8, &p).error);  // shorter than the fixed part
    len = build(buf, 2, 0);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadCamera, (uint8_t)parse_staging(buf, len, &p).error);
    len = build(buf, 2, kMaxCameras + 1);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadCamera, (uint8_t)parse_staging(buf, len, &p).error);
    len = build(buf, 0, 1);
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadChannelCount,
                            (uint8_t)parse_staging(buf, len, &p).error);
}

void test_parse_rejects_more_frames_than_the_u16_counter(void) {
    uint8_t buf[kStagingBytes];
    uint16_t len = build(buf, 2, 1);
    SeqLoop l;
    memcpy(&l, buf + kLoopOffset, sizeof l);
    l.n_layers = 40000;  // 40000 * 2 > 65535: frames_fired travels as a u16 in the status packet
    memcpy(buf + kLoopOffset, &l, sizeof l);
    ParsedProgram p;
    TEST_ASSERT_EQUAL_UINT8((uint8_t)SeqError::BadLayerCount, (uint8_t)parse_staging(buf, len, &p).error);
}

void test_status_byte_packing(void) {
    TEST_ASSERT_EQUAL_HEX8(0xE7, pack_status(SeqState::Returning, SeqError::WaitTimeout));
    TEST_ASSERT_EQUAL_HEX8(0x00, pack_status(SeqState::Idle, SeqError::None));
    // every error must fit the 5-bit field next to the 3-bit state
    TEST_ASSERT_TRUE((uint8_t)SeqError::NotCommitted <= 0x1F);
    TEST_ASSERT_TRUE((uint8_t)SeqState::Returning <= 0x07);
}

// Wire values are frozen: the host mirrors them by number.
void test_wire_numbers_are_frozen(void) {
    TEST_ASSERT_EQUAL_UINT8(60, kOpSeqWrite);
    TEST_ASSERT_EQUAL_UINT8(61, kOpSeqCommit);
    TEST_ASSERT_EQUAL_UINT8(62, kOpSeqRun);
    TEST_ASSERT_EQUAL_UINT8(63, kOpSeqCancel);
    TEST_ASSERT_EQUAL_UINT8(14, kStatusByteState);
    TEST_ASSERT_EQUAL_UINT8(17, kStatusByteFramesLo);
    TEST_ASSERT_EQUAL_UINT8(10, (uint8_t)SeqError::Canceled);
    TEST_ASSERT_EQUAL_UINT8(11, (uint8_t)SeqError::StackOutOfRange);
    TEST_ASSERT_EQUAL_UINT8(17, (uint8_t)SeqError::NotCommitted);
    TEST_ASSERT_EQUAL_UINT8(7, (uint8_t)SeqState::Returning);
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_layout_constants);
    RUN_TEST(test_parse_roundtrip);
    RUN_TEST(test_parsed_program_passes_semantic_validation);
    RUN_TEST(test_parse_rejects_bad_version_length_and_counts);
    RUN_TEST(test_parse_rejects_more_frames_than_the_u16_counter);
    RUN_TEST(test_status_byte_packing);
    RUN_TEST(test_wire_numbers_are_frozen);
    return UNITY_END();
}
