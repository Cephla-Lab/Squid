# Headless GUI Test Checklist

## Step 1: Coverage matrix (widgets -> commands -> services)
- [x] Enumerate GUI widgets created by HighContentScreeningGui
- [x] Map each widget action to EventBus commands
- [x] Map EventBus commands to backend services/controllers
- [x] Identify feature flags needed to enable each widget in tests

## Step 2: Headless test harness
- [x] Fixture: offscreen Qt app + simulated ApplicationContext
- [x] Patch modal dialogs (QMessageBox, QFileDialog)
- [x] Helper utilities: click/select/set widgets + wait for event bus drain
- [x] Event capture helpers (assert commands published + state changes)

## Step 3: Widget-level interaction tests
- [x] Live control (start/stop live, trigger mode/FPS, channel selection)
- [x] Camera settings (exposure, gain, pixel format, ROI, binning, temp, black level, auto WB)
- [x] Stage navigation (relative/absolute moves, home, zero, loading/scanning positions)
- [x] Illumination + DAC + trigger controls (trigger/illumination via live control; DAC covered)
- [x] Filter wheel + objective selection + piezo controls
- [x] Wellplate format + selection + calibration
- [ ] Multipoint widgets: flexible grid, wellplate grid, template, fluidics (flexible/wellplate/template done; fluidics pending)
- [x] Focus map + focus lock / autofocus widgets
- [x] Recording widget
- [x] Tracking widgets (if enabled)
- [x] Spinning disk / NL5 / confocal widgets (confocal + NL5 covered)

## Step 4: Workflow tests (end-to-end)
- [ ] Live imaging workflow: start live -> adjust camera -> stop live (skipped: headless timeout)
- [x] Stage navigation workflow: move around -> click-to-move -> verify position
- [x] Grid imaging workflow: regular/irregular grid -> acquisition -> stop
- [x] Focus lock workflow: set reference -> enable -> acquisition
- [x] Change camera settings mid-live and verify effect
- [x] Recording workflow: start/stop and verify output state

## Step 5: Boundary/error behavior
- [x] Out-of-range camera settings clamp or error
- [x] Invalid grid/ROI inputs handled without silent success
- [x] Disabled controls do not emit commands
- [x] Verify tests avoid stubbing core logic paths (no cheating)
