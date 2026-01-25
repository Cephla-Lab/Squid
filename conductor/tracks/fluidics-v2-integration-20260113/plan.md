# Fluidics v2 Integration Plan

## Overview

Integrate the `fluidics_v2` MERFISH operations module into the Squid v2 architecture for orchestrated multi-round FISH experiments.

**Depends on:** Experiment orchestrator (`conductor/tracks/experiment-orchestrator-20251230/`) - completed

---

## Goals

1. Wire `fluidics_v2.MERFISHOperations` to the orchestrator's `FluidicsExecutor`
2. Map protocol solution names (e.g., "wash_buffer") to physical ports via JSON config
3. Support both real hardware and simulation mode
4. Publish progress events for UI feedback

---

## Current State

### fluidics_v2 Module (`software/fluidics_v2/`)

The `fluidics_v2` git submodule provides:

- **MERFISHOperations** class with:
  - `flow_reagent(port, flow_rate, volume, fill_tubing_with_port)`
  - `priming_or_clean_up(port, flow_rate, volume, use_ports)`
- **Hardware components:**
  - `FluidController` - Teensy microcontroller (2M baud, COBS encoding)
  - `SyringePump` - Tecan XCalibur XCaliburD
  - `SelectorValveSystem` - Multi-valve port management
- **Configuration:** JSON with serial numbers, port mappings, tubing volumes
- **Simulation:** `FluidControllerSimulation`, `SyringePumpSimulation` classes

### Existing Squid Integration

| Component | File | Status |
|-----------|------|--------|
| FluidicsService | `backend/services/fluidics_service.py` | Exists but assumes different API |
| FluidicsExecutor | `backend/controllers/orchestrator/fluidics_executor.py` | Skeleton only - just logs |
| Protocol Schema | `core/protocol/schema.py` | Complete - has FluidicsStep with solution names |

### Gap Analysis

The `FluidicsExecutor` currently logs commands but doesn't execute hardware:

```python
# Current (skeleton)
def _execute_flow(self, step, cancel_token):
    _log.debug(f"[SIMULATED] FLOW: {step.solution} {step.volume_ul}ul")
    return True  # No actual hardware call
```

Needs to become:

```python
# Target
def _execute_flow(self, step, cancel_token):
    return self._fluidics_service.flow_solution_by_name(
        step.solution, step.volume_ul, step.flow_rate_ul_per_min
    )
```

---

## Architecture

### Layer Diagram

```
Protocol YAML (solution names)
         │
         ▼
   FluidicsExecutor
         │
         ▼
   FluidicsService (thread-safe, events)
         │
         ▼
AbstractFluidicsController (ABC)
         │
         ▼
MERFISHFluidicsDriver (wraps fluidics_v2)
         │
         ▼
  MERFISHOperations
         │
         ├── SyringePump
         ├── SelectorValveSystem
         └── FluidController
```

### Port Mapping Flow

```
Protocol says: flow solution="wash_buffer"
                     │
                     ▼
FluidicsExecutor.execute(step)
                     │
                     ▼
FluidicsService.flow_solution_by_name("wash_buffer")
                     │
                     ▼
Driver.get_port_for_solution("wash_buffer") → 25  (from JSON config)
                     │
                     ▼
MERFISHOperations.flow_reagent(port=25, ...)
```

---

## AbstractFluidicsController ABC

**File:** `software/src/squid/core/abc.py`

### Status Types

```python
class FluidicsOperationStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    INCUBATING = "incubating"
    COMPLETED = "completed"
    ERROR = "error"
    ABORTED = "aborted"

@dataclass(frozen=True)
class FluidicsStatus:
    status: FluidicsOperationStatus
    current_port: Optional[int] = None
    current_solution: Optional[str] = None
    syringe_volume_ul: float = 0.0
    is_busy: bool = False
    error_message: Optional[str] = None
```

### ABC Interface

