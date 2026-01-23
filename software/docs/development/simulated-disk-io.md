# Simulated Disk I/O

A development feature that simulates disk writing without actually saving files to SSD. Useful for testing acquisition throughput, debugging RAM/CPU bottlenecks, and avoiding SSD wear during development.

## How It Works

```
Real I/O:        Image → Encode/Compress → Write to SSD → Done
Simulated I/O:   Image → Encode/Compress → BytesIO buffer → Throttle delay → Discard
```

**What gets exercised:**
- Memory allocation for compression buffers (RAM pressure)
- CPU work for encoding (TIFF/LZW compression)
- Timing scaled to configured disk speed

**What's skipped:**
- Actual file system writes
- SSD wear
- File accumulation

## Enabling Simulated Disk I/O

1. Go to **Settings > Preferences**
2. Click the **Advanced** tab
3. Scroll down to **Development Settings**
4. Check **Simulated Disk I/O**
5. Configure speed and compression options
6. Click **OK** and **restart the application**

## Settings

| Setting | Description | Restart Required |
|---------|-------------|------------------|
| **Simulated Disk I/O** | Enable/disable the feature | Yes (for warning banner/dialog) |
| **Simulated Write Speed** | Target throughput in MB/s (10-3000) | No (next acquisition) |
| **Simulate Compression** | Exercise LZW compression for realistic CPU/RAM usage | No (next acquisition) |

### Speed Guidelines

| Disk Type | Typical Speed |
|-----------|---------------|
| HDD | 50-100 MB/s |
| SATA SSD | 200-500 MB/s |
| NVMe SSD | 1000-3000 MB/s |

## Visual Indicators

When simulated disk I/O is enabled:

1. **Startup Warning Dialog** - Shows when the application starts
2. **Red Warning Banner** - Persistent banner at top of main window: "SIMULATED DISK I/O - Images are encoded but NOT saved to disk"

## What Still Works

- **Plate view updates** - Downsampled images are still generated for display (just not saved to disk)
- **Live view** - Camera streaming works normally
- **All acquisition workflows** - Single image, multi-point, time-lapse, z-stack

## Technical Details

### Implementation

The feature works by intercepting image saving at the job level:

- `SaveImageJob` - Single TIFF files
- `SaveOMETiffJob` - OME-TIFF stacks (5D: TZCYX)
- `DownsampledViewJob` - Plate view thumbnails (uses existing `skip_saving` flag)

Each job checks `control._def.SIMULATED_DISK_IO_ENABLED` and, if true, encodes the image to a `BytesIO` buffer, throttles based on configured speed, then discards the buffer.

### OME-TIFF Stack Simulation

For OME-TIFF stacks, the simulation tracks:
- Stack initialization overhead (~4KB for OME-XML header)
- Per-plane encoding and throttling
- Stack finalization overhead (~8KB for OME-XML update)

This provides realistic timing for multi-dimensional acquisitions.

### Subprocess Behavior

Settings are read by the `JobRunner` subprocess at acquisition start. Since each acquisition spawns a fresh subprocess:
- Speed/compression changes take effect on the **next acquisition**
- No need to restart the app for these settings
- Only the enable/disable toggle requires restart (for UI elements)

## Use Cases

1. **Throughput Testing** - Test acquisition speed without filling disk
2. **RAM Debugging** - Monitor memory usage during encoding
3. **CI/Testing** - Run acquisition tests without disk I/O
4. **Development** - Iterate quickly without cleaning up test files

## Logging

When verbose logging is enabled (`--verbose`), simulation operations are logged at DEBUG level:

```
DEBUG:control.core.io_simulation:Simulated TIFF write: 4194304 bytes, shape=(2048, 2048)
DEBUG:control.core.io_simulation:Initialized simulated OME stack: /path/to/stack, expected planes: 12
DEBUG:control.core.io_simulation:Simulated OME plane write: 4194304 bytes, plane=0-0-0, progress=1/12
```
