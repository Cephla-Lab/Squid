# TTL-Only Light Source Controller

Simplified firmware for Teensy 4.1 that only controls 5 TTL triggered light sources. Compatible with existing Squid software - **no software changes required**.

## Features

- 5 TTL outputs for laser/LED enable (pins 1-5)
- DAC-based intensity control via DAC80508
- Full protocol compatibility with Squid software

## What's NOT Included (compared to main firmware)

- XYZ stage control
- LED matrix (APA102)
- Filter wheel
- Joystick panel support
- Camera triggering

All unsupported commands are ACK'd with `COMPLETED_WITHOUT_ERRORS`, so the software continues to work without timeouts.

## Pin Mapping

| Pin | Function | Notes |
|-----|----------|-------|
| 1 | LASER_405nm | TTL output (HIGH = on) |
| 2 | LASER_488nm | TTL output |
| 3 | LASER_561nm | TTL output |
| 4 | LASER_638nm | TTL output |
| 5 | LASER_730nm | TTL output |
| 33 | DAC8050x_CS | SPI chip select |
| 11 | SPI_MOSI | SPI data out (to DAC) |
| 12 | SPI_MISO | SPI data in |
| 13 | SPI_SCK | SPI clock |

## DAC Channel Mapping (Intensity Control)

| DAC Channel | Light Source |
|-------------|--------------|
| 0 | 405nm |
| 1 | 488nm |
| 2 | 561nm |
| 3 | 638nm |
| 4 | 730nm |

## Protocol

Uses the same 8-byte command / 24-byte response protocol as the main firmware.

### Supported Commands

| Code | Name | Description |
|------|------|-------------|
| 10 | TURN_ON_ILLUMINATION | Turn on current light source |
| 11 | TURN_OFF_ILLUMINATION | Turn off current light source |
| 12 | SET_ILLUMINATION | Set source and intensity |
| 15 | ANALOG_WRITE_ONBOARD_DAC | Direct DAC write |
| 16 | SET_DAC80508_REFDIV_GAIN | Configure DAC gain |
| 17 | SET_ILLUMINATION_INTENSITY_FACTOR | Set global intensity scaling |
| 254 | INITIALIZE | Reset to initial state |
| 255 | RESET | Reset command tracking |

### Unsupported Commands (ACK'd without execution)

All stage movement, homing, PID, filter wheel, and LED matrix commands are acknowledged but not executed. This ensures software compatibility.

## Building

Requires PlatformIO.

```bash
cd firmware/controller_ttl_only
pio run
```

## Flashing

```bash
pio run -t upload
```

Or use Teensy Loader directly with the generated `.hex` file.

## Code Size Comparison

| Firmware | Flash | RAM |
|----------|-------|-----|
| Main controller | ~100 KB | ~30 KB |
| TTL-only | ~15 KB | ~5 KB |

Approximately 85% reduction in code size.
