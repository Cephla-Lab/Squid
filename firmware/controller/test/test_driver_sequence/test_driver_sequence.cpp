/*
  Register-sequence harness for the three stepper-driver modules.

  WHAT THIS EXISTS FOR
  --------------------
  env:native's build_src_filter admits the src/utils sources and nothing else,
  so tmc2660.cpp, tmc2240.cpp and driver_probe.cpp are compiled by env:teensy41
  and by nothing else. test_driver_regs pins the pure builders in tmc2660_regs.h /
  tmc2240_regs.h, but a builder returning the right word says nothing about
  whether the driver CALLS it, calls it in the right ORDER, or calls it at all.
  Before this file, an edit that moved the SPIOUT_CONF write after the cover
  datagrams, dropped tmc4361A_writeSPR(), or deleted the REVERSE_MOTOR_DIR
  write left every test green and mis-initialised real hardware.

  HOW IT COMPILES ON THE HOST
  ---------------------------
  Two obstacles, both solved the way the repo already solves them:

  1. env:native will not compile the src/tmc tree. This file therefore #includes
     the three implementation .cpp files directly, which is the established
     convention here — test/test_crc8/test_crc8.cpp includes utils/crc8.cpp the
     same way. Widening the shared build_src_filter to take in the driver
     sources is NOT an option: they need <Arduino.h> and <SPI.h>. Instead
     [env:native] gains "-I test/test_driver_sequence/stubs", a native-only
     include path holding shims for exactly those two headers. env:teensy41 is
     untouched and keeps the real Arduino core.

  2. The recording stand-ins below define tmc4361A_writeInt, _readInt, _setBits,
     _rstBits, _cScaleInit, _writeMicrosteps and _writeSPR, which are the real
     symbols from TMC4361A.cpp / TMC4361A_Utils.cpp. Those two files are not in
     env:native's build_src_filter, so nothing collides today — but if either is
     ever added to the native build, THIS TEST MUST STAY IN ITS OWN
     TRANSLATION UNIT AND ITS OWN BINARY (one directory under test/ is one
     binary in PlatformIO), and the stubs must not leak into a build that also
     links the real definitions.

  WHAT IS PINNED, AND WHY IT IS A LITERAL
  ---------------------------------------
  Every expected address and value below is a LITERAL, never the production
  constant that produced it. Asserting tmc2660_chopconf_datagram(3) against
  TMC_SPIOUT_CONF_2660 would move with any edit to either and pin nothing.

  The TMC2660 literals are master 856bc0ee's own, read out of
    git show 856bc0ee:firmware/controller/src/tmc/TMC4361A_TMC2660_Utils.cpp
  (tmc4361A_tmc2660_init, _enable_driver, _disable_driver,
  tmc4361A_config_init_stallGuard) rather than transcribed from a report.
  Design M5 claims this branch is bit-identical to master on 2660 hardware;
  these cases are what makes that claim checkable in CI.

  The TMC2240 and probe literals are decoded field by field in the comments at
  each case, so a failure says which field moved rather than only that a hex
  word changed.
*/

#include <unity.h>

#include <math.h>
#include <stdio.h>
#include <string.h>

#include <Arduino.h>                      /* the stub: delayMicroseconds()     */
#include "tmc/TMC4361A_Utils.h"           /* TMC4361ATypeDef + the primitives  */
#include "tmc/drivers/stepper_driver.h"   /* DRIVER_* verdict constants        */

/* ------------------------------------------------------------------------- */
/* Recorder                                                                   */
/* ------------------------------------------------------------------------- */

enum OpKind {
    OP_WRITE,    /* tmc4361A_writeInt          */
    OP_READ,     /* tmc4361A_readInt           */
    OP_SET,      /* tmc4361A_setBits           */
    OP_RST,      /* tmc4361A_rstBits           */
    OP_CSCALE,   /* tmc4361A_cScaleInit        */
    OP_USTEPS,   /* tmc4361A_writeMicrosteps   */
    OP_SPR       /* tmc4361A_writeSPR          */
};

struct Op {
    int      kind;
    uint8_t  addr;    /* TMC4361A register address; 0 for the argument-less calls */
    uint32_t value;
};

#define MAX_OPS 64

static Op       g_ops[MAX_OPS];
static unsigned g_n;
static bool     g_overflow;

/* Scripted words for COVER_DRV_LOW_RD, consumed in order by the probe. */
static uint32_t g_responses[8];
static unsigned g_response_count;
static unsigned g_response_idx;

/* Any read the modules are not supposed to perform gets this back. Its TOFF
   nibble is 0xF and its MRES nibble is 0xD, so a leaked read-modify-write
   surfaces as an obviously wrong chopper word instead of a plausible one. */
static const uint32_t READ_POISON = 0xDEADBEEFu;

unsigned long tmc_test_delay_us_total;    /* declared extern by stubs/Arduino.h */

static void record(int kind, uint8_t addr, uint32_t value)
{
    if (g_n >= MAX_OPS) { g_overflow = true; return; }
    g_ops[g_n].kind  = kind;
    g_ops[g_n].addr  = addr;
    g_ops[g_n].value = value;
    g_n++;
}

/* ------------------------------------------------------------------------- */
/* Stand-ins for the TMC4361A primitives                                      */
/* ------------------------------------------------------------------------- */

void tmc4361A_writeInt(TMC4361ATypeDef *, uint8_t address, int32_t value)
{
    record(OP_WRITE, address, (uint32_t)value);
}

int32_t tmc4361A_readInt(TMC4361ATypeDef *, uint8_t address)
{
    uint32_t v = READ_POISON;
    if (address == TMC4361A_COVER_DRV_LOW_RD && g_response_idx < g_response_count)
        v = g_responses[g_response_idx++];
    record(OP_READ, address, v);
    return (int32_t)v;
}

void tmc4361A_setBits(TMC4361ATypeDef *, uint8_t address, int32_t dat)
{
    record(OP_SET, address, (uint32_t)dat);
}

void tmc4361A_rstBits(TMC4361ATypeDef *, uint8_t address, int32_t dat)
{
    record(OP_RST, address, (uint32_t)dat);
}

void tmc4361A_cScaleInit(TMC4361ATypeDef *)      { record(OP_CSCALE, 0, 0); }
void tmc4361A_writeMicrosteps(TMC4361ATypeDef *) { record(OP_USTEPS, 0, 0); }
void tmc4361A_writeSPR(TMC4361ATypeDef *)        { record(OP_SPR,    0, 0); }

/* ------------------------------------------------------------------------- */
/* The modules under test, compiled into this translation unit                */
/* ------------------------------------------------------------------------- */

#include "tmc/drivers/tmc2660.cpp"
#include "tmc/drivers/tmc2240.cpp"
#include "tmc/drivers/driver_probe.cpp"

/* ------------------------------------------------------------------------- */
/* Assertion helpers                                                          */
/* ------------------------------------------------------------------------- */

static const char *kind_name(int kind)
{
    switch (kind) {
        case OP_WRITE:  return "writeInt";
        case OP_READ:   return "readInt";
        case OP_SET:    return "setBits";
        case OP_RST:    return "rstBits";
        case OP_CSCALE: return "cScaleInit";
        case OP_USTEPS: return "writeMicrosteps";
        case OP_SPR:    return "writeSPR";
        default:        return "?";
    }
}

static char g_msg[192];

