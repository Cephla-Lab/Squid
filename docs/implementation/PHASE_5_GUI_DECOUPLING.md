# Phase 5: GUI Decoupling

**Goal**: Move controller creation out of GUI into ApplicationContext.

**Impact**: Long-term stability, testability, crash isolation.

**Estimated Effort**: 2 weeks

**Note**: This is the largest refactor. Break into multiple PRs.

---

## Checklist

### Task 5.1: Create ApplicationContext class
- [ ] Create `software/squid/application.py`
- [ ] Create test file `software/tests/squid/test_application.py`
- [ ] Implement microscope building
- [ ] Test with simulation
- [ ] Commit: "Add ApplicationContext for dependency management"

### Task 5.2: Move controller creation to ApplicationContext
- [ ] Add Controllers dataclass
- [ ] Create LiveController in ApplicationContext
- [ ] Create StreamHandler in ApplicationContext
- [ ] Create MultiPointController in ApplicationContext
- [ ] Test all controllers
- [ ] Commit: "Move controller creation to ApplicationContext"

### Task 5.3: Update GUI to receive controllers
- [ ] Modify gui_hcs.py to accept Controllers
- [ ] Remove controller creation from GUI
- [ ] Update signal connections
- [ ] Test GUI functionality
- [ ] Commit: "Update GUI to receive pre-built controllers"

### Task 5.4: Update entry point
- [ ] Modify main_hcs.py to use ApplicationContext
- [ ] Remove controller creation from main
- [ ] Test full application startup
- [ ] Commit: "Use ApplicationContext in main entry point"

---

## Overview

The current architecture has GUI creating and owning all controllers:

```
main_hcs.py
  └── HighContentScreeningGui (creates everything)
        ├── Microscope (created here)
        ├── LiveController (created here)
        ├── MultiPointController (created here)
        └── ... 20+ more widgets and controllers
```

The new architecture separates concerns:

```
main_hcs.py
  └── ApplicationContext (creates everything)
        ├── Microscope
        └── Controllers
              ├── LiveController
              ├── MultiPointController
              └── ...
  └── HighContentScreeningGui (receives pre-built controllers)
        └── Displays state, sends user actions
```

---

## Task 5.1: Create ApplicationContext class

### Implementation File

**File**: `software/squid/application.py`

