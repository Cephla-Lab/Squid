#include <unity.h>

#include <stdint.h>
#include <set>

// Header-only wire contract.
#include "protocol/frames.h"

using namespace protocol;

// --- Compile-time layout guarantees (the whole point of frames.h) ---------
// These fire on both native (x86_64) and ARM builds; packed structs keep them
// identical, so any drift breaks the build immediately.
static_assert(sizeof(FrameHeader) == 4, "FrameHeader must be 4 bytes");
static_assert(sizeof(Slot) == 4, "Slot must be 4 bytes");
static_assert(sizeof(RingEntry) == 4, "RingEntry must be 4 bytes");
static_assert(sizeof(AxisStateWire) == 8, "AxisStateWire must be 8 bytes");
static_assert(sizeof(SeqProgressWire) == 12, "SeqProgressWire must be 12 bytes");
static_assert(sizeof(StandardResponse) == 158, "StandardResponse must be 158 bytes");
static_assert(sizeof(HelloPayload) == 16, "HelloPayload must be 16 bytes");
static_assert(sizeof(InfoPayload) == 22, "InfoPayload must be 22 bytes");
static_assert(sizeof(DiagPayload) == 40, "DiagPayload must be 40 bytes");
static_assert(sizeof(FaultEntryWire) == 8, "FaultEntryWire must be 8 bytes");

// Sub-arrays must line up with the documented byte budget.
static_assert(sizeof(Slot) * 5 == 20, "slots[5] budget");
static_assert(sizeof(RingEntry) * 8 == 32, "ring[8] budget");
static_assert(sizeof(AxisStateWire) * 8 == 64, "axes[8] budget");

void setUp(void) {}
void tearDown(void) {}

// --- Runtime: sizes reachable as values (mirrors static_asserts) ----------

void test_struct_sizes(void) {
    TEST_ASSERT_EQUAL_size_t(158, sizeof(StandardResponse));
    TEST_ASSERT_EQUAL_size_t(16, sizeof(HelloPayload));
    TEST_ASSERT_EQUAL_size_t(22, sizeof(InfoPayload));
    TEST_ASSERT_EQUAL_size_t(40, sizeof(DiagPayload));
}

void test_frame_capacity_relationship(void) {
    // Header + payload + crc16 must fill exactly one max frame.
    TEST_ASSERT_EQUAL_size_t(kMaxFrame, sizeof(FrameHeader) + kMaxPayload + 2);
    TEST_ASSERT_EQUAL_UINT8(2, kProtocolVersion);
}

// --- System command codes: unique and within the 0xF0-0xFF block ----------

void test_system_command_codes_unique_and_in_block(void) {
    std::set<int> codes;
    int cmds[] = {HELLO, GET_INFO, GET_STATE, DIAG, ACK_ERROR,
                  SET_WATCHDOG, HEARTBEAT, REBOOT_TO_BOOTLOADER, INITIALIZE, RESET};
    for (int c : cmds) {
        TEST_ASSERT_TRUE_MESSAGE(codes.find(c) == codes.end(), "duplicate command code");
        codes.insert(c);
        TEST_ASSERT_TRUE_MESSAGE(c >= 0xF0 && c <= 0xFF, "command outside system block");
    }
    // Spot-check the exact wire values the Python side mirrors.
    TEST_ASSERT_EQUAL_HEX8(0xF0, HELLO);
    TEST_ASSERT_EQUAL_HEX8(0xF1, GET_INFO);
    TEST_ASSERT_EQUAL_HEX8(0xF2, GET_STATE);
    TEST_ASSERT_EQUAL_HEX8(0xF3, DIAG);
}

// --- Resource-bit helpers -------------------------------------------------

void test_resource_bit_helpers(void) {
    TEST_ASSERT_EQUAL_HEX32(0x00000001u, res_axis(0));
    TEST_ASSERT_EQUAL_HEX32(0x00000080u, res_axis(7));
    TEST_ASSERT_EQUAL_HEX32(0x00000100u, res_dac(0));
    TEST_ASSERT_EQUAL_HEX32(0x00008000u, res_dac(7));
    TEST_ASSERT_EQUAL_HEX32(0x00010000u, RES_ILLUM_TTL);
    TEST_ASSERT_EQUAL_HEX32(0x00020000u, RES_LED_MATRIX);
    TEST_ASSERT_EQUAL_HEX32(0x00040000u, RES_CAM_TRIGGERS);
    TEST_ASSERT_EQUAL_HEX32(0x00080000u, RES_GPIO);
    TEST_ASSERT_EQUAL_HEX32(0x00100000u, RES_SEQUENCER);
    TEST_ASSERT_EQUAL_HEX32(0x00200000u, RES_SYS_CONFIG);

    // Distinct axes do not overlap; DAC bank sits above the axis bank.
    TEST_ASSERT_EQUAL_HEX32(0u, res_axis(0) & res_axis(1));
    TEST_ASSERT_EQUAL_HEX32(0u, res_axis(7) & res_dac(0));
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_struct_sizes);
    RUN_TEST(test_frame_capacity_relationship);
    RUN_TEST(test_system_command_codes_unique_and_in_block);
    RUN_TEST(test_resource_bit_helpers);
    return UNITY_END();
}
