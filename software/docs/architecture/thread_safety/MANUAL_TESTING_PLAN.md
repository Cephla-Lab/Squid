# Manual Testing Plan - GUI Thread Safety

This document provides a comprehensive checklist for manually testing the GUI thread safety implementation. All tests should be run in **simulation mode** first, then repeated with **real hardware** if available.

## Prerequisites

```bash
cd software
python main_hcs.py --simulation
```

For each test, watch for:
- GUI freezes or hangs
- Crash dialogs or error popups
- Console errors (especially `QObject::*` warnings)
- Widgets not updating or showing stale data

---

## 1. Application Startup

### 1.1 Clean Startup
- [X] Launch application in simulation mode
- [X] Verify all widgets appear and are responsive
- [X] Verify no console errors about threading or Qt
- [X] Verify status bar shows "Simulation Mode" or similar
- [X] Verify camera settings widget shows default values

### 1.2 Live View Startup
- [X] Click "Live" button to start live view
- [X] Verify image appears in display
- [X] Verify live view is smooth (no stuttering)
- [X] Verify exposure/gain sliders update correctly
- [X] Click "Live" again to stop
- [X] Verify live view stops cleanly

---

## 2. Stage Movement

### 2.1 Manual Movement (Keyboard/Joystick)
- [ ] Use arrow keys or navigation widget to move stage
- [ ] Verify position display updates in real-time
- [ ] Verify navigation viewer (map) updates FOV position
- [ ] Move rapidly in multiple directions
- [ ] Verify no GUI freeze during rapid movement
- [ ] Verify position settles correctly when movement stops

### 2.2 Click-to-Move
- [ ] Click on navigation viewer to move stage
- [ ] Verify stage moves to clicked position
- [ ] Verify position display updates during move
- [ ] Verify FOV indicator moves to new position
- [ ] Click on live view image to move stage (if enabled)
- [ ] Verify same behavior as navigation viewer click

### 2.3 Go-to-Position
- [ ] Enter specific X/Y coordinates in navigation widget
- [ ] Click "Go" or press Enter
- [ ] Verify stage moves to position
- [ ] Verify position display shows target position

### 2.4 Movement While Live
- [X] Start live view
- [X] Move stage using various methods above
- [X] Verify live view continues without interruption
- [X] Verify no frame drops or stuttering during movement
- [X] Stop live view

---

## 3. Camera Settings

### 3.1 Exposure Time
- [ ] Start live view
- [ ] Change exposure time using slider
- [ ] Verify image brightness changes
- [ ] Verify exposure display updates
- [ ] Enter exposure time directly in spinbox
- [ ] Verify same behavior

### 3.2 Analog Gain
- [ ] With live view running
- [ ] Change analog gain using slider
- [ ] Verify image brightness changes
- [ ] Verify gain display updates

### 3.3 Binning (if available)
- [ ] Change binning mode
- [ ] Verify image resolution changes
- [ ] Verify no crash on binning change

### 3.4 ROI/Crop (if available)
- [ ] Set a region of interest
- [ ] Verify cropped image displays correctly
- [ ] Reset to full frame
- [ ] Verify full image displays

---

## 4. Illumination Control

### 4.1 Microscope Mode Changes
- [ ] Select different microscope modes (BF, Fluorescence, etc.)
- [ ] Verify illumination changes appropriately
- [ ] Verify exposure/gain settings update per mode
- [ ] Verify UI reflects current mode settings

### 4.2 Manual Intensity Control
- [ ] Adjust LED/laser intensity sliders
- [ ] Verify illumination intensity changes
- [ ] Verify live view reflects changes

### 4.3 Channel Switching During Live
- [ ] Start live view in one channel
- [ ] Switch to different channel/mode
- [ ] Verify smooth transition
- [ ] Verify no artifacts or frozen frames

---

## 5. Autofocus

### 5.1 Software Autofocus
- [ ] Navigate to a sample
- [ ] Start live view
- [ ] Click "Autofocus" button
- [ ] Verify autofocus sequence runs (Z sweep visible)
- [ ] Verify progress indicator updates
- [ ] Verify focus plane is found
- [ ] Verify live view continues after autofocus
- [ ] Verify no GUI freeze during autofocus

