# Squid Microscopy Package: Comprehensive Testing Strategy

## Goal
Keep Squid fully testable offline (simulated camera/stage/filter wheel/microcontroller) across services, controllers, and GUI widgets while staying resilient to future refactors.

---

## Current State (Refactored Architecture)

### Test infrastructure
- Directories: `tests/unit/`, `tests/integration/`, `tests/manual/`, `tests/data/`
- Fixtures (see `tests/conftest.py`): simulated camera (with streaming variant), filter wheel (1 or 2 wheels), SimSerial + Microcontroller, SimulatedStage and CephlaStage-on-simulated-microcontroller, full simulated Microscope, ApplicationContext, EventBus, matplotlib Agg backend, repo_root helper, qtbot stub, piezo stage
- Pytest config (`pyproject.toml`): markers `unit`, `integration`, `slow`, `qt`, `manual`; addopts `-v --tb=short -ra`; testpaths `tests`
- Headless guards: napari/pyqtgraph stubs are loaded in `conftest.py` to avoid heavy imports and cache writes

### Simulation & factories
| Component | Location | Notes |
|-----------|----------|-------|
| SimulatedCamera | `control/peripherals/cameras/camera_utils.py` | AbstractCamera implementation; ROI/binning/pixel format; streaming thread |
| SimulatedStage | `control/peripherals/stage/simulated.py` | AbstractStage implementation with limits, optional delays, busy flag helpers |
| SimSerial | `control/peripherals/stage/serial.py` | Simulates Cephla microcontroller protocol |
| SimulatedFilterWheelController | `control/peripherals/filter_wheel/utils.py` | Multi-wheel support, optional delays |
| Lighting sims | `control/peripherals/lighting/{xlight,dragonfly,ldi,nl5}.py` | Used via MicroscopeAddons |
| CellX_Simulation | `control/peripherals/lighting/cellx.py` | Still constructs SerialDevice → not fully offline |

Factory entrypoints:
```python
from control.peripherals.cameras.camera_utils import get_camera
from control.peripherals.stage.stage_utils import get_stage
from control.peripherals.filter_wheel.utils import get_filter_wheel_controller
from control.microscope import Microscope

cam = get_camera(config, simulated=True)
stage = get_stage(stage_config, microcontroller=None, simulated=True)
fw = get_filter_wheel_controller(filter_config, simulated=True)
scope = Microscope.build_from_global_config(simulated=True)
```

### Service layer and GUI wiring
- Services live in `squid/services/{camera_service,stage_service,peripheral_service}.py` with `BaseService` pub/sub plumbing in `squid/services/base.py` and registry in `squid/services/__init__.py`
- ApplicationContext (`squid/application.py`) now owns Microscope, controllers, services, and hands them to `HighContentScreeningGui`
- GUI widgets (camera settings, navigation, DAC, trigger, laser autofocus, wellplate) publish/subscribe through `squid.events.event_bus` and expect services rather than raw hardware

### Coverage snapshot
- Automated: config models, registry, events, logging, service-layer units (camera/stage/peripheral), simulated hardware basics (camera, stage, filter wheel, microcontroller), microscope construction, ApplicationContext creation, MultiPointController happy paths, stage position caching, manual OME-TIFF/spot-detection smoke tests
- Missing/weak: GUI-service wiring (CameraSettingsWidget, NavigationWidget, DAC/Trigger, StageUtils), service integration with real sims (event_bus → hardware → state events), MultiPointWorker tests are skipped, laser autofocus workflows, live/trigger lifecycles, flakiness controls (timeouts/waiters) not applied, CellX_Simulation still opens serial

---

## Near-Term Focus
1) Service + simulation integration
- EventBus-driven commands exercise real `SimulatedStage`, `SimulatedCamera`, `SimulatedFilterWheelController`, and `Microcontroller` to verify side effects and emitted events
- Async StageService paths (`move_to_loading_position`/`move_to_scanning_position` callbacks/threads) covered with deterministic wait helpers