```python
"""
Application context for dependency management.

Centralizes creation of microscope and controllers, replacing the
pattern where GUI creates and owns everything.

Usage:
    context = ApplicationContext(simulation=True)
    gui = context.create_gui()
    gui.show()

    # Later:
    context.shutdown()
"""
from dataclasses import dataclass
from typing import Optional
import squid.logging
from control.microscope import Microscope
from control.core.live_controller import LiveController
from control.core.stream_handler import StreamHandler
from control.core.multi_point_controller import MultiPointController
from control.core.auto_focus_controller import AutoFocusController
from control.core.channel_configuration_mananger import ChannelConfigurationManager
from control.core.objective_store import ObjectiveStore


@dataclass
class Controllers:
    """
    Container for all controllers.

    This replaces the pattern where GUI has 20+ instance variables
    for different controllers.
    """
    live: LiveController
    stream_handler: StreamHandler
    multipoint: Optional[MultiPointController] = None
    autofocus: Optional[AutoFocusController] = None
    channel_config_manager: Optional[ChannelConfigurationManager] = None
    objective_store: Optional[ObjectiveStore] = None


class ApplicationContext:
    """
    Application-level context that owns all components.

    This replaces the pattern where GUI creates everything.
    Now: Application creates everything, GUI just displays.

    Example:
        # Create context
        context = ApplicationContext(simulation=True)

        # Create and show GUI
        gui = context.create_gui()
        gui.show()

        # When done
        context.shutdown()
    """

    def __init__(self, simulation: bool = False):
        """
        Initialize the application context.

        Args:
            simulation: If True, use simulated hardware
        """
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._simulation = simulation
        self._microscope: Optional[Microscope] = None
        self._controllers: Optional[Controllers] = None
        self._gui = None

        self._log.info(f"Creating ApplicationContext (simulation={simulation})")

        # Build components
        self._build_microscope()
        self._build_controllers()

    def _build_microscope(self):
        """Build the microscope from configuration."""
        self._log.info("Building microscope...")
        self._microscope = Microscope.build_from_global_config(
            simulation=self._simulation
        )
        self._log.info("Microscope built successfully")

    def _build_controllers(self):
        """Build all controllers with proper dependency injection."""
        self._log.info("Building controllers...")

        # Live controller
        live = LiveController(
            camera=self._microscope.camera,
            microcontroller=self._microscope.low_level_drivers.microcontroller,
            illumination_controller=self._microscope.illumination_controller,
        )

        # Stream handler
        stream_handler = StreamHandler(
            accept_new_frame_fn=lambda: live.is_live
        )

        # Channel configuration manager
        channel_config_manager = ChannelConfigurationManager(
            filename="channel_configurations.xml"  # TODO: make configurable
        )

        # Objective store
        objective_store = ObjectiveStore(
            objectives_dict=None,  # Uses global config
            default_objective=None,
        )

        self._controllers = Controllers(
            live=live,
            stream_handler=stream_handler,
            channel_config_manager=channel_config_manager,
            objective_store=objective_store,
        )

        self._log.info("Controllers built successfully")

    @property
    def microscope(self) -> Microscope:
        """Get the microscope instance."""
        if self._microscope is None:
            raise RuntimeError("Microscope not initialized")
        return self._microscope

    @property
    def controllers(self) -> Controllers:
        """Get the controllers container."""
        if self._controllers is None:
            raise RuntimeError("Controllers not initialized")
        return self._controllers

    def create_gui(self):
        """
        Create the GUI with pre-built controllers.

        Returns:
            HighContentScreeningGui instance
        """
        # Import here to avoid circular imports
        from control.gui_hcs import HighContentScreeningGui

        self._log.info("Creating GUI...")
        self._gui = HighContentScreeningGui(
            controllers=self._controllers,
            microscope=self._microscope,
        )
        self._log.info("GUI created successfully")
        return self._gui

    def shutdown(self):
        """Clean shutdown of all components."""
        self._log.info("Shutting down application...")

        if self._gui:
            self._gui.close()

        # Shutdown controllers
        if self._controllers:
            if self._controllers.live:
                self._controllers.live.stop_live()
            if self._controllers.stream_handler:
                self._controllers.stream_handler.stop()

        # Shutdown microscope
        if self._microscope:
            self._microscope.close()

        self._log.info("Application shutdown complete")
```

### Test File

**File**: `software/tests/squid/test_application.py`

```python
"""Tests for ApplicationContext."""
import pytest
from squid.application import ApplicationContext, Controllers


class TestApplicationContext:
    """Test suite for ApplicationContext."""

    def test_creates_microscope(self):
        """Should create microscope in simulation mode."""
        context = ApplicationContext(simulation=True)

        assert context.microscope is not None
        context.shutdown()

    def test_creates_controllers(self):
        """Should create all controllers."""
        context = ApplicationContext(simulation=True)

        assert context.controllers is not None
        assert context.controllers.live is not None
        assert context.controllers.stream_handler is not None
        context.shutdown()

    def test_shutdown_doesnt_crash(self):
        """Shutdown should complete without errors."""
        context = ApplicationContext(simulation=True)
        context.shutdown()  # Should not raise
```

### Commit

```bash
git add software/squid/application.py software/tests/squid/test_application.py
git commit -m "Add ApplicationContext for dependency management

Creates centralized ApplicationContext that builds microscope and
controllers, replacing the pattern where GUI creates everything.

Controllers are now built with explicit dependency injection,
making them testable and the system more maintainable.

Part of stability improvements - see docs/IMPROVEMENTS_V2.md Section 6.
"
```

---

## Task 5.2: Move controller creation to ApplicationContext

This task expands the `_build_controllers` method to include all controllers.

### Expanded Implementation

**Update** `software/squid/application.py`:

```python
def _build_controllers(self):
    """Build all controllers with proper dependency injection."""
    self._log.info("Building controllers...")

    # Live controller - manages live view
    live = LiveController(
        camera=self._microscope.camera,
        microcontroller=self._microscope.low_level_drivers.microcontroller,
        illumination_controller=self._microscope.illumination_controller,
    )

    # Stream handler - manages frame display
    stream_handler = StreamHandler(
        accept_new_frame_fn=lambda: live.is_live
    )

    # Channel configuration manager
    channel_config_manager = ChannelConfigurationManager(
        filename="channel_configurations.xml"
    )

    # Objective store
    objective_store = ObjectiveStore(
        objectives_dict=None,
        default_objective=None,
    )

    # Autofocus controller (if autofocus hardware is available)
    autofocus = None
    if self._microscope.autofocus_laser is not None:
        autofocus = AutoFocusController(
            camera=self._microscope.camera,
            stage=self._microscope.stage,
            autofocus_laser=self._microscope.autofocus_laser,
            illumination_controller=self._microscope.illumination_controller,
        )

    # MultiPoint controller (optional, for acquisitions)
    multipoint = None
    # MultiPointController requires more setup - see gui_hcs.py for full init

    self._controllers = Controllers(
        live=live,
        stream_handler=stream_handler,
        channel_config_manager=channel_config_manager,
        objective_store=objective_store,
        autofocus=autofocus,
        multipoint=multipoint,
    )

    self._log.info("Controllers built successfully")
```

