#include <unity.h>

#include <stdint.h>
#include <string.h>
#include <vector>

#include "protocol/dispatch_v2.h"
#include "protocol/frames.h"

// Include sources directly for native tests.
#include "protocol/claims.cpp"
#include "protocol/cobs.cpp"
#include "protocol/crc16.cpp"
#include "protocol/dispatch_v2.cpp"
#include "protocol/framer.cpp"
#include "protocol/slots.cpp"

using namespace protocol;

// --- Fake state/boot/board provider ---------------------------------------

class FakeProvider : public StateProvider {
public:
    // Machine-state template copied into fill_state().
    uint8_t mode = 0;
    int32_t axis0_pos = 0;
    uint16_t dac0 = 0;
    uint8_t fw_major = 0, fw_minor = 0, proto = kProtocolVersion;

    HelloPayload hello_data{};
    InfoPayload info_data{};
    DiagPayload diag_data{};
    FaultEntryWire faults[16]{};
    uint8_t n_faults = 0;

    void fill_state(StandardResponse& r) override {
        r.mode = mode;
        r.axes[0].pos = axis0_pos;
        r.dac_values[0] = dac0;
        r.fw_version_major = fw_major;
        r.fw_version_minor = fw_minor;
        r.protocol_version = proto;
    }
    void fill_hello(HelloPayload& h) override { h = hello_data; }
    void fill_info(InfoPayload& i) override { i = info_data; }
    void fill_diag_page0(DiagPayload& d) override { d = diag_data; }
    uint8_t fill_diag_faults(uint8_t page, FaultEntryWire* out, uint8_t cap) override {
        (void)page;
        uint8_t n = (n_faults < cap) ? n_faults : cap;
        for (uint8_t i = 0; i < n; ++i) out[i] = faults[i];
        return n;
    }
};

// Slotted command handler that counts invocations (proves RETRY skips it).
static int g_handler_calls = 0;
static void counting_handler(Dispatcher&, const uint8_t*, size_t, ResponseWriter& w) {
    ++g_handler_calls;
    (void)w;
}

void setUp(void) { g_handler_calls = 0; }
void tearDown(void) {}

// --- Helpers --------------------------------------------------------------

static size_t make_request(uint8_t cmd_id, uint8_t cmd_type, uint8_t flags,
                           const uint8_t* payload, size_t plen, uint8_t* out) {
    out[0] = REQUEST;
    out[1] = cmd_id;
    out[2] = cmd_type;
    out[3] = flags;
    for (size_t i = 0; i < plen; ++i) out[4 + i] = payload[i];
    return 4 + plen;
}

// Parse a response frame; copies the StandardResponse out for aligned access.
static void parse_response(const uint8_t* resp, size_t n, uint8_t* type, uint8_t* cmd_id,
                           uint8_t* cmd_type, StandardResponse* sr) {
    *type = resp[0];
    *cmd_id = resp[1];
    *cmd_type = resp[2];
    memcpy(sr, &resp[4], sizeof(StandardResponse));
}

// --- Unknown command ------------------------------------------------------

void test_unknown_command_rejected(void) {
    SlotManager slots;
    FakeProvider prov;
    Dispatcher d(slots, prov);
    d.register_system_commands();

    uint8_t req[8], resp[600];
    size_t rn = make_request(0x77, 0x0E /*unregistered*/, 0, nullptr, 0, req);
    size_t n = d.build_response(req, rn, resp, sizeof(resp));
    TEST_ASSERT_TRUE(n > 0);

    uint8_t type, cid, ct;
    StandardResponse sr;
    parse_response(resp, n, &type, &cid, &ct, &sr);
    TEST_ASSERT_EQUAL_UINT8(RESPONSE, type);
    TEST_ASSERT_EQUAL_UINT8(0x77, cid);         // echoed cmd_id
    TEST_ASSERT_EQUAL_UINT8(0x0E, ct);          // echoed cmd_type
    TEST_ASSERT_EQUAL_UINT8(STATUS_REJECTED, sr.status);
    TEST_ASSERT_EQUAL_UINT8(ERR_UNKNOWN_COMMAND, sr.error_code);
}

// --- Bad payload length ---------------------------------------------------

