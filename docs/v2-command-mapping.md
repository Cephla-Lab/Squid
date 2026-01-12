# V2 Protocol Command Mapping

**Purpose:** Map current firmware commands to v2 protocol commands

## Current Firmware Commands

| Current Command | Code | v2 Command | v2 Code | Notes |
|-----------------|------|------------|---------|-------|
| **Motion - Relative** |
| MOVE_X | 0 | CMD_MOVE_RELATIVE | 0x02 | axis_id=0 |
| MOVE_Y | 1 | CMD_MOVE_RELATIVE | 0x02 | axis_id=1 |
| MOVE_Z | 2 | CMD_MOVE_RELATIVE | 0x02 | axis_id=2 |
| MOVE_W | 4 | CMD_MOVE_RELATIVE | 0x02 | axis_id=5 (FILTER2 in v2) |
| **Motion - Absolute** |
| MOVETO_X | 6 | CMD_MOVE_AXIS | 0x01 | axis_id=0 |
| MOVETO_Y | 7 | CMD_MOVE_AXIS | 0x01 | axis_id=1 |
| MOVETO_Z | 8 | CMD_MOVE_AXIS | 0x01 | axis_id=2 |
| MOVETO_W | 18 | CMD_MOVE_AXIS | 0x01 | axis_id=5 |
| **Homing** |
| HOME_OR_ZERO | 5 | CMD_HOME_AXIS | 0x03 | |
| **Illumination** |
| TURN_ON_ILLUMINATION | 10 | CMD_SET_ILLUMINATION | 0x30 | channel_mask based |
| TURN_OFF_ILLUMINATION | 11 | CMD_SET_ILLUMINATION | 0x30 | channel_mask based |
| SET_ILLUMINATION | 12 | CMD_SET_DAC | 0x20 | Intensity only |
| SET_ILLUMINATION_LED_MATRIX | 13 | CMD_SET_LED_MATRIX | 0x31 | |
| SET_ILLUMINATION_INTENSITY_FACTOR | 17 | *deprecate or new* | | Global scaling factor |
| **DAC** |
| ANALOG_WRITE_ONBOARD_DAC | 15 | CMD_SET_DAC | 0x20 | |
| SET_DAC80508_REFDIV_GAIN | 16 | *new cmd needed* | | DAC config |
| **Camera/Trigger** |
| SEND_HARDWARE_TRIGGER | 30 | CMD_TRIGGER_CAMERA | 0x40 | |
| SET_STROBE_DELAY | 31 | CMD_SET_CAMERA_PARAMS | 0x12 | |
| SET_TRIGGER_MODE | 33 | CMD_SET_CAMERA_PARAMS | 0x12 | |
| **Axis Configuration** |
| SET_LIM | 9 | CMD_SET_AXIS_PARAMS | 0x10 | |
| SET_LIM_SWITCH_POLARITY | 20 | CMD_SET_AXIS_PARAMS | 0x10 | |
| CONFIGURE_STEPPER_DRIVER | 21 | CMD_SET_AXIS_PARAMS | 0x10 | |
| SET_MAX_VELOCITY_ACCELERATION | 22 | CMD_SET_AXIS_PARAMS | 0x10 | |
| SET_LEAD_SCREW_PITCH | 23 | CMD_SET_AXIS_PARAMS | 0x10 | |
| SET_OFFSET_VELOCITY | 24 | CMD_SET_AXIS_PARAMS | 0x10 | |
| SET_HOME_SAFETY_MERGIN | 28 | CMD_SET_AXIS_PARAMS | 0x10 | |
| SET_AXIS_DISABLE_ENABLE | 32 | *new cmd* | | Enable/disable driver |
| **PID** |
| CONFIGURE_STAGE_PID | 25 | *new cmd* | | |
| ENABLE_STAGE_PID | 26 | *new cmd* | | |
| DISABLE_STAGE_PID | 27 | *new cmd* | | |
| SET_PID_ARGUMENTS | 29 | *new cmd* | | |
| **GPIO** |
| SET_PIN_LEVEL | 41 | CMD_WRITE_GPIO | 0x23 | |
| **System** |
| ACK_JOYSTICK_BUTTON_PRESSED | 14 | *deprecate or new* | | |
| INITFILTERWHEEL | 253 | *new cmd* | | |
| INITIALIZE | 254 | *new cmd* | | |
| RESET | 255 | CMD_RESET | 0xFF | |