```python
class AbstractFluidicsController(ABC):
    """Abstract base class for fluidics controllers."""

    # Lifecycle
    @abstractmethod
    def initialize(self) -> bool: ...

    @abstractmethod
    def close(self) -> None: ...

    # Operations
    @abstractmethod
    def flow_solution(
        self,
        port: int,
        volume_ul: float,
        flow_rate_ul_per_min: float,
        fill_tubing_with_port: Optional[int] = None,
    ) -> bool: ...

    @abstractmethod
    def prime(
        self,
        ports: list[int],
        volume_ul: float,
        flow_rate_ul_per_min: float,
        final_port: int,
    ) -> bool: ...

    @abstractmethod
    def wash(
        self,
        wash_port: int,
        volume_ul: float,
        flow_rate_ul_per_min: float,
        repeats: int = 1,
    ) -> bool: ...

    @abstractmethod
    def empty_syringe(self) -> bool: ...

    # Abort/Control
    @abstractmethod
    def abort(self) -> None: ...

    @abstractmethod
    def reset_abort(self) -> None: ...

    # Status/Query
    @abstractmethod
    def get_status(self) -> FluidicsStatus: ...

    @abstractmethod
    def get_port_name(self, port: int) -> Optional[str]: ...

    @abstractmethod
    def get_port_for_solution(self, solution_name: str) -> Optional[int]: ...

    @abstractmethod
    def get_available_ports(self) -> list[int]: ...

    @property
    @abstractmethod
    def is_busy(self) -> bool: ...
```

**Note:** `get_available_solutions() -> dict[str, int]` is NOT in the ABC. It's implemented in `FluidicsService` by iterating `get_available_ports()` and calling `get_port_name()` for each:

```python
# In FluidicsService (not ABC)
def get_available_solutions(self) -> dict[str, int]:
    """Build solution→port mapping from driver's available ports."""
    result = {}
    for port in self._driver.get_available_ports():
        name = self._driver.get_port_name(port)
        if name:
            result[name] = port
    return result
```

---

## Configuration Schema

**File:** `software/configurations/fluidics/merfish_config.json`

```json
{
  "application": "MERFISH",
  "microcontroller": {
    "serial_number": "15579610"
  },
  "syringe_pump": {
    "serial_number": "A9TLZRF2",
    "volume_ul": 5000,
    "waste_port": 3,
    "extract_port": 2,
    "speed_code_limit": 10
  },
  "selector_valves": {
    "valve_ids_allowed": [0, 1, 2],
    "number_of_ports": {
      "0": 10,
      "1": 10,
      "2": 10
    },
    "tubing_fluid_amount_to_valve_ul": {
      "0": 800,
      "1": 1000,
      "2": 1140
    },
    "tubing_fluid_amount_to_port_ul": {
      "port_1": 700,
      "port_2": 700
    }
  },
  "solution_port_mapping": {
    "probe_mix_1": 1,
    "probe_mix_2": 2,
    "probe_mix_3": 3,
    "wash_buffer": 25,
    "imaging_buffer": 26,
    "cleavage_buffer": 27,
    "stripping_buffer": 28
  },
  "reagent_name_mapping": {
    "port_1": "probe_mix_1",
    "port_2": "probe_mix_2",
    "port_25": "wash_buffer",
    "port_26": "imaging_buffer"
  },
  "limits": {
    "max_flow_rate_ul_per_min": 10000,
    "min_flow_rate_ul_per_min": 1,
    "max_volume_ul": 5000
  }
}
```

### Config Validation

`MERFISHFluidicsConfig` must validate on load:
- Required fields present: `microcontroller`, `syringe_pump`, `selector_valves`, `solution_port_mapping`
- No duplicate port numbers in `solution_port_mapping`
- All port numbers within valid range (1 to total available ports)
- Syringe volume > 0
- Speed code limit in valid range

Fail fast with clear error messages on invalid config.

---

## Event Model

**File:** `software/src/squid/core/events.py`

### Operation Events

