#pragma once
#include <stdint.h>

#include "sequencer/seq_wire.h"

// Host -> MCU program staging for the v1 shim — pure C++11, NO Arduino deps (natively tested).
//
// A v1 command carries 5 payload bytes and there is no multi-packet mechanism, so the host
// writes the program 4 bytes at a time (SEQ_WRITE) and seals it with SEQ_COMMIT. Writes are
// addressed by ABSOLUTE word index: v1 resends a command blindly when an ack is lost, and a
// repeated absolute write is harmless where an appending one would corrupt the program. The
// commit's length + CRC-16 catch a chunk that never arrived.

namespace seq {
namespace wire {

class Staging {
   public:
    Staging() { clear(); }
    void clear();
    // Copy 4 bytes to word_index * 4. False when the word lies outside the buffer.
    bool write_word(uint8_t word_index, const uint8_t* data4);
    // Verify length and CRC-16/CCITT-FALSE over the first `length` staged bytes, then parse.
    ValidationResult commit(uint16_t length, uint16_t crc, ParsedProgram* out) const;

   private:
    uint8_t buf_[kStagingBytes];
};

// The ONLY opcodes the dispatcher accepts while a sequence runs. The MCU owns the stage, the
// light and the camera trigger for the duration; anything else is refused at this one choke
// point instead of by guards scattered over forty callbacks.
//   HEARTBEAT           keeps the serial watchdog fed during a long run
//   SEQ_CANCEL          finish the current exposure, then wind down
//   TURN_OFF_ALL_PORTS  safety shutdown — aborts the run through the engine
//   RESET               a host that restarted mid-run must be able to recover the controller
bool allowed_while_running(uint8_t opcode);

}  // namespace wire
}  // namespace seq
