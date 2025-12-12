At a high level you need three families of functionality:

1. **Infrastructure** – config, event bus, task runner, logging.
2. **Hardware & data plane** – actors/services over `AbstractCamera/Stage/...` + frame streams.
3. **Domain controllers** – camera, stage, live, autofocus, acquisition, tracking, fluidics, etc., wired to the GUI via events.

Below I’ll enumerate the essential functions/APIs in each family and how they compose, mapped onto your current Squid structure and clean-architecture document.

---

## 1. Infrastructure / bootstrap

### 1.1. Configuration and bootstrap

These are mostly free functions that run in `main_hcs.py`, plus a small builder module. 

* `parse_cli_args(argv) -> CliOptions`
* `load_ini_config(path) -> RawConfigParser`
* `build_runtime_config(raw_config, cli_opts) -> RuntimeConfig`
  (Pydantic model aggregating camera, stage, illumination, fluidics, acquisition defaults, etc.)
* `build_hardware(runtime_config) -> Microscope`

  * Uses existing factories: `get_camera`, stage constructors, light sources, filter wheels.
* `build_services(microscope) -> ServicesBundle`

  * `CameraService(AbstractCamera)`
  * `StageService(AbstractStage)`
  * `IlluminationService(LightSource + microcontroller)`
  * `FilterWheelService(AbstractFilterWheelController)`
  * `PiezoService`, `PeripheralService` (DAC/TTL), `FluidicsService` etc.
* `build_event_bus() -> EventBus`
* `build_controllers(services, bus, runtime_config) -> ControllersBundle`
* `build_gui(bus, controllers, runtime_config) -> HighContentScreeningGui`
* `run_qt_app(gui) -> int`

These compose linearly in `main_hcs.py`:

```python
opts = parse_cli_args(sys.argv)
raw_cfg = load_ini_config(opts.config)
runtime_cfg = build_runtime_config(raw_cfg, opts)
microscope = build_hardware(runtime_cfg)
services = build_services(microscope)
bus = build_event_bus()
controllers = build_controllers(services, bus, runtime_cfg)
gui = build_gui(bus, controllers, runtime_cfg)
run_qt_app(gui)
```

### 1.2. Event bus

Minimal methods, strongly typed events (your `Event` dataclasses).

* `EventBus.subscribe(event_type, callback)`
* `EventBus.unsubscribe(event_type, callback)`
* `EventBus.publish(event)`

  * Synchronous, executes all callbacks on the caller’s thread.
* Optionally: `EventBus.post_from_thread(event)` for safe cross-thread dispatch into the GUI thread (wrapping Qt’s `invokeMethod`/`QMetaObject`).

Everything in the control plane (GUI ↔ controllers) goes through these three.

### 1.3. Task runner (for long-running work)

To standardize acquisition loops, tracking, etc.:

* `start_task(task: Task) -> TaskHandle`
* `cancel_task(handle: TaskHandle) -> None`
* `get_task_status(handle) -> TaskStatus`

Where `Task` is a small protocol with a `.run()` method, and `TaskStatus` is a dataclass with fields like `progress`, `message`, `done`, `error`. 

Controllers use this to run multi-point acquisition, long autofocus, tracking, etc., rather than rolling their own threading each time.

---

## 2. Hardware layer: actors + services

Here the essential functions are the *service* APIs the controllers call, backed by single-threaded actors that own `AbstractCamera`, `AbstractStage`, etc.

### 2.1. Camera actor + CameraService

**Actor** (internal):

* `CameraActor.run()` – event loop for commands:

  * `handle_start_stream(config)`
  * `handle_stop_stream()`
  * `handle_snap()` – blocking snap, returns `CameraFrame`.
  * `handle_set_exposure(ms)`
  * `handle_set_gain(...)`
  * `handle_set_roi(...)`
  * `handle_set_pixel_format(...)`
  * `handle_set_trigger_mode(mode)`
  * `handle_shutdown()`
* Inside, uses `AbstractCamera.start_streaming`, `stop_streaming`, `add_frame_callback`, etc.

**Service** (public API used by controllers):