```python
@dataclass(frozen=True)
class FluidicsOperationStarted(Event):
    """Emitted BEFORE driver method is called."""
    operation: str  # "flow", "prime", "wash", "empty_syringe"
    port: Optional[int] = None
    solution: Optional[str] = None
    volume_ul: float = 0.0
    flow_rate_ul_per_min: float = 0.0

@dataclass(frozen=True)
class FluidicsOperationProgress(Event):
    """Emitted periodically during long operations (optional)."""
    operation: str
    progress_percent: float
    elapsed_seconds: float
    remaining_seconds: Optional[float] = None

@dataclass(frozen=True)
class FluidicsOperationCompleted(Event):
    """Emitted AFTER driver method returns (success or failure)."""
    operation: str
    success: bool
    error_message: Optional[str] = None
    duration_seconds: float = 0.0
```

### Incubation Events

```python
@dataclass(frozen=True)
class FluidicsIncubationStarted(Event):
    """Emitted when incubation period begins."""
    duration_seconds: float
    solution: Optional[str] = None

@dataclass(frozen=True)
class FluidicsIncubationProgress(Event):
    """Emitted every ~1 second during incubation."""
    elapsed_seconds: float
    remaining_seconds: float
    progress_percent: float

@dataclass(frozen=True)
class FluidicsIncubationCompleted(Event):
    """Emitted when incubation finishes (success) or is aborted (cancelled)."""
    completed: bool = True  # False if aborted
```

### Status Events

```python
@dataclass(frozen=True)
class FluidicsStatusChanged(Event):
    """Emitted when fluidics system status changes."""
    status: FluidicsOperationStatus  # Use enum directly for type safety
    current_port: Optional[int] = None
    current_solution: Optional[str] = None
    is_busy: bool = False
    error_message: Optional[str] = None
```

**Note:** All status fields use `FluidicsOperationStatus` enum directly (not `.value` string) for type consistency with `FluidicsStatus` dataclass.

### Event Serialization

If events are serialized to JSON (e.g., for logging, remote monitoring, or persistence), the `FluidicsOperationStatus` enum must be converted to its `.value` string. This is handled at the serialization boundary, not in the event itself:

```python
# In event serializer (e.g., JSON logger, remote publisher)
def serialize_event(event):
    data = asdict(event)
    # Convert enum fields to their string values for JSON compatibility
    if "status" in data and isinstance(data["status"], FluidicsOperationStatus):
        data["status"] = data["status"].value
    return json.dumps(data)
```

**Rules:**
1. **Events use enum directly** - For type safety and IDE support within Python code
2. **Serializers handle conversion** - `.value` conversion happens only when crossing the JSON boundary
3. **EventBus handlers** - Receive the enum directly; no conversion needed for in-process consumers

### Event Firing Rules

1. **FluidicsOperationStarted** - Fired BEFORE calling driver method
2. **FluidicsOperationCompleted** - Fired AFTER driver method returns, includes success/failure and duration
3. **FluidicsOperationProgress** - **Future/optional**: Not emitted in initial implementation; reserved for drivers that support progress callbacks. Initial version only emits Started/Completed.
4. **FluidicsStatusChanged** - Fired after operation completes or on error
5. **FluidicsIncubation*** - Managed by FluidicsService, not driver

---

## Thread Safety

### FluidicsService Locking

```python
class FluidicsService(BaseService):
    def __init__(self, driver, event_bus, mode_gate=None):
        super().__init__(event_bus, mode_gate)
        self._driver = driver
        self._lock = threading.RLock()  # Serializes all driver access
        self._abort_incubation = threading.Event()

    def flow_solution(self, port, volume_ul, flow_rate_ul_per_min, fill_tubing_with_port=None):
        # Events published OUTSIDE lock to avoid deadlock
        self.publish(FluidicsOperationStarted(...))

        with self._lock:  # All driver calls inside lock
            start_time = time.time()
            try:
                success = self._driver.flow_solution(...)
            except Exception as e:
                success = False
                error = str(e)

        # Events after lock released
        self.publish(FluidicsOperationCompleted(...))
        self._publish_status()
        return success
```

### Rules

1. **FluidicsService owns the lock** - All driver method calls are serialized
2. **Events published outside lock** - Prevents deadlock with EventBus handlers
3. **RLock for reentrant calls** - Allows nested service method calls if needed
4. **Driver assumed not thread-safe** - Never call driver directly, always through service

