# PR 1: Firmware v3

**Upstream Commit:** `a4db687` - Firmware v3 (#335) (#396)
**Priority:** CRITICAL
**Effort:** Large (+8900 lines)

## Summary

Complete Firmware v3 implementation with modular architecture, TMC motor controller support, and new command handlers.

## Upstream Changes

**Files Added:** 49 files in `firmware/main_controller_teensy41/`

```
firmware/main_controller_teensy41/
├── main_controller_teensy41.ino    # Main Arduino sketch
└── src/
    ├── commands/
    │   ├── commands.cpp/h          # Command dispatcher
    │   ├── light_commands.cpp/h    # Lighting control
    │   └── stage_commands.cpp/h    # Stage movement
    ├── def/
    │   ├── def.h                   # Default definitions
    │   ├── def_gravitymachine.h
    │   ├── def_octopi.h
    │   ├── def_platereader.h
    │   ├── def_squid.h
    │   └── def_squid_vertical.h
    ├── tmc/
    │   ├── TMC4361A.cpp/h          # TMC4361A driver
    │   ├── TMC4361A_Constants.h
    │   ├── TMC4361A_Fields.h
    │   ├── TMC4361A_Register.h
    │   ├── TMC4361A_TMC2660_Utils.cpp/h
    │   └── helpers/                # TMC helper utilities
    ├── utils/
    │   └── crc8.cpp/h              # CRC8 checksum
    ├── constants.h
    ├── functions.cpp/h
    ├── global_defs.cpp/h
    ├── globals.cpp/h
    ├── init.cpp/h
    ├── operations.cpp/h
    └── serial_communication.cpp/h
```

## arch_v2 Target

**Location:** `firmware/octopi_firmware_v3/main_controller_teensy41/`

This is a direct copy - firmware is not part of the 3-layer architecture refactoring.

## Implementation Checklist

### Step 1: Verify Current State
- [x] Check if `firmware/octopi_firmware_v3/` exists in arch_v2 (did not exist)
- [x] If exists, compare with upstream to identify conflicts (N/A - new directory)
- [x] Document any arch_v2-specific firmware modifications (N/A - new files)

### Step 2: Port Firmware
- [x] Create directory structure: `firmware/octopi_firmware_v3/main_controller_teensy41/src/`
- [x] Copy main sketch: `main_controller_teensy41.ino`
- [x] Copy all source files maintaining structure
- [x] Verify all files are present (48 files total)

### Step 3: Verification
- [ ] Compile firmware with Arduino IDE/PlatformIO (if available)
- [ ] Verify no syntax errors
- [ ] Check that all includes resolve correctly

## Key Components

### Command System
- `commands.cpp` - Main command dispatcher
- `light_commands.cpp` - LED/illumination control
- `stage_commands.cpp` - Motor movement commands

### TMC Motor Control
- `TMC4361A.cpp` - TMC4361A motion controller driver
- `TMC4361A_TMC2660_Utils.cpp` - Combined TMC4361A + TMC2660 utilities
- Supports: position control, velocity control, homing

### Configuration
- Multiple `def_*.h` files for different microscope configurations
- Select configuration by modifying `def.h` include

## Testing

- [ ] Verify firmware compiles
- [ ] Test on hardware if available (Teensy 4.1)
- [ ] Verify serial communication protocol compatibility

## Notes

- This is new firmware, not a modification to existing code
- May require PlatformIO or Arduino IDE setup for compilation testing
- Hardware testing requires physical Teensy 4.1 board

## Port Status

**Status:** COMPLETED
**Date:** 2025-12-29
**Notes:**
- Ported 48 files from upstream/master commit a4db687
- Files copied to `firmware/octopi_firmware_v3/main_controller_teensy41/`
- All files verified to match upstream exactly
- Compilation testing deferred (requires Arduino/PlatformIO)