* `CameraService.set_exposure_time(ms) -> float`
* `CameraService.set_gain(gain) -> float`
* `CameraService.set_roi(x, y, w, h) -> ROI`
* `CameraService.set_pixel_format(fmt) -> str`
* `CameraService.set_trigger_mode(mode: AcquisitionMode) -> AcquisitionMode`
* `CameraService.start_stream(config: LiveConfig) -> None`
* `CameraService.stop_stream() -> None`
* `CameraService.snap(config: SnapConfig) -> CameraFrame`
* `CameraService.get_capabilities() -> CameraCapabilities`
* `CameraService.get_state_from_hardware() -> CameraState` (for initial state sync)

All of these enqueue commands to `CameraActor` and wait on a reply (if needed).

### 2.2. Stage actor + StageService

**Service functions** reflecting stage operations:

* `StageService.move_to(x_mm, y_mm, z_mm | None) -> None`
* `StageService.move_by(dx_mm, dy_mm, dz_mm | None) -> None`
* `StageService.home() -> None`
* `StageService.get_position() -> Pos`
* `StageService.stop() -> None`
* `StageService.set_velocity(v_mm_s) -> None`
* `StageService.set_limits(limits: StageLimits) -> None`

Backed by `StageActor` that exclusively owns `AbstractStage` and runs similar to `CameraActor`.

### 2.3. Other device services

Each of these can be stateless facades over their ABC / microcontroller.

* **IlluminationService**

  * `set_channel_power(channel: str, percent: float) -> None`
  * `set_shutter(open: bool) -> None`
  * `apply_channel_config(channel_cfg: ChannelConfig) -> None`
* **FilterWheelService**

  * `set_position(idx: int) -> None`
  * `get_position() -> int`
* **PiezoService**

  * `set_z(z_um: float) -> None`
  * `get_z() -> float`
* **PeripheralService (microcontroller DAC/TTL)**

  * `set_dac(channel: int, value_percent: float) -> None`
  * `set_digital(channel: int, high: bool) -> None`
* **FluidicsService** (wrapper around `fluidics_v2`)

  * `run_protocol(proto: FluidicsProtocol) -> None`
  * `abort_protocol() -> None`
  * Optional: `get_status() -> FluidicsStatus`

Controllers compose these services to implement higher-level behavior.

---

## 3. Data plane: frame streams and processing

You want real-time data separated from control. Essential functions:

### 3.1. CameraStream

* `CameraStream.subscribe(callback: Callable[[CameraFrame], None]) -> None`
* `CameraStream.unsubscribe(callback) -> None`
* Internal: `CameraStream.push(frame: CameraFrame) -> None` – called by `CameraActor` when a new frame arrives.

This replaces the current `StreamHandler` + `image_to_display` signal as the core primitive.

### 3.2. Derived stream utilities

Pure functions operating on streams:

* `throttle_stream(input_stream, max_fps) -> CameraStream`
* `scale_for_display(input_stream, scale: float) -> CameraStream`
* `crop_stream(input_stream, roi) -> CameraStream`
* `attach_overlay(input_stream, overlay_fn) -> CameraStream`

These are used by LiveController / display widgets / tracking, but are themselves just functions wiring streams together.

---

## 4. Controllers: essential APIs and composition

Controllers:

* Own their `*State` dataclass.
* Subscribe to command events.
* Call services.
* Publish `*StateChanged` and completion events.

### 4.1. CameraController

Commands handled (from GUI):

* `SetExposureCommand(exposure_ms)`
* `SetGainCommand(gain)`
* `SetBinningCommand(x, y)`
* `SetROICommand(x, y, w, h)`
* `SetPixelFormatCommand(fmt)`
* `SetAcquisitionModeCommand(mode)`
* `StartLiveCommand(config)`
* `StopLiveCommand()`
* `RequestCameraStateQuery()`

Internally:

* `CameraController._on_set_exposure(cmd) -> None`

  * calls `CameraService.set_exposure_time`, updates `CameraState`, publishes `CameraStateChanged`.
