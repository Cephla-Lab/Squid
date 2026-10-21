#include "limit_switches.h"

#include <EEPROM.h>

#include "globals.h"
#include "utils/limit_switch_config.h"

// Indexed by internal axis (x, y, z are 1, 0, 2); entries for other axes are never used.
static uint8_t limit_switch_config[TOTAL_AXES];

static bool is_limit_switch_axis(uint8_t internal_axis)
{
    return internal_axis == x || internal_axis == y || internal_axis == z;
}

void limit_switches_load()
{
    uint8_t record[LSC_RECORD_LEN];
    for (uint8_t i = 0; i < LSC_RECORD_LEN; i++)
        record[i] = EEPROM.read(i);

    uint8_t config_xyz[3];   // protocol order X, Y, Z
    lsc_record_unpack(record, config_xyz);   // fills defaults when the record is invalid
    limit_switch_config[x] = config_xyz[0];
    limit_switch_config[y] = config_xyz[1];
    limit_switch_config[z] = config_xyz[2];
}

void limit_switches_program(uint8_t internal_axis, bool flipped)
{
    if (!is_limit_switch_axis(internal_axis))
        return;
    uint8_t config = limit_switch_config[internal_axis];
    TMC4361ATypeDef *tdef = &tmc4361[internal_axis];

    // Both inputs are programmed for every axis (Z homing needs the left call too).
    tmc4361A_enableLimitSwitch(tdef, lsc_polarity(config, LEFT_SW), LEFT_SW, flipped);
    tmc4361A_enableLimitSwitch(tdef, lsc_polarity(config, RGHT_SW), RGHT_SW, flipped);

    // enableLimitSwitch only ORs bits in: rewrite the stop nibble so enables and polarities are exact.
    uint32_t reference_conf = (uint32_t)tmc4361A_readInt(tdef, TMC4361A_REFERENCE_CONF);
    tmc4361A_writeInt(tdef, TMC4361A_REFERENCE_CONF, (int32_t)lsc_apply(reference_conf, config));
}

uint8_t limit_switch_homing_polarity(uint8_t internal_axis)
{
    return lsc_polarity(limit_switch_config[internal_axis], TMC4361_homing_sw[internal_axis]);
}

bool limit_switches_set(uint8_t protocol_axis, uint8_t config)
{
    if (protocol_axis != AXIS_X && protocol_axis != AXIS_Y && protocol_axis != AXIS_Z)
        return false;
    if (config > 0x0F)
        return false;

    uint8_t axis = protocol_axis_to_internal(protocol_axis);
    limit_switch_config[axis] = config;

    bool flipped = (axis == x) ? flip_limit_switch_x : (axis == y) ? flip_limit_switch_y : false;
    limit_switches_program(axis, flipped);
    tmc4361A_enableHomingLimit(&tmc4361[axis], limit_switch_homing_polarity(axis), TMC4361_homing_sw[axis], home_safety_margin[axis]);

    uint8_t config_xyz[3] = {limit_switch_config[x], limit_switch_config[y], limit_switch_config[z]};
    uint8_t record[LSC_RECORD_LEN];
    lsc_record_pack(config_xyz, record);
    for (uint8_t i = 0; i < LSC_RECORD_LEN; i++)
        EEPROM.update(i, record[i]);
    return true;
}
