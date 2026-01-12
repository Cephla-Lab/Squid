# Firmware Communication Protocol v2.0

**Status:** Draft
**Created:** 2025-01-12
**Authors:** Ian O'Hara, Hongquan Li

## Overview

This document specifies a new communication protocol between Squid Python software and Teensy firmware. The protocol replaces the existing fixed-length packet system with a more robust, flexible design that supports:

- Variable-length packets with headers and CRC
- Concurrent multi-axis motion control (8 stepper axes)
- Hardware Sequenced Acquisition (HSA) for high-throughput imaging
- Independent illumination channel control (8 TTL + LED matrix)
- Complex multi-camera triggering with per-camera timing
- GPIO flexibility for custom hardware configurations
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
6. Support complex multi-camera triggering with individual timing and illumination
7. Maintain <10ms state update latency

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

## Hardware Resources

### Stepper Motor Axes (8 total)

```c
enum AxisId {
    AXIS_X           = 0,
    AXIS_Y           = 1,
    AXIS_Z           = 2,
    AXIS_FILTER1     = 3,    // Filter wheel 1
    AXIS_TURRET      = 4,    // Objective turret
    AXIS_FILTER2     = 5,    // Filter wheel 2
    AXIS_AUX1        = 6,    // Auxiliary axis 1
    AXIS_AUX2        = 7,    // Auxiliary axis 2
    NUM_AXES         = 8,
};
```

### Other Resources

| Resource | Count | Description |
|----------|-------|-------------|
| DACs | 8 | DAC0 = piezo (convention), DAC1-7 general/illumination intensity |
| Illumination TTL | 8 | On/off control for light sources, paired with DACs |
| LED Matrix | 1 | Pattern-based control (256 pre-stored patterns) |
| Camera triggers | 8 | TTL output pairs for camera triggering |
| Camera ready | 2 | Shared ready signal inputs |
| General TTL | 16 | Additional digital outputs |

### GPIO Flexibility

Many dedicated pins can be reconfigured as general-purpose I/O:
- Illumination TTL pins (8)
- Camera trigger pins (8)
- Auxiliary pins

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
    // Motion (0x01-0x0F)
    CMD_MOVE_AXIS           = 0x01,
    CMD_MOVE_RELATIVE       = 0x02,
    CMD_HOME_AXIS           = 0x03,
    CMD_STOP_AXIS           = 0x04,
    CMD_STOP_ALL            = 0x05,

    // Configuration (0x10-0x1F)
    CMD_SET_AXIS_PARAMS     = 0x10,
    CMD_GET_AXIS_PARAMS     = 0x11,
    CMD_SET_CAMERA_PARAMS   = 0x12,

    // Analog/Digital Output (0x20-0x2F)
    CMD_SET_DAC             = 0x20,  // Includes piezo (DAC0)
    CMD_SET_TTL             = 0x21,
    CMD_CONFIG_GPIO         = 0x22,
    CMD_WRITE_GPIO          = 0x23,
    CMD_READ_GPIO           = 0x24,

    // Illumination (0x30-0x3F)
    CMD_SET_ILLUMINATION    = 0x30,  // TTL on/off
    CMD_SET_LED_MATRIX      = 0x31,  // Pattern selection
    CMD_PULSE_ILLUMINATION  = 0x32,  // Timed pulse with intensity

    // Camera (0x40-0x4F)
    CMD_TRIGGER_CAMERA      = 0x40,

    // HSA (0x50-0x5F)
    CMD_HSA_UPLOAD_HEADER   = 0x50,
    CMD_HSA_UPLOAD_ACTIONS  = 0x51,
    CMD_HSA_UPLOAD_TRIGGER_PROFILE = 0x52,
    CMD_HSA_UPLOAD_INTENSITY= 0x53,  // Future: photobleaching curves
    CMD_HSA_START           = 0x54,
    CMD_HSA_CANCEL          = 0x55,

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

Motion commands use velocity and acceleration from axis parameters (set via `CMD_SET_AXIS_PARAMS`).