static void assert_trace(const Op *want, unsigned n, const char *label)
{
    TEST_ASSERT_FALSE_MESSAGE(g_overflow, "recorder overflowed MAX_OPS");

    for (unsigned i = 0; i < n && i < g_n; i++) {
        snprintf(g_msg, sizeof g_msg, "%s: op %u should be %s, is %s",
                 label, i, kind_name(want[i].kind), kind_name(g_ops[i].kind));
        TEST_ASSERT_EQUAL_INT_MESSAGE(want[i].kind, g_ops[i].kind, g_msg);

        snprintf(g_msg, sizeof g_msg, "%s: op %u (%s) register address",
                 label, i, kind_name(want[i].kind));
        TEST_ASSERT_EQUAL_HEX8_MESSAGE(want[i].addr, g_ops[i].addr, g_msg);

        snprintf(g_msg, sizeof g_msg, "%s: op %u (%s, reg 0x%02X) value",
                 label, i, kind_name(want[i].kind), (unsigned)want[i].addr);
        TEST_ASSERT_EQUAL_HEX32_MESSAGE(want[i].value, g_ops[i].value, g_msg);
    }

    snprintf(g_msg, sizeof g_msg, "%s: number of bus operations", label);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(n, g_n, g_msg);
}

static unsigned count_kind(int kind)
{
    unsigned c = 0;
    for (unsigned i = 0; i < g_n; i++) if (g_ops[i].kind == kind) c++;
    return c;
}

/* Index of the first op matching kind+addr, or g_n if absent. */
static unsigned index_of(int kind, uint8_t addr)
{
    for (unsigned i = 0; i < g_n; i++)
        if (g_ops[i].kind == kind && g_ops[i].addr == addr) return i;
    return g_n;
}

/* How many TMC2240 cover WRITES in the trace targeted `reg2240`. A cover write
   is COVER_HIGH_WR carrying (address | 0x80) followed by COVER_LOW_WR. */
static unsigned count_cover_writes(uint8_t reg2240)
{
    unsigned c = 0;
    for (unsigned i = 0; i + 1 < g_n; i++)
        if (g_ops[i].kind == OP_WRITE && g_ops[i].addr == 0x6D &&
            g_ops[i].value == (uint32_t)(reg2240 | 0x80u) &&
            g_ops[i + 1].kind == OP_WRITE && g_ops[i + 1].addr == 0x6C)
            c++;
    return c;
}

/* Value of the first op matching kind+addr. Fails rather than reading past the
   end of the trace, so a mutation that removes the op cannot be masked by a
   stale slot. */
static uint32_t op_value(int kind, uint8_t addr)
{
    unsigned i = index_of(kind, addr);
    if (i >= g_n) {
        snprintf(g_msg, sizeof g_msg, "expected a %s to register 0x%02X; the trace has none",
                 kind_name(kind), (unsigned)addr);
        TEST_FAIL_MESSAGE(g_msg);
    }
    return g_ops[i].value;
}

/* Data word of the first cover write to `reg2240`. */
static uint32_t cover_write_value(uint8_t reg2240)
{
    for (unsigned i = 0; i + 1 < g_n; i++)
        if (g_ops[i].kind == OP_WRITE && g_ops[i].addr == 0x6D &&
            g_ops[i].value == (uint32_t)(reg2240 | 0x80u) &&
            g_ops[i + 1].kind == OP_WRITE && g_ops[i + 1].addr == 0x6C)
            return g_ops[i + 1].value;
    TEST_FAIL_MESSAGE("no cover write to the requested TMC2240 register");
    return 0;
}

static TMC4361ATypeDef g_axis;

void setUp(void)
{
    memset(g_ops, 0, sizeof g_ops);
    g_n        = 0;
    g_overflow = false;

    memset(g_responses, 0, sizeof g_responses);
    g_response_count = 0;
    g_response_idx   = 0;

    tmc_test_delay_us_total = 0;

    memset(&g_axis, 0, sizeof g_axis);
}

void tearDown(void) {}

static void reset_trace(void)
{
    memset(g_ops, 0, sizeof g_ops);
    g_n        = 0;
    g_overflow = false;
}

/* ===========================================================================
   TMC2660 — master 856bc0ee behaviour, which design M5 says must not move
   =========================================================================== */

/*
  master tmc4361A_tmc2660_init, verbatim:

      writeInt(RESET_REG,    0x52535400)
      writeInt(CLK_FREQ,     clk_Hz_TMC4361)
      writeInt(SPIOUT_CONF,  0x4440108A)     <-- BEFORE the cover datagrams
      writeInt(COVER_LOW_WR, 0x000900C3)     CHOPCONF
      writeInt(COVER_LOW_WR, 0x000A0000)     SMARTEN
      writeInt(COVER_LOW_WR, 0x000C000A)     SGCSCONF
      writeInt(COVER_LOW_WR, 0x000E00A1)     DRVCONF, SDOFF = 1 -> SPI mode
      cScaleInit(); writeMicrosteps(); writeSPR();

  The SPIOUT_CONF position is the load-bearing part: it selects the 20-bit
  automatic output format, and a cover datagram issued before it goes out under
  whatever format the probe left behind.
*/
void test_tmc2660_init_writes_masters_exact_sequence(void)
{
    static const Op want[] = {
        {OP_WRITE,  0x4F, 0x52535400u},   /* RESET_REG                        */
        {OP_WRITE,  0x31, 0x00F42400u},   /* CLK_FREQ = 16 MHz                */
        {OP_WRITE,  0x04, 0x4440108Au},   /* SPIOUT_CONF, master's exact word  */
        {OP_WRITE,  0x6C, 0x000900C3u},   /* COVER_LOW_WR  CHOPCONF  TOFF = 3 */
        {OP_WRITE,  0x6C, 0x000A0000u},   /* COVER_LOW_WR  SMARTEN            */
        {OP_WRITE,  0x6C, 0x000C000Au},   /* COVER_LOW_WR  SGCSCONF  CS = 10  */
        {OP_WRITE,  0x6C, 0x000E00A1u},   /* COVER_LOW_WR  DRVCONF            */
        {OP_CSCALE, 0x00, 0x00000000u},
        {OP_USTEPS, 0x00, 0x00000000u},
        {OP_SPR,    0x00, 0x00000000u},
    };

    tmc2660_driver_init(&g_axis, 16000000u);
    assert_trace(want, sizeof want / sizeof want[0], "tmc2660_driver_init");

    /* init() must leave the boot TOFF cached, or enable() cannot restore it. */
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(3, g_axis.driver_toff,
                                    "init must cache TOFF = 3 for enable()");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, count_kind(OP_READ),
                                     "init must not read any register");
}

void test_tmc2660_init_writes_spiout_conf_before_any_cover_datagram(void)
{
    tmc2660_driver_init(&g_axis, 16000000u);

    unsigned spiout = index_of(OP_WRITE, 0x04);   /* SPIOUT_CONF   */
    unsigned cover  = index_of(OP_WRITE, 0x6C);   /* COVER_LOW_WR  */

    TEST_ASSERT_TRUE_MESSAGE(spiout < g_n, "init never wrote SPIOUT_CONF");
    TEST_ASSERT_TRUE_MESSAGE(cover  < g_n, "init never wrote a cover datagram");
    TEST_ASSERT_TRUE_MESSAGE(spiout < cover,
        "SPIOUT_CONF must be written BEFORE the first cover datagram: the probe "
        "leaves SPIOUT_CONF at the 40-bit probe word, so a cover datagram sent "
        "first goes out in the wrong format");
}