---

## Cancellation and Abort

### Abort Semantics

All long-running operations (flow, prime, wash) must be abortable:

```python
class FluidicsExecutor:
    def execute(self, step, cancel_token):
        try:
            cancel_token.check_point()  # Check before starting

            if step.command == FluidicsCommand.FLOW:
                return self._execute_flow(step, cancel_token)
            # ...

        except CancellationError:
            _log.info("Fluidics operation cancelled")
            self._fluidics_service.abort()  # ALWAYS call abort on cancellation
            raise

    def _execute_flow(self, step, cancel_token):
        # For long operations, we can't interrupt mid-flow
        # but we can check before starting
        cancel_token.check_point()
        return self._fluidics_service.flow_solution_by_name(...)
```

### Driver Abort Behavior

The `MERFISHFluidicsDriver.abort()` method:
1. Sets `is_aborted` flag on syringe pump
2. Sets `_current_status = FluidicsOperationStatus.ABORTED`
3. Current operation may complete (hardware doesn't support mid-operation abort)
4. Subsequent operations check `is_aborted` and return early
5. `reset_abort()` must be called before new operations (resets status to IDLE)

### Abort Recovery: Who Calls reset_abort()?

**The OrchestratorController is responsible for calling `reset_abort()`:**

- On **resume after pause**: Before resuming from checkpoint
- On **new experiment start**: Before first fluidics step
- After **user acknowledges abort**: Before allowing new operations

```python
# In OrchestratorController
def _on_resume_command(self, event):
    if self._fluidics_executor and self._fluidics_executor._fluidics_service:
        self._fluidics_executor._fluidics_service.reset_abort()
    # ... continue execution

def _start_experiment(self, ...):
    if self._fluidics_executor and self._fluidics_executor._fluidics_service:
        self._fluidics_executor._fluidics_service.reset_abort()
    # ... begin rounds
```

### Incubation Behavior

Incubation is the only operation that can be interrupted mid-execution.

**Locking behavior:** Incubation does NOT hold the service lock during the wait (since it doesn't call driver methods). However, it sets `_is_incubating = True` to signal busy state:

```python
def incubate(self, duration_seconds, ...):
    self._abort_incubation.clear()
    self._is_incubating = True  # Signal busy state
    self.publish(FluidicsIncubationStarted(...))

    try:
        while elapsed < duration_seconds:
            if self._abort_incubation.is_set():
                self.publish(FluidicsIncubationCompleted(completed=False))
                return False
            time.sleep(min(1.0, remaining))
            self.publish(FluidicsIncubationProgress(...))

        self.publish(FluidicsIncubationCompleted(completed=True))
        return True
    finally:
        self._is_incubating = False
```

**Concurrent operation prevention:** The `is_busy` property checks both incubation and driver state:

```python
# In FluidicsService
@property
def is_busy(self) -> bool:
    """True if driver is busy OR incubation is in progress."""
    if self._is_incubating:
        return True
    if self._driver:
        return self._driver.is_busy
    return False
```

**Rejection behavior:** Flow/prime/wash operations check `is_busy` and **reject immediately** (not block) if busy:

```python
def flow_solution(self, port, volume_ul, flow_rate_ul_per_min, ...):
    if self.is_busy:
        raise RuntimeError("Fluidics system is busy - operation rejected")
    # ... proceed with operation
```

This prevents operation queue buildup and makes concurrent access errors explicit.

### FluidicsExecutor Busy-Rejection Handling

When `FluidicsService` raises `RuntimeError("busy")`, the `FluidicsExecutor` handles it as follows:

```python
# In FluidicsExecutor._execute_flow()
def _execute_flow(self, step, cancel_token):
    cancel_token.check_point()
    try:
        return self._fluidics_service.flow_solution_by_name(...)
    except RuntimeError as e:
        if "busy" in str(e).lower():
            # Busy rejection is a transient error - log warning, return False
            _log.warning(f"Fluidics busy during {step.command}: {e}")
            return False  # Signal failure to orchestrator
        raise  # Re-raise other RuntimeErrors
```

**Rules:**
1. **No retry in executor** - Executor does not retry on busy; it returns `False`
2. **Orchestrator handles failure** - The `OrchestratorController` receives `False` and can decide to pause, retry at round level, or fail the experiment
3. **Logging** - Log at WARNING level to make concurrent access visible in logs
4. **No automatic retry policy** - Keeping executor simple; retry logic belongs in orchestrator

---

## Simulation Strategy

### Two Simulation Modes

1. **fluidics_v2 Simulation** - Uses `FluidControllerSimulation`, `SyringePumpSimulation` from fluidics_v2
2. **Pure Simulation** - `SimulatedFluidicsController` with no fluidics_v2 dependency

### Selection Logic

```python
def _build_fluidics_driver(self) -> Optional[AbstractFluidicsController]:
    if not getattr(_config, "RUN_FLUIDICS", False):
        return None

    # Config path rooted at software/ directory (not CWD-dependent)
    # application.py is at software/src/squid/application.py
    # parent chain: squid/ -> src/ -> software/
    software_dir = Path(__file__).parent.parent.parent  # software/

    # Config file based on FLUIDICS_APPLICATION setting (default: MERFISH)
    app_type = getattr(_config, "FLUIDICS_APPLICATION", "MERFISH").lower()
    config_path = software_dir / "configurations" / "fluidics" / f"{app_type}_config.json"
    if not config_path.exists():
        _log.warning(f"Fluidics config not found: {config_path}")
        return None

    if self._simulation:
        # Prefer pure simulation (no external dependency)
        from squid.backend.drivers.fluidics.simulation import SimulatedFluidicsController
        driver = SimulatedFluidicsController(str(config_path))
        driver.initialize()
        return driver

    # Real hardware - requires fluidics_v2 submodule
    try:
        from squid.backend.drivers.fluidics.merfish_driver import MERFISHFluidicsDriver
        driver = MERFISHFluidicsDriver(str(config_path), simulation=False)
        if driver.initialize():
            return driver
        _log.error("MERFISH fluidics driver failed to initialize")
        return None
    except ImportError as e:
        _log.error(f"fluidics_v2 submodule not available: {e}")
        _log.info("Falling back to pure simulation")
        from squid.backend.drivers.fluidics.simulation import SimulatedFluidicsController
        driver = SimulatedFluidicsController(str(config_path))
        driver.initialize()
        return driver
```

### Pure Simulation Driver

```python
class SimulatedFluidicsController(AbstractFluidicsController):
    """Simulation driver with no fluidics_v2 dependency.

    - Loads config for port mapping
    - Tracks state in memory
    - Simulates timing (optional delays)
    - Useful for tests and when submodule missing
    """

    def __init__(self, config_path: str, simulate_timing: bool = False):
        self._config_path = config_path
        self._simulate_timing = simulate_timing
        # ... state tracking
```

---

## Import/Path Handling

### Problem

The `fluidics_v2` submodule lives at `software/fluidics_v2/software/fluidics/`, which is not on the default Python path.

### Solution

In `application.py._build_fluidics_driver()`:

```python
def _build_fluidics_driver(self):
    # Add fluidics_v2 to path if available
    fluidics_v2_path = Path(__file__).parent.parent.parent / "fluidics_v2" / "software"
    if fluidics_v2_path.exists():
        import sys
        if str(fluidics_v2_path) not in sys.path:
            sys.path.insert(0, str(fluidics_v2_path))
            _log.debug(f"Added fluidics_v2 to path: {fluidics_v2_path}")

    # ... rest of driver creation
```

### Failure Behavior

If fluidics_v2 import fails:
1. Log error with specific import failure
2. Fall back to pure simulation driver
3. Continue application startup (fluidics not critical for other features)

---

## Resource Lifecycle

### Initialization

```
ApplicationContext.__init__()
    └── _build_services()
            └── _build_fluidics_driver()
                    ├── Add fluidics_v2 to sys.path
                    ├── Create driver (real or simulation)
                    └── driver.initialize()
                            ├── Load config
                            ├── Connect to hardware
                            └── Return True/False
```

### Shutdown

```
ApplicationContext.shutdown()
    └── _services.shutdown()
            └── FluidicsService.shutdown()
                    └── self._driver.close()
                            ├── Empty syringe to waste
                            ├── Close serial connections
                            └── Release resources
```

### FluidicsService Shutdown

```python
class FluidicsService(BaseService):
    def shutdown(self):
        super().shutdown()  # Unsubscribe from events
        if self._driver:
            try:
                self._driver.close()
            except Exception as e:
                _log.exception(f"Error closing fluidics driver: {e}")
```

---

## Validation and Guardrails

### MERFISHFluidicsConfig Validation

```python
class MERFISHFluidicsConfig:
    def __init__(self, config_path: str):
        with open(config_path) as f:
            self._config = json.load(f)
        self._validate()
        self._build_port_mapping()

    def _validate(self):
        # Required sections
        required = ["microcontroller", "syringe_pump", "selector_valves", "solution_port_mapping"]
        for key in required:
            if key not in self._config:
                raise ValueError(f"Missing required config section: {key}")

        # Syringe pump validation
        sp = self._config["syringe_pump"]
        if sp.get("volume_ul", 0) <= 0:
            raise ValueError("syringe_pump.volume_ul must be > 0")

        # Port mapping validation
        mapping = self._config["solution_port_mapping"]
        ports_used = list(mapping.values())
        if len(ports_used) != len(set(ports_used)):
            raise ValueError("Duplicate port numbers in solution_port_mapping")

        # Validate port range against explicit allowed ports
        # Note: Hardware may use per-valve numbering (1-10 on each) or sparse IDs.
        # We derive allowed_ports from config rather than assuming 1..sum(number_of_ports).
        allowed_ports = self._derive_allowed_ports()
        for solution, port in mapping.items():
            if port not in allowed_ports:
                raise ValueError(
                    f"Port {port} for '{solution}' not in allowed ports: {sorted(allowed_ports)}"
                )

    def _derive_allowed_ports(self) -> set[int]:
        """Derive allowed port numbers from selector_valves config.

        Supports two config styles:
        1. Explicit: "allowed_ports": [1, 2, 3, 25, 26, 27] - use directly
        2. Per-valve: "number_of_ports": {"0": 10, "1": 10, "2": 10}
           - Assumes contiguous 1-based IDs per valve
           - Valve 0: 1-10, Valve 1: 11-20, Valve 2: 21-30
        """
        sv = self._config.get("selector_valves", {})

        # Check for explicit allowed_ports first
        if "allowed_ports" in sv:
            return set(sv["allowed_ports"])

        # Fall back to deriving from number_of_ports
        allowed = set()
        offset = 0
        for valve_id in sorted(sv.get("valve_ids_allowed", [])):
            num_ports = sv.get("number_of_ports", {}).get(str(valve_id), 0)
            for i in range(1, num_ports + 1):
                allowed.add(offset + i)
            offset += num_ports

        return allowed
```

### Flow/Volume Limits

```python
def flow_solution(self, port, volume_ul, flow_rate_ul_per_min, ...):
    limits = self._config.get("limits", {})
    max_rate = limits.get("max_flow_rate_ul_per_min", 10000)
    min_rate = limits.get("min_flow_rate_ul_per_min", 1)
    max_vol = limits.get("max_volume_ul", 5000)

    if not (min_rate <= flow_rate_ul_per_min <= max_rate):
        raise ValueError(f"Flow rate {flow_rate_ul_per_min} out of range ({min_rate}-{max_rate})")
    if volume_ul > max_vol:
        raise ValueError(f"Volume {volume_ul} exceeds max {max_vol}")
```

### Unknown Solution Handling

```python
# In FluidicsService
def flow_solution_by_name(self, solution_name, volume_ul, flow_rate):
    port = self._driver.get_port_for_solution(solution_name)
    if port is None:
        available = list(self.get_available_solutions().keys())  # service method, not driver
        raise ValueError(
            f"Unknown solution '{solution_name}'. "
            f"Available: {available}"
        )
    return self.flow_solution(port, volume_ul, flow_rate)
```

---

## Implementation Phases

### Phase 1: Hardware Abstraction (ABC)

- Add `FluidicsOperationStatus` enum to `core/abc.py`
- Add `FluidicsStatus` dataclass
- Add `AbstractFluidicsController` ABC with full interface

### Phase 2: MERFISH Driver

- Create `backend/drivers/fluidics/` package
- Create `MERFISHFluidicsConfig` with validation
- Create `MERFISHFluidicsDriver` implementing ABC
- Handle fluidics_v2 imports with path manipulation

### Phase 3: Simulation Driver

- Create `SimulatedFluidicsController` with no fluidics_v2 dependency
- Support same config format for port mapping
- Optional timing simulation

### Phase 4: Configuration

- Create `configurations/fluidics/` directory
- Create `merfish_config.json` template with all required fields
- Document config schema

### Phase 5: Events

- Add all fluidics events to `core/events.py`
- Ensure frozen=True on all event dataclasses

### Phase 6: FluidicsService Update

- Rewrite to accept `AbstractFluidicsController`
- Add thread-safe locking (RLock)
- Add `flow_solution_by_name()` with validation
- Add `incubate()` with cancellation support
- Add `shutdown()` for driver cleanup
- Publish events before/after operations

### Phase 7: FluidicsExecutor Implementation

- Wire to FluidicsService
- Map FluidicsStep commands to service methods
- Always call `abort()` on CancellationError
- Check cancel_token before each operation

### Phase 8: Application Wiring

- Add `_build_fluidics_driver()` with path manipulation
- Add simulation fallback logic
- Register service in ServiceRegistry
- Wire shutdown in `ApplicationContext.shutdown()`

---

## Testing Strategy

### Unit Tests - Happy Path

- Config loading and port mapping
- Driver initialization (mocked hardware)
- Service method calls with mocked driver
- Event emission verification

### Unit Tests - Error Cases

- Missing config file → clear error
- Invalid config (missing fields, duplicate ports) → validation error
- Unknown solution name → ValueError with available solutions
- Volume/flow rate out of limits → ValueError
- Missing fluidics_v2 submodule → graceful fallback to simulation

### Unit Tests - Cancellation

- Abort during incubation → returns False, emits incomplete event
- CancellationError during flow → abort() called, exception propagated
- reset_abort() required before new operations

### Integration Tests

- Full protocol execution with simulation driver
- Round with fluidics + imaging coordination
- Checkpoint/resume with fluidics state
- **Missing submodule fallback**: Application starts successfully when `fluidics_v2` not initialized, falls back to pure simulation, logs indicate fallback occurred

### Manual Tests

- Run with `--simulation` flag, verify logs
- Verify event emission in UI (if wired)
- Test with real hardware when available
- Verify shutdown cleans up properly

---

## Files Summary

### New Files

| File | Purpose |
|------|---------|
| `backend/drivers/fluidics/__init__.py` | Package init, exports |
| `backend/drivers/fluidics/merfish_driver.py` | ABC implementation wrapping fluidics_v2 |
| `backend/drivers/fluidics/simulation.py` | Pure simulation driver |
| `configurations/fluidics/merfish_config.json` | Hardware config template |

### Modified Files

| File | Changes |
|------|---------|
| `core/abc.py` | Add AbstractFluidicsController, FluidicsStatus, FluidicsOperationStatus |
| `core/events.py` | Add all fluidics events (7 event classes) |
| `backend/services/fluidics_service.py` | Rewrite with ABC, locking, events, shutdown |
| `backend/controllers/orchestrator/fluidics_executor.py` | Implement real execution, abort handling |
| `application.py` | Add driver creation, path handling, shutdown wiring |

---

## Dependencies

- `fluidics_v2` submodule (optional - falls back to simulation)
- `_def.RUN_FLUIDICS` flag in configuration
- Serial port access for real hardware