#### CMD_MOVE_AXIS (0x01)

Move axis to absolute position.

```c
struct CmdMoveAxis {
    uint8_t axis_id;           // 0-7
    int32_t target_usteps;     // Absolute position in microsteps
};  // 5 bytes
```

#### CMD_MOVE_RELATIVE (0x02)

Move axis by relative amount.

```c
struct CmdMoveRelative {
    uint8_t axis_id;
    int32_t delta_usteps;      // Relative move in microsteps
};  // 5 bytes
```

#### CMD_HOME_AXIS (0x03)

Home an axis to its reference position.

```c
struct CmdHomeAxis {
    uint8_t axis_id;
    int8_t direction;          // -1 or +1
};  // 2 bytes
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

### Configuration Commands

#### CMD_SET_AXIS_PARAMS (0x10)

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

#### CMD_SET_CAMERA_PARAMS (0x12)

Configure camera trigger behavior.

```c
struct CmdSetCameraParams {
    uint8_t camera_id;          // 0-7
    uint8_t trigger_mode;       // 0=EDGE, 1=LEVEL
    uint8_t trigger_polarity;   // 0=active_low, 1=active_high
    uint16_t pre_illum_delay_us;// Camera-specific illumination delay
    uint8_t wait_ready;         // 0=no, 1=wait for ready signal
    uint8_t ready_input;        // Which ready input to use (0 or 1)
};  // 7 bytes
```

### Analog/Digital Output Commands

#### CMD_SET_DAC (0x20)

Set DAC output value. DAC0 is piezo by convention.

```c
struct CmdSetDAC {
    uint8_t dac_id;            // 0-7
    uint16_t value;            // 16-bit DAC value
};  // 3 bytes
```

#### CMD_SET_TTL (0x21)

Set general TTL output states.

```c
struct CmdSetTTL {
    uint16_t pin_mask;         // Which pins to modify (16 bits)
    uint16_t state_mask;       // 1=high, 0=low
};  // 4 bytes
```

#### CMD_CONFIG_GPIO (0x22)

Configure pins as GPIO.

```c
enum GPIOGroup {
    GPIO_GROUP_ILLUM = 0,       // 8 illumination TTL pins
    GPIO_GROUP_CAM_TRIGGER = 1, // 8 camera trigger pins
    GPIO_GROUP_AUX = 2,         // Auxiliary pins
};

enum GPIOMode {
    GPIO_MODE_DEDICATED = 0,    // Primary function
    GPIO_MODE_INPUT = 1,
    GPIO_MODE_OUTPUT = 2,
};

struct CmdConfigGPIO {
    uint8_t group;             // GPIOGroup
    uint8_t pin_mask;          // Which pins to configure
    uint8_t mode;              // GPIOMode
};  // 3 bytes
```

#### CMD_WRITE_GPIO (0x23)

Write to GPIO pins.

```c
struct CmdWriteGPIO {
    uint8_t group;
    uint8_t pin_mask;
    uint8_t state_mask;
};  // 3 bytes
```

#### CMD_READ_GPIO (0x24)

Read GPIO pin states. Response includes current states.

```c
struct CmdReadGPIO {
    uint8_t group;
};  // 1 byte
```

### Illumination Commands

#### CMD_SET_ILLUMINATION (0x30)

Turn illumination channels on/off.

```c
struct CmdSetIllumination {
    uint8_t channel_mask;      // Which channels (8 bits)
    uint8_t state_mask;        // 1=ON, 0=OFF
};  // 2 bytes
```

#### CMD_SET_LED_MATRIX (0x31)

Select LED matrix pattern.

```c
struct CmdSetLEDMatrix {
    uint8_t pattern_id;        // 0-255 (pre-stored patterns)
};  // 1 byte
```

#### CMD_PULSE_ILLUMINATION (0x32)

Trigger a timed illumination pulse with intensity control.

```c
struct CmdPulseIllumination {
    uint8_t channel_id;        // 0-7
    uint16_t intensity;        // DAC value for intensity
    uint32_t duration_us;      // Pulse duration
};  // 7 bytes
```

### Camera Commands

#### CMD_TRIGGER_CAMERA (0x40)

Trigger one or more cameras with individual timing and illumination settings.

```c
struct CameraTriggerEntry {
    uint8_t camera_id;          // 0-7
    uint16_t delay_us;          // Offset from trigger command
    uint8_t illum_channels;     // TTL channel mask (8 bits)
    uint8_t led_pattern;        // LED matrix pattern (0 = none)
    uint16_t illum_intensity;   // DAC intensity value
    uint32_t illum_duration_us; // Illumination pulse duration
};  // 11 bytes