/*
  master wrote the two CHOPCONF words as unconditional constants:
    disable -> 0x000900C0 (TOFF = 0)   enable -> 0x000900C3 (TOFF = 3)
  The extracted enable() sources TOFF from the cached driver_toff, with a
  fallback for a struct that never saw init. Both paths must reach master's
  word, including the fallback.
*/
void test_tmc2660_enable_and_disable_write_masters_chopconf_words(void)
{
    static const Op want_off[] = {{OP_WRITE, 0x6C, 0x000900C0u}};
    static const Op want_on[]  = {{OP_WRITE, 0x6C, 0x000900C3u}};

    g_axis.driver_toff = 3;

    tmc2660_driver_enable(&g_axis, false);
    assert_trace(want_off, 1, "tmc2660_driver_enable(false)");

    reset_trace();
    tmc2660_driver_enable(&g_axis, true);
    assert_trace(want_on, 1, "tmc2660_driver_enable(true), driver_toff = 3");

    /* A struct that never saw tmc4361A_init() or tmc2660_driver_init(). */
    reset_trace();
    g_axis.driver_toff = 0;
    tmc2660_driver_enable(&g_axis, true);
    assert_trace(want_on, 1, "tmc2660_driver_enable(true), driver_toff = 0 fallback");
}

/*
  master tmc4361A_config_init_stallGuard performs FOUR operations, in this
  order, and returns the bool `success` — 1 means accepted, 0 means an argument
  was clamped. That polarity is the opposite of the NO_ERR (= 0) convention the
  rest of TMC4361A_Utils uses, so it is pinned deliberately: a later caller
  writing `if (config_stallguard(...))` depends on which one is in force.

  sensitivity 12, filter on, vstall 1, cscaleParam[CSCALE_IDX] = 29 is the real
  call site (init.cpp:187-188) and yields SGCSCONF = 0x000D0C1D.
*/
void test_tmc2660_config_stallguard_writes_four_operations_in_order(void)
{
    static const Op want[] = {
        {OP_WRITE, 0x6C, 0x000D0C1Du},   /* COVER_LOW_WR SGCSCONF|SFILT|SGT|CS */
        {OP_WRITE, 0x67, 0x00000001u},   /* VSTALL_LIMIT_WR                    */
        {OP_SET,   0x01, 0x04000000u},   /* REFERENCE_CONF STOP_ON_STALL       */
        {OP_RST,   0x01, 0x08000000u},   /* REFERENCE_CONF DRV_AFTER_STALL     */
    };

    g_axis.cscaleParam[CSCALE_IDX] = 29;
    int16_t rc = tmc2660_driver_config_stallguard(&g_axis, 12, true, 1);

    assert_trace(want, 4, "tmc2660_driver_config_stallguard(12, on, 1)");
    TEST_ASSERT_EQUAL_INT16_MESSAGE(1, rc,
        "master returns the bool success: 1 = accepted, NOT the NO_ERR (0) convention");
}

/*
  master: `datagram |= tmc4361A->cscaleParam[CSCALE_IDX];` — no mask.

  cs above 31 is reachable: callback_configure_stepper_driver takes a u16
  milliamp value and the current formula stores whatever uint8_t it yields
  (65535 mA on X gives 153). Routing cs through the masking builder instead
  would silently drop the high bits and change SGCSCONF on a live command path.
  cs = 0..31 cannot show the difference, so the cases below are all above it.
*/
void test_tmc2660_config_stallguard_ors_current_scale_unmasked(void)
{
    g_axis.cscaleParam[CSCALE_IDX] = 32;      /* first value a & 0x1F would eat */
    tmc2660_driver_config_stallguard(&g_axis, 12, true, 1);
    TEST_ASSERT_EQUAL_UINT32(4, g_n);
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x000D0C20u, g_ops[0].value,
        "cs = 32 must OR in unmasked (a masked build writes 0x000D0C00)");

    reset_trace();
    g_axis.cscaleParam[CSCALE_IDX] = 153;     /* 65535 mA on X */
    tmc2660_driver_config_stallguard(&g_axis, 12, true, 1);
    TEST_ASSERT_EQUAL_UINT32(4, g_n);
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x000D0C99u, g_ops[0].value,
        "cs = 153 must OR in unmasked (a masked build writes 0x000D0C19)");
}

/*
  master set success = false, then CLAMPED, then performed all four writes
  anyway. Returning early instead would leave VSTALL_LIMIT, STOP_ON_STALL and
  DRV_AFTER_STALL unconfigured on an axis whose two call sites both discard the
  return value — stall protection silently off, which is worse than a clamped
  sensitivity.
*/
void test_tmc2660_config_stallguard_still_writes_when_out_of_range(void)
{
    /* sensitivity 100 clamps to +63 -> SGT field 0x3F. */
    int16_t rc = tmc2660_driver_config_stallguard(&g_axis, 100, true, 1);

    TEST_ASSERT_EQUAL_INT16_MESSAGE(0, rc, "out-of-range must report 0 (clamped)");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(4, g_n,
        "all four writes must still happen on the clamped path");
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x000D3F00u, g_ops[0].value,
                                    "sensitivity must clamp to +63, not wrap");

    /* vstall_lim is a 24-bit field; 1 << 24 clamps to 0x00FFFFFF. */
    reset_trace();
    rc = tmc2660_driver_config_stallguard(&g_axis, 12, true, 1UL << 24);

    TEST_ASSERT_EQUAL_INT16_MESSAGE(0, rc, "out-of-range vstall must report 0");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(4, g_n, "all four writes must still happen");
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x00FFFFFFu, g_ops[1].value,
                                    "vstall_lim must clamp to 24 bits");
}

/*
  tmc2660_current_scale returns TMC_CURRENT_OUT_OF_RANGE (0xFF) when the
  request cannot be encoded. 0xFF & 0x1F is 31 — MAXIMUM current — so the
  sentinel must be rejected before tmc2660_sgcsconf_datagram ever sees it. The
  contract is refuse, not clamp: nothing may be written and the axis keeps the
  current it had.
*/
void test_tmc2660_set_current_refuses_out_of_range_sentinel(void)
{
    g_axis.r_sense = 0.22f;                       /* X/Y sense resistor */
    g_axis.cscaleParam[CSCALE_IDX] = 0x5A;        /* poison: must survive */

    tmc2660_driver_set_current(&g_axis, 65535.0f, 0.5f);

    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, g_n,
        "an unencodable current must produce ZERO bus operations");
    TEST_ASSERT_EQUAL_INT32_MESSAGE(0x5A, g_axis.cscaleParam[CSCALE_IDX],
        "the rejected request must not disturb the cached current scale");

    /* Positive control, so the case above cannot pass by doing nothing at all:
       1000 mA at R = 0.22 is master's own X/Y working point, cs = 29. */
    reset_trace();
    tmc2660_driver_set_current(&g_axis, 1000.0f, 0.5f);

    TEST_ASSERT_EQUAL_INT32_MESSAGE(29, g_axis.cscaleParam[CSCALE_IDX],
                                    "1000 mA at R = 0.22 must encode CS = 29");
    TEST_ASSERT_EQUAL_INT32_MESSAGE(127, g_axis.cscaleParam[HOLDSCALE_IDX],
                                    "hold 0.5 must truncate to 127, as master does");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(1, count_kind(OP_CSCALE),
                                     "an accepted current must push the scale values");
}

