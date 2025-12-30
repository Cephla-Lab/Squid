# PR 15: Configuration Dialog

**Upstream Commit:** `decdcc7` - feat: Add Configuration dialog for editing settings via GUI (#389)
**Priority:** Medium
**Effort:** Large (+826 lines in widgets.py + tests)

## Summary

Add a comprehensive Configuration/Preferences dialog for editing application settings via the GUI.

## Upstream Changes

**Files Modified:**
- `.github/workflows/main.yml` (+3 lines)
- `software/control/gui_hcs.py` (+17 lines)
- `software/control/widgets.py` (+826 lines)
- `software/main_hcs.py` (-43 lines, moved to dialog)

**Files Created:**
- `software/tests/control/test_preferences_dialog.py` (+351 lines)

## arch_v2 Targets

| Upstream File | arch_v2 Location |
|---------------|------------------|
| `gui_hcs.py` | `src/squid/ui/main_window.py` |
| `widgets.py` (preferences) | `src/squid/ui/widgets/preferences/` (NEW directory) |
| `main_hcs.py` | `software/main_hcs.py` |
| `test_preferences_dialog.py` | `tests/unit/squid/ui/widgets/test_preferences.py` |

## Implementation Checklist

### Step 1: Analyze Upstream Dialog
- [x] Read upstream diff thoroughly (800+ lines)
- [x] Identify all settings categories
- [x] Document dialog structure
- [x] List all configuration options

### Step 2: Design arch_v2 Structure
- [x] Create `src/squid/ui/widgets/config.py` (added to existing module)
- [x] Plan widget decomposition:
  - `PreferencesDialog` - Main dialog with tabbed interface
  - Note: Kept as single class (upstream pattern) rather than splitting panels

### Step 3: Implement Dialog Components
- [x] Create main PreferencesDialog (QDialog, not EventBusDialog per upstream pattern)
- [x] Create settings tabs for each category (General, Acquisition, Camera, Advanced)
- [x] Implement settings persistence via ConfigParser
- [x] Add input validation and change detection

### Step 4: Integrate with Main Window
- [x] Add "Configuration..." menu item to Settings menu
- [x] Connect to openPreferences handler
- [x] Handle dialog show/hide via exec_()

### Step 5: Port Tests (REQUIRED)

**Test Files to Port:**
| Upstream Test | arch_v2 Location | Lines |
|---------------|------------------|-------|
| `tests/control/test_preferences_dialog.py` | `tests/unit/squid/ui/widgets/test_preferences_dialog.py` | +351 (NEW) |

- [x] Create `test_preferences_dialog.py`
- [x] Port all test cases
- [x] Use Qt test fixtures from conftest.py
- [x] Mock configuration loading/saving
- [x] Test each settings panel
- [x] Tests ported successfully

### Step 6: Testing
- [x] Open preferences dialog (import verified)
- [x] Test each settings category (unit tests ported)
- [x] Verify settings persist on restart (tested via unit tests)
- [x] Test validation and error handling (tested via unit tests)

## Dialog Structure (Expected)

```
Preferences Dialog
├── General Settings
│   ├── Default acquisition folder
│   ├── Auto-save settings
│   └── UI preferences
├── Camera Settings
│   ├── Default exposure
│   ├── Default gain
│   └── ROI defaults
├── Stage Settings
│   ├── Speed presets
│   └── Limits
├── Acquisition Settings
│   ├── Default Z-stack parameters
│   └── Default timepoint settings
└── Advanced Settings
    ├── Debug logging
    └── Performance options
```

## arch_v2 Pattern

```python
# preferences_dialog.py
class PreferencesDialog(EventBusDialog):
    def __init__(self, event_bus: UIEventBus, config: Config):
        super().__init__(event_bus)
        self.config = config
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Tab widget for categories
        self.tabs = QTabWidget()
        self.tabs.addTab(GeneralSettingsWidget(), "General")
        self.tabs.addTab(CameraSettingsWidget(), "Camera")
        # ...

        layout.addWidget(self.tabs)

        # OK/Cancel/Apply buttons
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply
        )
        layout.addWidget(self.button_box)
```

## Notes

- Large feature - consider splitting into multiple PRs
- May overlap with arch_v2 config system - coordinate carefully
- Settings should integrate with existing config models
- Follow EventBusDialog pattern for proper cleanup