struct CmdTriggerCamera {
    uint8_t num_cameras;        // 1-8
    CameraTriggerEntry entries[];
};  // 1 + 11*N bytes (max 89 bytes for 8 cameras)
```

**Timing per camera entry:**
```
t=0:                                    Command received
t=delay_us:                             Camera trigger asserted
t=delay_us + pre_illum_delay_us:        Illumination ON (intensity set via DAC)
t=delay_us + pre_illum_delay_us + illum_duration_us: Illumination OFF
```

Note: `pre_illum_delay_us` is per-camera configuration from `CMD_SET_CAMERA_PARAMS`.

---

## Hardware Sequenced Acquisition (HSA)

HSA enables high-throughput imaging by pre-programming a sequence of motions and camera triggers that execute autonomously on the firmware.

### HSA Characteristics

- Supports 1 to 65535 layers (single-layer HSA for non-stack acquisitions)
- Stack axis can be stepper motor OR piezo (DAC0)
- Up to 8 cameras with individual timing
- Trigger profiles include filter wheel configuration
- Cancel completes current layer before stopping (atomic layers)
- Errors abort the sequence and transition to ERROR mode

### HSA Upload

#### CMD_HSA_UPLOAD_HEADER (0x50)

```c
struct CmdHSAHeader {
    uint16_t num_layers;        // 1-65535
    uint8_t stack_axis_type;    // 0=stepper, 1=piezo (DAC0)
    uint8_t stack_axis_id;      // Stepper axis ID (0-7) if type=0
    int32_t step_per_layer;     // Movement per layer (usteps or DAC units)
    uint8_t num_actions;        // Actions per layer
    uint8_t flags;              // Bit 0: has intensity curves (future)
};  // 11 bytes
```

#### CMD_HSA_UPLOAD_ACTIONS (0x51)

```c
struct CmdHSAActions {
    uint8_t start_index;        // For chunked upload
    uint8_t count;              // Number of actions in this packet
    HSAAction actions[];        // 8 bytes each
};  // 2 + 8*N bytes

struct HSAAction {
    uint8_t type;               // HSAActionType enum
    uint8_t params[7];          // Type-dependent
};  // 8 bytes fixed
```

#### CMD_HSA_UPLOAD_TRIGGER_PROFILE (0x52)

Trigger profiles combine filter wheel settings with multi-camera trigger configuration.

```c
struct FilterSetting {
    uint8_t wheel_id;           // 0=Filter1 (axis 3), 1=Filter2 (axis 5), 0xFF=skip
    uint8_t position;           // Target position (integer index)
    uint8_t wait_complete;      // 0=fire-and-forget, 1=wait for completion
};  // 3 bytes

