/**
 * Resource-claims lookup and conflict checking for the command dispatcher.
 *
 * `claims_for` maps a command to the u32 resource mask it wants (via the
 * production table in claims_table.h). `claims_conflict` decides whether a
 * wanted mask can coexist with the union of in-flight claims. Pure, no heap.
 */

#ifndef PROTOCOL_CLAIMS_H
#define PROTOCOL_CLAIMS_H

#include <stddef.h>
#include <stdint.h>

#include "protocol/claims_table.h"

namespace protocol {

// Resource mask an incoming command claims, from the production table.
uint32_t claims_for(uint8_t cmd_type, const uint8_t* payload, size_t len);

// Same lookup against an explicit table (tests and Phase D supply their own).
uint32_t claims_for_in(const ClaimsRow* table, size_t count, uint8_t cmd_type,
                       const uint8_t* payload, size_t len);

// 0 if `wanted` and `inflight_union` share no resources; otherwise the
// lowest-set conflicting resource bit index + 1 (so 0 unambiguously means
// "no conflict", and the caller recovers the resource id by subtracting 1).
uint8_t claims_conflict(uint32_t wanted, uint32_t inflight_union);

}  // namespace protocol

#endif  // PROTOCOL_CLAIMS_H