* Similarly for gain/binning/ROI/pixel format.
* `CameraController._on_start_live(cmd)`

  * delegates to `LiveController` (or sets a flag so LiveController configures streaming).
* `CameraController._sync_from_hardware() -> CameraState` on startup.

### 4.2. StageController

Handles:

* `MoveStageToCommand(x_mm, y_mm, z_mm | None)`
* `MoveStageByCommand(dx_mm, dy_mm, dz_mm | None)`
* `HomeStageCommand()`
* `RequestStageStateQuery()`

Core functions:

* `_on_move_to(cmd)` – calls `StageService.move_to`, updates `StageState`, publishes `StageStateChanged`.
* `_on_move_by(cmd)` – same pattern.
* `_on_home(cmd)` – home + update state.
* Optionally: background position polling using `StageService.get_position` if encoders are noisy. 

### 4.3. LiveController

Orchestrates live view; composes `CameraService` + `CameraStream` + display/AF/tracking consumers.

Commands:

* `StartLiveCommand(config: LiveConfig)`
* `StopLiveCommand()`
* `SetLiveConfigCommand(config: LiveConfig)`
* `RequestLiveStateQuery()`

Functions:

* `_on_start_live(cmd)`:

  * Configure camera via `CameraService` (exposure, ROI, trigger).
  * Start streaming via `CameraService.start_stream(config)`.
  * Subscribe to `CameraStream` and route frames:

    * to display stream (`CameraStream` → throttle/scale → GUI widget),
    * optional hooks: AF/Tracking.
  * Update `LiveState` and publish `LiveStateChanged`.
* `_on_stop_live()`:

  * Unsubscribe from `CameraStream`, stop streaming in `CameraService`.
  * Update `LiveState`.
* `_on_set_live_config(cmd)`:

  * Adjust throttling, display scaling, overlay flags, etc.

All real-time data composition happens here or in helper modules, not in the GUI or acquisition controllers.

### 4.4. AutofocusController (image-based)

Uses stage + camera + stream; domain logic mostly in pure functions.

Commands:

* `RunAutofocusCommand(config: AutofocusConfig, position: Pos | None)`
* `ClearFocusMapCommand()`
* `RequestAutofocusStateQuery()`

Functions:

* `_on_run_autofocus(cmd)`:

  * If `position` present: move stage there.
  * Plan scan: `plan_z_stack(config) -> list[z_mm]`.
  * For each z: `StageService.move_to`, `CameraService.snap` (or temporarily hijack the stream), compute focus metric via `compute_focus_score(frame, cfg)`.
  * Select best z: `select_best_focus(z_list, scores)`.
  * Move to best z and update `AutofocusState`; optionally update focus map for this XY.
  * Publish `AutofocusCompleted(z_mm=best_z)` and `AutofocusStateChanged`.
* `_on_clear_focus_map` – resets focus map model.
* Pure logic helpers:

  * `compute_focus_score(image, cfg) -> float`
  * `plan_z_stack(cfg: AutofocusConfig, current_z) -> list[float]`
  * `update_focus_map(map, pos_xy, z_best) -> FocusMap`

### 4.5. LaserAutofocusController

Wraps laser-AF camera + piezo + microcontroller.

Commands:

* `InitializeLaserAFCommand(config)`
* `SetLaserAFReferenceCommand()`
* `MeasureDisplacementCommand()`
* `MoveToTargetDisplacementCommand(target_um)`

Functions:

* `_on_initialize` – set up focus camera streaming, ROI, and reference pattern.
* `_on_set_reference` – capture current pattern as reference.
* `_on_measure` – grab frame, compute displacement via cross-correlation; update `LaserAFState`.
* `_on_move_to_target` – compute required z-offset and call `StageService` or `PiezoService`.

### 4.6. TrackingController

Consumes camera stream, controls stage and optional AF. 

Commands:

* `StartTrackingCommand(config: TrackingConfig)`
* `StopTrackingCommand()`
* `SetTrackingParamsCommand(config: TrackingConfig)`

Functions:

