# Testing Implementation Checklist

Actionable steps to align tests with the refactored service-layer/GUI architecture while keeping everything runnable offline.

---

## Completed Foundation
- [x] SimulatedStage lives at `control/peripherals/stage/simulated.py` and implements `AbstractStage` with limits/busy helpers
- [x] `control/peripherals/stage/stage_utils.get_stage` returns `SimulatedStage` when `simulated=True`
- [x] SimulatedCamera/SimSerial/SimulatedFilterWheel wired through factories and `Microscope.build_from_global_config(simulated=True)`
- [x] Shared fixtures for simulated hardware/microscope/app context/event bus in `tests/conftest.py`
- [x] ServiceRegistry + BaseService in `squid/services` with unit tests for camera/stage/peripheral services
- [x] Pytest config uses `tests` testpath and markers (`unit`, `integration`, `slow`, `qt`, `manual`)

---

## Phase 1: Service + Simulation Integration
- [ ] Add integration tests that run CameraService against `SimulatedCamera` and assert exposure/gain/binning/ROI/pixel-format changes publish events (`tests/integration/squid/services/test_camera_service_integration.py`)
- [ ] Add StageService + SimulatedStage integration tests verifying Move/MoveTo/Home/Zero commands via EventBus update stage position and emit `StagePositionChanged` (`tests/integration/squid/services/test_stage_service_integration.py`)
- [ ] Cover async StageService paths: `move_to_loading_position` / `move_to_scanning_position` using callbacks/threaded_operation_helper without blocking hangs
- [ ] Add PeripheralService + simulated Microcontroller integration tests for DAC and trigger commands publishing/receiving `DACValueChanged` (`tests/integration/squid/services/test_peripheral_service_integration.py`)

---

## Phase 2: GUI Widgets on Services
- [ ] CameraSettingsWidget (`control/widgets/camera/settings.py`): value changes publish SetExposure/SetGain/SetROI/SetBinning/SetPixelFormat; UI updates on ExposureTimeChanged/BinningChanged/ROIChanged events (`tests/qt/control/widgets/test_camera_settings_widget.py`)
- [ ] NavigationWidget & StageUtils: button clicks emit MoveStage* commands; labels refresh from `StagePositionChanged`; mm↔µm conversions honored (`tests/qt/control/widgets/test_navigation_widget.py`)
- [ ] DACControWidget: sliders/spinboxes emit `SetDACCommand`; `DACValueChanged` reflects back into controls without signal loops (`tests/qt/control/widgets/test_dac_widget.py`)
- [ ] TriggerControlWidget: toggling live publishes start/stop and SetCameraTriggerFrequency; signals fire correctly (`tests/qt/control/widgets/test_trigger_widget.py`)
- [ ] Run GUI tests under `pytest -m qt --xvfb` with simulated services/fixtures

---

## Phase 3: Acquisition/Live/Autofocus Workflows
- [ ] Expand MultiPointController simulated e2e: image count matches config, coordinates.csv written, merge-channels path covered, abort mid-run handled cleanly (`tests/integration/control/test_MultiPointController.py` or new e2e file)
- [ ] Unskip/refactor `tests/integration/control/test_MultiPointWorker.py` so it passes without relying on `QApplication.processEvents()` quirks
- [ ] LiveController/StreamHandler + simulated camera: start/stop streaming, trigger mode switches, callbacks invoked (`tests/integration/control/test_live_controller.py`)
- [ ] Minimal laser autofocus loop with simulated focus camera (reflection + contrast modes) verifies reference image usage and safe shutdown (`tests/integration/control/test_laser_autofocus.py`)

---

## Phase 4: Flakiness & Infra
- [ ] Add autouse timeout fixture (unit/integration/qt/e2e tiers) using `pytest-timeout`; update `pyproject.toml` dev deps
- [ ] Introduce wait helpers (e.g., `wait_for_condition`, `DeterministicEventWaiter`) in `tests/tools.py` or a new `tests/fixtures/waiters.py`; replace `time.sleep` in tests
- [ ] Add isolation fixtures for EventBus and `control._def` state where tests mutate globals
- [ ] Ensure GUI tests skip cleanly when `QT_QPA_PLATFORM=offscreen`

---

## Phase 5: Simulation Gaps
- [ ] Make `CellX_Simulation` hardware-free (no SerialDevice) or guard with `pytest.importorskip("serial")` so offline suites never open serial ports (`control/peripherals/lighting/cellx.py`)
- [ ] Add a smoke test proving `Microscope.build_from_global_config(simulated=True)` uses only simulated peripherals even when optional addons are enabled (`tests/integration/control/test_microscope.py`)

---

## Phase 6: Markers/Config/CI
- [ ] Tag new tests with `unit`/`integration`/`qt` consistently; add `e2e` marker to `pyproject.toml` when long-running workflows land
- [ ] Add `pytest-xvfb` usage guidance to docs/CI; fail fast if xvfb not available for `-m qt`
- [ ] Optionally wire a CI job to run `pytest -m "unit or integration"` and a separate `-m qt --xvfb` if needed

---

## Verification
- [ ] `pytest tests/unit` and `pytest tests/integration` pass offline
- [ ] GUI suite passes with `pytest -m qt --xvfb` in headless mode
- [ ] MultiPointController/Worker tests pass without skips
- [ ] Live/autofocus workflow tests pass using only simulated hardware
- [ ] No test touches real serial/USB/network; simulations are safe by default
- [ ] Flakiness controls active (timeouts + waiters) and no lingering `time.sleep` calls

---

## Files to Create (new tests/utilities)
| File | Purpose |
|------|---------|
| `tests/integration/squid/services/test_camera_service_integration.py` | CameraService + SimulatedCamera integration via EventBus |
| `tests/integration/squid/services/test_stage_service_integration.py` | StageService + SimulatedStage integration, loading/scanning coverage |
| `tests/integration/squid/services/test_peripheral_service_integration.py` | PeripheralService + simulated Microcontroller integration |
| `tests/qt/control/widgets/test_camera_settings_widget.py` | Service-driven CameraSettingsWidget Qt tests |
| `tests/qt/control/widgets/test_navigation_widget.py` | Navigation/StageUtils widget Qt tests |
| `tests/qt/control/widgets/test_dac_widget.py` | DAC widget command/event loop coverage |
| `tests/qt/control/widgets/test_trigger_widget.py` | Trigger control widget coverage |
| `tests/integration/control/test_live_controller.py` | LiveController/StreamHandler with simulated camera |
| `tests/integration/control/test_laser_autofocus.py` | Simulated laser autofocus workflow |
| `tests/fixtures/waiters.py` (or extend `tests/tools.py`) | Deterministic wait helpers for replacing sleeps |

---

## Files to Modify (existing)
| File | Change |
|------|--------|
| `tests/integration/control/test_MultiPointWorker.py` | Remove skips/refactor to pass with simulated Qt |
| `tests/integration/control/test_MultiPointController.py` | Add file-output/abort/merge-channel coverage |
| `tests/conftest.py` | Add timeout/isolation fixtures; expose wait helpers if shared |
| `tests/tools.py` | Add wait utilities if not creating a dedicated fixture module |
| `pyproject.toml` | Add `pytest-timeout` (dev), `e2e` marker (when needed), ensure `pytest-xvfb` usage documented |
| `control/peripherals/lighting/cellx.py` | Make `CellX_Simulation` hardware-free or skip when serial unavailable |