struct CmdHSAUploadTriggerProfile {
    uint8_t profile_id;         // 0-255
    FilterSetting filter1;      // Filter wheel 1 setting (or skip)
    FilterSetting filter2;      // Filter wheel 2 setting (or skip)
    uint8_t num_cameras;        // 1-8
    CameraTriggerEntry cameras[];
};  // 8 + 11*N bytes
```

### HSA Action Types

```c
enum HSAActionType {
    HSA_NOP                 = 0x00,    // No operation
    HSA_MOVE_STACK_AXIS     = 0x01,    // Move by step_per_layer
    HSA_WAIT_AXIS           = 0x02,    // Wait for axis to stop
    HSA_SET_FILTER          = 0x03,    // Set filter wheel position
    HSA_SET_ILLUMINATION    = 0x04,    // Turn channels on/off
    HSA_SET_DAC             = 0x05,    // Set DAC value (includes piezo)
    HSA_TRIGGER_PROFILE     = 0x06,    // Execute trigger profile
    HSA_SET_LED_MATRIX      = 0x07,    // Set LED matrix pattern
    HSA_DELAY_US            = 0x08,
    HSA_DELAY_MS            = 0x09,
    HSA_SET_TTL             = 0x0A,    // Set TTL outputs
};
```

### HSA Action Parameters

#### HSA_NOP (0x00)
No parameters.

#### HSA_MOVE_STACK_AXIS (0x01)
No parameters. Uses `step_per_layer` from header.

#### HSA_WAIT_AXIS (0x02)
```
params[0]: axis_id
params[1-6]: unused
```

#### HSA_SET_FILTER (0x03)
```
params[0]: wheel_id (0=Filter1, 1=Filter2)
params[1]: position (integer index)
params[2]: wait_complete (0=fire-and-forget, 1=wait)
params[3-6]: unused
```

#### HSA_SET_ILLUMINATION (0x04)
```
params[0]: channel_mask (8 bits)
params[1]: state_mask (1=ON, 0=OFF)
params[2-6]: unused
```

#### HSA_SET_DAC (0x05)
```
params[0]: dac_id (0-7, 0=piezo)
params[1-2]: value (uint16 LE)
params[3-6]: unused
```

#### HSA_TRIGGER_PROFILE (0x06)
```
params[0]: profile_id (references uploaded trigger profile)
params[1-6]: unused
```

#### HSA_SET_LED_MATRIX (0x07)
```
params[0]: pattern_id (0-255)
params[1-6]: unused
```

#### HSA_DELAY_US (0x08)
```
params[0-3]: delay_us (uint32 LE)
params[4-6]: unused
```

#### HSA_DELAY_MS (0x09)
```
params[0-1]: delay_ms (uint16 LE)
params[2-6]: unused
```

#### HSA_SET_TTL (0x0A)
```
params[0-1]: pin_mask (uint16 LE)
params[2-3]: state_mask (uint16 LE)
params[4-6]: unused
```

### HSA Example: 4-Channel Fluorescence Z-Stack

```c
// Upload trigger profiles first
// Profile 0: DAPI - Filter1 pos 0, Camera 0 with illum channel 0
// Profile 1: GFP  - Filter1 pos 1, Camera 0 with illum channel 1
// Profile 2: RFP  - Filter1 pos 2, Camera 0 with illum channel 2
// Profile 3: Cy5  - Filter1 pos 3, Camera 0 with illum channel 3

// Then upload HSA header and actions
CmdHSAHeader header = {
    .num_layers = 2000,
    .stack_axis_type = 0,      // Stepper
    .stack_axis_id = AXIS_Z,
    .step_per_layer = 100,     // 100 usteps per layer
    .num_actions = 5,
    .flags = 0,
};

HSAAction layer_actions[] = {
    {HSA_MOVE_STACK_AXIS, {0}},             // Move Z by dz
    {HSA_WAIT_AXIS, {AXIS_Z}},              // Wait for Z
    {HSA_TRIGGER_PROFILE, {0}},             // DAPI acquisition
    {HSA_TRIGGER_PROFILE, {1}},             // GFP acquisition
    {HSA_TRIGGER_PROFILE, {2}},             // RFP acquisition
    {HSA_TRIGGER_PROFILE, {3}},             // Cy5 acquisition
};
// 6 actions × 8 bytes = 48 bytes
```

---

## Response Structure

Every response includes full system state for polling efficiency.

```c
struct Response {
    // Command acknowledgment (3 bytes)
    uint8_t cmd_id;              // Echo of command_id
    uint8_t status;              // ResponseStatus enum
    uint8_t error_code;          // ErrorCode (if status != OK)