/*
  set_microsteps is deliberately a no-op on the TMC2660. Under DRVCONF.SDOFF = 1
  the TMC4361A's STEP_CONF owns microstepping and the part has no MRES field to
  program, so the correct number of bus operations is zero. Pinned because the
  obvious "fix" — mirroring the TMC2240's CHOPCONF.MRES write — would send a
  DRVCTRL datagram that the automatic SPI output is already driving.
*/
void test_tmc2660_set_microsteps_writes_nothing(void)
{
    tmc2660_driver_set_microsteps(&g_axis, 16);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, g_n,
        "the TMC2660 has no microstep register under SDOFF = 1; STEP_CONF owns it");
}

/* ===========================================================================
   TMC2240
   =========================================================================== */

/*
  The full init sequence for a CURRENT_RANGE = 1, 256-microstep axis at 16 MHz.
  Cover writes appear as the COVER_HIGH_WR (address | 0x80) / COVER_LOW_WR pair
  that actually goes on the bus.

  Field decode of the composite words:
    DRV_CONF      0x00000011  CURRENT_RANGE[1:0] = 1, SLOPE_CONTROL[5:4] = 1
    GLOBAL_SCALER 0x00000000  0 means 256/256 — every IRUN figure assumes this
    IHOLD_IRUN    0x00070000  IHOLD 0, IRUN 0, IHOLDDELAY 7 (safe zero seed)
    TPOWERDOWN    0x0000000A  10
    GCONF         0x00010000  bit 16 direct_mode; en_pwm_mode clear = SpreadCycle
    CHOPCONF      0x00010183  TOFF 3, HSTRT 0, HEND raw 3, TBL 2, MRES 0, INTPOL 0
    GSTAT         0x00000007  clears reset / drv_err / uv_cp
    SCALE_VALUES  0x00000000  zero current until set_current() runs
*/
void test_tmc2240_init_writes_expected_sequence(void)
{
    static const Op want[] = {
        {OP_WRITE,  0x4F, 0x52535400u},   /* RESET_REG                        */
        {OP_WRITE,  0x31, 0x00F42400u},   /* CLK_FREQ = 16 MHz                */
        {OP_WRITE,  0x04, 0x4445000Du},   /* SPIOUT_CONF: length 40, fmt 0x0D */
        {OP_WRITE,  0x6D, 0x0000008Au},   /* cover addr DRV_CONF      | write */
        {OP_WRITE,  0x6C, 0x00000011u},
        {OP_WRITE,  0x6D, 0x0000008Bu},   /* cover addr GLOBAL_SCALER | write */
        {OP_WRITE,  0x6C, 0x00000000u},
        {OP_WRITE,  0x6D, 0x00000090u},   /* cover addr IHOLD_IRUN    | write */
        {OP_WRITE,  0x6C, 0x00070000u},
        {OP_WRITE,  0x6D, 0x00000091u},   /* cover addr TPOWERDOWN    | write */
        {OP_WRITE,  0x6C, 0x0000000Au},
        {OP_WRITE,  0x6D, 0x00000080u},   /* cover addr GCONF         | write */
        {OP_WRITE,  0x6C, 0x00010000u},
        {OP_WRITE,  0x6D, 0x000000ECu},   /* cover addr CHOPCONF      | write */
        {OP_WRITE,  0x6C, 0x00010183u},
        {OP_WRITE,  0x6D, 0x00000081u},   /* cover addr GSTAT         | write */
        {OP_WRITE,  0x6C, 0x00000007u},
        {OP_SET,    0x00, 0x10000000u},   /* GENERAL_CONF REVERSE_MOTOR_DIR   */
        {OP_WRITE,  0x06, 0x00000000u},   /* SCALE_VALUES                     */
        {OP_SET,    0x05, 0x00000002u},   /* CURRENT_CONF DRIVE_CURRENT_SCALE */
        {OP_SET,    0x05, 0x00000001u},   /* CURRENT_CONF HOLD_CURRENT_SCALE  */
        {OP_USTEPS, 0x00, 0x00000000u},
        {OP_SPR,    0x00, 0x00000000u},
    };

    g_axis.current_range = 1;
    g_axis.microsteps    = 256;
    /* POISON, not the 3 that tmc4361A_init leaves behind. Seeding 3 here would
       make the driver_toff assertion below unfalsifiable: delete the caching
       line in init() and the seed survives, so the case stays green. */
    g_axis.driver_toff   = 0xAA;

    tmc2240_driver_init(&g_axis, 16000000u);
    assert_trace(want, sizeof want / sizeof want[0], "tmc2240_driver_init");

    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x00010183u, g_axis.tmc2240_shadow[0x6C],
        "init must leave CHOPCONF in the shadow; enable() and set_microsteps() read it");
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(3, g_axis.driver_toff,
        "init must cache TOFF from the CHOPCONF word it wrote, overwriting the "
        "TMC2660-shaped 3 that tmc4361A_init leaves in this field. Without the "
        "cache, a retune of TMC2240_DEFAULT_TOFF leaves every 2240 axis "
        "restoring a stale TOFF after each disable/enable cycle");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, count_kind(OP_READ),
        "init must not read any TMC2240 register");
}

void test_tmc2240_init_writes_spiout_conf_before_any_cover_datagram(void)
{
    g_axis.current_range = 1;
    g_axis.microsteps    = 256;

    tmc2240_driver_init(&g_axis, 16000000u);

    unsigned spiout = index_of(OP_WRITE, 0x04);   /* SPIOUT_CONF   */
    unsigned cover  = index_of(OP_WRITE, 0x6D);   /* COVER_HIGH_WR */

    TEST_ASSERT_TRUE_MESSAGE(spiout < g_n, "init never wrote SPIOUT_CONF");
    TEST_ASSERT_TRUE_MESSAGE(cover  < g_n, "init never wrote a cover datagram");
    TEST_ASSERT_TRUE_MESSAGE(spiout < cover,
        "SPIOUT_CONF must select the 40-bit format BEFORE the first cover "
        "datagram; the probe leaves it at the 0x0A probe word");
}

/*
  GENERAL_CONF bit 28, REVERSE_MOTOR_DIR.

  Under GCONF.direct_mode the TMC2240's SHAFT bit is inert, so rotation
  direction comes entirely from the phase sequence the TMC4361A transmits, and
  SPI_OUTPUT_FORMAT 0x0D maps it opposite to the TMC2660's 0x0A. Lose this
  write and every TMC2240 axis runs backwards — on first power-on that means
  homing drives away from the limit switch and into the hard stop, which is why
  it gets a case of its own rather than only living inside the sequence above.

  It must also come AFTER the RESET_REG write, which soft-resets the TMC4361A
  and would clear it, and it must be a setBits so that USE_ASTART_AND_VSTART
  (maintained by tmc4361A_sRampInit) survives.
*/
void test_tmc2240_init_sets_reverse_motor_dir(void)
{
    g_axis.current_range = 1;
    g_axis.microsteps    = 256;

    tmc2240_driver_init(&g_axis, 16000000u);

    unsigned reset = index_of(OP_WRITE, 0x4F);
    unsigned rev   = index_of(OP_SET,   0x00);

    TEST_ASSERT_TRUE_MESSAGE(rev < g_n,
        "init must set GENERAL_CONF.REVERSE_MOTOR_DIR or the axis runs backwards");
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x10000000u, g_ops[rev].value,
        "REVERSE_MOTOR_DIR is bit 28 of GENERAL_CONF");
    TEST_ASSERT_TRUE_MESSAGE(reset < rev,
        "the REVERSE_MOTOR_DIR write must follow RESET_REG, which would clear it");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, count_kind(OP_RST),
        "nothing in init may clear GENERAL_CONF bits");
}

