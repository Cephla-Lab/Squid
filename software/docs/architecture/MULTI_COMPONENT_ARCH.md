Below are two concrete plans. Plan 1 stays single‑process and focuses on eliminating the current deadlock/race surface by making **all control‑plane communication queued** (no synchronous callbacks, no “handler runs in publisher thread”). Plan 2 builds directly on Plan 1 and incrementally factors your existing code into an **MCS / Orchestrator / Data Storage / UI** split without a rewrite.

---

# Plan 1: Single‑process, message‑queue control plane (fix deadlocks + threading)

## A. Target threading contract (make it real)

Your own V3 target already states the correct model: **GUI thread**, **EventBus thread**, **Camera callback thread**, **Worker threads**; and “EventBus processes queued events; handlers must not block.” 

Right now, at least some of your handlers still execute in the publisher’s thread (e.g., `MultiPointController._on_start_acquisition` calls `run_acquisition` directly when it receives `StartAcquisitionCommand`).  Widgets publish that command from the GUI path. 

### Goal

Make these statements true everywhere:

1. **UI thread never runs controller logic** (beyond rendering, validation, formatting).
2. **No controller method is invoked directly from a worker thread via callbacks**; worker→controller communication is via queued events.
3. **All control-plane events are queued** (ordering is deterministic, reentrancy eliminated).
4. **Data-plane (frames) never touches the EventBus** (keep StreamHandler/QtStreamHandler).

---

## B. Step 1 — Convert the core EventBus into a queued dispatcher thread

Implement the “EventBus thread” you describe in V3: `publish()` enqueues; a dedicated thread drains the queue and invokes handlers. 

**Key property:** handlers do *not* run in the caller’s thread anymore, so UI publishes can’t accidentally execute backend logic synchronously.

Minimal API shape:

* `EventBus.publish(event)` → O(1), thread-safe enqueue.
* `EventBus.subscribe(event_type, handler)` → registers handler.
* `EventBus.start()` / `stop()` → lifecycle (or auto-start on init).

**Implementation constraints**

* The dispatch thread must never call Qt widgets.
* The dispatch thread must treat handlers as potentially fallible: catch/log exceptions so one bad handler doesn’t stall the queue.

This change aligns your implementation with the “bus thread” described in the V3 document. 

---

## C. Step 2 — Treat `UIEventBus` as *the only* bus that widgets see

You already instantiate a `UIEventBus` in `HighContentScreeningGui` (preferably from `ServiceRegistry`), with a fallback that constructs `QtEventDispatcher + UIEventBus` on the main thread. 

**Make this a hard rule:**

* Widgets receive only `UIEventBus`.
* Controllers/services use only the core `EventBus` (queued).

This eliminates “Qt touched from wrong thread” without requiring every handler to manually bounce to the UI thread.

---

## D. Step 3 — Remove direct worker→controller callbacks (replace with events)

This is a high-leverage fix for deadlocks and “second run” weirdness.

### Why

Your acquisition worker currently calls into controller-owned cleanup code indirectly via callback plumbing; your controller logs indicate `_on_acquisition_completed` can run on the acquisition thread.  That method performs nontrivial hardware operations (stop streaming, stage moves, live restart).

That is a classic race generator: worker thread and bus thread can both touch services + controller state.

### Change

Define one or two events that represent worker completion and worker progress, e.g.:

* `AcquisitionWorkerFinished(experiment_id, success, error)`
* `AcquisitionWorkerProgress(experiment_id, ...)`

Then:

* **Worker thread** publishes `AcquisitionWorkerFinished` (to core EventBus).
* **Controller** subscribes to that event and runs `_on_acquisition_completed` on the **EventBus thread**, not on the worker thread.

This preserves decoupling and reduces concurrent access to services/controllers.

You already publish “acquisition finished” from the worker via EventBus in places; keep doing that, but route *controller cleanup* through the bus thread rather than direct callback invocation.

---

## E. Step 4 — Enforce backend ownership of hardware state during acquisition (not just UI disabling)

Your GUI currently disables controls during acquisition (e.g., `toggleAcquisitionStart` disables tabs, click-to-move, hides well selector).  That helps, but it is not a correctness mechanism; other code paths can still publish movement/live commands.

