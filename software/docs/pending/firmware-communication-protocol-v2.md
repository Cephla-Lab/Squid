# Firmware Communication Protocol v2.0

**Status:** Draft
**Created:** 2025-01-12
**Authors:** Ian O'Hara, Hongquan Li

## Overview

This document specifies a new communication protocol between Squid Python software and Teensy firmware. The protocol replaces the existing fixed-length packet system with a more robust, flexible design that supports:

- Variable-length packets with headers and CRC
- Concurrent multi-axis motion control
- Hardware Sequenced Acquisition (HSA) for high-throughput imaging
- Independent illumination channel control
- Guaranteed command acknowledgment and error reporting

## Motivation

The existing protocol (as of September 2025) has several limitations:

1. **No packet header** — Cannot recover from byte misalignment
2. **Fixed 10-byte packets** — Limits command complexity
3. **Fragile async interface** — Command IDs can be clobbered, no guaranteed responses
4. **Manual byte packing** — Error-prone implementation

## Design Goals

1. Detect and recover from communication faults
2. Support variable packet lengths up to 512 bytes
3. Guarantee command acknowledgment (accepted/rejected/completed/failed)
4. Support concurrent motion of up to 8 stepper motor axes
5. Enable Hardware Sequenced Acquisition for Z-stacks with multi-channel fluorescence
6. Maintain <10ms state update latency

## Assumptions

- Single client connected to firmware at any time
- UART communication at 2 Mbps
- Polling-based communication (software initiates all exchanges)

---

## Packet Format

```
┌──────────┬──────────┬─────────────────┬──────────┐
│ Header   │ Length   │ Payload         │ CRC-16   │
│ 0xAA 0xBB│ uint16 LE│ 1-506 bytes     │ CCITT    │
└──────────┴──────────┴─────────────────┴──────────┘
     2          2         variable           2

Max packet size: 512 bytes (6 overhead + 506 payload)
Overhead at 2 Mbps: ~27 µs per packet
```

- **Header:** Fixed bytes `0xAA 0xBB` for packet synchronization
- **Length:** Little-endian uint16, payload byte count (1-506)
- **Payload:** Command or response data
- **CRC-16:** CRC-CCITT over Length + Payload bytes

### Packet Recovery

When a corrupted packet is detected:
1. Scan forward for next header bytes (`0xAA 0xBB`)
2. Validate length (must be ≤ 506)
3. If length implies more bytes than available, wait with timeout
4. Verify CRC; if invalid, repeat from step 1

---

## System State Machine

```
                    ┌─────────────────────────────────────────┐
                    │              NORMAL MODE                │
                    │  • Per-axis concurrent operation        │
                    │  • HSA program upload allowed           │
                    │  • All commands (subject to locks)      │
                    └─────────────────────────────────────────┘
                           │                    │
              START_HSA    │                    │  Any axis error
          (all axes idle)  │                    │  → all axes stop
                           ▼                    ▼
          ┌─────────────────────┐    ┌─────────────────────────┐
          │     HSA MODE        │    │      ERROR MODE         │
          │                     │    │                         │
          │ Accepts ONLY:       │    │ Accepts ONLY:           │
          │  • GET_STATE        │    │  • GET_STATE            │
          │  • CANCEL_HSA       │    │  • ACKNOWLEDGE_ERROR    │
          │                     │    │                         │
          │ On cancel: finish   │    │ Preserves error info:   │
          │ current layer, stop │    │  • which axis           │
          │                     │    │  • error code           │
          │ On error: abort,    │    │  • HSA progress (if     │
          │ → ERROR MODE        │    │    was running)         │
          └─────────────────────┘    └─────────────────────────┘
                    │                         │
         complete/cancel              ACKNOWLEDGE_ERROR
                    │                         │
                    └────────► NORMAL ◄───────┘
```

---

## Hardware Resources

### Stepper Motor Axes (8 total)

```c
enum AxisId {
    AXIS_X           = 0,
    AXIS_Y           = 1,
    AXIS_Z           = 2,
    AXIS_W           = 3,    // 4th linear axis
    AXIS_FILTER1     = 4,
    AXIS_FILTER2     = 5,
    AXIS_TRANSILLUM  = 6,    // Transilluminator Z
    AXIS_TURRET      = 7,    // Objective turret
    NUM_AXES         = 8,
};
```

