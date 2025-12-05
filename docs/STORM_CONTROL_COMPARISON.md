# Storm-Control vs Squid Architecture Comparison

This document provides a detailed architectural comparison between storm-control (Zhuang Lab, Harvard) and Squid microscopy control software.

## Overview

| Aspect | Storm-Control | Squid |
|--------|---------------|-------|
| **Age** | ~15 years | Newer (~5 years) |
| **Architecture** | Message-passing broker | Direct composition |
| **Configuration** | XML with custom parser | INI files + Pydantic |
| **Hardware Abstraction** | Per-module, config-driven | ABC classes in `squid/abc.py` |
| **Inter-process** | TCP/IP (HAL↔Dave↔Steve) | Single process |
| **GUI coupling** | Separate via message broker | Tightly coupled |

---

## 1. Core Architecture Pattern

### Storm-Control: Centralized Message Broker

```
HalCore (broker)
  ├── Message Queue (ordered, reference counted)
  ├── Module Registry
  └── Response Router
       ↓ broadcasts to
    All HalModules
```

- All modules subclass `HalModule`
- Communication via `HalMessage` objects with reference counting
- Messages broadcast to all modules, each processes or ignores
- `Functionality` pattern for real-time data (camera frames bypass queue)

### Squid: Direct Composition

```
Microscope
  ├── camera (AbstractCamera)
  ├── stage (AbstractStage)
  ├── illumination_controller
  └── addons (MicroscopeAddons)
       ↓ passed directly to
    Controllers → GUI
```

- No central broker
- Objects passed directly via constructor injection
- Qt signals for async events
- Callbacks registered via `add_frame_callback()`

**Verdict**: Storm-control's broker provides decoupling but adds complexity and latency. Squid's direct composition is simpler but creates tight coupling (GUI creates controllers).

---

## 2. Hardware Abstraction

### Storm-Control

- No formal abstract base classes
- Each hardware type in `sc_hardware/` follows conventions but no enforcement
- Drivers expose a "Functionality" object for real-time use
- 40+ device drivers organized by vendor

### Squid

- Formal ABCs in `squid/abc.py`:
  - `AbstractCamera`, `AbstractStage`, `AbstractFilterWheelController`, `LightSource`
- Factory functions: `get_camera()`, `get_filter_wheel_controller()`
- 8+ camera vendors, 2 stage types, 4 filter wheel types

**Verdict**: Squid has cleaner, more modern abstraction. Storm-control's flexibility comes at cost of less consistency.

---

## 3. Configuration System

### Storm-Control

- **Format**: XML with inline type attributes
- **Parser**: Custom `StormXMLObject` in `parameters.py` (~900 lines)
- **Access**: `params.get("camera.exposure")` dot-notation
- **Issues**: No schema, no validation, "magic strings", undocumented

```xml
<camera1>
  <exposure_time type="float">0.1</exposure_time>
  <video_mode type="string">Mode7</video_mode>  <!-- magic string -->
</camera1>
```

### Squid

- **Format**: INI files + Pydantic models
- **Parser**: ConfigParser + `conf_attribute_reader()`
- **Access**: Direct attribute access on config objects
- **Issues**: 990-line `_def.py` global state pollution

```ini
[camera]
type = Toupcam
default_exposure_ms = 100
```

**Verdict**: Both have problems. Storm-control's XML is more verbose but hierarchical. Squid's INI is simpler but global `_def.py` is worse than storm-control's approach.

---

## 4. Multi-Application Design

### Storm-Control

- **Separate applications**: HAL, Dave, Steve, Kilroy
- **Communication**: TCP/IP with JSON messages
- **Use case**: HAL controls hardware; Dave automates sequences; Steve stitches images

### Squid

- **Single application**: `main_hcs.py` (High Content Screening)
- **All-in-one**: Acquisition, automation, live view in one process
- **Fluidics**: Git submodule, not separate process

**Verdict**: Storm-control's multi-process design is more robust (crash isolation) and allows distributed operation. Squid is simpler but monolithic.

---

## 5. Threading Model

### Storm-Control

- `runWorkerTask()` for long operations
- `QThreadPool` managed worker threads
- Worker timeout detection with `faulthandler`
- Reference counting prevents message loss

### Squid

- `threading.Thread` for workers (MultiPointWorker)
- `multiprocessing.Pool` for image saving
- Multiple threading models mixed (Event, Timer, Qt signals)
- Known race conditions documented in code

**Verdict**: Storm-control's thread management is more robust. Squid has threading safety issues noted in IMPROVEMENTS.md.

---

## 6. GUI Coupling

### Storm-Control

- GUI modules are just another `HalModule`
- Business logic decoupled via messages
- HAL can run headless with TCP control

### Squid

- `HighContentScreeningGui` creates and owns controllers directly
- 25+ widget instance variables on main window
- Business logic embedded in GUI class
- IMPROVEMENTS.md rates this "Poor"

**Verdict**: Storm-control is significantly better decoupled. Squid's GUI coupling is a documented problem.

---

## 7. Code Quality Metrics

| Metric | Storm-Control | Squid |
|--------|---------------|-------|
| Bare `except:` | 51 | 30+ |
| Largest file | ~1,100 lines (lockModes.py) | 10,671 lines (widgets.py) |
| Type hints | ~0% | ~12% |
| ABC usage | None | Good (`squid/abc.py`) |
| Tests | Basic | More comprehensive |

---

## 8. What Storm-Control Does Well

### Message Broker Pattern

Storm-control's `HalCore` provides true decoupling:

