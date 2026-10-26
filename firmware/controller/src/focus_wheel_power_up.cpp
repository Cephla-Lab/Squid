#include "focus_wheel_power_up.h"

#include <EEPROM.h>

#include "utils/focus_wheel.h"

uint8_t focus_wheel_power_up_load()
{
    uint8_t record[FW_RECORD_LEN];
    for (uint8_t i = 0; i < FW_RECORD_LEN; i++)
        record[i] = EEPROM.read(FW_RECORD_ADDR + i);
    return fw_record_unpack(record);
}

void focus_wheel_power_up_save(uint8_t mode)
{
    uint8_t record[FW_RECORD_LEN];
    fw_record_pack(mode, record);
    for (uint8_t i = 0; i < FW_RECORD_LEN; i++)
        EEPROM.update(FW_RECORD_ADDR + i, record[i]);
}
