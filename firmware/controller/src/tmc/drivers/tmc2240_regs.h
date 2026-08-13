#ifndef TMC2240_REGS_H
#define TMC2240_REGS_H

#include <stdint.h>

/*
  TMC2240 register addresses, field positions and pure value builders.

  The TMC2240 uses a 40-bit SPI frame — [addr | write-bit][d31:24][d23:16]
  [d15:8][d7:0] — carried through the TMC4361A cover datagram as
  COVER_HIGH_WR (address byte) followed by COVER_LOW_WR (32 data bits);
  writing COVER_LOW_WR triggers the transfer.

  Addresses transcribed from the ADI TMC2240 register map (matching
  tmc/ic/TMC2240/TMC2240_HW_Abstraction.h in the octoaxes tree).
*/

#define TMC2240_REG_GCONF         0x00
#define TMC2240_REG_GSTAT         0x01
#define TMC2240_REG_IOIN          0x04
#define TMC2240_REG_DRV_CONF      0x0A
#define TMC2240_REG_GLOBAL_SCALER 0x0B
#define TMC2240_REG_IHOLD_IRUN    0x10
#define TMC2240_REG_TPOWERDOWN    0x11
#define TMC2240_REG_CHOPCONF      0x6C
#define TMC2240_REG_COOLCONF      0x6D
#define TMC2240_REG_PWMCONF       0x70
#define TMC2240_REG_SG4_THRS      0x74

#define TMC2240_WRITE_BIT 0x80
#define TMC2240_ADDRESS_MASK 0x7F

/* Highest register address we shadow. Sized to cover SG4_THRS (0x74). */
#define TMC2240_SHADOW_COUNT 0x75

/* IOIN.VERSION is bits [31:24] and reads 0x40 on a TMC2240. */
#define TMC2240_IOIN_VERSION_SHIFT 24
#define TMC2240_IOIN_VERSION_VALUE 0x40

/* CHOPCONF field positions */
#define TMC2240_TOFF_SHIFT   0
#define TMC2240_TOFF_MASK    (0x0Fu << TMC2240_TOFF_SHIFT)
#define TMC2240_HSTRT_SHIFT  4
#define TMC2240_HEND_SHIFT   7
#define TMC2240_TBL_SHIFT    15
#define TMC2240_MRES_SHIFT   24
#define TMC2240_MRES_MASK    (0x0Fu << TMC2240_MRES_SHIFT)
#define TMC2240_INTPOL_SHIFT 28

/* IHOLD_IRUN field positions */
#define TMC2240_IHOLD_SHIFT      0
#define TMC2240_IRUN_SHIFT       8
#define TMC2240_IHOLDDELAY_SHIFT 16

/* GCONF bits */
#define TMC2240_EN_PWM_MODE_BIT 2
#define TMC2240_DIRECT_MODE_BIT 16

/* COOLCONF: StallGuard threshold, bits [22:16] */
#define TMC2240_SGT_SHIFT 16
#define TMC2240_SGT_MASK  (0x7Fu << TMC2240_SGT_SHIFT)

static inline uint32_t tmc2240_ihold_irun_value(uint8_t ihold, uint8_t irun, uint8_t ihold_delay)
{
    return ((uint32_t)(ihold & 0x1Fu) << TMC2240_IHOLD_SHIFT)
         | ((uint32_t)(irun  & 0x1Fu) << TMC2240_IRUN_SHIFT)
         | ((uint32_t)(ihold_delay & 0x0Fu) << TMC2240_IHOLDDELAY_SHIFT);
}

/* CURRENT_RANGE [1:0]; SLOPE_CONTROL [7:4] = 1 (200 V/us, the ADI default). */
static inline uint32_t tmc2240_drv_conf_value(uint8_t current_range)
{
    return ((uint32_t)(current_range & 0x03u)) | (1u << 4);
}

/*
  direct_mode (bit 16) is MANDATORY in this topology: the TMC4361A drives the
  coil currents over SPI, exactly as DRVCONF.SDOFF=1 does for the TMC2660.
  Without it the TMC2240 waits for step/dir input and ignores current commands.
  SHAFT (bit 4) has no effect in direct mode — direction comes from the
  TMC4361A's REVERSE_MOTOR_DIR.
*/
static inline uint32_t tmc2240_gconf_value(bool stealthchop)
{
    uint32_t v = (1u << TMC2240_DIRECT_MODE_BIT);
    if (stealthchop) v |= (1u << TMC2240_EN_PWM_MODE_BIT);
    return v;
}

/*
  HEND is stored with a +3 offset, so the legal input range is -3..12. Like the
  other builders here this one masks rather than validates: hend = -4 wraps to
  the encoding for +12, i.e. MAXIMUM hysteresis end. Validate at the caller.
*/
static inline uint32_t tmc2240_chopconf_value(uint8_t toff, uint8_t hstrt, int8_t hend,
                                              uint8_t tbl, uint8_t mres, bool interpolate)
{
    uint32_t v = ((uint32_t)(toff  & 0x0Fu) << TMC2240_TOFF_SHIFT)
               | ((uint32_t)(hstrt & 0x07u) << TMC2240_HSTRT_SHIFT)
               | ((uint32_t)(((uint8_t)(hend + 3)) & 0x0Fu) << TMC2240_HEND_SHIFT)
               | ((uint32_t)(tbl   & 0x03u) << TMC2240_TBL_SHIFT)
               | ((uint32_t)(mres  & 0x0Fu) << TMC2240_MRES_SHIFT);
    if (interpolate) v |= (1u << TMC2240_INTPOL_SHIFT);
    return v;
}

/*
  Read-modify-write helpers operating on a SHADOW copy of CHOPCONF.

  Never source the input from a live register read: cover reads are unreliable
  (TMC4361A datasheet §10.3.6 requires waiting for COVER_DONE, and the
  automatic SPI output interferes). A garbage read-back here writes TOFF = 0
  and silently disables the driver mid-operation.
*/
static inline uint32_t tmc2240_chopconf_with_mres(uint32_t chopconf, uint8_t mres)
{
    return (chopconf & ~TMC2240_MRES_MASK)
         | (((uint32_t)mres << TMC2240_MRES_SHIFT) & TMC2240_MRES_MASK);
}

static inline uint32_t tmc2240_chopconf_with_toff(uint32_t chopconf, uint8_t toff)
{
    return (chopconf & ~TMC2240_TOFF_MASK)
         | (((uint32_t)toff << TMC2240_TOFF_SHIFT) & TMC2240_TOFF_MASK);
}

#endif /* TMC2240_REGS_H */
