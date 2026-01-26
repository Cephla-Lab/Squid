/**
 * CRC-8 CCITT Implementation
 * Polynomial: x^8 + x^2 + x + 1 (0x07)
 */

#include "crc8.h"

uint8_t crc8ccitt(const void *data, size_t size)
{
    uint8_t val = 0;
    uint8_t *pos = (uint8_t *)data;
    uint8_t *end = pos + size;

    while (pos < end)
    {
        val ^= *pos++;
        for (int i = 0; i < 8; i++)
        {
            if (val & 0x80)
                val = (val << 1) ^ 0x07;
            else
                val <<= 1;
        }
    }
    return val;
}