### Other Resources

- **Piezo:** 2-byte DAC value for fine Z positioning
- **Cameras:** Up to 4 cameras with edge/level trigger modes
- **Illumination:** Up to 10 channels, on/off control (intensity via separate DAC)

---

## Command Structure

### Base Command Packet

```c
struct CommandPacket {
    uint8_t command_id;    // Echoed in response (0-255, wraps)
    uint8_t command_type;  // CommandType enum
    uint8_t payload[];     // Variable length, type-dependent
};
```

### Command Types

```c
enum CommandType {
    // Motion (0x01-0x1F)
    CMD_MOVE_AXIS           = 0x01,
    CMD_MOVE_RELATIVE       = 0x02,
    CMD_HOME_AXIS           = 0x03,
    CMD_STOP_AXIS           = 0x04,
    CMD_STOP_ALL            = 0x05,
    CMD_SET_PIEZO           = 0x06,

    // Configuration (0x20-0x3F)
    CMD_SET_AXIS_PARAMS     = 0x20,
    CMD_GET_AXIS_PARAMS     = 0x21,
    CMD_SET_CAMERA_PARAMS   = 0x22,

    // Illumination (0x40-0x4F)
    CMD_SET_ILLUMINATION    = 0x40,
    CMD_SET_INTENSITY       = 0x41,

    // Camera (0x50-0x5F)
    CMD_TRIGGER_CAMERA      = 0x50,

    // HSA (0x60-0x6F)
    CMD_HSA_UPLOAD_HEADER   = 0x60,
    CMD_HSA_UPLOAD_ACTIONS  = 0x61,
    CMD_HSA_UPLOAD_INTENSITY= 0x62,  // Future: photobleaching curves
    CMD_HSA_START           = 0x63,
    CMD_HSA_CANCEL          = 0x64,

    // System (0xF0-0xFF)
    CMD_GET_STATE           = 0xF0,
    CMD_ACK_ERROR           = 0xF1,
    CMD_GET_VERSION         = 0xF2,
    CMD_RESET               = 0xFF,
};
```

---

## Concurrency Rules

| System Mode | Axis N State | Command | Result |
|-------------|--------------|---------|--------|
| NORMAL | Any | `GET_STATE` | ✓ Accept |
| NORMAL | Idle | `MOVE_AXIS(N)` | ✓ Accept |
| NORMAL | Moving | `MOVE_AXIS(N)` | ✗ ERR_AXIS_BUSY |
| NORMAL | Moving | `MOVE_AXIS(M)` where M≠N | ✓ Accept |
| NORMAL | Idle | `SET_AXIS_PARAMS(N)` | ✓ Accept |
| NORMAL | Moving | `SET_AXIS_PARAMS(N)` | ✗ ERR_AXIS_BUSY |
| NORMAL | Any | `SET_ILLUMINATION` | ✓ Accept |
| NORMAL | Any | `TRIGGER_CAMERA` | ✓ Accept |
| NORMAL | Any | `HSA_UPLOAD_*` | ✓ Accept |
| NORMAL | All Idle | `HSA_START` | ✓ Accept → HSA mode |
| NORMAL | Any Moving | `HSA_START` | ✗ ERR_AXIS_BUSY |
| HSA | - | `GET_STATE` | ✓ Accept |
| HSA | - | `HSA_CANCEL` | ✓ Accept |
| HSA | - | Any other | ✗ ERR_HSA_RUNNING |
| ERROR | - | `GET_STATE` | ✓ Accept |
| ERROR | - | `ACK_ERROR` | ✓ Accept → NORMAL |
| ERROR | - | Any other | ✗ ERR_SYSTEM_IN_ERROR |

---

## Command Definitions

### Motion Commands

#### CMD_MOVE_AXIS (0x01)

Move axis to absolute position.

```c
struct CmdMoveAxis {
    uint8_t  axis_id;           // 0-7
    int32_t  target_usteps;     // Absolute position in microsteps
    uint32_t velocity;          // usteps/sec
    uint32_t acceleration;      // usteps/sec²
};  // 13 bytes
```

#### CMD_MOVE_RELATIVE (0x02)