/*
  enable() must build CHOPCONF from the SHADOW and never issue a cover read.

  A cover read here can return garbage, and garbage with a zero low nibble is
  TOFF = 0 — the driver silently switched off mid-operation. That is the bug
  that took out the octoaxes W axis. The shadow is seeded with a word init did
  not produce, so a build that reconstructed CHOPCONF from constants would fail
  this too, not just one that read the register.
*/
void test_tmc2240_enable_sources_chopconf_from_shadow_without_reading(void)
{
    g_axis.tmc2240_shadow[0x6C] = 0x0A0201C7u;    /* distinctive, TOFF = 7 */
    g_axis.driver_toff          = 5;

    tmc2240_driver_enable(&g_axis, true);

    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, count_kind(OP_READ),
        "enable() must not read a TMC2240 register; a garbage read-back lands TOFF = 0");
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x0A0201C5u, cover_write_value(0x6C),
        "enable(true) must take the shadow word and replace only TOFF");

    reset_trace();
    tmc2240_driver_enable(&g_axis, false);
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x0A0201C0u, cover_write_value(0x6C),
        "enable(false) must clear only TOFF");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, count_kind(OP_READ), "still no reads");

    /* Round trip through the real init state: disable then re-enable must
       return CHOPCONF to the exact word init wrote. */
    reset_trace();
    memset(&g_axis, 0, sizeof g_axis);
    g_axis.current_range = 1;
    g_axis.microsteps    = 256;
    g_axis.driver_toff   = 0xAA;   /* poison, so the round trip cannot pass via
                                      enable()'s TMC2240_DEFAULT_TOFF fallback */
    tmc2240_driver_init(&g_axis, 16000000u);
    tmc2240_driver_enable(&g_axis, false);
    reset_trace();
    tmc2240_driver_enable(&g_axis, true);
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x00010183u, cover_write_value(0x6C),
        "disable then enable must restore init's CHOPCONF exactly");

    /* driver_toff = 0 (a struct that never saw init) falls back to TOFF 3. */
    reset_trace();
    g_axis.driver_toff          = 0;
    g_axis.tmc2240_shadow[0x6C] = 0x00010180u;
    tmc2240_driver_enable(&g_axis, true);
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x00010183u, cover_write_value(0x6C),
        "enable(true) with driver_toff = 0 must fall back to TOFF 3");
}

void test_tmc2240_set_microsteps_sources_chopconf_from_shadow_without_reading(void)
{
    g_axis.tmc2240_shadow[0x6C] = 0x0A0201C7u;    /* distinctive, MRES = 0x0A */

    tmc2240_driver_set_microsteps(&g_axis, 1);    /* MRES 8 */

    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, count_kind(OP_READ),
        "set_microsteps() must not read a TMC2240 register");
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x080201C7u, cover_write_value(0x6C),
        "only MRES may change; TOFF and the chopper fields must survive");

    /* From init state: 16 microsteps is MRES 4, TOFF still 3. */
    reset_trace();
    memset(&g_axis, 0, sizeof g_axis);
    g_axis.current_range = 1;
    g_axis.microsteps    = 256;
    tmc2240_driver_init(&g_axis, 16000000u);
    reset_trace();
    tmc2240_driver_set_microsteps(&g_axis, 16);
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x04010183u, cover_write_value(0x6C),
                                    "16 microsteps is MRES 4");
}

/*
  tmc_microsteps_to_mres returns TMC_MRES_INVALID (0xFF) for zero, above 256,
  or a non-power-of-two. 0xFF & 0x0F is 15 — a RESERVED MRES code — so the
  sentinel must never reach tmc2240_chopconf_with_mres. set_microsteps rejects
  rather than falling back to 256 the way init() does, because the TMC4361A's
  STEP_CONF is set from the same number and a silent substitution would desync
  the two.
*/
void test_tmc2240_set_microsteps_refuses_invalid_sentinel(void)
{
    g_axis.tmc2240_shadow[0x6C] = 0x00010183u;

    tmc2240_driver_set_microsteps(&g_axis, 100);   /* not a power of two */
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, g_n, "100 microsteps must write nothing");

    tmc2240_driver_set_microsteps(&g_axis, 0);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, g_n, "0 microsteps must write nothing");

    tmc2240_driver_set_microsteps(&g_axis, 512);   /* above 256 */
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, g_n, "512 microsteps must write nothing");

    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x00010183u, g_axis.tmc2240_shadow[0x6C],
                                    "a rejected request must not touch the shadow");

    /* Positive control. */
    tmc2240_driver_set_microsteps(&g_axis, 128);   /* MRES 1 */
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x01010183u, cover_write_value(0x6C),
                                    "128 microsteps is MRES 1");
}

/*
  tmc2240_irun returns TMC2240_IRUN_OUT_OF_RANGE (0xFF) when the request
  exceeds the selected CURRENT_RANGE. 0xFF & 0x1F is IRUN 31 — MAXIMUM current
  through a motor the caller just said is too small for the request — so the
  sentinel must be rejected before tmc2240_ihold_irun_value.
*/
void test_tmc2240_set_current_refuses_out_of_range_sentinel(void)
{
    g_axis.current_range        = 1;               /* 2.0 A peak, 1.414 A rms */
    g_axis.tmc2240_shadow[0x10] = 0xA5A5A5A5u;     /* poison: must survive */

    tmc2240_driver_set_current(&g_axis, 3000.0f, 0.5f);

    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, g_n,
        "3000 mA on CURRENT_RANGE 1 must produce ZERO bus operations");
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0xA5A5A5A5u, g_axis.tmc2240_shadow[0x10],
        "the rejected request must not touch IHOLD_IRUN, not even in the shadow");

    /* Positive control: 1000 mA rms is IRUN 21, IHOLD 10 at hold 0.5. */
    reset_trace();
    tmc2240_driver_set_current(&g_axis, 1000.0f, 0.5f);

    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x0007150Au, cover_write_value(0x10),
        "1000 mA at CURRENT_RANGE 1 is IRUN 21, IHOLD 10, IHOLDDELAY 7");
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x7FFFFFFFu, op_value(OP_WRITE, 0x06),
        "SCALE_VALUES: hold 127, drv2/drv1/boost 255 — without it the TMC4361A "
        "transmits zero current and the motor never moves");
}