* `_on_start`:

  * Subscribe to `CameraStream` (maybe via LiveController).
  * Start periodic tracking task (`TaskRunner`), which:

    * For each nth frame: run `run_tracker(frame, state.tracker_type)` to compute ROI/centroid,
    * If enabled, call `StageService.move_by` to keep object centered,
    * Optionally call `AutofocusController` at intervals.
* `_on_stop` – unsubscribe from stream, cancel task.
* `_on_set_params` – update `TrackingState`.

Pure helpers:

* `run_tracker(frame, tracker_type, previous_state) -> TrackerResult`
* `compute_stage_offset(tracker_result, pixel_size, magnification) -> (dx_mm, dy_mm)`

### 4.7. AcquisitionController (multi-point / experiment runtime)

This is the main “experiment brain”.

Commands:

* `SetAcquisitionConfigCommand(config: AcquisitionConfig)`
* `StartAcquisitionCommand(config: AcquisitionConfig)` (optionally separate)
* `StopAcquisitionCommand()`
* `PauseAcquisitionCommand()`
* `ResumeAcquisitionCommand()`
* `RequestAcquisitionStateQuery()`

Core functions:

* `_on_set_config(cmd)` – store config in `AcquisitionState`.

* `_on_start(cmd)`:

  * Store config (if provided).
  * Spawn task via `TaskRunner` / thread: `_run_acquisition(self._state.config)`.
  * Update `AcquisitionState` and publish `AcquisitionStateChanged`.

* `_run_acquisition(config)`:

  * Generate positions: `positions = generate_positions(config.scan)` (using logic from `scan_coordinates.py`).
  * Loop over timepoints, positions, rounds, channels:

    * Check `is_running` / `is_paused` flags.
    * Move stage: `StageService.move_to(pos.x, pos.y, z=focus_map_z or config.default_z)`.
    * Optionally run AF: `AutofocusController.run_at_position(pos)` if `config.use_autofocus`.
    * For each channel:

      * `IlluminationService.apply_channel_config(channel_cfg)`.
      * Filter wheel: `FilterWheelService.set_position(channel_cfg.filter_index)`.
      * Camera config: `CameraService.set_exposure_time(...)`, etc.
      * Acquire frame(s): either `CameraService.snap(...)` or rely on streaming.
      * Build `CaptureInfo` (position, channel, z, timepoint) and call `enqueue_save_job(frame, capture_info)`.
    * After each tile/timepoint, update progress: `AcquisitionState.progress`, publish `AcquisitionStateChanged`.
  * On completion, set `is_running = False`, publish `AcquisitionStateChanged` and `AcquisitionCompleted(success=True)`.

* `_on_stop` – set `is_running=False`, signal task to terminate.

* `_on_pause` / `_on_resume` – flip flags so `_run_acquisition` waits or continues.

* Helper logic:

  * `generate_positions(scan_cfg) -> list[Pos]`
  * `compute_focus_z(pos, focus_map, default_z) -> float`
  * `build_capture_path(config, capture_info) -> Path`

### 4.8. FluidicsController

If fluidics is integrated:

Commands:

* `RunFluidicsProtocolCommand(protocol: FluidicsProtocol)`
* `AbortFluidicsProtocolCommand()`
* `RequestFluidicsStateQuery()`

Functions:

* `_on_run_protocol(cmd)` – call `FluidicsService.run_protocol`, update `FluidicsState`.
* `_on_abort` – call `FluidicsService.abort_protocol`.
* Potential: integrate with `AcquisitionController` (e.g., run protocols between rounds).

### 4.9. PeripheralController

Simple microcontroller wrapper for DAC/TTL, laser on/off, AF laser toggle, etc. 

Commands:

* `SetDACCommand(channel, value_percent)`
* `TurnOnAFLaserCommand()`
* `TurnOffAFLaserCommand()`

Functions:

* `_on_set_dac(cmd)` – `PeripheralService.set_dac`, publish `DACValueChanged`.
* `_on_turn_on_af_laser` – set digital line, maybe update `LaserAFState`.
* `_on_turn_off_af_laser` – same.

---

## 5. Image saving and job processing

You already have a job queue for image saving; essential functions are:

