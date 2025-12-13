What’s aligned already

  - Core EventBus is now queued/dispatch-threaded (src/squid/core/events.py), so UI publishes can’t run backend
    logic synchronously.
  - UIEventBus exists and most widgets use it for subscriptions (src/squid/ui/ui_event_bus.py), preserving Qt
    thread rules.
  - MultiPoint acquisition is event‑only: worker→controller via AcquisitionWorkerProgress/Finished, no callbacks
    (per your summary).
  - Controllers are mostly in ApplicationContext and UI/backend separation is improved.

  Mismatches to your desired architecture

  - Control plane now correctly has a single backend thread: the queued EventBus dispatch thread.
  - The lease-based coordinator has been removed and replaced with a minimal global mode gate.
  - Callbacks still exist:
      - AutoFocusController still accepts/calls finished_fn + image_to_display_fn (src/squid/mcs/controllers/
        autofocus/auto_focus_controller.py).
      - LaserAutofocusController still uses LaserAFCallbacks and calls them (src/squid/mcs/controllers/autofocus/
        laser_auto_focus_controller.py).
      - Two command types still carry callbacks: MoveStageToLoadingPositionCommand.callback and
        MoveStageToScanningPositionCommand.callback (src/squid/core/events.py:412, src/squid/core/events.py:421).
  - Some controllers still have “dual access paths” (direct hardware refs and services), which breaks strict
    backend encapsulation and threadsafety.

  Concrete failure modes still possible
    thread (EventBus dispatch) while long-running handlers block the queue. Locks help but don’t guarantee SDK
    thread safety, especially if any direct hardware paths remain.
  - Handlers that block control-plane threads. Stage moves and some controller setup paths are synchronous; they
    stall EventBus dispatch, delaying Stop/Abort and UI state.
  - FIFO queue can’t preempt. Stop commands still wait for long-running handlers to return.
  - Unsafe commands during acquisition if they bypass services. EventBus-driven stage/camera/peripheral commands
    are now backend-gated, but any remaining direct hardware paths can still violate mode.
  - Real bug in acquisition focus‑map branch. In MultiPointController.run_acquisition the bounds check is mis-
    indented so bounds can be undefined and the focus‑map generation block is partly unreachable (src/squid/ops/
    acquisition/multi_point_controller.py around the elif self.gen_focus_map / if not bounds area). That can crash
    or incorrectly abort acquisitions.
  - Focus‑map “quick path” bypasses resource gating. AutoFocusController focus‑map mode runs without
    _acquire_resources, so it can collide with acquisition/live.
  - Callback fields in commands can execute UI code off-thread. Stage service will invoke event.callback on
    EventBus thread, which can crash Qt or deadlock.

  How to simplify to exactly your architecture (recommended path)

  1. Collapse to a single queued control plane.
      - Completed: control-plane is now a single queued EventBus dispatch thread (“EventBus thread = backend control thread”).
  2. Replace coordinator with a minimal mode/resource gate.
      - Completed: implemented `GlobalModeGate` and service-level gating.
  3. Eliminate all callbacks, end-to-end.
      - Delete callback fields from MoveStageToLoadingPositionCommand and MoveStageToScanningPositionCommand;
        StageService publishes completion events instead.
      - Remove finished_fn/image_to_display_fn from AutoFocusController. Publish state/completion events (you
        already have AutofocusStateChanged) and send any images through StreamHandler.
      - Remove LaserAFCallbacks from LaserAutofocusController core. Use EventBus state events + StreamHandler for
        images; keep the Qt adapter only as a UI‑side bridge that subscribes to events.
  4. Make services the only hardware access path.
      - Drop direct camera, stage, etc. from controllers and require services (no fallback). You said
        compatibility doesn’t matter; this is the cleanest separation.
  5. Keep state machines, but remove duplicated flags.
      - Derive is_live, autofocus_in_progress, etc. from state; delete parallel booleans. Implement
        _publish_state_changed for multipoint or remove the legacy state publisher.
  6. Fix the concrete acquisition bug.
      - Re-indent/fix the bounds / focus‑map generation branch in MultiPointController.run_acquisition so it only
        runs when intended and never references undefined bounds.
  7. Finish UI decoupling phases that matter.
      - Convert remaining complex widgets + main_window.py to pure containers that only publish commands and
        render state.
      - Move NavigationViewer/ScanCoordinates logic into backend services/controllers with EventBus API.
  8. Small polish.
      - Downgrade UIEventBus per‑event INFO logs to DEBUG to avoid perf/log spam.

  If you want, next step I can draft a refactor checklist for the simplification work (removing actor/router/
  coordinator, callback purge, mode gate, and the bounds bug fix), and then start implementing it in order.