Move axis by relative amount.

```c
struct CmdMoveRelative {
    uint8_t  axis_id;
    int32_t  delta_usteps;      // Relative move in microsteps
    uint32_t velocity;
    uint32_t acceleration;
};  // 13 bytes
```

#### CMD_HOME_AXIS (0x03)

Home an axis to its reference position.

```c
struct CmdHomeAxis {
    uint8_t  axis_id;
    int8_t   direction;         // -1 or +1
    uint32_t velocity;          // Homing speed
};  // 6 bytes
```

#### CMD_STOP_AXIS (0x04)

Stop a single axis with controlled deceleration.

```c
struct CmdStopAxis {
    uint8_t axis_id;
};  // 1 byte
```

#### CMD_STOP_ALL (0x05)

Stop all axes with controlled deceleration. No payload.

#### CMD_SET_PIEZO (0x06)

Set piezo position (fine Z focus).

```c
struct CmdSetPiezo {
    uint16_t position;          // DAC value (2 bytes)
};  // 2 bytes
```

### Configuration Commands

#### CMD_SET_AXIS_PARAMS (0x20)

Configure axis motion parameters. Rejected if axis is moving.

```c
struct CmdSetAxisParams {
    uint8_t  axis_id;
    uint32_t velocity_max;      // usteps/sec
    uint32_t acceleration_max;  // usteps/sec²
    uint32_t jerk;              // usteps/sec³ (0 = disabled)
    uint16_t current_ma;        // Motor current limit in mA
    uint8_t  microstep;         // Microstepping divisor (1,2,4,8,16,32,64,128,256)
    int32_t  soft_limit_min;    // Minimum position in usteps
    int32_t  soft_limit_max;    // Maximum position in usteps
    uint16_t pid_kp;            // PID proportional (0 if open-loop)
    uint16_t pid_ki;            // PID integral
    uint16_t pid_kd;            // PID derivative
};  // 31 bytes
```

#### CMD_SET_CAMERA_PARAMS (0x22)

Configure camera trigger behavior.

```c
struct CmdSetCameraParams {
    uint8_t  camera_id;         // 0-3
    uint8_t  trigger_mode;      // 0=EDGE, 1=LEVEL
    uint32_t exposure_us;       // Exposure time for LEVEL mode
    uint8_t  wait_ready;        // 0=no, 1=wait for camera ready signal
};  // 8 bytes
```

### Illumination Commands

#### CMD_SET_ILLUMINATION (0x40)

Turn illumination channels on/off.

```c
struct CmdSetIllumination {
    uint16_t channel_mask;      // Which channels to modify (bits 0-9)
    uint16_t state_mask;        // 1=ON, 0=OFF for each channel
};  // 4 bytes
```

#### CMD_SET_INTENSITY (0x41)

Set illumination intensity via DAC.

```c
struct CmdSetIntensity {
    uint16_t channel_mask;      // Which channels to set
    uint16_t intensities[10];   // DAC values (only masked channels used)
};  // 22 bytes
```

### Camera Commands

#### CMD_TRIGGER_CAMERA (0x50)

Trigger camera(s) with synchronized illumination.

```c
struct CmdTriggerCamera {
    uint8_t  camera_mask;              // Bit per camera (bits 0-3)
    uint16_t inter_camera_delay_us;    // Delay between sequential triggers
    uint16_t illumination_mask;        // Channels to turn ON during exposure
    uint16_t pre_illum_delay_us;       // Delay after trigger before light ON
    uint16_t illum_duration_us;        // How long illumination stays ON
};  // 9 bytes
```

**Timing diagram:**

```
Trigger Camera 0  ──┐
                    └──────
                    |<---->| inter_camera_delay_us
Trigger Camera 1  ────┐
                      └────

                    |<->| pre_illum_delay_us
Illumination      ──────┐         ┌────
                        └─────────┘
                        |<------->|
                        illum_duration_us
```

---

## Hardware Sequenced Acquisition (HSA)

HSA enables high-throughput imaging by pre-programming a sequence of motions and camera triggers that execute autonomously on the firmware.

### HSA Characteristics

- Supports Z-stacks up to 65535 layers
- Each layer executes a programmed sequence of actions
- Cancel completes current layer before stopping (atomic layers)
- Errors abort the sequence and transition to ERROR mode

