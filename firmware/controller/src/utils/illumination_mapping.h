/**
 * Illumination port mapping utilities.
 *
 * Pure C++ utility functions for mapping between legacy illumination source
 * codes and port indices. No Arduino/hardware dependencies.
 */

#ifndef ILLUMINATION_MAPPING_H
#define ILLUMINATION_MAPPING_H

#include "../constants_protocol.h"

// Number of illumination ports supported
#define NUM_ILLUMINATION_PORTS 16

/**
 * Map legacy illumination source code to port index.
 *
 * Legacy source codes are non-sequential for historical API compatibility:
 *   D1 = 11, D2 = 12, D3 = 14, D4 = 13, D5 = 15
 *
 * Port indices are sequential: 0=D1, 1=D2, 2=D3, 3=D4, 4=D5
 *
 * @param source Legacy illumination source code (11-15)
 * @return Port index (0-4), or -1 for unknown source codes
 */
inline int illumination_source_to_port_index(int source)
{
    switch (source)
    {
        case ILLUMINATION_D1: return 0;  // 11 -> 0
        case ILLUMINATION_D2: return 1;  // 12 -> 1
        case ILLUMINATION_D3: return 2;  // 14 -> 2 (non-sequential!)
        case ILLUMINATION_D4: return 3;  // 13 -> 3 (non-sequential!)
        case ILLUMINATION_D5: return 4;  // 15 -> 4
        default: return -1;  // Unknown source
    }
}

/**
 * Map port index to legacy illumination source code.
 *
 * @param port_index Port index (0-4)
 * @return Legacy source code (11-15), or -1 for invalid port index
 */
inline int port_index_to_illumination_source(int port_index)
{
    switch (port_index)
    {
        case 0: return ILLUMINATION_D1;  // 0 -> 11
        case 1: return ILLUMINATION_D2;  // 1 -> 12
        case 2: return ILLUMINATION_D3;  // 2 -> 14
        case 3: return ILLUMINATION_D4;  // 3 -> 13
        case 4: return ILLUMINATION_D5;  // 4 -> 15
        default: return -1;  // Invalid port
    }
}

#endif // ILLUMINATION_MAPPING_H
