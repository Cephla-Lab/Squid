#include "sequencer/seq_staging.h"

#include <string.h>

#include "constants_protocol.h"
#include "protocol/crc16.h"

namespace seq {
namespace wire {

void Staging::clear() { memset(buf_, 0, sizeof buf_); }

bool Staging::write_word(uint8_t word_index, const uint8_t* data4) {
    const uint16_t offset = (uint16_t)word_index * kWordBytes;
    if (offset + kWordBytes > kStagingBytes) return false;
    memcpy(buf_ + offset, data4, kWordBytes);
    return true;
}

ValidationResult Staging::commit(uint16_t length, uint16_t crc, ParsedProgram* out) const {
    if (length < kChannelsOffset || length > kStagingBytes)
        return {SeqError::BadProgram, kBadProgramLength};
    if (protocol::crc16_ccitt(buf_, length) != crc) return {SeqError::BadProgram, kBadProgramCrc};
    return parse_staging(buf_, length, out);
}

bool allowed_while_running(uint8_t opcode) {
    return opcode == HEARTBEAT || opcode == SEQ_CANCEL || opcode == TURN_OFF_ALL_PORTS ||
           opcode == RESET;
}

}  // namespace wire
}  // namespace seq
