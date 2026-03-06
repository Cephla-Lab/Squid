# Illumination Control

This document describes the illumination control system, including the hardware ports, software APIs, and when to use each approach.

## Overview

The Squid microscope controller supports up to 16 illumination ports (D1-D16), with D1-D5 currently implemented. Each port controls:
- **DAC output** - Analog voltage for intensity control (0-2.5V range)
- **GPIO output** - Digital on/off control

Two software APIs are available:
- **Legacy API** - Single channel at a time (standard acquisition)
- **Multi-port API** - Multiple channels ON simultaneously (firmware v1.0+)

## Hardware Ports

| Port | Port Index | Source Code | DAC Channel | GPIO Pin | Typical Use |
|------|------------|-------------|-------------|----------|-------------|
| D1 | 0 | 11 | 0 | PIN_D1 | 405nm (violet) |
| D2 | 1 | 12 | 1 | PIN_D2 | 470/488nm (blue) |
| D3 | 2 | 14 | 2 | PIN_D3 | 545-561nm (green) |
| D4 | 3 | 13 | 3 | PIN_D4 | 638/640nm (red) |
| D5 | 4 | 15 | 4 | PIN_D5 | 730-750nm (NIR) |

**Important:** D3 and D4 have non-sequential source codes (14 and 13) for historical API compatibility. The multi-port API uses sequential port indices (0-4) which map correctly internally.

## Intensity Scaling

The DAC output is scaled by `illumination_intensity_factor`:
- **0.6** - Squid LEDs (0-1.5V output range)
- **0.8** - Squid laser engine (0-2V output range)
- **1.0** - Full range (0-2.5V, when DAC gain is 1 instead of 2)

## Legacy API (Single Channel)

Use when only ONE illumination source is needed at a time. This is the standard acquisition workflow.

**Works with any firmware version.**

### Methods

```python
from control.lighting import IlluminationController

# Set intensity by wavelength (uses channel mapping)
controller.set_intensity(wavelength=488, intensity=50)  # 488nm at 50%

# Turn on/off the currently selected source
controller.turn_on_illumination()
controller.turn_off_illumination()
```

### Example: Sequential Multi-Channel Acquisition

```python
channels = [488, 561, 640]  # Blue, Green, Red

for channel in channels:
    controller.set_intensity(channel, intensity=50)
    controller.turn_on_illumination()
    camera.capture()
    controller.turn_off_illumination()
```

## Multi-Port API (Multiple Channels)

Use when MULTIPLE illumination sources must be ON simultaneously.

**Requires firmware v1.0 or later.** Methods automatically check firmware version and raise `RuntimeError` if not supported.

### Methods

```python
# Set intensity by port index (0=D1, 1=D2, 2=D3, 3=D4, 4=D5)
controller.set_port_intensity(port_index=0, intensity=50)

# Turn on/off specific ports
controller.turn_on_port(port_index=0)
controller.turn_off_port(port_index=0)

# Combined: set intensity and on/off in one command
controller.set_port_illumination(port_index=0, intensity=50, turn_on=True)

# Turn on multiple ports simultaneously
controller.turn_on_multiple_ports([0, 1, 2])

# Turn off all ports
controller.turn_off_all_ports()

# Query active ports
active = controller.get_active_ports()  # Returns list of port indices
```

### Example: Simultaneous Multi-Color Imaging

```python
# Set intensities for multiple channels
controller.set_port_intensity(0, 30)  # D1 (405nm) at 30%
controller.set_port_intensity(1, 50)  # D2 (488nm) at 50%
controller.set_port_intensity(3, 40)  # D4 (640nm) at 40%

# Turn on all three simultaneously
controller.turn_on_multiple_ports([0, 1, 3])

# Capture with all three illumination sources
camera.capture()

# Turn off all
controller.turn_off_all_ports()
```

## When to Use Each API

| Scenario | API | Why |
|----------|-----|-----|
| Standard sequential acquisition | Legacy | Simpler, works with any firmware |
| Z-stack with single channel per slice | Legacy | Standard workflow |
| Simultaneous multi-color imaging | Multi-port | Multiple sources ON at once |
| Custom light mixing experiments | Multi-port | Independent control of each port |
| Backward compatibility required | Legacy | Works with older firmware |

## Firmware Version Detection

The software automatically detects firmware version from MCU responses:

```python
# Check firmware version
version = microcontroller.firmware_version  # Returns tuple (major, minor)

# Check if multi-port is supported
if microcontroller.supports_multi_port():
    # Use multi-port API
    controller.turn_on_multiple_ports([0, 1])
else:
    # Fall back to legacy API
    controller.turn_on_illumination()
```

## Port Index ↔ Source Code Mapping

For developers working with both APIs, use the mapping functions in `control._def`:

```python
from control._def import source_code_to_port_index, port_index_to_source_code

# Legacy source code → port index
port = source_code_to_port_index(14)  # Returns 2 (D3)
port = source_code_to_port_index(13)  # Returns 3 (D4)

# Port index → legacy source code
source = port_index_to_source_code(2)  # Returns 14 (D3)
source = port_index_to_source_code(3)  # Returns 13 (D4)
```

## MCU Protocol Commands

For low-level debugging or firmware development:

| Command | Code | Description |
|---------|------|-------------|
| SET_ILLUMINATION | 5 | Legacy: set source + intensity |
| TURN_ON_ILLUMINATION | 6 | Legacy: turn on current source |
| TURN_OFF_ILLUMINATION | 7 | Legacy: turn off current source |
| SET_PORT_INTENSITY | 34 | Multi-port: set DAC for port |
| TURN_ON_PORT | 35 | Multi-port: turn on GPIO for port |
| TURN_OFF_PORT | 36 | Multi-port: turn off GPIO for port |
| SET_PORT_ILLUMINATION | 37 | Multi-port: combined intensity + on/off |
| SET_MULTI_PORT_MASK | 38 | Multi-port: control multiple ports |
| TURN_OFF_ALL_PORTS | 39 | Multi-port: turn off all ports |

## Troubleshooting

### "Firmware does not support multi-port illumination commands"

The connected MCU has firmware older than v1.0. Options:
1. Update the firmware to v1.0+
2. Use the legacy API instead

### Intensity appears wrong

Check `illumination_intensity_factor` in firmware matches your hardware:
- LEDs: 0.6
- Laser engine: 0.8
- Full range: 1.0

### D3/D4 channels swapped

The legacy source codes are non-sequential (D3=14, D4=13). If using the multi-port API with port indices, the mapping is handled automatically. If mixing APIs, use the mapping functions to convert.