2) GUI widgets on the service layer
- `control/widgets/camera/settings.py`: spinboxes dispatch Set* commands, Exposure/Gain/Binning/ROI events update UI
- `control/widgets/stage/navigation.py` and `control/widgets/stage/utils.py`: button clicks publish MoveStage* commands; StagePositionChanged updates labels; respects mm/µm conversions
- `control/widgets/hardware/{dac,trigger,laser_autofocus}.py`: DAC sliders emit SetDACCommand, trigger/live buttons send start/stop/fps commands, AF laser buttons publish turn on/off and reflect events
- Use `pytest-qt` + Xvfb; rely on simulated hardware or service mocks

3) Acquisition/live/autofocus workflows
- MultiPointController end-to-end in simulation (image count, file outputs, merge channels, abort/timeout paths)
- Unskip/refactor MultiPointWorker tests so they no longer depend on QApplication.processEvents quirks
- LiveController/StreamHandler start/stop streaming and trigger mode toggles with simulated camera
- Laser autofocus (reflection/contrast) minimal offline loop using simulated focus camera

4) Flakiness/infra
- Add autouse timeouts (`pytest-timeout`) and shared wait utilities; replace `time.sleep` in tests
- Normalize markers (`unit` vs `integration` vs `qt`) and skip expensive GUI tests when `QT_QPA_PLATFORM=offscreen`

5) Simulation gaps
- Make `CellX_Simulation` hardware-free or guard with importorskip so offline suites never open serial ports

---

## Fixtures Cheat Sheet
```python
# Camera/Stage
def test_camera(simulated_camera): ...
def test_stage(simulated_stage): ...
def test_cephla_stage(simulated_cephla_stage): ...

# Filter wheel & microcontroller
def test_filter(simulated_filter_wheel): ...
def test_micro(simulated_microcontroller): ...

# Full stack
def test_scope(simulated_microscope): ...
def test_app(simulated_application_context): ...

# Utilities
def test_event_bus(event_bus): ...
def test_matplotlib_backend(matplotlib_agg_backend): ...
```

---

## Running Tests
```bash
# Fast logic
pytest tests/unit

# Integration (simulated hardware)
pytest tests/integration

# GUI/Qt
pytest -m qt tests/ --xvfb

# Specific file
pytest tests/integration/squid/test_stage.py -v

# With coverage
pytest --cov=squid --cov=control tests/
```

---

## Markers (current)
- `unit`: no hardware side effects; pure logic/services with mocks
- `integration`: simulated hardware exercised
- `qt`: requires Qt event loop; run with xvfb in CI
- `slow`: >5s runtime
- `manual`: human verification

Standardize marker usage across new tests so selection works; consider adding `e2e` once long-running workflows land.

---

## GUI Testing Pattern
Use services + EventBus; avoid touching hardware directly in widgets.
```python
@pytest.mark.qt
def test_navigation_buttons_publish_events(qtbot, simulated_stage):
    from squid.services import StageService
    from squid.events import event_bus, MoveStageCommand
    from control.widgets.stage.navigation import NavigationWidget

    service = StageService(simulated_stage, event_bus)
    widget = NavigationWidget(service)
    qtbot.addWidget(widget)

    received = []
    event_bus.subscribe(MoveStageCommand, lambda e: received.append(e))

    widget.entry_dX.setValue(1.5)
    widget.move_x_forward()

    assert received and received[0].axis == "x" and received[0].distance_mm == 1.5
```

---

## Success Criteria
- [x] Unit/integration/manual test roots and shared simulated fixtures exist
- [x] SimulatedStage/SimulatedCamera/SimSerial/SimulatedFilterWheel used in factories and Microscope.build_from_global_config(simulated=True)
- [x] Service layer unit tests for camera/stage/peripheral cover event subscriptions and clamping
- [ ] Service layer integration tests prove EventBus commands move simulated hardware and emit Stage/Exposure/DAC events
- [ ] GUI widgets (camera settings, navigation, DAC/trigger, stage utils) covered with service-driven Qt tests
- [ ] MultiPointWorker skips removed and MultiPointController e2e verified offline (counts, files, abort)
- [ ] LiveController/trigger and laser autofocus have simulated-flow coverage
- [ ] Flakiness controls in place (timeouts fixture, wait helpers, no stray sleeps)
- [ ] Simulations are hardware-free (CellX_Simulation no serial) or safely skipped