void test_bad_length_rejected(void) {
    SlotManager slots;
    FakeProvider prov;
    Dispatcher d(slots, prov);
    d.register_system_commands();

    // GET_STATE expects 0 payload bytes; send 1.
    uint8_t extra = 0xAB;
    uint8_t req[8], resp[600];
    size_t rn = make_request(0x01, GET_STATE, 0, &extra, 1, req);
    size_t n = d.build_response(req, rn, resp, sizeof(resp));
    TEST_ASSERT_TRUE(n > 0);

    uint8_t type, cid, ct;
    StandardResponse sr;
    parse_response(resp, n, &type, &cid, &ct, &sr);
    TEST_ASSERT_EQUAL_UINT8(STATUS_REJECTED, sr.status);
    TEST_ASSERT_EQUAL_UINT8(ERR_BAD_LENGTH, sr.error_code);
}

// --- GET_STATE ------------------------------------------------------------

void test_get_state_returns_injected_state(void) {
    SlotManager slots;
    FakeProvider prov;
    prov.mode = 7;
    prov.axis0_pos = 123456;
    prov.dac0 = 4095;
    prov.fw_major = 2;
    prov.fw_minor = 3;
    Dispatcher d(slots, prov);
    d.register_system_commands();

    uint8_t req[8], resp[600];
    size_t rn = make_request(0x99, GET_STATE, 0, nullptr, 0, req);
    size_t n = d.build_response(req, rn, resp, sizeof(resp));
    TEST_ASSERT_TRUE(n > 0);

    uint8_t type, cid, ct;
    StandardResponse sr;
    parse_response(resp, n, &type, &cid, &ct, &sr);
    TEST_ASSERT_EQUAL_UINT8(0x99, cid);  // echoed cmd_id
    TEST_ASSERT_EQUAL_UINT8(STATUS_OK, sr.status);
    TEST_ASSERT_EQUAL_UINT8(7, sr.mode);
    TEST_ASSERT_EQUAL_INT32(123456, sr.axes[0].pos);
    TEST_ASSERT_EQUAL_UINT16(4095, sr.dac_values[0]);
    TEST_ASSERT_EQUAL_UINT8(2, sr.fw_version_major);
    TEST_ASSERT_EQUAL_UINT8(kProtocolVersion, sr.protocol_version);
    // GET_STATE is immediate: it must not occupy a slot.
    TEST_ASSERT_EQUAL_UINT8(SLOT_EMPTY, sr.slots[0].state);
}

// --- HELLO: extra payload + session reset ---------------------------------

void test_hello_appends_payload_and_resets_session(void) {
    SlotManager slots;
    FakeProvider prov;
    prov.hello_data.protocol_version = kProtocolVersion;
    prov.hello_data.fw_major = 2;
    prov.hello_data.reset_cause = 0x03;
    prov.hello_data.session_nonce = 0xDEADBEEF;
    prov.hello_data.boot_count = 42;
    prov.hello_data.uptime_ms = 1000;
    Dispatcher d(slots, prov);
    d.register_system_commands();
    d.register_command(0x01, false, 0, 8, counting_handler);  // slotted

    // Create a stale slot with a slotted command.
    uint8_t req[16], resp[600];
    size_t rn = make_request(0x10, 0x01, 0, nullptr, 0, req);
    d.build_response(req, rn, resp, sizeof(resp));
    TEST_ASSERT_NOT_NULL(slots.find(0x10));  // slot is occupied

    // HELLO establishes a new session and clears slots.
    rn = make_request(0x20, HELLO, 0, nullptr, 0, req);
    size_t n = d.build_response(req, rn, resp, sizeof(resp));
    TEST_ASSERT_TRUE(n > 0);

    uint8_t type, cid, ct;
    StandardResponse sr;
    parse_response(resp, n, &type, &cid, &ct, &sr);
    TEST_ASSERT_EQUAL_UINT8(STATUS_OK, sr.status);
    TEST_ASSERT_EQUAL_UINT8(SLOT_EMPTY, sr.slots[0].state);  // stale slot cleared
    TEST_ASSERT_NULL(slots.find(0x10));

    // The HELLO extra payload follows the StandardResponse.
    TEST_ASSERT_EQUAL_size_t(4 + sizeof(StandardResponse) + sizeof(HelloPayload), n);
    HelloPayload hp;
    memcpy(&hp, &resp[4 + sizeof(StandardResponse)], sizeof(HelloPayload));
    TEST_ASSERT_EQUAL_HEX32(0xDEADBEEF, hp.session_nonce);
    TEST_ASSERT_EQUAL_UINT32(42, hp.boot_count);
    TEST_ASSERT_EQUAL_UINT8(0x03, hp.reset_cause);
}

// --- GET_INFO: descriptor passthrough -------------------------------------