### Commit

```bash
git commit -m "Move controller creation to ApplicationContext

Expands ApplicationContext to create all controllers:
- LiveController
- StreamHandler
- ChannelConfigurationManager
- ObjectiveStore
- AutoFocusController (conditional)

Each controller receives its dependencies via constructor injection
instead of creating them internally.
"
```

---

## Task 5.3: Update GUI to receive controllers

### Changes to gui_hcs.py

This is a large change. The key modifications:

1. **Change constructor signature** to receive pre-built controllers:

```python
class HighContentScreeningGui(QMainWindow):
    def __init__(
        self,
        controllers: Controllers,
        microscope: Microscope,
        *args, **kwargs
    ):
        super().__init__(*args, **kwargs)

        # Store pre-built references (don't create them)
        self.controllers = controllers
        self.microscope = microscope

        # Alias for backward compatibility (gradually remove)
        self.liveController = controllers.live
        self.streamHandler = controllers.stream_handler
        self.channelConfigManager = controllers.channel_config_manager
        self.objectiveStore = controllers.objective_store

        # Connect signals
        self._connect_signals()

        # Build UI (widgets only, not controllers)
        self._setup_ui()
```

2. **Remove all controller creation code** from GUI `__init__`:

```python
# REMOVE lines like:
# self.liveController = LiveController(...)
# self.streamHandler = StreamHandler(...)
```

3. **Connect to controller signals** for UI updates:

```python
def _connect_signals(self):
    """Connect to controller signals for UI updates."""
    # Live controller signals
    self.controllers.live.frame_ready.connect(self._on_frame_ready)
    self.controllers.live.live_started.connect(self._on_live_started)
    self.controllers.live.live_stopped.connect(self._on_live_stopped)

    # Stream handler signals
    self.controllers.stream_handler.frame_to_display.connect(self._update_display)

    # If multipoint exists
    if self.controllers.multipoint:
        self.controllers.multipoint.signals.acquisition_started.connect(
            self._on_acquisition_started
        )
        self.controllers.multipoint.signals.acquisition_finished.connect(
            self._on_acquisition_finished
        )
```

4. **Update widget creation** to not create controllers:

```python
def _setup_ui(self):
    """Set up UI widgets (not controllers)."""
    # Create widgets that display controller state
    self.camera_widget = CameraSettingsWidget(
        camera=self.microscope.camera,
        live_controller=self.controllers.live,
    )

    # Create acquisition widget (receives multipoint controller)
    self.acquisition_widget = AcquisitionWidget(
        multipoint_controller=self.controllers.multipoint,
    )
```

### Key Principle

The GUI should:
- **Receive** pre-built controllers
- **Connect** to their signals
- **Display** their state
- **Forward** user actions to them

The GUI should **NOT**:
- Create controllers
- Create hardware objects
- Own business logic

### Test

```bash
cd /Users/wea/src/allenlab/Squid/software
python main_hcs.py --simulation
# Verify:
# - Application starts
# - Live view works
# - Settings can be changed
# - Widgets display correct state
```

### Commit

```bash
git add software/control/gui_hcs.py
git commit -m "Update GUI to receive pre-built controllers

Major refactor of HighContentScreeningGui:
- Constructor now receives Controllers dataclass and Microscope
- All controller creation removed from GUI
- GUI now only displays state and forwards user actions
- Signal connections moved to dedicated _connect_signals() method

This decouples the GUI from controller creation, enabling:
- Easier testing (can provide mock controllers)
- Cleaner separation of concerns
- Better error isolation

Part of stability improvements - see docs/IMPROVEMENTS_V2.md Section 6.
"
```

---

## Task 5.4: Update entry point