* `enqueue_save_job(frame: np.ndarray, info: CaptureInfo) -> None`

  * Called from `AcquisitionController` (and optionally from manual snapshots).
* `SaveImageJob.run()` – in worker process:

  * `write_image(path, frame, info)`
  * `write_ome_metadata(path, info)`
* `JobRunner.enqueue(job: Job) -> None`
* `JobRunner.run_forever()` – worker loop.

Pure helpers:

* `build_capture_info(config, pos, channel, z, t) -> CaptureInfo`
* `build_output_path(root, info) -> Path`
* `write_ome_tiff(path, frame, metadata) -> None`

---

## 6. GUI: widgets and their interaction with functions

The GUI side is conceptually straightforward: every widget needs two “functions”:

* `on_state_changed(event)` – update UI controls.
* `on_user_input(...)` – publish command event.

Examples:

* **CameraSettingsWidget**

  * Subscribes: `CameraStateChanged`.
  * Publishes: `SetExposureCommand`, `SetGainCommand`, `SetROICommand`, etc.
* **LiveControlWidget**

  * Subscribes: `LiveStateChanged`.
  * Publishes: `StartLiveCommand`, `StopLiveCommand`, `SetLiveConfigCommand`.
* **NavigationWidget**

  * Subscribes: `StageStateChanged`.
  * Publishes: `MoveStageToCommand`, `MoveStageByCommand`, `HomeStageCommand`.
* **AutoFocusWidget**

  * Subscribes: `AutofocusStateChanged`, `AutofocusCompleted`.
  * Publishes: `RunAutofocusCommand`, `ClearFocusMapCommand`.
* **WellplateMultiPointWidget / MultiPointWithFluidicsWidget**

  * Build `AcquisitionConfig` / `FluidicsProtocol` from UI.
  * Publishes: `SetAcquisitionConfigCommand`, `StartAcquisitionCommand`, `RunFluidicsProtocolCommand`, etc.
  * Subscribes: `AcquisitionStateChanged`, `AcquisitionCompleted`.
* **FocusMapWidget**

  * Subscribes: `AutofocusStateChanged` (for updates) and possibly a dedicated `FocusMapChanged` event.
  * Publishes: `ClearFocusMapCommand`, maybe manual z adjustments.

They all use the same pattern you already sketched: dumb, reactive widgets, no hardware calls.

---

## 7. How it all composes in real workflows

To make the composition concrete:

### Live imaging

1. User presses “Start live” → `StartLiveCommand(config)` on EventBus.
2. `LiveController._on_start_live`:

   * Configures camera via `CameraService`.
   * Starts streaming and subscribes to `CameraStream`.
   * Updates `LiveState` and emits `LiveStateChanged`.
3. `CameraActor` pushes frames into `CameraStream`.
4. LiveController routes frames:

   * To GUI display stream → widget updates via Qt signals, independent of EventBus.

### Multi-point with autofocus + fluidics

1. User defines protocol in `MultiPointWithFluidicsWidget` → `StartAcquisitionCommand(config)`.
2. `AcquisitionController._on_start`:

   * Stores config; spawns `_run_acquisition(config)` in a task.
3. `_run_acquisition`:

   * Uses `StageService`, `AutofocusController`, `IlluminationService`, `CameraService`, `FilterWheelService`, `FluidicsService` as needed for each position/round/channel.
   * Enqueues `SaveImageJob`s via `enqueue_save_job`.
   * Publishes `AcquisitionStateChanged` for progress, `AcquisitionCompleted` at the end.
4. GUI widgets update progress bar; Napari/pyqtgraph viewer shows latest frames.

### Laser autofocus

1. User initializes → `InitializeLaserAFCommand`.
2. `LaserAutofocusController` sets up focus camera stream and reference.
3. During acquisition, `AcquisitionController` can call `MoveToTargetDisplacementCommand` between tiles to keep focus lock.

---

This is essentially the complete “function-level API” you need under the clean architecture you outlined: typed events on the control plane; small, explicit service functions over hardware; simple controllers that glue events to services; and a separate data plane for frames. Everything else is details of implementation, but if you implement these surfaces cleanly, you’ll have a stable foundation to grow on.