### HSA Upload

#### CMD_HSA_UPLOAD_HEADER (0x60)

```c
struct CmdHSAHeader {
    uint16_t num_layers;               // Total layers in stack
    uint8_t  stack_axis_id;            // Axis that moves between layers
    int32_t  step_per_layer;           // Movement per layer (signed, usteps)
    uint8_t  num_actions_per_layer;    // Actions executed each layer
    uint8_t  num_cameras;              // 1-4
    uint16_t inter_camera_delay_us;    // For multi-camera simultaneous trigger
    uint8_t  flags;                    // Reserved (bit 0: has intensity curves)
};  // 14 bytes
```

#### CMD_HSA_UPLOAD_ACTIONS (0x61)

```c
struct CmdHSAActions {
    uint8_t   start_index;             // For chunked upload
    uint8_t   count;                   // Number of actions in this packet
    HSAAction actions[];               // 8 bytes each, up to ~60 per packet
};  // 2 + 8*N bytes

struct HSAAction {
    uint8_t type;                      // HSAActionType enum
    uint8_t params[7];                 // Type-dependent
};  // 8 bytes fixed
```

### HSA Action Types

```c
enum HSAActionType {
    HSA_NOP                 = 0x00,    // No operation
    HSA_MOVE_STACK_AXIS     = 0x01,    // Move by step_per_layer
    HSA_WAIT_AXIS           = 0x02,    // Wait for axis to stop
    HSA_SET_FILTER          = 0x03,    // Set filter wheel position
    HSA_SET_ILLUMINATION    = 0x04,    // Turn channels on/off
    HSA_SET_INTENSITY       = 0x05,    // Set DAC intensity
    HSA_TRIGGER_CAMERAS     = 0x06,    // Trigger with illumination
    HSA_DELAY_US            = 0x07,    // Microsecond delay
    HSA_DELAY_MS            = 0x08,    // Millisecond delay
    HSA_SET_PIEZO           = 0x09,    // Set piezo position
};
```

### HSA Action Parameters

#### HSA_MOVE_STACK_AXIS (0x01)
No parameters. Uses `step_per_layer` from header.

#### HSA_WAIT_AXIS (0x02)
```
params[0]: axis_id
params[1-6]: unused
```

#### HSA_SET_FILTER (0x03)
```
params[0]: wheel_id (0 or 1)
params[1]: position (integer index)
params[2]: wait_complete (0=fire-and-forget, 1=wait)
params[3-6]: unused
```

#### HSA_SET_ILLUMINATION (0x04)
```
params[0-1]: channel_mask (uint16 LE)
params[2-3]: state_mask (uint16 LE)
params[4-6]: unused
```

#### HSA_SET_INTENSITY (0x05)
```
params[0]: channel_id (0-9)
params[1-2]: intensity (uint16 LE)
params[3-6]: unused
```

#### HSA_TRIGGER_CAMERAS (0x06)
```
params[0]: camera_mask
params[1-2]: illumination_mask (uint16 LE)
params[3-4]: pre_illum_delay_us (uint16 LE)
params[5-6]: illum_duration_us (uint16 LE)
```

#### HSA_DELAY_US (0x07)
```
params[0-3]: delay_us (uint32 LE)
params[4-6]: unused
```

#### HSA_DELAY_MS (0x08)
```
params[0-1]: delay_ms (uint16 LE)
params[2-6]: unused
```

#### HSA_SET_PIEZO (0x09)
```
params[0-1]: position (uint16 LE)
params[2-6]: unused
```

### HSA Example: 4-Channel Fluorescence Z-Stack

