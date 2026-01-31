/**
 * Illumination utility functions.
 *
 * Pure C++ utility functions for illumination calculations.
 * No Arduino/hardware dependencies - suitable for host testing.
 */

#ifndef ILLUMINATION_UTILS_H
#define ILLUMINATION_UTILS_H

#include <stdint.h>

/**
 * Convert intensity percentage (0-100) to 16-bit DAC value (0-65535).
 *
 * @param intensity_percent Intensity as percentage (0.0 to 100.0)
 * @return 16-bit DAC value (0-65535)
 */
inline uint16_t intensity_percent_to_dac(float intensity_percent)
{
    if (intensity_percent <= 0.0f) return 0;
    if (intensity_percent >= 100.0f) return 65535;
    return (uint16_t)((intensity_percent / 100.0f) * 65535.0f);
}

/**
 * Convert 16-bit DAC value (0-65535) to intensity percentage (0-100).
 *
 * @param dac_value 16-bit DAC value (0-65535)
 * @return Intensity as percentage (0.0 to 100.0)
 */
inline float dac_to_intensity_percent(uint16_t dac_value)
{
    return (dac_value / 65535.0f) * 100.0f;
}

/**
 * Encode firmware version as single byte (nibble-encoded).
 * High nibble = major version (0-15), low nibble = minor version (0-15)
 *
 * @param major Major version (0-15)
 * @param minor Minor version (0-15)
 * @return Encoded version byte
 */
inline uint8_t encode_firmware_version(uint8_t major, uint8_t minor)
{
    return ((major & 0x0F) << 4) | (minor & 0x0F);
}

/**
 * Decode firmware version byte to major version.
 *
 * @param version_byte Encoded version byte
 * @return Major version (0-15)
 */
inline uint8_t decode_version_major(uint8_t version_byte)
{
    return (version_byte >> 4) & 0x0F;
}

/**
 * Decode firmware version byte to minor version.
 *
 * @param version_byte Encoded version byte
 * @return Minor version (0-15)
 */
inline uint8_t decode_version_minor(uint8_t version_byte)
{
    return version_byte & 0x0F;
}

/**
 * Check if a port is selected in a port mask.
 *
 * @param port_mask 16-bit port selection mask
 * @param port_index Port index (0-15)
 * @return true if port is selected, false otherwise
 */
inline bool is_port_selected(uint16_t port_mask, int port_index)
{
    if (port_index < 0 || port_index > 15) return false;
    return (port_mask & (1 << port_index)) != 0;
}

/**
 * Check if a port should be turned on based on on_mask.
 *
 * @param on_mask 16-bit on/off mask
 * @param port_index Port index (0-15)
 * @return true if port should be on, false if off
 */
inline bool should_port_be_on(uint16_t on_mask, int port_index)
{
    if (port_index < 0 || port_index > 15) return false;
    return (on_mask & (1 << port_index)) != 0;
}

/**
 * Create a port mask with specified ports selected.
 *
 * @param ports Array of port indices to select
 * @param num_ports Number of ports in array
 * @return 16-bit port mask
 */
inline uint16_t create_port_mask(const int* ports, int num_ports)
{
    uint16_t mask = 0;
    for (int i = 0; i < num_ports; i++) {
        if (ports[i] >= 0 && ports[i] <= 15) {
            mask |= (1 << ports[i]);
        }
    }
    return mask;
}

/**
 * Count number of ports selected in a mask.
 *
 * @param port_mask 16-bit port mask
 * @return Number of ports selected (0-16)
 */
inline int count_selected_ports(uint16_t port_mask)
{
    int count = 0;
    for (int i = 0; i < 16; i++) {
        if (port_mask & (1 << i)) count++;
    }
    return count;
}

#endif // ILLUMINATION_UTILS_H