### 5.2 Autofocus Cancellation
- [ ] Start autofocus
- [ ] Click "Cancel" or "Stop" during operation
- [ ] Verify autofocus stops cleanly
- [ ] Verify stage returns to safe position
- [ ] Verify GUI remains responsive

### 5.3 Laser Autofocus (if available)
- [ ] Enable laser autofocus
- [ ] Verify laser autofocus initializes
- [ ] Lock to a focus plane
- [ ] Move stage in XY
- [ ] Verify focus tracking maintains position
- [ ] Verify piezo position display updates

---

## 6. Multi-Point Acquisition

### 6.1 Single-Position Snap
- [ ] Set up a single position
- [ ] Start acquisition
- [ ] Verify image is captured
- [ ] Verify image displays correctly
- [ ] Verify acquisition completes
- [ ] Verify file is saved (check output folder)

### 6.2 Multi-Position Grid
- [ ] Define a grid of positions (e.g., 3x3)
- [ ] Start acquisition
- [ ] Verify progress bar updates
- [ ] Verify region counter updates
- [ ] Verify FOV counter updates within region
- [ ] Verify images display during acquisition
- [ ] Verify navigation viewer shows scanned positions
- [ ] Let acquisition complete
- [ ] Verify all files saved correctly

### 6.3 Multi-Channel Acquisition
- [ ] Select multiple channels for acquisition
- [ ] Define positions
- [ ] Start acquisition
- [ ] Verify channel indicator updates during acquisition
- [ ] Verify all channels captured per position
- [ ] Verify display tabs show all channels

### 6.4 Z-Stack Acquisition
- [ ] Configure Z-stack parameters
- [ ] Start acquisition
- [ ] Verify Z position changes during acquisition
- [ ] Verify all Z planes captured
- [ ] Verify Z plot widget updates (if visible)

### 6.5 Time-Lapse Acquisition
- [ ] Configure time-lapse (multiple timepoints)
- [ ] Start acquisition
- [ ] Verify timepoint counter updates
- [ ] Let at least 2-3 timepoints complete
- [ ] Stop acquisition early if needed
- [ ] Verify partial data saved

### 6.6 Acquisition Cancellation
- [ ] Start a multi-position acquisition
- [ ] Click "Stop" or "Abort" during acquisition
- [ ] Verify acquisition stops within reasonable time
- [ ] Verify GUI remains responsive
- [ ] Verify partial data is preserved
- [ ] Verify system is ready for next acquisition

### 6.7 Large Acquisition (Stress Test)
- [ ] Define a large grid (10x10 or more)
- [ ] Multiple channels, multiple Z planes
- [ ] Start acquisition
- [ ] Monitor for several minutes
- [ ] Verify no memory leaks (check system memory)
- [ ] Verify no GUI freeze over time
- [ ] Verify consistent frame rate

---

## 7. Wellplate Navigation (if HCS mode)

### 7.1 Wellplate Selection
- [ ] Select wellplate format (96-well, 384-well, etc.)
- [ ] Verify navigation viewer updates grid
- [ ] Verify well labels appear correctly

### 7.2 Well Selection
- [ ] Click on a well in selector
- [ ] Verify stage moves to well center
- [ ] Verify position display updates
- [ ] Select multiple wells
- [ ] Verify selection is tracked

### 7.3 Well Calibration
- [ ] Open calibration dialog
- [ ] Perform 3-point calibration
- [ ] Verify calibration completes
- [ ] Navigate to a well
- [ ] Verify correct positioning

### 7.4 Wellplate Acquisition
- [ ] Select multiple wells
- [ ] Configure acquisition parameters
- [ ] Start acquisition
- [ ] Verify wells are visited in order
- [ ] Verify progress shows current well
- [ ] Verify data organized by well

---

## 8. Profile and Objective Changes

### 8.1 Profile Switching
- [ ] Change imaging profile
- [ ] Verify all settings update (exposure, gain, illumination)
- [ ] Verify microscope mode updates
- [ ] Start live view
- [ ] Verify new profile settings active

### 8.2 Objective Switching
- [ ] Change objective (if motorized or manual)
- [ ] Verify scale bar updates
- [ ] Verify FOV indicator size changes
- [ ] Verify pixel size calculations update
- [ ] Verify live view works with new objective

---

## 9. Peripheral Controls