void test_get_info_passthrough(void) {
    SlotManager slots;
    FakeProvider prov;
    prov.info_data.board_id = 1;
    prov.info_data.n_axes = 5;
    prov.info_data.n_dacs = 8;
    prov.info_data.max_program_channels = 16;
    prov.info_data.feature_bits = 0xCAFEF00D;
    Dispatcher d(slots, prov);
    d.register_system_commands();

    uint8_t req[8], resp[600];
    size_t rn = make_request(0x05, GET_INFO, 0, nullptr, 0, req);
    size_t n = d.build_response(req, rn, resp, sizeof(resp));
    TEST_ASSERT_EQUAL_size_t(4 + sizeof(StandardResponse) + sizeof(InfoPayload), n);

    InfoPayload ip;
    memcpy(&ip, &resp[4 + sizeof(StandardResponse)], sizeof(InfoPayload));
    TEST_ASSERT_EQUAL_UINT8(1, ip.board_id);
    TEST_ASSERT_EQUAL_UINT8(5, ip.n_axes);
    TEST_ASSERT_EQUAL_UINT8(16, ip.max_program_channels);
    TEST_ASSERT_EQUAL_HEX32(0xCAFEF00D, ip.feature_bits);
}

// --- DIAG page 0 counters + page 1 fault entries --------------------------

void test_diag_page0_counters(void) {
    SlotManager slots;
    FakeProvider prov;
    prov.diag_data.crc_err = 11;
    prov.diag_data.resync = 22;
    prov.diag_data.rx_overflow = 33;
    prov.diag_data.tx_drop = 44;
    prov.diag_data.boot_count = 7;
    prov.diag_data.fault_count = 3;
    Dispatcher d(slots, prov);
    d.register_system_commands();

    uint8_t page = 0;
    uint8_t req[8], resp[600];
    size_t rn = make_request(0x06, DIAG, 0, &page, 1, req);
    size_t n = d.build_response(req, rn, resp, sizeof(resp));
    TEST_ASSERT_EQUAL_size_t(4 + sizeof(StandardResponse) + sizeof(DiagPayload), n);

    DiagPayload dp;
    memcpy(&dp, &resp[4 + sizeof(StandardResponse)], sizeof(DiagPayload));
    TEST_ASSERT_EQUAL_UINT32(11, dp.crc_err);
    TEST_ASSERT_EQUAL_UINT32(44, dp.tx_drop);
    TEST_ASSERT_EQUAL_UINT8(3, dp.fault_count);
    TEST_ASSERT_EQUAL_UINT8(0, dp.page);
}

void test_diag_page1_fault_entries(void) {
    SlotManager slots;
    FakeProvider prov;
    prov.n_faults = 2;
    prov.faults[0].uptime_ms = 100;
    prov.faults[0].code = 0x41;
    prov.faults[0].detail = 0x01;
    prov.faults[1].uptime_ms = 200;
    prov.faults[1].code = 0x42;
    Dispatcher d(slots, prov);
    d.register_system_commands();

    uint8_t page = 1;
    uint8_t req[8], resp[600];
    size_t rn = make_request(0x06, DIAG, 0, &page, 1, req);
    size_t n = d.build_response(req, rn, resp, sizeof(resp));
    TEST_ASSERT_EQUAL_size_t(4 + sizeof(StandardResponse) + 2 * sizeof(FaultEntryWire), n);

    FaultEntryWire f0, f1;
    memcpy(&f0, &resp[4 + sizeof(StandardResponse)], sizeof(FaultEntryWire));
    memcpy(&f1, &resp[4 + sizeof(StandardResponse) + sizeof(FaultEntryWire)],
           sizeof(FaultEntryWire));
    TEST_ASSERT_EQUAL_UINT32(100, f0.uptime_ms);
    TEST_ASSERT_EQUAL_UINT8(0x41, f0.code);
    TEST_ASSERT_EQUAL_UINT8(0x42, f1.code);
}

// --- Every response fits within kMaxPayload -------------------------------

void test_all_responses_within_max_payload(void) {
    SlotManager slots;
    FakeProvider prov;
    prov.n_faults = 16;  // largest DIAG extra
    Dispatcher d(slots, prov);
    d.register_system_commands();

    uint8_t page1 = 1;
    struct { uint8_t cmd; const uint8_t* pl; size_t pln; } cases[] = {
        {GET_STATE, nullptr, 0},
        {HELLO, nullptr, 0},
        {GET_INFO, nullptr, 0},
        {DIAG, &page1, 1},
    };
    uint8_t req[8], resp[600];
    for (auto& c : cases) {
        size_t rn = make_request(0x01, c.cmd, 0, c.pl, c.pln, req);
        size_t n = d.build_response(req, rn, resp, sizeof(resp));
        TEST_ASSERT_TRUE(n > 0);
        size_t payload_len = n - 4;  // exclude the 4-byte frame header
        TEST_ASSERT_TRUE_MESSAGE(payload_len <= kMaxPayload, "response payload exceeds kMaxPayload");
    }
}

