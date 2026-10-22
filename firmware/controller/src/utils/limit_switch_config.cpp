#include "limit_switch_config.h"
#include "crc8.h"
#include "../constants_protocol.h"

static const uint32_t LSC_HOME_EVENT_MASK = 0x0F0000;
static const uint8_t LSC_VERSION = 1;

uint32_t lsc_apply(uint32_t reference_conf, uint8_t config) {
    return (reference_conf & ~(uint32_t)0x0F & ~LSC_HOME_EVENT_MASK) | (config & 0x0F);
}

uint8_t lsc_polarity(uint8_t config, uint8_t which) {
    // which: LEFT_SW = 0b01, RGHT_SW = 0b10
    uint8_t mask = (which == 1) ? LIMIT_SWITCH_LEFT_ACTIVE_HIGH : LIMIT_SWITCH_RIGHT_ACTIVE_HIGH;
    return (config & mask) ? 1 : 0;
}

uint8_t lsc_enabled_mask(uint8_t config) {
    return config & (LIMIT_SWITCH_LEFT_ENABLED | LIMIT_SWITCH_RIGHT_ENABLED);
}

uint8_t lsc_homing_mask(uint8_t config, uint8_t toward) {
    return lsc_enabled_mask(config) | toward;
}

void lsc_record_pack(const uint8_t config_xyz[3], uint8_t out[LSC_RECORD_LEN]) {
    out[0] = 'L';
    out[1] = 'S';
    out[2] = LSC_VERSION;
    out[3] = config_xyz[0];
    out[4] = config_xyz[1];
    out[5] = config_xyz[2];
    out[6] = crc8ccitt(out, 6);
}

bool lsc_record_unpack(const uint8_t in[LSC_RECORD_LEN], uint8_t config_xyz[3]) {
    bool ok = in[0] == 'L' && in[1] == 'S' && in[2] == LSC_VERSION && in[6] == crc8ccitt(in, 6) &&
              in[3] <= 0x0F && in[4] <= 0x0F && in[5] <= 0x0F;
    for (int i = 0; i < 3; i++)
        config_xyz[i] = ok ? in[3 + i] : LSC_DEFAULT;
    return ok;
}

uint8_t lsc_status_bits(uint8_t z_lr) {
    return (uint8_t)((((z_lr >> 0) & 1) << BIT_POS_Z_LIMIT_LEFT) | (((z_lr >> 1) & 1) << BIT_POS_Z_LIMIT_RIGHT));
}