```c
// Layer actions for DAPI/GFP/RFP/Cy5 acquisition
HSAAction layer_actions[] = {
    {HSA_MOVE_STACK_AXIS, {0}},                                    // Move Z
    {HSA_WAIT_AXIS, {AXIS_Z}},                                     // Wait for Z
    {HSA_SET_FILTER, {0, FILTER_DAPI, 1}},                        // Filter → DAPI, wait
    {HSA_TRIGGER_CAMERAS, {0x01, CH_DAPI, 0, 0, 100, 0}},         // Trigger + illum
    {HSA_SET_FILTER, {0, FILTER_GFP, 1}},                         // Filter → GFP
    {HSA_TRIGGER_CAMERAS, {0x01, CH_GFP, 0, 0, 100, 0}},
    {HSA_SET_FILTER, {0, FILTER_RFP, 1}},                         // Filter → RFP
    {HSA_TRIGGER_CAMERAS, {0x01, CH_RFP, 0, 0, 100, 0}},
    {HSA_SET_FILTER, {0, FILTER_CY5, 1}},                         // Filter → Cy5
    {HSA_TRIGGER_CAMERAS, {0x01, CH_CY5, 0, 0, 100, 0}},
};
// 10 actions × 8 bytes = 80 bytes + 14 byte header = 94 bytes total
```

---

## Response Structure

Every response includes full system state for polling efficiency.

```c
struct Response {
    // Command acknowledgment (3 bytes)
    uint8_t  cmd_id;              // Echo of command_id
    uint8_t  status;              // ResponseStatus enum
    uint8_t  error_code;          // ErrorCode (if status != OK)

    // System state (1 byte)
    uint8_t  system_mode;         // MODE_NORMAL, MODE_HSA, MODE_ERROR

    // Axis states (8 × 12 = 96 bytes)
    struct {
        int32_t position_usteps;  // Current position
        int32_t target_usteps;    // Target position (for progress)
        uint8_t state;            // IDLE, MOVING, HOMING, ERROR
        uint8_t error_code;       // Axis-specific error
        uint8_t homed;            // 0=not homed, 1=homed
        uint8_t reserved;
    } axes[8];

    // Piezo (2 bytes)
    uint16_t piezo_position;

    // Illumination (22 bytes)
    uint16_t illum_on_mask;       // Which channels are ON
    uint16_t illum_intensities[10];

    // HSA progress (8 bytes)
    struct {
        uint16_t current_layer;
        uint16_t total_layers;
        uint8_t  current_action;
        uint8_t  total_actions;
        uint8_t  abort_axis;      // Axis that caused abort (if any)
        uint8_t  abort_error;     // Error that caused abort
    } hsa;

    // Camera states (4 bytes)
    uint8_t camera_states[4];     // IDLE, WAITING_READY, EXPOSING

};  // Total: ~136 bytes
```

### Response Status Codes

```c
enum ResponseStatus {
    STATUS_OK           = 0x00,   // Command completed successfully
    STATUS_ACCEPTED     = 0x01,   // Command started (motion, HSA)
    STATUS_REJECTED     = 0x02,   // Command rejected (see error_code)
    STATUS_ERROR        = 0x03,   // System in error state
};
```

### Axis State Codes

```c
enum AxisState {
    AXIS_IDLE           = 0,
    AXIS_MOVING         = 1,
    AXIS_HOMING         = 2,
    AXIS_ERROR          = 3,
};
```

### System Mode Codes

```c
enum SystemMode {
    MODE_NORMAL         = 0,
    MODE_HSA_RUNNING    = 1,
    MODE_ERROR          = 2,
};
```

---

## Error Codes

```c
enum ErrorCode {
    ERR_NONE                    = 0x00,

    // Command rejection (0x10-0x2F)
    ERR_UNKNOWN_COMMAND         = 0x10,
    ERR_INVALID_AXIS            = 0x11,
    ERR_INVALID_CAMERA          = 0x12,
    ERR_INVALID_CHANNEL         = 0x13,
    ERR_INVALID_PARAMETER       = 0x14,
    ERR_AXIS_BUSY               = 0x15,
    ERR_HSA_RUNNING             = 0x16,
    ERR_HSA_NOT_RUNNING         = 0x17,
    ERR_HSA_NOT_LOADED          = 0x18,
    ERR_SYSTEM_IN_ERROR         = 0x19,
    ERR_SOFT_LIMIT_MIN          = 0x1A,
    ERR_SOFT_LIMIT_MAX          = 0x1B,
    ERR_AXES_NOT_IDLE           = 0x1C,

    // Hardware faults (0x40-0x5F)
    ERR_MOTOR_STALL             = 0x40,
    ERR_LIMIT_SWITCH_NEG        = 0x41,
    ERR_LIMIT_SWITCH_POS        = 0x42,
    ERR_ENCODER_FAULT           = 0x43,
    ERR_FOLLOWING_ERROR         = 0x44,
    ERR_OVERCURRENT             = 0x45,
    ERR_OVERTEMPERATURE         = 0x46,
    ERR_CAMERA_TIMEOUT          = 0x47,

    // Communication (0x60-0x6F)
    ERR_PACKET_CRC              = 0x60,
    ERR_PACKET_LENGTH           = 0x61,
    ERR_PACKET_TIMEOUT          = 0x62,
};
```