Add a backend “resource gate”:

* Introduce a small `SystemMode` / `ResourceLease` in the control plane, owned by a controller (or a dedicated coordinator):

  * `mode = Idle | Live | Acquiring | Aborting`
* When `mode == Acquiring`:

  * ignore/deny `MoveStageCommand`, `StartLiveCommand`, etc. (or queue them for later, explicitly).
  * ensure live is stopped (backend-enforced, not only UI). This matches your V3 “Live path split” cleanup goal.

This step alone often eliminates “intermittent deadlocks” caused by two threads driving camera/stage concurrently.

---

## F. Step 5 — Ensure camera streaming lifecycle is deterministic across runs

You already have a very explicit comment in `MultiPointWorker`:

> stop camera streaming before removing callback … otherwise camera continues streaming after acquisition, causing issues on subsequent acquisitions (e.g., GUI freeze). 

Treat that as a contract:

* Stop streaming *in all exit paths* (success, abort, exception).
* Only one component “owns” streaming state at a time (resource gate above).
* Avoid situations where live restarts before acquisition teardown is fully complete.

This is orthogonal to the EventBus queue, but it’s directly relevant to “second tiled acquisition hangs.”

---

## G. Step 6 — Add an experiment/run ID to progress/state events; ignore stale events in UI

Your widget publishes `StartNewExperimentCommand(experiment_id=…)` then `StartAcquisitionCommand()`.  Your V3 event list includes `AcquisitionStarted(experiment_id, timestamp)`. 

Make **every** acquisition event carry `experiment_id` (or an internal `run_id`):

* `AcquisitionProgress(..., experiment_id=...)`
* `AcquisitionRegionProgress(..., experiment_id=...)`
* `AcquisitionStateChanged(..., experiment_id=...)`

Then in widgets:

* Only update UI if `event.experiment_id == current_experiment_id`.

This prevents a whole class of “late event from previous run locks the UI state” problems.

---

## H. Step 7 — Instrumentation to prove you fixed it

Add low-cost instrumentation:

* EventBus queue depth (periodically).
* Handler runtime warnings (e.g., > 20 ms).
* Log thread name at key transitions (you already do this in parts of multipoint).

This gives you a feedback loop for remaining stalls (bus thread blocked, worker stuck, camera callback blocked).

---

### Definition of Done for Plan 1

* `StartAcquisitionCommand` publication from widget returns immediately and never runs controller logic on GUI thread.
* No controller method is invoked on the acquisition worker thread via callbacks; worker→controller is event-only.
* During acquisition, backend rejects conflicting commands (move stage/live) regardless of GUI state.
* Multiple acquisitions back-to-back do not alter the camera streaming/callback state incorrectly. 

---

# Plan 2: Incremental split into MCS / Orchestrator / Data Storage / UI

This plan assumes Plan 1 is in place, because Plan 1 gives you a **transport-agnostic message boundary** (queued events + clean separation of control vs data plane), which is exactly what you need before splitting processes.

Storm-control’s multi-process structure (HAL + Dave + Steve + Kilroy communicating via TCP/JSON) is a good mental model for the end state.

## Phase 0 — Reframe your existing modules into the 4 roles (no code move yet)

Use your current architecture mapping (widgets ↔ EventBus/StreamHandler ↔ controllers ↔ services) as the starting point.

* **UI**: `gui_hcs.py` + `control/widgets/*` + napari widgets.
* **MCS (Microscope Control Service)**: services + controllers + acquisition workers (`MultiPointController`, `MultiPointWorker`, autofocus, stage/camera services).
* **Data Storage**: your job pipeline (`JobRunner`, `SaveImageJob`) is already the nucleus.
* **Orchestrator**: initially “missing” as a formal component; it exists implicitly in your acquisition UI/controller coupling. Build it explicitly.

**Deliverable:** a thin interface layer (Python protocols) for:

* `McsAPI` (command submission + state subscription)
* `StorageAPI` (submit frame + metadata; ack; health)
* `OrchestratorAPI` (start/stop protocol; resume; status)