/*
  SGT (COOLCONF[22:16]) and SFILT (COOLCONF bit 24) share one register, so they
  must be applied in ONE read-modify-write against the shadow. Two writes would
  each clobber the other's field.

  filter_en must land on SFILT specifically: the TMC2660 path routes the same
  argument to SGCSCONF.SFILT, so anywhere else leaves a 2240 axis running
  UNFILTERED StallGuard from an identical call through the same dispatcher.
*/
void test_tmc2240_config_stallguard_sets_sgt_and_sfilt_in_one_write(void)
{
    int16_t rc = tmc2240_driver_config_stallguard(&g_axis, 12, true, 0);

    TEST_ASSERT_EQUAL_INT16_MESSAGE(1, rc, "in-range must report 1 (accepted)");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(1, count_cover_writes(0x6D),
        "SGT and SFILT must go out in ONE COOLCONF write, not two");
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x010C0000u, cover_write_value(0x6D),
                                    "SGT = 12 and SFILT set, in the same word");

    /* Toggling the filter must not disturb the threshold. */
    reset_trace();
    tmc2240_driver_config_stallguard(&g_axis, 12, false, 0);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(1, count_cover_writes(0x6D),
                                     "still one COOLCONF write");
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x000C0000u, cover_write_value(0x6D),
        "clearing SFILT must leave SGT = 12 intact");

    /* SGT is a signed 7-bit field: -64 encodes as 0x40. Out of range clamps,
       reports 0, and still performs every write — the TMC2660 contract. */
    reset_trace();
    rc = tmc2240_driver_config_stallguard(&g_axis, -100, false, 1UL << 24);

    TEST_ASSERT_EQUAL_INT16_MESSAGE(0, rc, "out-of-range must report 0 (clamped)");
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x00400000u, cover_write_value(0x6D),
                                    "sensitivity must clamp to -64, encoded 0x40");
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x00FFFFFFu, op_value(OP_WRITE, 0x67),
                                    "vstall_lim must clamp to 24 bits");
    TEST_ASSERT_TRUE_MESSAGE(index_of(OP_SET, 0x01) < g_n,
                             "STOP_ON_STALL must still be set on the clamped path");
    TEST_ASSERT_TRUE_MESSAGE(index_of(OP_RST, 0x01) < g_n,
                             "DRV_AFTER_STALL must still be cleared on the clamped path");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, count_kind(OP_READ),
        "config_stallguard must source COOLCONF from the shadow, never from a cover read");
}

/* Only SGT and SFILT may move; the rest of COOLCONF (CoolStep configuration)
   has to survive, which is the whole reason for the read-modify-write. */
void test_tmc2240_config_stallguard_preserves_unrelated_coolconf_bits(void)
{
    g_axis.tmc2240_shadow[0x6D] = 0x80FF00FFu;

    tmc2240_driver_config_stallguard(&g_axis, 12, true, 0);

    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x818C00FFu, cover_write_value(0x6D),
        "only SGT [22:16] and SFILT [24] may change");
}

/*
  tmc2240_shadow is indexed by raw register address and is sized 0x75 entries.
  SG4_RESULT (0x75) and SG4_IND (0x76) are real TMC2240 addresses PAST the end
  of it, so an unguarded write lands on whatever follows the array — on this
  struct that is status, the cover pointer and the two tolerance fields. Both
  registers are read-only on the chip, so nothing should ever get here; the
  bounds check is what makes that true rather than assumed.

  The assertion deliberately does not name one victim member. An earlier draft
  checked `status` alone and a mutation that deleted the guard still passed,
  because on a 64-bit host address 0x76 lands on the cover POINTER, not on
  status. Everything after the array is checked, plus a canary past the end of
  the struct.
*/
void test_tmc2240_cover_write_guards_the_shadow_bounds(void)
{
    struct Guarded { TMC4361ATypeDef axis; uint8_t canary[64]; };
    static Guarded g;

    memset(&g, 0, sizeof g);
    memset(g.canary, 0x5A, sizeof g.canary);

    /* 0x75 is the first address past the shadow; sweep to the top of the
       TMC2240's 7-bit address space. */
    for (unsigned addr = 0x75; addr <= 0x7F; addr++)
        tmc2240_cover_write(&g.axis, (uint8_t)addr, 0xFFFFFFFFu);

    TEST_ASSERT_EQUAL_UINT8_MESSAGE(0, g.axis.status,
        "an out-of-array shadow address must not reach struct member `status`");
    TEST_ASSERT_NULL_MESSAGE(g.axis.cover,
        "an out-of-array shadow address must not reach the `cover` pointer");
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, g.axis.target_tolerance,
        "an out-of-array shadow address must not reach `target_tolerance`");
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, g.axis.pid_tolerance,
        "an out-of-array shadow address must not reach `pid_tolerance`");
    for (unsigned i = 0; i < sizeof g.canary; i++)
        TEST_ASSERT_EQUAL_HEX8_MESSAGE(0x5A, g.canary[i],
            "an out-of-array shadow address wrote past the end of the struct");

    /* The datagrams themselves must still go out; only the shadow write is
       guarded. 11 addresses x 2 ops. */
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(22, g_n,
        "the cover datagram must still be sent for an unshadowed address");

    /* And the last in-range address must still reach the shadow. */
    reset_trace();
    tmc2240_cover_write(&g.axis, 0x74, 0x12345678u);   /* SG4_THRS, the last slot */
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x12345678u, g.axis.tmc2240_shadow[0x74],
        "0x74 is the last valid shadow index and must still be recorded");
}

/*
  init()'s TMC_MRES_INVALID fallback.

  tmc_microsteps_to_mres returns 0xFF for a microstep count that is zero, above
  256, or not a power of two, and 0xFF & 0x0F is 15 — a RESERVED MRES code that
  would program an undefined microstep resolution on a real part. init() falls
  back to 0 (256 microsteps), matching what octoaxes' own mstepVal computation
  produces for the same bad input, rather than rejecting: an axis that cannot be
  initialised at all is worse than one initialised at full resolution.

  Note this is the OPPOSITE choice from set_microsteps(), which rejects. The two
  differ deliberately (init has no caller expecting a specific resolution; the
  setter does), so both directions need a case or a future "harmonisation" will
  break one of them silently.
*/
void test_tmc2240_init_falls_back_to_256_microsteps_on_invalid_count(void)
{
    g_axis.current_range = 1;
    g_axis.microsteps    = 0;          /* tmc4361A_init leaves this zero */

    tmc2240_driver_init(&g_axis, 16000000u);

    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x00010183u, cover_write_value(0x6C),
        "an invalid microstep count must fall back to MRES 0 (256 usteps); "
        "the 0xFF sentinel masks to MRES 15, which is RESERVED");
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(3, g_axis.driver_toff,
        "the fallback path must still cache TOFF");

    reset_trace();
    memset(&g_axis, 0, sizeof g_axis);
    g_axis.current_range = 1;
    g_axis.microsteps    = 100;        /* not a power of two */
    tmc2240_driver_init(&g_axis, 16000000u);
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x00010183u, cover_write_value(0x6C),
        "a non-power-of-two microstep count must fall back to MRES 0");
}