### Changes to main_hcs.py

**New** `software/main_hcs.py`:

```python
#!/usr/bin/env python
"""
Main entry point for Squid High Content Screening application.
"""
import sys
import faulthandler

# Enable faulthandler for debugging hangs
faulthandler.enable()

from PyQt5.QtWidgets import QApplication
from squid.application import ApplicationContext


def main():
    """Main entry point."""
    app = QApplication(sys.argv)

    # Parse arguments
    simulation = "--simulation" in sys.argv

    # Create application context (builds everything)
    context = ApplicationContext(simulation=simulation)

    try:
        # Create and show GUI
        gui = context.create_gui()
        gui.show()

        # Run event loop
        return app.exec_()
    finally:
        # Clean shutdown
        context.shutdown()


if __name__ == "__main__":
    sys.exit(main())
```

### Benefits of This Structure

1. **Clear ownership**: ApplicationContext owns everything
2. **Clean shutdown**: `finally` block ensures cleanup
3. **Testable**: Can create ApplicationContext without GUI for testing
4. **Configurable**: Easy to add config file loading, command-line args

### Test

```bash
cd /Users/wea/src/allenlab/Squid/software
python main_hcs.py --simulation
# Verify full application lifecycle:
# - Startup
# - Live view
# - Acquisition (if available)
# - Clean shutdown (Ctrl+C or window close)
```

### Commit

```bash
git add software/main_hcs.py
git commit -m "Use ApplicationContext in main entry point

Replaces GUI-creates-everything pattern with:
1. ApplicationContext builds microscope and controllers
2. GUI receives pre-built controllers
3. Clean shutdown via context.shutdown()

This completes the GUI decoupling refactor.

Part of stability improvements - see docs/IMPROVEMENTS_V2.md Section 6.
"
```

---

## Incremental Migration Strategy

Since this is a large change, consider breaking it into smaller PRs:

### PR 1: Add ApplicationContext (no GUI changes)
- Create `squid/application.py`
- Create tests
- Don't modify GUI yet

### PR 2: Add Controllers dataclass
- Move one controller (e.g., LiveController) to ApplicationContext
- Update GUI to optionally receive it
- Keep backward compatibility

### PR 3: Move remaining controllers
- Move StreamHandler, ChannelConfigManager, etc.
- Update GUI incrementally

### PR 4: Update entry point
- Change main_hcs.py to use ApplicationContext
- Remove old initialization code

### PR 5: Clean up
- Remove backward compatibility shims
- Remove unused code
- Update tests

---

## Phase 5 Complete

After completing all tasks:

1. Run full test suite:
```bash
pytest --tb=short -v
```

2. Test application startup:
```bash
cd /Users/wea/src/allenlab/Squid/software
python main_hcs.py --simulation
```

3. Verify:
- Application starts without errors
- Live view works
- Acquisition works
- Clean shutdown (no hanging threads)

4. Check for remaining tight coupling:
```bash
# Search for controller creation in GUI
grep -n "LiveController\|StreamHandler\|MultiPointController" control/gui_hcs.py
# Should find only references, not instantiation
```

---

## Troubleshooting

### Import Errors

If you see circular import errors:
```python
# Use local imports in ApplicationContext
def create_gui(self):
    from control.gui_hcs import HighContentScreeningGui  # Local import
    ...
```

### Missing Signals

If signals aren't being emitted:
```python
# Ensure controllers expose signals properly
class LiveController:
    # Define signals as class attributes
    frame_ready = pyqtSignal(CameraFrame)
    live_started = pyqtSignal()
    live_stopped = pyqtSignal()
```

### Shutdown Hangs

If shutdown hangs:
```python
# Check for running threads
import threading
print(f"Active threads: {threading.enumerate()}")

# Add timeouts to cleanup
def shutdown(self):
    if self._controllers.live:
        self._controllers.live.stop_live()
        # Give thread time to stop
        time.sleep(0.1)
```

### Tests Fail

If tests fail after refactoring:
```python
# Create test fixtures with mock controllers
@pytest.fixture
def mock_controllers():
    return Controllers(
        live=Mock(spec=LiveController),
        stream_handler=Mock(spec=StreamHandler),
        ...
    )

def test_gui_creation(mock_controllers, mock_microscope):
    gui = HighContentScreeningGui(
        controllers=mock_controllers,
        microscope=mock_microscope,
    )
    # Test GUI behavior without real hardware
```
