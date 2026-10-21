#ifndef LIMIT_SWITCH_CONFIG_H_
#define LIMIT_SWITCH_CONFIG_H_

#include <stdint.h>

// Per-axis limit-switch configuration nibble (see LIMIT_SWITCH_* in constants_protocol.h):
// bit0 left enabled, bit1 right enabled, bit2 left active-high, bit3 right active-high.
// The same nibble is the SET_LIMIT_SWITCH_CONFIG payload, the EEPROM value and the low nibble of REFERENCE_CONF.
// Pure helpers, no Arduino or TMC4361A dependencies, so they are host-tested.

static const uint8_t LSC_DEFAULT = 0x03;     // both switches enabled, active-low
static const uint8_t LSC_RECORD_LEN = 7;     // 'L', 'S', version, x, y, z, crc8

// REFERENCE_CONF with its low nibble (STOP_LEFT_EN, STOP_RIGHT_EN, POL_STOP_LEFT, POL_STOP_RGHT) replaced by
// config and HOME_EVENT (0x0F0000) cleared; every other bit (e.g. INVERT_STOP_DIRECTION) is kept.
uint32_t lsc_apply(uint32_t reference_conf, uint8_t config);

// 1 when the given input is active-high. which is LEFT_SW (0b01) or RGHT_SW (0b10) of TMC4361A_Utils.h.
uint8_t lsc_polarity(uint8_t config, uint8_t which);

// EEPROM record: 'L', 'S', 1, x, y, z, crc8ccitt of the first six bytes.
void lsc_record_pack(const uint8_t config_xyz[3], uint8_t out[LSC_RECORD_LEN]);

// False on wrong marker, version, checksum or any nibble > 0x0F; config_xyz is then filled with LSC_DEFAULT.
bool lsc_record_unpack(const uint8_t in[LSC_RECORD_LEN], uint8_t config_xyz[3]);

// z_lr in tmc4361A_readLimitSwitches format (bit0 left, bit1 right) -> status byte 18 bits 2-3.
uint8_t lsc_status_bits(uint8_t z_lr);

#endif /* LIMIT_SWITCH_CONFIG_H_ */
