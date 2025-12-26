# High-Performance Data Handling Strategy - Squid

## 1. Problem Statement
The current `StreamHandler` performs image manipulation (crop/squeeze) directly in the camera driver's callback thread. This creates a synchronous bottleneck where slow processing or IO can cause the camera driver to drop frames or introduce jitter in hardware control loops.

## 2. Target Architecture: Asynchronous Pipeline

### A. Producer-Consumer Pattern
Decouple the "Data Producer" (Camera Driver) from the "Data Consumers" (Display, Disk, Analysis) using thread-safe, high-speed queues or circular buffers.

### B. Zero-Copy Image Transfer
- **Pre-allocated Buffer Pool:** Implement a pool of pre-allocated `numpy` arrays (buffers).
- **In-Place Updates:** The camera driver writes raw frame data directly into a buffer from the pool.
- **Reference Passing:** Consumers receive a reference to the buffer, avoiding expensive data copying.

### C. Tiered Processing Threads
Organize processing into three priority tiers:
1. **Tier 1 (Real-time):** Critical hardware-software feedback loops (e.g., laser auto-focus centroid calculation). Runs in a dedicated, high-priority thread.
2. **Tier 2 (Near Real-time):** UI Display and Live View. Performs necessary cropping/scaling. Discards frames if the UI is busy.
3. **Tier 3 (Background):** Data persistence (Disk IO), OME-Zarr encoding, and heavy analysis. Runs in a low-priority thread pool or separate process.

## 3. High-Performance Hardware Control

### A. Dedicated Control Thread
Move all direct hardware interactions (Serial/USB/VISA) to a dedicated "Hardware Worker" thread. This prevents UI or data-plane stalls from affecting hardware timing.

### B. Command Batching
For complex acquisition sequences, batch commands to the microcontroller to reduce communication overhead and improve synchronization precision.

### C. Latency-Sensitive Feedback (Edge Control)
Where possible, offload high-speed control logic (e.g., hardware-triggered acquisition, closed-loop focus) to the microcontroller firmware to achieve microsecond-scale precision that is impossible in Python.

## 4. Implementation Strategy

### Phase 1: Buffer Pool Implementation
Create a `BufferManager` that manages a fixed-size pool of `numpy` arrays, handling allocation and recycling.

### Phase 2: Refactor `StreamHandler`
Modify `StreamHandler` to only enqueue received frames into a `ProcessingQueue` and immediately return, freeing the camera callback thread.

### Phase 3: Dedicated Processing Workers
Implement specialized worker threads for Display and Disk IO that consume from the `ProcessingQueue`.
- **Display Worker:** Uses `cv2` (with optional OpenCL/GPU acceleration) for fast scaling and sends results to the UI.
- **Disk Worker:** Handles asynchronous writing to TIFF or Zarr.

## 5. Expected Outcomes
- **Zero Frame Drops:** At high frame rates (e.g., >100 FPS), the system remains responsive and reliable.
- **Lower Control Jitter:** Hardware-software feedback loops become more deterministic.
- **Improved UI Responsiveness:** The GUI remains fluid even during intensive data-saving operations.
