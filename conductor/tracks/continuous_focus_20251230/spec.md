# Specification: Continuous Closed-Loop Laser Autofocus

## 1. Overview
This feature introduces a continuous, closed-loop feedback mode for the laser autofocus system. It enables active Z-piezo adjustment in real-time (even during XY stage movement) to maintain focus. The existing Laser Autofocus UI will be enhanced to support this mode, featuring a live feed from the laser AF camera with data overlays, bar indicators for key metrics, and real-time status controls.

## 2. Functional Requirements

### 2.1 Continuous Feedback Loop
- **Active Compensation:** The system must adjust the Z-piezo position in real-time based on laser feedback, specifically while the XY stage is in motion.
- **Lock Logic:**
  - Continuously monitor the focus error signal.
  - If error exceeds a defined threshold, correct immediately.
  - Maintain a specific "target" Z offset.

### 2.2 Acquisition Integration
- **Pre-Acquisition Check:** Verify focus is "Locked" (error within bound) before acquisition.
- **Failure Handling:**
  - **Retry:** Attempt to re-stabilize for a configurable timeout if unstable.
  - **Abort:** Abort acquisition and notify user if timeout expires without a lock.

### 2.3 Real-Time Feedback & Metrics
- **Correlation/Quality:** Confidence metric of the laser reflection.
- **Z-Error:** Difference between current and target Z position.
- **Z-Position:** Current absolute Z-piezo position.
- **Lock Status:** `Locked`, `Searching`, or `Lost`.

## 3. User Interface Requirements

### 3.1 Integration into Existing Laser AF Widget
- **Target File:** `software/src/squid/ui/widgets/hardware/laser_autofocus.py`
- **Live Display with Overlay:**
  - Option to show a small, live video feed from the laser AF camera.
  - **Overlay:** Superimpose the calculated fit/profile on the live video to visualize the detection algorithm's performance.
- **Metric Bars:**
  - **Piezo Position Bar:** Visual indicator of the current Z-piezo extension.
  - **Lock Quality Bar:** Visual indicator of the signal correlation/quality.
- **New Controls:**
  - Toggle for "Continuous Mode".
  - Toggle for "Show Live Feed".
  - Settings for "Lock Threshold" and "Timeout Duration".
- **Status Indicators:** `Locked` (Green), `Searching` (Yellow), `Lost` (Red).

## 4. Technical Constraints & Considerations
- **Thread Safety:** The continuous loop and video feed must run asynchronously.
- **Performance:** Feedback loop and video rendering must be optimized to prevent UI lag or CPU starvation.
- **Compatibility:** Extend `LaserAutoFocusController` to support continuous operation and video streaming hooks.

## 5. Out of Scope
- Hardware modifications.
- Non-laser AF methods.