No process split yet.

---

## Phase 1 — Make the *in-process* boundary look like IPC

### 1A. Make EventBus messages your “wire protocol”

Keep your typed events/commands as the canonical schema. Your V3 doc already treats them as a formal taxonomy.

Add:

* explicit `experiment_id/run_id` everywhere (as in Plan 1).
* a version field if you anticipate evolving message schemas.

### 1B. Build a transport abstraction

Create an interface like:

* `publish_command(cmd)`
* `subscribe_state(event_type, handler)`
* `publish_state(evt)` (MCS side)

Implement it first with your in-process queued EventBus from Plan 1. Later, you swap the transport to TCP/ZeroMQ without changing controllers/widgets.

This is precisely how you avoid “burn it down.”

---

## Phase 2 — Extract Data Storage first (lowest risk, immediate throughput/robustness wins)

You already have multiprocessing-capable job runners inside `MultiPointWorker`.  Promote that into a long-lived **Data Storage service**.

### 2A. In-process refactor (still one process)

* Move “save job” creation out of `MultiPointWorker` into a `StorageClient`.
* Worker calls `storage.submit(frame, metadata)` instead of directly scheduling `SaveImageJob`.

### 2B. Split into a separate process

* Run a `storage_process` that owns:

  * writer threads/processes
  * dataset index and atomic file writes
* Use shared memory to pass frames efficiently and a small metadata queue for descriptors (as discussed previously; do not pickle large arrays).

This reduces acquisition stalls from disk hiccups and makes “days/weeks” runs more restartable (storage can be restarted independently).

---

## Phase 3 — Introduce an Orchestrator (in-process), then split it

### 3A. Build Orchestrator in-process first

Implement a durable state machine that runs MERFISH-like protocols:

* fluidics step → wait/verify → autofocus check → acquisition step → QC → next

Persist progress (journal/checkpoint) so you can resume after crash.

This is storm-control Dave-like logic, but embedded first.

### 3B. Split Orchestrator into a separate process

Once stable:

* `orchestrator_process` communicates with MCS via the transport abstraction (now socket-based).
* Orchestrator persists state; if it dies it restarts and resumes by reconciling with MCS + storage.

---

## Phase 4 — Extract MCS as a headless service process

At this stage, your MCS becomes “HAL-like”:

* Owns all hardware services/controllers.
* Exposes command/state over sockets.
* Does not import Qt UI code.

Your current `HighContentScreeningGui` is already careful to instantiate UIEventBus after QApplication and treat it as GUI-side glue.  The next step is to ensure backend (MCS) has **no Qt dependencies** (watch for any Qt signal use in backend utilities).

---

## Phase 5 — UI becomes a client (optional but consistent with the end state)

When you are ready:

* UI connects to MCS for control-plane state and command submission.
* UI connects to the video data plane:

  * **local machine**: shared-memory ring buffer for frames
  * **remote UI**: compressed stream (downsampled preview), not full-fidelity raw

This preserves your core rule “frames never go through EventBus” even across processes.

---

### Definition of Done for Plan 2 (incremental)

* Storage is restartable independently; acquisition does not block on slow I/O.
* Orchestrator can resume a protocol after crash/restart using its durable log.
* MCS is the single “hardware authority” (no other process touches drivers).
* UI is replaceable (headless runs possible), but still can show live preview via shared memory or compressed stream.

---

## Practical sequencing recommendation

If you want the highest value-to-disruption order:

1. **Plan 1: queued EventBus thread + remove worker→controller callbacks + backend resource gating** (fix today’s deadlocks/races).
2. **Plan 2 Phase 2: Data Storage extraction** (improves throughput + robustness with minimal UI impact).
3. **Plan 2 Phase 3: Orchestrator in-process → separate process** (MERFISH-grade long-run robustness).
4. **Plan 2 Phase 4/5: headless MCS + UI as client** (optional, but completes the architecture).

If you want, I can make Plan 1 even more concrete by outlining the exact event(s) to introduce for “worker finished” and the specific callback paths in `MultiPointController`/`MultiPointWorker` that should be replaced first (those are the highest-risk cross-thread calls).
