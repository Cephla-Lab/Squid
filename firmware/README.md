# Firmware

## Directory Structure

```
firmware/
├── controller/          # Main motion controller (Teensy 4.1)
├── joystick/            # Joystick/control panel (Teensy LC)
└── legacy/              # Archived firmware versions
```

## Controller

The main motion controller firmware for Teensy 4.1. Handles:
- XYZ stage motion control (TMC4361A + TMC2660 drivers)
- Illumination control (lasers and LED matrix)
- Camera triggering
- Serial communication with host software

### Building

1. Install [Teensyduino](https://www.pjrc.com/teensy/teensyduino.html)
2. Open `controller/main_controller_teensy41.ino` in Arduino IDE
3. Select Board: "Teensy 4.1"
4. Select the appropriate `def_*.h` configuration in the .ino file
5. Upload to Teensy

## Joystick

Control panel firmware for Teensy LC. Handles joystick input and button states.

### Building

1. Install [Teensyduino](https://www.pjrc.com/teensy/teensyduino.html)
2. Open `joystick/control_panel_teensyLC.ino` in Arduino IDE
3. Select Board: "Teensy LC"
4. Upload to Teensy

## Legacy

Archived firmware versions kept for reference. Not actively maintained.