/*
  hold_ratio outside 0..1 must be clamped in FLOAT space, before either cast.

  A negative or NaN product makes the float-to-uint8_t conversion undefined
  behaviour rather than merely wrong, and on a saturating conversion it lands at
  0xFF -> IHOLD 31 after masking, i.e. FULL hold current on an axis the caller
  just asked to hold at nothing. A post-cast `if (ihold > 31)` cannot catch that:
  the damage is done inside the conversion. The `!(x > 0.0f)` form is what makes
  this catch NaN as well as negatives.

  HOLD_SCALE_VAL is a separate hazard: it is an 8-bit field at bit 24 of
  SCALE_VALUES and cscaleParam is a signed int32_t, so an unclamped value shifts
  garbage across the top of the word instead of wrapping inside its field.

  HONEST LIMIT — READ BEFORE TRUSTING THE IHOLD HALF OF THIS CASE.
  The two IHOLD assertions below are a SPECIFICATION, not a pin. Deleting
  `if (!(ihold_f > 0.0f))` makes `(uint8_t)(-21.0f)` undefined behaviour rather
  than defined-wrong, and measured on this toolchain the unguarded path produces
  the same IHOLD 0 the guarded path does — so the case stays green. Rewriting
  the block into the brief's post-cast form
      uint8_t ihold = (uint8_t)(irun * hold_ratio); if (ihold > 31) ihold = 31;
  also survives, for the same reason. Both were run as mutations and both
  SURVIVED; see the task 6b report §5 item 3.
  What IS pinned here: the IHOLD ceiling of 31 (see the case below), and both
  ends of the HOLD_SCALE_VAL clamp — that one lands in a signed int32_t, where
  the conversion is defined, so removing either guard changes SCALE_VALUES by a
  measurable amount.
*/
void test_tmc2240_set_current_clamps_hostile_hold_ratio(void)
{
    g_axis.current_range = 1;

    tmc2240_driver_set_current(&g_axis, 1000.0f, -1.0f);
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x00071500u, cover_write_value(0x10),
        "a negative hold_ratio must yield IHOLD 0, not a wrapped 0xFF -> 31");
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x00FFFFFFu, op_value(OP_WRITE, 0x06),
        "a negative hold_ratio must yield HOLD_SCALE_VAL 0");

    reset_trace();
    tmc2240_driver_set_current(&g_axis, 1000.0f, nanf(""));
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x00071500u, cover_write_value(0x10),
        "a NaN hold_ratio must yield IHOLD 0; a plain `x < 0` test would let it through");
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x00FFFFFFu, op_value(OP_WRITE, 0x06),
        "a NaN hold_ratio must yield HOLD_SCALE_VAL 0");
}

void test_tmc2240_set_current_clamps_hold_ratio_above_one(void)
{
    g_axis.current_range = 1;

    /* irun is 21 at 1000 mA, so 21 x 2.0 = 42 overflows the 5-bit IHOLD field
       and 2.0 x 255 = 510 overflows the 8-bit HOLD_SCALE_VAL field. */
    tmc2240_driver_set_current(&g_axis, 1000.0f, 2.0f);

    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x0007151Fu, cover_write_value(0x10),
        "hold_ratio > 1 must saturate IHOLD at 31; without the clamp 42 masks "
        "to 10, i.e. a LOWER hold current than asked for");
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0xFFFFFFFFu, op_value(OP_WRITE, 0x06),
        "hold_ratio > 1 must saturate HOLD_SCALE_VAL at 255; without the clamp "
        "510 shifts across the top of SCALE_VALUES as 0xFE000000");
}

/* ===========================================================================
   Driver probe
   =========================================================================== */

static uint8_t run_probe(const uint32_t *responses, unsigned n)
{
    reset_trace();
    for (unsigned i = 0; i < n && i < 8; i++) g_responses[i] = responses[i];
    g_response_count = n;
    g_response_idx   = 0;
    tmc_test_delay_us_total = 0;

    g_axis.driver_type      = 0xAA;          /* poison: must be overwritten */
    g_axis.driver_probe_raw = 0xA5A5A5A5u;   /* poison: must be overwritten */
    return tmc_driver_probe(&g_axis);
}

/*
  The probe writes SPIOUT_CONF = 0x4445000A — cover length 40 with the TMC2660
  20-bit auto-format — so the automatic SPI output does not overwrite the
  40-bit cover reply. Under the 2240's own 0x0D format the read is clobbered.

  It must NOT restore SPIOUT_CONF on the way out: both driver init()s reset the
  TMC4361A and write their own full word, so a restore here would be undone
  immediately and would mean guessing at an identity not yet decided.
*/
void test_probe_writes_probe_spiout_conf_first_and_does_not_restore(void)
{
    const uint32_t live_2240[] = {0x40001234u, 0x40001234u, 0x40001234u};
    run_probe(live_2240, 3);

    TEST_ASSERT_TRUE_MESSAGE(g_n > 0, "the probe issued no bus operations at all");
    TEST_ASSERT_EQUAL_INT_MESSAGE(OP_WRITE, g_ops[0].kind,
                                  "the probe's first operation must be a write");
    TEST_ASSERT_EQUAL_HEX8_MESSAGE(0x04, g_ops[0].addr,
                                   "the probe must write SPIOUT_CONF first");
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x4445000Au, g_ops[0].value,
        "SPIOUT_CONF must be length 40 with the 2660 auto-format 0x0A, or the "
        "auto-SPI output overwrites the cover reply");

    unsigned spiout_writes = 0;
    for (unsigned i = 0; i < g_n; i++)
        if (g_ops[i].kind == OP_WRITE && g_ops[i].addr == 0x04) spiout_writes++;
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(1, spiout_writes,
        "SPIOUT_CONF must be written exactly once — the probe does not restore it");
    TEST_ASSERT_TRUE_MESSAGE(!(g_ops[g_n - 1].kind == OP_WRITE && g_ops[g_n - 1].addr == 0x04),
        "the probe must not restore SPIOUT_CONF on exit");
}

/*
  Three reads of IOIN and nothing else. Each tmc2240_cover_read issues two
  cover triggers — the first requests, the second clocks the reply out — so a
  healthy probe is 1 SPIOUT_CONF write + 6 cover datagrams + 3 COVER_DRV_LOW_RD
  reads = 16 operations.

  The cover address byte must carry NO write bit: 0x04, not 0x84. A write bit
  here would turn every probe into six writes to the 2240's IOIN address.
*/
void test_probe_reads_only_ioin_three_times_with_settle_delays(void)
{
    const uint32_t live_2240[] = {0x40001234u, 0x40001234u, 0x40001234u};
    run_probe(live_2240, 3);

    unsigned covers = 0, reads = 0;
    for (unsigned i = 0; i < g_n; i++) {
        if (g_ops[i].kind == OP_WRITE && g_ops[i].addr == 0x6C) covers++;
        if (g_ops[i].kind == OP_READ) {
            reads++;
            TEST_ASSERT_EQUAL_HEX8_MESSAGE(0x6E, g_ops[i].addr,
                "the probe may only read COVER_DRV_LOW_RD");
        }
        if (g_ops[i].kind == OP_WRITE && g_ops[i].addr == 0x6D)
            TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x04u, g_ops[i].value,
                "cover address must be IOIN (0x04) with the write bit CLEAR");
    }
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(6, covers, "3 reads x 2 cover passes");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(3, reads, "the probe must read three times and vote");
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(16, g_n, "1 SPIOUT_CONF + 6 cover triggers + 3 reads");

    /* 500 us after the format switch, 50 us after each of the six triggers. */
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(800, (uint32_t)tmc_test_delay_us_total,
        "the settle delays are part of the protocol: 500 us + 6 x 50 us");
}

/*
  The decision ladder, one read at a time:
    all-zeros or all-ones -> nothing is answering  -> votes for NOTHING
    else byte [31:24] == 0x40                      -> TMC2240
    else                                           -> TMC2660
  and a verdict needs a STRICT MAJORITY (2 of 3). No majority is
  DRIVER_UNKNOWN, which disables the axis and rejects its moves — a loud
  failure. Guessing TMC2660 instead would be a silent one: a real 2240 driven
  with 20-bit datagrams never enters direct_mode, never moves, and accepts
  every move command.
*/
static void expect_verdict(const char *label, const uint32_t *r, unsigned n, uint8_t want)
{
    uint8_t got = run_probe(r, n);

    snprintf(g_msg, sizeof g_msg, "%s: verdict should be %s, was %s",
             label, tmc_driver_name(want), tmc_driver_name(got));
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(want, got, g_msg);

    /* Tautological against `return tmc4361A->driver_type;` as the function is
       written today, and kept only so that a future signature change (returning
       a local, an out-parameter) cannot silently stop updating the field. The
       0xAA poison does its real work through the `want` assertion above. */
    snprintf(g_msg, sizeof g_msg, "%s: driver_type must equal the returned verdict", label);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(got, g_axis.driver_type, g_msg);

    snprintf(g_msg, sizeof g_msg, "%s: driver_probe_raw must hold the LAST read word", label);
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(r[n - 1], g_axis.driver_probe_raw, g_msg);
}

