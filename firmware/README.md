# Firmware

## Directory Structure

```
firmware/
├── controller/          # Main motion controller (Teensy 4.1)
├── joystick/            # Joystick/control panel (Teensy LC)
└── legacy/              # Archived firmware versions
```

## Building with PlatformIO (Recommended)

[PlatformIO](https://platformio.org/) is the recommended build system for firmware development. It provides consistent builds, dependency management, and command-line tooling.

### Installation

```bash
# Using pip
pip install platformio

# Or using Homebrew (macOS)
brew install platformio
```

### Quick Start

```bash
# Build controller firmware
cd firmware/controller
pio run

# Build joystick firmware
cd firmware/joystick
pio run
```

### Common Commands

| Command | Description |
|---------|-------------|
| `pio run` | Compile firmware |
| `pio run -t upload` | Compile and upload to device |
| `pio run -t clean` | Clean build artifacts |
| `pio device monitor` | Open serial monitor |
| `pio run -t upload && pio device monitor` | Upload and monitor |

### Build Output

After successful compilation, the firmware binary is located at:
- `.pio/build/teensy41/firmware.hex` (controller)
- `.pio/build/teensyLC/firmware.hex` (joystick)

### Troubleshooting

**Device not found during upload:**
- Ensure Teensy is connected via USB
- Press the button on Teensy to enter bootloader mode
- Check that no other application is using the serial port

**Permission denied (Linux):**
```bash
sudo usermod -a -G dialout $USER
# Log out and back in
```

**First build is slow:**
- PlatformIO downloads toolchains and libraries on first run
- Subsequent builds are much faster (incremental compilation)

## Building with Arduino IDE (Alternative)

If you prefer Arduino IDE:

### Controller (Teensy 4.1)

1. Install [Teensyduino](https://www.pjrc.com/teensy/teensyduino.html)
2. Open `controller/main_controller_teensy41.ino` in Arduino IDE
3. Select Board: "Teensy 4.1"
4. Click Upload

### Joystick (Teensy LC)

1. Install [Teensyduino](https://www.pjrc.com/teensy/teensyduino.html)
2. Open `joystick/control_panel_teensyLC.ino` in Arduino IDE
3. Select Board: "Teensy LC"
4. Click Upload

## Controller

The main motion controller firmware for Teensy 4.1. Handles:
- XYZ stage motion control (TMC4361A + TMC2660 drivers)
- Illumination control (lasers and LED matrix)
- Camera triggering
- Serial communication with host software

### Configuration

Hardware-specific settings are in `src/def/def_v1.h`. This includes:
- Motor parameters (steps per rev, microstepping, current)
- Stage limits and velocities
- Joystick sensitivity
- Limit switch polarity

### Source Structure

```
controller/
├── main_controller_teensy41.ino    # Entry point
├── platformio.ini                   # PlatformIO config
└── src/
    ├── commands/                    # Command handlers
    │   ├── commands.cpp/h          # General commands
    │   ├── light_commands.cpp/h    # Illumination control
    │   └── stage_commands.cpp/h    # Motion control
    ├── def/
    │   └── def_v1.h                # Hardware configuration
    ├── tmc/                         # TMC stepper driver library
    ├── utils/
    │   └── crc8.cpp/h              # CRC calculation
    ├── init.cpp/h                   # Initialization routines
    ├── operations.cpp/h             # Main loop operations
    ├── serial_communication.cpp/h   # Serial protocol handling
    ├── functions.cpp/h              # Utility functions
    ├── globals.cpp/h                # Global state variables
    └── constants.h                  # Constants and pin definitions
```

## Joystick

Control panel firmware for Teensy LC. Handles:
- Joystick X/Y axis input
- Rotary encoder for focus control
- Button states
- Serial communication with main controller

## Legacy

Archived firmware versions kept for reference. Not actively maintained.
