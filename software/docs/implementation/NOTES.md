What’s aligned already

  - Core EventBus is now queued/dispatch-threaded (src/squid/core/events.py), so UI publishes can’t run backend
    logic synchronously.
  - UIEventBus exists and most widgets use it for subscriptions (src/squid/ui/ui_event_bus.py), preserving Qt
    thread rules.
  - MultiPoint acquisition is event‑only: worker→controller via AcquisitionWorkerProgress/Finished, no callbacks
    (per your summary).
  - Controllers are mostly in ApplicationContext and UI/backend separation is improved.

  Mismatches to your desired architecture

  - You now have two control-plane threads: EventBus dispatch thread and BackendActor thread (src/squid/core/
    actor/*, wired in src/squid/application.py). That’s not in your diagram, and it reintroduces cross‑thread
    service access.
  - ResourceCoordinator leasing is heavier than Plan 1’s “simple mode gate,” and it doesn’t actually block unsafe
    commands at the service level. Services still execute moves even if acquisition holds leases.
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
    thread while services also handle other commands on EventBus thread (e.g., StageService, CameraService). Locks
    help but don’t guarantee SDK thread safety, especially if any direct hardware paths remain.
  - Handlers that block control-plane threads. Stage moves and some controller setup paths are synchronous; they
    stall EventBus dispatch (and/or BackendActor), delaying Stop/Abort and UI state.
  - Priority queue can’t preempt. Stop commands jump the queue but still wait for long-running handlers to return.
  - Unsafe commands during acquisition. Because services don’t consult coordinator/mode, a widget publishing
    MoveStageCommand can move the stage mid-acquisition.
  - Lease revocation isn’t enforced. If timeouts are enabled later, watchdog revokes leases but controllers don’t
    react, so two owners could drive hardware.
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
      - Remove BackendActor and BackendCommandRouter entirely and wire controllers back to core EventBus
        subscriptions. The queued EventBus already enforces “UI thread never runs controller logic.”
      - This restores the simple “EventBus thread = backend control thread” model you want.
  2. Replace ResourceCoordinator with a minimal mode/resource gate.
      - Implement a tiny SystemMode/GlobalModeService (Idle | Live | Acquiring | Aborting) owned on EventBus
        thread.
      - Controllers set mode when starting/stopping long ops.
      - Services (or a single GateController subscribed early) reject/ignore unsafe commands when mode disallows
        them. This fixes Step 4 without leasing/UUIDs/watchdog complexity.
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