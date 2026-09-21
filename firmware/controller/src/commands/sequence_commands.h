#ifndef SEQUENCE_COMMANDS_H
#define SEQUENCE_COMMANDS_H

#include <stdint.h>

// v1-protocol transport for the hardware sequencer (opcodes 60-63). Wire contract:
// src/sequencer/seq_wire.h. Design: AI-docs Squid/to-do/2026-09-20-...-v1-shim-design.md §4.4.
//
// v1 tracks exactly ONE pending command, so the host never polls during a run: SEQ_RUN holds
// mcu_cmd_execution_in_progress until the sequence is terminal (the host's existing
// wait_till_operation_is_completed() works unchanged) and progress rides bytes 14-17 of the
// status packet that is broadcast every 10 ms anyway.

void callback_seq_write();
void callback_seq_commit();
void callback_seq_run();
void callback_seq_cancel();

// Dispatcher choke point: true when `opcode` must be refused because a sequence is running.
bool seq_command_refused(uint8_t opcode);

// Once per loop() pass, after seq_tick(): completes the pending SEQ_RUN / SEQ_CANCEL.
void seq_transport_tick();

// Fill status packet bytes 14..17: state|error, detail, frames_fired (big-endian).
void seq_fill_status(uint8_t* bytes14_to_17);

// TURN_OFF_ALL_PORTS: abort a running sequence through the engine. The shutdown command
// itself still reports success — a safety command must not raise on the host.
void seq_transport_host_shutdown();

// RESET: abort, drop the pending-run bookkeeping and the committed program. The host that
// sends RESET has restarted and must upload again.
void seq_transport_reset();

#endif  // SEQUENCE_COMMANDS_H
