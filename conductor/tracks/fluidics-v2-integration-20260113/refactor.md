Problem

       The current architecture mixes abstraction levels:
       - FluidicsExecutor in the orchestrator knows low-level commands
       (FLOW, WASH, PRIME, INCUBATE, ASPIRATE)
       - FluidicsWidget duplicates execution logic with its own backgroun
        threads
       - The orchestrator is doing fluidics orchestration instead of just
       experiment orchestration
       - No central place for named protocols with state machine control

       Solution

       Introduce a FluidicsController that owns protocol loading,
       execution, and state management. The orchestrator and GUI both
       interact with this controller at a high level ("Run ProtocolX").

       Current:
         Orchestrator → FluidicsExecutor → FluidicsService → Driver
                        (knows commands)

       Proposed:
         Orchestrator ─┐
                       ├→ FluidicsController → FluidicsService → Driver
         FluidicsWidget┘   (owns protocols,
                            state machine,
                            named protocols)

       ---
       Architecture

       State Machine

                     ┌─────────────────────────────────────┐
                     │               IDLE                  │
                     └──────────────────┬──────────────────┘
                                        │ run_protocol()
                                        ▼
                     ┌─────────────────────────────────────┐
             ┌───────│             RUNNING                 │◄───────┐
             │       └──────────────────┬──────────────────┘        │
             │ resume()                 │ pause()              skip_step(
             │                          ▼                           │
             │       ┌─────────────────────────────────────┐        │
             └───────│              PAUSED                 │────────┘
                     └──────────────────┬──────────────────┘
                                        │ stop() / error / complete
                                        ▼
            ┌──────────────┬────────────────────────┬──────────────┐
            │  COMPLETED   │        STOPPED         │    FAILED    │
            └──────────────┴────────────────────────┴──────────────┘
                                        │
                                        │ (auto-transition)
                                        ▼
                                     IDLE

       FluidicsController Class

       Location: squid/backend/controllers/fluidics_controller.py

       class FluidicsControllerState(Enum):
           IDLE = auto()
           RUNNING = auto()
           PAUSED = auto()
           STOPPED = auto()
           COMPLETED = auto()
           FAILED = auto()

       class FluidicsController(StateMachine[FluidicsControllerState]):
           """Controller for fluidics protocol execution."""

           # Protocol management
           def load_protocols(self, path: str) -> int
           def get_protocol(self, name: str) -> Optional[FluidicsProtocol
           def list_protocols(self) -> List[str]

           # Execution control
           def run_protocol(self, name: str) -> bool
           def pause(self) -> bool
           def resume(self) -> bool
           def stop(self) -> bool
           def skip_to_next_step(self, empty_syringe: bool = True) -> boo

           # Status properties
           @property current_protocol: Optional[str]
           @property current_step_index: int
           @property total_steps: int
           @property is_available: bool

       YAML Protocol Schema

       Location: squid/core/protocol/fluidics_protocol.py

       Design decisions:
       - Single file: All protocols in one fluidics_protocols.yaml file
       (simpler to manage)
       - Named protocols only: Experiment YAML references protocol names,
       no inline steps
       - YAML only: No CSV support (cleaner, matches experiment protocol
       format)

       # Example: fluidics_protocols.yaml
       protocols:
         Wash_Round1:
           description: "Standard wash after Round 1 imaging"
           steps:
             - operation: wash
               solution: wash_buffer
               volume_ul: 500
               flow_rate_ul_per_min: 100
               repeats: 3
             - operation: incubate
               duration_s: 30
             - operation: aspirate

         Probe_Delivery:
           description: "Deliver probe mix to chamber"
           steps:
             - operation: prime
               solution: probe_mix
               volume_ul: 100
             - operation: flow
               solution: probe_mix
               volume_ul: 200
               flow_rate_ul_per_min: 25
             - operation: incubate
               duration_s: 1800
               description: "30 min hybridization"

       New Events

       Location: squid/core/events.py

       # Commands (UI/Orchestrator -> FluidicsController)
       @dataclass
       class RunFluidicsProtocolCommand(Event):
           protocol_name: str

       @dataclass
       class PauseFluidicsCommand(Event): pass

       @dataclass
       class ResumeFluidicsCommand(Event): pass

       @dataclass
       class StopFluidicsCommand(Event): pass

       @dataclass
       class SkipFluidicsStepCommand(Event):
           empty_syringe: bool = True

       # State Events (FluidicsController -> UI/Orchestrator)
       @dataclass
       class FluidicsControllerStateChanged(Event):
           old_state: str
           new_state: str
           protocol_name: Optional[str] = None

       @dataclass
       class FluidicsProtocolStarted(Event):
           protocol_name: str
           total_steps: int
           estimated_duration_s: float

       @dataclass
       class FluidicsProtocolStepStarted(Event):
           protocol_name: str
           step_index: int
           total_steps: int
           step_description: str
           next_step_description: Optional[str] = None

       @dataclass
       class FluidicsProtocolCompleted(Event):
           protocol_name: str
           success: bool
           steps_completed: int
           total_steps: int
           error_message: Optional[str] = None

       ---
       Integration Points

       Orchestrator

       Before (in _execute_fluidics):
       for step in round_.fluidics:
           success = self._fluidics_executor.execute(step,
       self._cancel_token)

       After (named protocols only):
       # Round now has fluidics_protocol: str field instead of fluidics:
       List[FluidicsStep]
       if round_.fluidics_protocol:

       self._fluidics_controller.run_protocol(round_.fluidics_protocol)
           # Wait for FluidicsProtocolCompleted event or poll state

       Experiment YAML change:
       # Before (inline steps)
       rounds:
         - name: Round 1
           fluidics:
             - command: wash
               solution: wash_buffer
               ...

       # After (named protocol)
       rounds:
         - name: Round 1
           fluidics_protocol: Wash_Round1  # References protocol from
       fluidics_protocols.yaml

       FluidicsWidget

       Remove:
       - _execute_sequence_rows() method
       - _execute_single_step() method
       - _is_sequence_running, _sequence_current_step state tracking
       - Background thread management for execution

       Keep:
       - Manual operation buttons (flow, wash, prime) → call
       FluidicsService directly
       - Protocol selection UI → publish RunFluidicsProtocolCommand
       - Progress display → subscribe to controller events
       - Emergency stop → publish StopFluidicsCommand

       ---
       Files to Modify





       File: squid/backend/controllers/fluidics_controller.py
       Action: CREATE - New controller with state machine
       ────────────────────────────────────────
       File: squid/core/protocol/fluidics_protocol.py
       Action: CREATE - Protocol schema (Pydantic models)
       ────────────────────────────────────────
       File: squid/core/protocol/schema.py
       Action: MODIFY - Replace fluidics: List[FluidicsStep] with
         fluidics_protocol: Optional[str] in Round
       ────────────────────────────────────────
       File: squid/core/events.py
       Action: MODIFY - Add new command and state events
       ────────────────────────────────────────
       File:
       squid/backend/controllers/orchestrator/orchestrator_controller.py
       Action: MODIFY - Use FluidicsController instead of FluidicsExecuto
       ────────────────────────────────────────
       File: squid/backend/controllers/orchestrator/fluidics_executor.py
       Action: DELETE - Logic absorbed into controller
       ────────────────────────────────────────
       File: squid/ui/widgets/fluidics.py
       Action: MODIFY - Remove execution logic, subscribe to controller
       events
       ────────────────────────────────────────
       File: squid/backend/services/fluidics_service.py
       File: squid/application.py
       Action: MODIFY - Wire up FluidicsController

       ---
       Implementation Phases

       Phase 1: Core Controller

       1. Create FluidicsControllerState enum
       2. Create FluidicsProtocol and FluidicsProtocolStep Pydantic model
       3. Create FluidicsController with state machine
       4. Add protocol loading from YAML
       5. Add execution with worker thread

       Phase 2: Events

       1. Add command events (RunFluidicsProtocolCommand, etc.)
       2. Add state events (FluidicsProtocolStarted, etc.)
       3. Add @handles decorators to controller

       Phase 3: Orchestrator Integration

       1. Update OrchestratorController to use FluidicsController
       2. Replace fluidics: List[FluidicsStep] with fluidics_protocol:
       Optional[str] in Round schema
       3. Update _execute_fluidics() to call run_protocol(name)
       4. Remove direct FluidicsExecutor usage

       Phase 4: Widget Update

       1. Remove execution logic from FluidicsWidget
       2. Add event subscriptions for controller state
       3. Update UI to use command events
       4. Keep manual operation buttons using FluidicsService directly

       Phase 5: Cleanup

       1. Delete fluidics_executor.py
       2. Remove set_sequences()/get_sequences() from FluidicsService
       3. Update application.py wiring

       ---
       Verification

       1. Unit tests: Test FluidicsController state machine transitions
       2. Integration test: Run protocol via controller in simulation mod
       3. Manual test:
         - Load protocol YAML in GUI
         - Run protocol, verify progress events
         - Test pause/resume/stop/skip
         - Verify orchestrator can run named protocols
  Can you analyze the architecture and implementation, suggest changes or
  improvements, look for bugs or features that were incorrectly or
  incompletedly implemented, make sure that all the signaling between
  different components is correctly wired