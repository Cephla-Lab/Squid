#pragma once
#include <stdint.h>

#include "sequencer/seq_engine.h"
#include "sequencer/seq_wire.h"

// Hardware binding of the sequencer engine for the Squid controller (Arduino side).
// The engine itself stays pure; this file is the only place it touches TMC / DAC / GPIO.

// Load a parsed program. On top of the engine's own validation this rejects anything the
// flashed controller profile cannot do — a camera beyond the profile's trigger count, a
// ready line the controller does not have, a TTL port that does not exist — so a claimed
// hardware resource fails loudly here instead of mis-timing a run later.
seq::ValidationResult seq_load(const seq::wire::ParsedProgram& program);

// Run the loaded program from stack_start (piezo: DAC code). False = nothing loaded / busy.
bool seq_start(int32_t stack_start);

void seq_cancel();              // finish the current exposure, then wind down
void seq_abort(seq::SeqError);  // interlock / watchdog / TURN_OFF_ALL_PORTS: terminal now

// Call once per loop() pass.
void seq_tick();

bool seq_running();
seq::SeqState seq_state();
const seq::SeqProgress& seq_progress();