### 9.1 Filter Wheel
- [ ] Change filter wheel position
- [ ] Verify wheel moves
- [ ] Verify position indicator updates
- [ ] Change during live view
- [ ] Verify smooth transition

### 9.2 DAC Controls (if available)
- [ ] Adjust DAC output values
- [ ] Verify hardware responds
- [ ] Verify display shows current values

### 9.3 Piezo Control (if available)
- [ ] Adjust piezo position manually
- [ ] Verify position display updates
- [ ] Verify movement is smooth
- [ ] Verify limits are enforced

---

## 10. Display and Napari

### 10.1 Image Display
- [ ] Verify live view displays correctly
- [ ] Adjust contrast/brightness
- [ ] Verify histogram updates
- [ ] Enable auto-level
- [ ] Verify image adjusts automatically

### 10.2 Napari Live View (if enabled)
- [ ] Start live view
- [ ] Verify Napari viewer shows frames
- [ ] Adjust display settings in Napari
- [ ] Click on Napari view to move stage (if enabled)
- [ ] Verify coordinate translation works

### 10.3 Display Tabs
- [ ] During multi-channel acquisition
- [ ] Verify tabs appear for each channel
- [ ] Click between tabs
- [ ] Verify correct channel displays

---

## 11. Error Handling

### 11.1 Simulated Errors
- [ ] If simulation supports error injection, trigger errors
- [ ] Verify error dialogs appear
- [ ] Verify GUI remains responsive after error
- [ ] Verify recovery is possible

### 11.2 Cancel Operations
- [ ] Start various operations (live, autofocus, acquisition)
- [ ] Cancel each operation type
- [ ] Verify clean cancellation
- [ ] Verify system ready for next operation

### 11.3 Rapid Operation Switching
- [ ] Start live view, stop, start again rapidly
- [ ] Switch between modes rapidly
- [ ] Start/stop acquisitions rapidly
- [ ] Verify no crashes or hangs

---

## 12. Application Shutdown

### 12.1 Clean Shutdown
- [ ] Close application via File > Exit or window close
- [ ] Verify all threads stop
- [ ] Verify no orphan processes
- [ ] Verify settings saved (check config files)

### 12.2 Shutdown During Operations
- [ ] Start a long acquisition
- [ ] Close application during acquisition
- [ ] Verify graceful shutdown
- [ ] Verify partial data preserved
- [ ] Verify no crash on exit

---

## 13. Concurrent Operations (Stress Tests)

### 13.1 Live + Movement
- [ ] Start live view
- [ ] Move stage continuously while live
- [ ] Verify both operations remain smooth
- [ ] Run for 2-3 minutes
- [ ] Verify no degradation over time

### 13.2 Live + Settings Changes
- [ ] Start live view
- [ ] Rapidly change exposure, gain, mode
- [ ] Verify updates apply correctly
- [ ] Verify no frame corruption

### 13.3 Acquisition + UI Interaction
- [ ] Start long acquisition
- [ ] Interact with other UI elements (navigation, settings)
- [ ] Verify acquisition continues uninterrupted
- [ ] Verify UI remains responsive

---

## Test Results Template

For each test session, record:

| Test ID | Description | Pass/Fail | Notes |
|---------|-------------|-----------|-------|
| 1.1     | Clean Startup | | |
| 1.2     | Live View Startup | | |
| ... | ... | ... | ... |

**Test Environment:**
- Date:
- Tester:
- OS:
- Python version:
- Simulation/Hardware:
- Branch/Commit:

**Issues Found:**
1. ...
2. ...

**Overall Result:** [ ] PASS / [ ] FAIL

---

## Hardware-Specific Tests

After passing all simulation tests, repeat key tests with real hardware:

- [ ] Tests 1.1, 1.2 - Startup with real camera
- [ ] Tests 2.1-2.4 - Real stage movement
- [ ] Tests 3.1-3.4 - Real camera settings
- [ ] Tests 4.1-4.3 - Real illumination
- [ ] Tests 5.1-5.3 - Real autofocus
- [ ] Tests 6.1-6.7 - Real acquisition (shorter versions)
- [ ] Tests 9.1-9.3 - Real peripherals
- [ ] Test 12.1-12.2 - Hardware shutdown

Pay special attention to:
- Timing differences between simulation and hardware
- Hardware-specific latency affecting UI responsiveness
- Real I/O operations that may block threads