```python
# Any module can send a message
message = HalMessage(m_type="take movie", data={"length": 1000})
self.sendMessage(message)

# Any module can respond
def processMessage(self, message):
    if message.isType("take movie"):
        # Handle it
```

Benefits:
- GUI is just another module, not special
- Easy to add new modules without modifying existing code
- Can run headless via TCP control
- Crash in one module doesn't affect others

### Functionality Pattern

For real-time data (camera frames), storm-control bypasses the message queue:

```python
# Display module requests camera functionality
message = HalMessage(m_type="get functionality", data={"name": "camera1"})

# Then connects directly to signals
camera_functionality.newFrame.connect(self.displayFrame)
```

This provides:
- Low-latency frame delivery
- No message queue bottleneck for high-frequency data
- Clean separation of configuration (messages) vs data (signals)

### Multi-Process Architecture

Separate applications communicate via TCP:
- **HAL**: Hardware control (port 9000)
- **Dave**: Automation sequences (connects to HAL)
- **Steve**: Image stitching (connects to HAL)
- **Kilroy**: Fluidics control (port 9500)

Benefits:
- Crash isolation
- Can run on separate machines
- Independent development/testing

### Worker Thread Management

```python
def runWorkerTask(module, message, task, job_time_ms=None):
    """Run long task in thread pool with timeout detection."""
    ct_task = HalWorker(job_time_ms=job_time_ms, message=message, task=task)
    ct_task.hwsignaler.workerDone.connect(module.handleWorkerDone)
    ct_task.hwsignaler.workerError.connect(module.handleWorkerError)
    threadpool.start(ct_task)
```

- Centralized worker management
- Timeout detection with stack traces
- Reference counting ensures messages complete

---

## 9. What Squid Does Well

### Formal Abstract Base Classes

`squid/abc.py` defines clean hardware contracts:

```python
class AbstractCamera(ABC):
    @abstractmethod
    def start_streaming(self) -> None: ...

    @abstractmethod
    def stop_streaming(self) -> None: ...

    @abstractmethod
    def add_frame_callback(self, callback: Callable[[CameraFrame], None]) -> None: ...
```

Benefits:
- Enforced interface compliance
- IDE autocomplete and type checking
- Clear documentation of requirements

### Pydantic Configuration Models

```python
class CameraConfig(BaseModel):
    type: CameraType
    default_exposure_ms: float = 100

    class Config:
        extra = "forbid"  # Catch typos
```

Benefits:
- Type validation at load time
- Clear error messages
- IDE support

### Factory Pattern

```python
def get_camera(config: CameraConfig) -> AbstractCamera:
    """Factory for camera implementations."""
    if config.type == CameraType.TOUPCAM:
        return ToupcamCamera(config)
    elif config.type == CameraType.FLIR:
        return FlirCamera(config)
    # ...
```

Benefits:
- Centralized instantiation logic
- Easy to add new implementations
- Configuration-driven selection

### Callback Architecture

```python
@dataclass
class MultiPointControllerFunctions:
    signal_acquisition_start: Callable
    signal_new_image: Callable
    signal_coordinates: Callable
    # Pluggable callbacks for dependency injection
```

Benefits:
- Controllers don't know about GUI
- Easy to test with NoOp callbacks
- Flexible event subscription

---

## 10. Recommendations

### For Squid (Learning from Storm-Control)

1. **Decouple GUI from Controllers**
   - Create `ApplicationContext` that builds all components
   - GUI receives pre-built controllers via constructor
   - Controllers should not be created by GUI

2. **Consider Message Broker for Complex Workflows**
   - Would help with multi-step acquisitions
   - Would enable headless operation
   - Would improve testability

3. **Add Timeout Detection to Workers**
   - Use storm-control's `HalWorker` pattern
   - Add faulthandler for debugging hung threads

4. **Consider Multi-Process for Robustness**
   - Fluidics as separate process
   - Image processing as separate process

### For Storm-Control (Learning from Squid)

1. **Add Formal ABCs**
   - Create `AbstractCamera`, `AbstractStage`, etc.
   - Enforce interface compliance

2. **Use Pydantic for Configuration**
   - Replace raw XML parsing with validated models
   - Add clear error messages for config issues

3. **Add Type Hints**
   - Start with core modules (halMessage, halModule)
   - Use mypy for static analysis

4. **Add Factory Functions**
   - Explicit `get_camera()`, `get_stage()` factories
   - Centralize instantiation logic

---

## Summary Table

| Category | Winner | Notes |
|----------|--------|-------|
| **Architecture** | Storm-Control | Message broker provides decoupling |
| **Hardware Abstraction** | Squid | Formal ABCs are cleaner |
| **Configuration** | Neither | Both have significant issues |
| **Multi-process** | Storm-Control | Crash isolation, distributed operation |
| **Threading** | Storm-Control | More robust worker management |
| **GUI Decoupling** | Storm-Control | Squid has documented tight coupling |
| **Modern Python** | Squid | Type hints, Pydantic, dataclasses |
| **Code Organization** | Neither | Both have large files, poor naming |

---

## Conclusion

**Storm-control** has a more robust architecture but outdated implementation. Its message broker pattern provides excellent decoupling, and the multi-process design enables distributed operation and crash isolation.

**Squid** has more modern Python practices (type hints, ABCs, Pydantic) but significant architectural problems, especially GUI coupling and global configuration state.

A hybrid approach combining storm-control's message broker with Squid's ABCs and Pydantic configuration would capture the best of both systems.