---

## Example Message Sequences

### Concurrent Multi-Axis Motion

```
Software                          Firmware
   │                                  │
   │  CMD_MOVE_AXIS(X, 10000, ...)   │
   │─────────────────────────────────>│
   │                                  │  Lock axis X, start move
   │  Response: ACCEPTED, X=MOVING    │
   │<─────────────────────────────────│
   │                                  │
   │  CMD_MOVE_AXIS(Y, 5000, ...)    │  (concurrent Y move OK)
   │─────────────────────────────────>│
   │                                  │
   │  Response: ACCEPTED,             │
   │    X=MOVING, Y=MOVING            │
   │<─────────────────────────────────│
   │                                  │
   │  CMD_GET_STATE                   │  (poll for completion)
   │─────────────────────────────────>│
   │                                  │
   │  Response: OK, X=IDLE, Y=MOVING  │
   │<─────────────────────────────────│
```

### HSA Execution

```
Software                          Firmware
   │                                  │
   │  CMD_HSA_UPLOAD_HEADER(...)     │
   │─────────────────────────────────>│
   │  Response: OK                    │
   │<─────────────────────────────────│
   │                                  │
   │  CMD_HSA_UPLOAD_ACTIONS(...)    │
   │─────────────────────────────────>│
   │  Response: OK                    │
   │<─────────────────────────────────│
   │                                  │
   │  CMD_HSA_START                   │
   │─────────────────────────────────>│
   │                                  │  → HSA MODE
   │  Response: ACCEPTED,             │
   │    mode=HSA, layer=0/2000        │
   │<─────────────────────────────────│
   │                                  │
   │  CMD_GET_STATE (poll)            │
   │─────────────────────────────────>│
   │  Response: OK, layer=142/2000    │
   │<─────────────────────────────────│
   │                                  │
   │  ...polling continues...         │
   │                                  │
   │  CMD_HSA_CANCEL                  │
   │─────────────────────────────────>│
   │                                  │  Finish current layer
   │  Response: OK, layer=857/2000,   │  → NORMAL MODE
   │    mode=NORMAL                   │
   │<─────────────────────────────────│
```

### Error Handling

```
Software                          Firmware
   │                                  │
   │  CMD_MOVE_AXIS(X, 999999, ...)  │
   │─────────────────────────────────>│
   │                                  │  X hits limit switch!
   │                                  │  Stop all axes
   │                                  │  → ERROR MODE
   │  Response: ERROR,                │
   │    error=ERR_LIMIT_SWITCH_POS,   │
   │    axes[X].error=0x42            │
   │<─────────────────────────────────│
   │                                  │
   │  CMD_MOVE_AXIS(Y, 1000, ...)    │  (rejected in ERROR mode)
   │─────────────────────────────────>│
   │  Response: REJECTED,             │
   │    error=ERR_SYSTEM_IN_ERROR     │
   │<─────────────────────────────────│
   │                                  │
   │  CMD_ACK_ERROR                   │
   │─────────────────────────────────>│
   │                                  │  → NORMAL MODE
   │  Response: OK, mode=NORMAL       │
   │<─────────────────────────────────│
```

---

## Future Extensions

The following features are planned but not yet specified:

1. **HSA Intensity Curves (CMD_HSA_UPLOAD_INTENSITY):** Per-layer illumination intensity adjustment to compensate for photobleaching during long Z-stacks.

2. **Pre-trigger Positioning:** Trigger camera N milliseconds before motion completes for reduced acquisition latency.

3. **Power Cycle Recovery:** Protocol for firmware to report state after power cycle and software to re-sync.

---

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| 2.0-draft | 2025-01-12 | Initial draft based on design discussions |
