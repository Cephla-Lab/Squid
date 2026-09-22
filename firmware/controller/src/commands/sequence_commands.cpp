#include "sequence_commands.h"

#include "../constants_protocol.h"
#include "../globals.h"
#include "../sequencer/seq_bind.h"
#include "../sequencer/seq_staging.h"

namespace {

seq::wire::Staging staging;
seq::wire::ParsedProgram program;  // ~0.5 kB: static, not on the callback's stack
bool committed = false;
bool run_pending = false;  // a SEQ_RUN / SEQ_CANCEL is holding mcu_cmd_execution_in_progress
bool quiet_abort = false;  // the run was ended by a host safety command that must itself succeed

// The engine reports run-time errors; the transport reports refusals (bad upload, RUN without
// a program). Whichever happened last is what the status bytes show.
bool show_transport_error = false;
uint8_t transport_error = 0;
uint8_t transport_detail = 0;

void refuse(seq::SeqError e, uint8_t detail) {
    transport_error = (uint8_t)e;
    transport_detail = detail;
    show_transport_error = true;
    mcu_cmd_execution_status = CMD_EXECUTION_ERROR;
}

}  // namespace

void callback_seq_write() {
    committed = false;  // any write invalidates the sealed program
    if (!staging.write_word(buffer_rx[2], &buffer_rx[3]))
        refuse(seq::SeqError::BadProgram, seq::wire::kBadProgramLength);
}

void callback_seq_commit() {
    committed = false;
    const uint16_t length = (uint16_t)(uint16_t(buffer_rx[2]) << 8 | buffer_rx[3]);
    const uint16_t crc = (uint16_t)(uint16_t(buffer_rx[4]) << 8 | buffer_rx[5]);
    seq::ValidationResult r = staging.commit(length, crc, &program);
    if (r.error == seq::SeqError::None) r = seq_load(program);
    if (r.error != seq::SeqError::None) {
        refuse(r.error, r.detail);
        return;
    }
    committed = true;
    show_transport_error = false;
}

void callback_seq_run() {
    if (!committed) {
        refuse(seq::SeqError::NotCommitted, 0);
        return;
    }
    const int32_t stack_start = int32_t(uint32_t(buffer_rx[2]) << 24 | uint32_t(buffer_rx[3]) << 16 |
                                        uint32_t(buffer_rx[4]) << 8 | uint32_t(buffer_rx[5]));
    if (!seq_start(stack_start)) {
        refuse(seq::SeqError::Busy, 0);
        return;
    }
    show_transport_error = false;
    quiet_abort = false;
    if (seq_running()) {
        mcu_cmd_execution_in_progress = true;
        run_pending = true;
    } else if (seq_state() == seq::SeqState::Failed) {
        // refused before anything moved, e.g. StackOutOfRange: the engine holds the reason
        mcu_cmd_execution_status = CMD_EXECUTION_ERROR;
    }
}

void callback_seq_cancel() {
    if (!seq_running()) return;  // nothing to cancel: completes at once, successfully
    seq_cancel();
    mcu_cmd_execution_in_progress = true;  // this command now completes when the run is terminal
    run_pending = true;
}

bool seq_command_refused(uint8_t opcode) {
    return seq_running() && !seq::wire::allowed_while_running(opcode);
}

void seq_transport_tick() {
    if (!run_pending || seq_running()) return;
    run_pending = false;
    mcu_cmd_execution_in_progress = false;
    if (seq_state() == seq::SeqState::Failed && !quiet_abort)
        mcu_cmd_execution_status = CMD_EXECUTION_ERROR;
    quiet_abort = false;
}

void seq_fill_status(uint8_t* b) {
    const seq::SeqProgress& progress = seq_progress();
    const uint8_t error = show_transport_error ? transport_error : progress.abort_error;
    const uint8_t detail = show_transport_error ? transport_detail : progress.abort_detail;
    const uint16_t frames = (uint16_t)progress.frames_fired;  // <= 65535 by parse_staging()
    b[0] = seq::wire::pack_status(seq_state(), (seq::SeqError)error);
    b[1] = detail;
    b[2] = (uint8_t)(frames >> 8);
    b[3] = (uint8_t)(frames & 0xFF);
}

void seq_transport_host_shutdown() {
    if (!seq_running()) return;
    quiet_abort = true;
    seq_abort(seq::SeqError::HostAbort);
}

void seq_transport_reset() {
    seq_abort(seq::SeqError::HostAbort);
    run_pending = false;
    quiet_abort = false;
    committed = false;
    show_transport_error = false;
}