    // System state (1 byte)
    uint8_t system_mode;         // MODE_NORMAL, MODE_HSA, MODE_ERROR

    // Axis states (8 × 12 = 96 bytes)
    struct {
        int32_t position_usteps; // Current position
        int32_t target_usteps;   // Target position (for progress)
        uint8_t state;           // IDLE, MOVING, HOMING, ERROR
        uint8_t error_code;      // Axis-specific error
        uint8_t homed;           // 0=not homed, 1=homed
        uint8_t reserved;
    } axes[8];

    // DAC values (8 × 2 = 16 bytes)
    uint16_t dac_values[8];      // DAC0 = piezo

    // TTL outputs (2 bytes)
    uint16_t ttl_states;

    // Illumination (2 bytes)
    uint8_t illum_on_mask;       // 8 TTL channels
    uint8_t led_pattern;         // Current LED matrix pattern

    // GPIO states (4 bytes)
    uint8_t gpio_illum_states;   // Illumination pins (when GPIO mode)
    uint8_t gpio_cam_states;     // Camera trigger pins (when GPIO mode)
    uint8_t cam_ready_inputs;    // Camera ready inputs (2 bits used)
    uint8_t gpio_modes;          // Packed mode configuration

    // HSA progress (8 bytes)
    struct {
        uint16_t current_layer;
        uint16_t total_layers;
        uint8_t current_action;
        uint8_t total_actions;
        uint8_t abort_axis;      // Axis that caused abort (if any)
        uint8_t abort_error;     // Error that caused abort
    } hsa;

    // Cameras (8 bytes)
    uint8_t camera_states[8];    // IDLE, WAITING_READY, TRIGGERED

};  // Total: ~140 bytes
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
    ERR_INVALID_PROFILE         = 0x1D,
    ERR_INVALID_GPIO_GROUP      = 0x1E,

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
   │  CMD_MOVE_AXIS(X, 10000)        │
   │─────────────────────────────────>│
   │                                  │  Lock axis X, start move
   │  Response: ACCEPTED, X=MOVING    │
   │<─────────────────────────────────│
   │                                  │
   │  CMD_MOVE_AXIS(Y, 5000)         │  (concurrent Y move OK)
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

### Multi-Camera Trigger with Individual Settings

```
Software                          Firmware
   │                                  │
   │  CMD_TRIGGER_CAMERA {            │
   │    num_cameras: 2                │
   │    entries: [                    │
   │      {cam:0, delay:0,            │
   │       illum:0x01, intensity:4000,│
   │       duration:1000},            │
   │      {cam:1, delay:100,          │  Camera 1 triggers 100µs later
   │       illum:0x02, intensity:3000,│  with different illumination
   │       duration:1500}             │
   │    ]                             │
   │  }                               │
   │─────────────────────────────────>│
   │                                  │
   │  Response: OK                    │
   │<─────────────────────────────────│
```

### HSA Execution

```
Software                          Firmware
   │                                  │
   │  CMD_HSA_UPLOAD_TRIGGER_PROFILE  │  (upload profiles first)
   │─────────────────────────────────>│
   │  Response: OK                    │
   │<─────────────────────────────────│
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
   │  CMD_MOVE_AXIS(X, 999999)       │
   │─────────────────────────────────>│
   │                                  │  X hits limit switch!
   │                                  │  Stop all axes
   │                                  │  → ERROR MODE
   │  Response: ERROR,                │
   │    error=ERR_LIMIT_SWITCH_POS,   │
   │    axes[X].error=0x42            │
   │<─────────────────────────────────│
   │                                  │
   │  CMD_MOVE_AXIS(Y, 1000)         │  (rejected in ERROR mode)
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
| 2.0-draft | 2025-01-12 | Updated axis IDs, expanded camera/DAC/GPIO support, added trigger profiles |