## V2 Commands Needed

### From v2 Spec (implemented)

| Command | Code | Payload |
|---------|------|---------|
| CMD_MOVE_AXIS | 0x01 | axis_id(1) + target_usteps(4) = 5 bytes |
| CMD_MOVE_RELATIVE | 0x02 | axis_id(1) + delta_usteps(4) = 5 bytes |
| CMD_HOME_AXIS | 0x03 | axis_id(1) + direction(1) = 2 bytes |
| CMD_STOP_AXIS | 0x04 | axis_id(1) = 1 byte |
| CMD_STOP_ALL | 0x05 | 0 bytes |
| CMD_SET_AXIS_PARAMS | 0x10 | axis_id(1) + params... = variable |
| CMD_SET_CAMERA_PARAMS | 0x12 | camera_id(1) + params... = variable |
| CMD_SET_DAC | 0x20 | dac_id(1) + value(2) = 3 bytes |
| CMD_SET_TTL | 0x21 | mask(2) + state(2) = 4 bytes |
| CMD_WRITE_GPIO | 0x23 | group(1) + pin_mask(1) + state_mask(1) = 3 bytes |
| CMD_SET_ILLUMINATION | 0x30 | channel_mask(1) + state_mask(1) = 2 bytes |
| CMD_SET_LED_MATRIX | 0x31 | pattern_id(1) = 1 byte |
| CMD_TRIGGER_CAMERA | 0x40 | num_cameras(1) + entries... = variable |
| CMD_GET_STATE | 0xF0 | 0 bytes |
| CMD_ACK_ERROR | 0xF1 | 0 bytes |
| CMD_GET_VERSION | 0xF2 | 0 bytes |
| CMD_RESET | 0xFF | 0 bytes |

### New Commands Needed (not in v2 spec)

| Command | Suggested Code | Purpose |
|---------|----------------|---------|
| CMD_SET_DAC_GAIN | 0x25 | SET_DAC80508_REFDIV_GAIN equivalent |
| CMD_SET_PID_PARAMS | 0x13 | PID configuration |
| CMD_ENABLE_PID | 0x14 | Enable PID control |
| CMD_DISABLE_PID | 0x15 | Disable PID control |
| CMD_ENABLE_AXIS | 0x06 | Enable/disable motor driver |
| CMD_INIT_FILTER_WHEEL | 0x07 | Initialize filter wheel axis |
| CMD_INITIALIZE | 0xFE | Full system init |

## Response Structure

Based on current firmware state, the response should include:

```c
struct Response {
    // Command acknowledgment (3 bytes)
    uint8_t cmd_id;
    uint8_t status;
    uint8_t error_code;

    // System state (1 byte)
    uint8_t system_mode;  // NORMAL=0, ERROR=1

    // Axis states - only X, Y, Z, W for now (4 × 12 = 48 bytes)
    struct {
        int32_t position_usteps;
        int32_t target_usteps;
        uint8_t state;      // IDLE, MOVING, HOMING
        uint8_t error_code;
        uint8_t homed;
        uint8_t reserved;
    } axes[4];

    // DAC values (8 × 2 = 16 bytes)
    uint16_t dac_values[8];

    // Illumination (2 bytes)
    uint8_t illum_on_mask;
    uint8_t led_pattern;

    // Joystick (4 bytes)
    int16_t joystick_delta_x;
    int16_t joystick_delta_y;

    // Buttons (1 byte)
    uint8_t buttons;  // joystick button, etc.

    // Reserved for future (padding to align)
    uint8_t reserved[2];
};  // Total: ~78 bytes (can expand later)
```

## Implementation Priority

### Phase 1: Core Protocol
1. Packet format (header, length, CRC-16)
2. Response structure
3. CMD_GET_STATE
4. CMD_RESET

### Phase 2: Motion
1. CMD_MOVE_AXIS
2. CMD_MOVE_RELATIVE
3. CMD_HOME_AXIS
4. CMD_STOP_AXIS

### Phase 3: Illumination & DAC
1. CMD_SET_DAC
2. CMD_SET_ILLUMINATION
3. CMD_SET_LED_MATRIX

### Phase 4: Camera Trigger
1. CMD_SET_CAMERA_PARAMS
2. CMD_TRIGGER_CAMERA

### Phase 5: Configuration
1. CMD_SET_AXIS_PARAMS
2. Other config commands
