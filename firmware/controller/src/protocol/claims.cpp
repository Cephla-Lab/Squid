/**
 * Resource-claims lookup and conflict checking. See claims.h for the contract.
 */

#include "protocol/claims.h"

namespace protocol {

uint32_t claims_for_in(const ClaimsRow* table, size_t count, uint8_t cmd_type,
                       const uint8_t* payload, size_t len) {
    for (size_t i = 0; i < count; ++i) {
        if (table[i].cmd_type == cmd_type) {
            if (table[i].computed != nullptr) {
                return table[i].computed(payload, len);  // computed overrides static
            }
            return table[i].static_claims;
        }
    }
    return 0;  // command not in the table claims nothing
}

uint32_t claims_for(uint8_t cmd_type, const uint8_t* payload, size_t len) {
    size_t count = 0;
    const ClaimsRow* table = claims_table(&count);
    return claims_for_in(table, count, cmd_type, payload, len);
}

uint8_t claims_conflict(uint32_t wanted, uint32_t inflight_union) {
    uint32_t overlap = wanted & inflight_union;
    if (overlap == 0) {
        return 0;  // compatible
    }
    for (uint8_t bit = 0; bit < 32; ++bit) {
        if (overlap & (uint32_t(1) << bit)) {
            return (uint8_t)(bit + 1);  // lowest-set conflicting resource + 1
        }
    }
    return 0;  // unreachable (overlap != 0 guarantees a set bit)
}

}  // namespace protocol