void test_probe_decision_table(void)
{
    const uint32_t V2240 = 0x40001234u;   /* VERSION byte 0x40                 */
    const uint32_t V2660 = 0x00204000u;   /* live, VERSION byte not 0x40       */
    const uint32_t VBAD  = 0x41001234u;   /* live, VERSION 0x41 — wrong constant */
    const uint32_t DEAD0 = 0x00000000u;   /* nothing driving MISO              */
    const uint32_t DEAD1 = 0xFFFFFFFFu;   /* stuck / shorted bus               */

    { const uint32_t r[] = {V2240, V2240, V2240}; expect_verdict("2240, unanimous",            r, 3, DRIVER_TMC2240); }
    { const uint32_t r[] = {DEAD0, V2240, V2240}; expect_verdict("2240, first read stale-zero", r, 3, DRIVER_TMC2240); }
    { const uint32_t r[] = {V2240, V2660, V2240}; expect_verdict("2240, one garbage read",      r, 3, DRIVER_TMC2240); }
    { const uint32_t r[] = {V2660, V2660, V2660}; expect_verdict("2660, unanimous",             r, 3, DRIVER_TMC2660); }
    { const uint32_t r[] = {DEAD0, V2660, V2660}; expect_verdict("2660, first read stale-zero", r, 3, DRIVER_TMC2660); }
    { const uint32_t r[] = {V2240, V2660, V2660}; expect_verdict("2660, one spurious 0x40",     r, 3, DRIVER_TMC2660); }
    { const uint32_t r[] = {DEAD0, DEAD0, DEAD0}; expect_verdict("unpopulated axis, all-zeros", r, 3, DRIVER_UNKNOWN); }
    { const uint32_t r[] = {DEAD1, DEAD1, DEAD1}; expect_verdict("stuck bus, all-ones",         r, 3, DRIVER_UNKNOWN); }
    { const uint32_t r[] = {V2240, DEAD0, DEAD0}; expect_verdict("mostly dead, one 2240 read",  r, 3, DRIVER_UNKNOWN); }
    { const uint32_t r[] = {V2660, DEAD1, DEAD1}; expect_verdict("mostly dead, one 2660 read",  r, 3, DRIVER_UNKNOWN); }
    { const uint32_t r[] = {V2240, DEAD0, V2660}; expect_verdict("three-way split",             r, 3, DRIVER_UNKNOWN); }
    { const uint32_t r[] = {VBAD,  VBAD,  VBAD }; expect_verdict("VERSION constant wrong",      r, 3, DRIVER_TMC2660); }
    { const uint32_t r[] = {DEAD0, DEAD0, VBAD }; expect_verdict("VERSION wrong AND bus dead",  r, 3, DRIVER_UNKNOWN); }
}

/*
  driver_probe_raw is the bench gate for the one assumption this design cannot
  close in software: whether a LIVE TMC2660 can reply all-zeros, in which case
  the liveness rule would declare a working axis DRIVER_UNKNOWN and refuse its
  moves. The measurement is only available if the field is populated on EVERY
  exit path — most of all on the DRIVER_UNKNOWN path, which is exactly the case
  the gate needs it for.
*/
void test_probe_records_raw_word_on_every_exit_path(void)
{
    const uint32_t dead[] = {0x00000000u, 0x00000000u, 0x00000000u};
    TEST_ASSERT_EQUAL_UINT8(DRIVER_UNKNOWN, run_probe(dead, 3));
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x00000000u, g_axis.driver_probe_raw,
        "a DRIVER_UNKNOWN verdict must still record the raw word (poison 0xA5A5A5A5 "
        "means the field was never written)");

    const uint32_t split[] = {0x40001234u, 0x00000000u, 0x00204000u};
    TEST_ASSERT_EQUAL_UINT8(DRIVER_UNKNOWN, run_probe(split, 3));
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x00204000u, g_axis.driver_probe_raw,
        "a no-majority verdict must record the LAST read word");

    const uint32_t live[] = {0x40001234u, 0x40001234u, 0x40009999u};
    TEST_ASSERT_EQUAL_UINT8(DRIVER_TMC2240, run_probe(live, 3));
    TEST_ASSERT_EQUAL_HEX32_MESSAGE(0x40009999u, g_axis.driver_probe_raw,
        "a positive verdict must record the LAST read word, not a selected one");
}

/* ------------------------------------------------------------------------- */

int main(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    UNITY_BEGIN();

    RUN_TEST(test_tmc2660_init_writes_masters_exact_sequence);
    RUN_TEST(test_tmc2660_init_writes_spiout_conf_before_any_cover_datagram);
    RUN_TEST(test_tmc2660_enable_and_disable_write_masters_chopconf_words);
    RUN_TEST(test_tmc2660_config_stallguard_writes_four_operations_in_order);
    RUN_TEST(test_tmc2660_config_stallguard_ors_current_scale_unmasked);
    RUN_TEST(test_tmc2660_config_stallguard_still_writes_when_out_of_range);
    RUN_TEST(test_tmc2660_set_current_refuses_out_of_range_sentinel);
    RUN_TEST(test_tmc2660_set_microsteps_writes_nothing);

    RUN_TEST(test_tmc2240_init_writes_expected_sequence);
    RUN_TEST(test_tmc2240_init_writes_spiout_conf_before_any_cover_datagram);
    RUN_TEST(test_tmc2240_init_sets_reverse_motor_dir);
    RUN_TEST(test_tmc2240_enable_sources_chopconf_from_shadow_without_reading);
    RUN_TEST(test_tmc2240_set_microsteps_sources_chopconf_from_shadow_without_reading);
    RUN_TEST(test_tmc2240_set_microsteps_refuses_invalid_sentinel);
    RUN_TEST(test_tmc2240_set_current_refuses_out_of_range_sentinel);
    RUN_TEST(test_tmc2240_config_stallguard_sets_sgt_and_sfilt_in_one_write);
    RUN_TEST(test_tmc2240_config_stallguard_preserves_unrelated_coolconf_bits);
    RUN_TEST(test_tmc2240_cover_write_guards_the_shadow_bounds);
    RUN_TEST(test_tmc2240_init_falls_back_to_256_microsteps_on_invalid_count);
    RUN_TEST(test_tmc2240_set_current_clamps_hostile_hold_ratio);
    RUN_TEST(test_tmc2240_set_current_clamps_hold_ratio_above_one);

    RUN_TEST(test_probe_writes_probe_spiout_conf_first_and_does_not_restore);
    RUN_TEST(test_probe_reads_only_ioin_three_times_with_settle_delays);
    RUN_TEST(test_probe_decision_table);
    RUN_TEST(test_probe_records_raw_word_on_every_exit_path);

    return UNITY_END();
}
