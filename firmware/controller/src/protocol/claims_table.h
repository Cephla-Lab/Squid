/**
 * Resource-claims table — THE AUTHORITATIVE MAP of which resources each
 * command claims. REVIEW CAREFULLY: an incorrect row lets two commands touch
 * the same hardware concurrently (or needlessly blocks compatible commands).
 *
 * One row per command. When `computed` is non-null it OVERRIDES `static_claims`
 * (the sequencer supplies a computed function in Phase D so its claim set can
 * depend on the program payload). Resource bits are defined in frames.h.
 *
 * Phase B: system commands only, all claiming nothing (read-only or
 * session-level). Motion / axis-config / illumination / camera / sequencer
 * rows are added alongside their handlers in Phase C/D.
 */

#ifndef PROTOCOL_CLAIMS_TABLE_H
#define PROTOCOL_CLAIMS_TABLE_H

#include <stddef.h>
#include <stdint.h>

#include "protocol/frames.h"

namespace protocol {

struct ClaimsRow {
    uint8_t cmd_type;
    uint32_t static_claims;
    uint32_t (*computed)(const uint8_t* payload, size_t len);  // null => static_claims
};

// The production claims table. Returns the row array; if `out_count` is
// non-null it receives the number of rows.
inline const ClaimsRow* claims_table(size_t* out_count) {
    static const ClaimsRow rows[] = {
        {HELLO, 0, nullptr},
        {GET_INFO, 0, nullptr},
        {GET_STATE, 0, nullptr},
        {DIAG, 0, nullptr},
    };
    if (out_count) {
        *out_count = sizeof(rows) / sizeof(rows[0]);
    }
    return rows;
}

}  // namespace protocol

#endif  // PROTOCOL_CLAIMS_TABLE_H