// --- RETRY of a completed command answered from the ring, no re-execution --

void test_retry_completed_answered_from_ring_no_handler(void) {
    SlotManager slots;
    FakeProvider prov;
    Dispatcher d(slots, prov);
    d.register_system_commands();
    d.register_command(0x01, false, 0, 8, counting_handler);  // slotted

    uint8_t req[16], resp[600];
    // First dispatch: NewCommand -> handler runs once, status ACCEPTED.
    size_t rn = make_request(0x30, 0x01, 0, nullptr, 0, req);
    size_t n = d.build_response(req, rn, resp, sizeof(resp));
    TEST_ASSERT_TRUE(n > 0);
    uint8_t type, cid, ct;
    StandardResponse sr;
    parse_response(resp, n, &type, &cid, &ct, &sr);
    TEST_ASSERT_EQUAL_UINT8(STATUS_ACCEPTED, sr.status);
    TEST_ASSERT_EQUAL_INT(1, g_handler_calls);

    // Simulate asynchronous completion.
    slots.complete(0x30, STATUS_FAILED, ERR_INVALID_PARAMETER);

    // RETRY of the completed command: answered from the ring, handler NOT run.
    rn = make_request(0x30, 0x01, FLAG_RETRY, nullptr, 0, req);
    n = d.build_response(req, rn, resp, sizeof(resp));
    parse_response(resp, n, &type, &cid, &ct, &sr);
    TEST_ASSERT_EQUAL_UINT8(STATUS_FAILED, sr.status);
    TEST_ASSERT_EQUAL_UINT8(ERR_INVALID_PARAMETER, sr.error_code);
    TEST_ASSERT_EQUAL_INT(1, g_handler_calls);  // unchanged: no re-execution
}

// --- Integration: request in -> response out through a real Framer --------

class CaptureByteSink : public ByteSink {
public:
    std::vector<uint8_t> written;
    size_t writable() override { return (size_t)1 << 20; }
    void write(const uint8_t* b, size_t n) override { written.insert(written.end(), b, b + n); }
};
class CaptureFrameSink : public FrameSink {
public:
    std::vector<uint8_t> last;
    void on_frame(const uint8_t* f, size_t n) override { last.assign(f, f + n); }
};

void test_framer_integration_roundtrip(void) {
    SlotManager slots;
    FakeProvider prov;
    prov.mode = 9;
    Dispatcher d(slots, prov);
    d.register_system_commands();

    // The dispatcher sends built responses through this framer.
    CaptureFrameSink tx_sink_unused;  // TX framer's RX sink is not exercised
    CaptureByteSink tx_bytes;
    Framer tx(tx_sink_unused, tx_bytes);
    d.set_framer(tx);

    // Simulate the RX framer having decoded a GET_STATE request.
    uint8_t req[8];
    size_t rn = make_request(0x42, GET_STATE, 0, nullptr, 0, req);
    d.on_frame(req, rn);

    // The response was CRC'd + COBS-framed into tx_bytes; decode it back.
    CaptureFrameSink got;
    CaptureByteSink sink_unused;
    Framer dec(got, sink_unused);
    for (uint8_t b : tx_bytes.written) dec.feed_rx(b);

    TEST_ASSERT_TRUE(got.last.size() >= 4 + sizeof(StandardResponse));
    uint8_t type, cid, ct;
    StandardResponse sr;
    parse_response(got.last.data(), got.last.size(), &type, &cid, &ct, &sr);
    TEST_ASSERT_EQUAL_UINT8(RESPONSE, type);
    TEST_ASSERT_EQUAL_UINT8(0x42, cid);
    TEST_ASSERT_EQUAL_UINT8(9, sr.mode);
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_unknown_command_rejected);
    RUN_TEST(test_bad_length_rejected);
    RUN_TEST(test_get_state_returns_injected_state);
    RUN_TEST(test_hello_appends_payload_and_resets_session);
    RUN_TEST(test_get_info_passthrough);
    RUN_TEST(test_diag_page0_counters);
    RUN_TEST(test_diag_page1_fault_entries);
    RUN_TEST(test_all_responses_within_max_payload);
    RUN_TEST(test_retry_completed_answered_from_ring_no_handler);
    RUN_TEST(test_framer_integration_roundtrip);
    return UNITY_END();
}
