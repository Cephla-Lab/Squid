# Configuration API Reference

This document provides a technical reference for developers working with Squid's configuration system. It covers `ConfigRepository`, `ChannelConfigService`, Pydantic models, and best practices for accessing configurations in code.

## Overview

The configuration system is built on:

- **ConfigRepository** (`squid.core.config.repository`): Centralized config I/O with caching (pure Python, no Qt)
- **ChannelConfigService** (`squid.backend.managers.channel_config_service`): EventBus wrapper around ConfigRepository
- **Pydantic Models** (`squid.core.config.models`): Type-safe configuration validation
- **Hierarchical Merge**: Combines general and objective-specific settings

### Key Design Decisions

1. **Pure Python Core**: ConfigRepository has no Qt dependencies, enabling use in subprocesses
2. **EventBus Integration**: ChannelConfigService handles UI commands and publishes state changes
3. **Lazy Loading**: Configs loaded on first access, cached for performance
4. **Profile Isolation**: Switching profiles clears cache to ensure fresh data
5. **Backward Compatibility**: AcquisitionChannel provides convenience properties matching the old ChannelMode API

---

## ChannelConfigService

The `ChannelConfigService` is the primary interface for most code. It wraps `ConfigRepository` with EventBus integration and illumination source resolution.

```python
from squid.backend.managers.channel_config_service import ChannelConfigService
```

### Getting Channels

```python
# Get all channels for an objective (merged general + objective overrides)
channels = service.get_configurations("20x")

# Get only enabled channels
channels = service.get_enabled_configurations("20x")

# Get a single channel by name
channel = service.get_channel_configuration_by_name("20x", "Fluorescence 488 nm Ex")
```

### Updating Settings

```python
# Update a single channel setting
service.update_configuration("20x", "Fluorescence 488 nm Ex", "ExposureTime", 50.0)
service.update_configuration("20x", "Fluorescence 488 nm Ex", "AnalogGain", 5.0)
service.update_configuration("20x", "Fluorescence 488 nm Ex", "IlluminationIntensity", 30.0)
```

**Supported settings:**

| Setting | Model Field |
|---------|-------------|
| `"ExposureTime"` | `camera_settings.exposure_time_ms` |
| `"AnalogGain"` | `camera_settings.gain_mode` |
| `"IlluminationIntensity"` | `illumination_settings.intensity` |
| `"ZOffset"` | `z_offset_um` |

### Confocal Mode

```python
# Toggle confocal/widefield mode
service.toggle_confocal_widefield(True)   # Enable confocal
service.toggle_confocal_widefield(False)  # Back to widefield

# Check mode
if service.is_confocal_mode():
    ...
```

### Protocol Overrides

```python
# Apply batch overrides before imaging
service.apply_channel_overrides("20x", [
    {
        "name": "Fluorescence 488 nm Ex",
        "exposure_time_ms": 100.0,
        "illumination_intensity": 50.0,
    },
])
```

### Acquisition Output

```python
# Save settings used during acquisition
service.save_acquisition_output(
    output_dir=Path("/path/to/experiment"),
    objective="20x",
    channels=channels,
)
# Creates: /path/to/experiment/acquisition_channels.yaml
```

### Event Handlers

ChannelConfigService automatically handles these events via `@handles`:

| Event | Action |
|-------|--------|
| `UpdateChannelConfigurationCommand` | Updates exposure/gain/intensity for a channel |
| `SetConfocalModeCommand` | Toggles confocal mode, publishes `ConfocalModeChanged` |

---

## ConfigRepository

The `ConfigRepository` class provides all configuration I/O operations. It has no Qt dependencies.

```python
from squid.core.config.repository import ConfigRepository

# Default: uses software/ as base path
config_repo = ConfigRepository()

# Custom base path (for testing)
config_repo = ConfigRepository(base_path=Path("/custom/path"))
```

### Profile Management

```python
# Set profile (validates existence, clears cache)
config_repo.set_profile("default_profile")

# Get current profile
current = config_repo.current_profile  # May be None

# Create empty profile
config_repo.create_profile("new_profile")

# Copy profile (for "Save As")
config_repo.copy_profile("source_profile", "destination_profile")

# Load profile with default config generation
config_repo.load_profile("my_profile", objectives=["20x", "40x", "60x"])
```

### Machine Configs

Machine configs are cached indefinitely (hardware doesn't change at runtime).

```python
# Illumination channel config
ill_config = config_repo.get_illumination_config()
if ill_config:
    channel = ill_config.get_channel_by_name("Fluorescence 488 nm Ex")
    source_code = ill_config.get_source_code(channel)

# Confocal config (None if no confocal)
confocal_config = config_repo.get_confocal_config()

# Check for confocal presence
if config_repo.has_confocal():
    ...
```

### Channel Configs

```python
# Get general config (raw, no merge)
general = config_repo.get_general_config()

# Get objective config (raw, no merge)
obj_config = config_repo.get_objective_config("20x")

# Get merged channels for an objective (recommended)
channels = config_repo.get_merged_channels("20x")
# Returns: List[AcquisitionChannel]

# Get available objectives for profile
objectives = config_repo.get_available_objectives()
```

### Convenience Methods

```python
# Update a single channel setting
success = config_repo.update_channel_setting(
    objective="20x",
    channel_name="Fluorescence 488 nm Ex",
    setting="ExposureTime",
    value=50.0,
)
```

### Laser AF Configs

```python
# Get laser AF config for objective
laser_af = config_repo.get_laser_af_config("20x")
if laser_af:
    print(f"Pixel to um: {laser_af.pixel_to_um}")

# Save laser AF config
config_repo.save_laser_af_config("my_profile", "20x", laser_af)
```

### Cache Management

```python
# Clear profile cache (done automatically on profile switch)
config_repo.clear_profile_cache()

# Clear all caches (rarely needed)
config_repo.clear_all_cache()
```

---

## Pydantic Models

All configuration models are in `squid.core.config.models`.

### AcquisitionChannel

The main model for acquisition channel settings.

```python
from squid.core.config.models import AcquisitionChannel, CameraSettings, IlluminationSettings

# Construct a channel
channel = AcquisitionChannel(
    name="Fluorescence 488 nm Ex",
    display_color="#1FFF00",
    camera_settings=CameraSettings(exposure_time_ms=20.0, gain_mode=10.0),
    illumination_settings=IlluminationSettings(
        illumination_channel="Fluorescence 488 nm Ex",
        intensity=20.0,
    ),
)

# Backward-compatible convenience properties
exposure = channel.exposure_time           # -> camera_settings.exposure_time_ms
gain = channel.analog_gain                 # -> camera_settings.gain_mode
intensity = channel.illumination_intensity # -> illumination_settings.intensity
z_off = channel.z_offset                   # -> z_offset_um
color = channel.color                      # -> display_color
name = channel.primary_illumination_channel  # -> illumination_settings.illumination_channel

# Setters (modify in-place)
channel.exposure_time = 50.0
channel.analog_gain = 5.0
channel.illumination_intensity = 30.0

# Apply confocal override
effective = channel.get_effective_settings(confocal_mode=True)
# Returns new AcquisitionChannel with confocal_override applied
```

**Note on `illumination_source`:** The integer hardware source code is injected by `ChannelConfigService` via `__dict__` (the model uses `extra="forbid"`). It is available as `channel.illumination_source` only after going through `ChannelConfigService.get_configurations()`.

### CameraSettings

```python
from squid.core.config.models import CameraSettings

settings = CameraSettings(
    exposure_time_ms=20.0,
    gain_mode=10.0,
    pixel_format=None,  # Optional
)
```

### IlluminationSettings

```python
from squid.core.config.models import IlluminationSettings

settings = IlluminationSettings(
    illumination_channel="Fluorescence 488 nm Ex",  # References illumination_channel_config.yaml
    intensity=20.0,  # 0-100%
)
```

### IlluminationChannelConfig

```python
from squid.core.config.models import IlluminationChannelConfig

config = config_repo.get_illumination_config()

# Access channels
for channel in config.channels:
    print(f"{channel.name}: {channel.type}, port={channel.controller_port}")

# Get channel by name
channel = config.get_channel_by_name("Fluorescence 488 nm Ex")

# Get source code for channel
source_code = config.get_source_code(channel)
```

### GeneralChannelConfig & ObjectiveChannelConfig

```python
from squid.core.config.models import (
    GeneralChannelConfig,
    ObjectiveChannelConfig,
    merge_channel_configs,
)

# Load configs
general = config_repo.get_general_config()
objective = config_repo.get_objective_config("20x")

# Get channel by name
channel = general.get_channel_by_name("Fluorescence 488 nm Ex")

# Merge configs manually
merged_channels = merge_channel_configs(general, objective)
# Returns: List[AcquisitionChannel]
```

### LaserAFConfig

```python
from squid.core.config.models import LaserAFConfig

laser_af = config_repo.get_laser_af_config("20x")

# Access parameters
print(f"Range: {laser_af.laser_af_range} um")
print(f"Averaging: {laser_af.laser_af_averaging_n}")
print(f"Mode: {laser_af.spot_detection_mode}")

# Reference image
if laser_af.has_reference:
    image = laser_af.reference_image_cropped  # numpy array
```

### ConfocalConfig

```python
from squid.core.config.models import ConfocalConfig

config = config_repo.get_confocal_config()
if config:
    filter_name = config.get_filter_name(wheel_id=1, slot=2)
```

---

## Access Patterns

### In Widgets (Qt)

Widgets access channels through the `ChannelConfigService` (exposed as `channelConfigurationManager` on the GUI object for backward compatibility):

```python
class MyWidget(QWidget):
    def __init__(self, channel_config_service, objective_store):
        self.config_service = channel_config_service
        self.objectives = objective_store

    def update_channels(self):
        objective = self.objectives.current_objective
        channels = self.config_service.get_configurations(objective)

        for channel in channels:
            self.add_channel_button(channel.name, channel.display_color)
```

### In Controllers

Controllers receive `ChannelConfigService` via constructor injection:

```python
class MyController:
    def __init__(self, channel_config_manager: ChannelConfigService, ...):
        self.config_service = channel_config_manager

    def get_channel(self, objective, name):
        return self.config_service.get_channel_configuration_by_name(objective, name)
```

### In Subprocesses

ConfigRepository is pure Python, safe for multiprocessing:

```python
def worker_process(base_path, profile, objective):
    config_repo = ConfigRepository(base_path=base_path)
    config_repo.set_profile(profile)

    channels = config_repo.get_merged_channels(objective)
    # Process channels...
```

---

## Error Handling

### Common Exceptions

```python
# ValueError: Profile doesn't exist
try:
    config_repo.set_profile("nonexistent")
except ValueError as e:
    print(f"Profile error: {e}")

# ValidationError: Invalid YAML structure
from pydantic import ValidationError
try:
    config = config_repo.get_general_config()
except ValidationError as e:
    print(f"Config validation failed: {e}")
```

### Graceful Degradation

Many methods return `None` if config doesn't exist:

```python
# Safe access pattern
confocal = config_repo.get_confocal_config()
if confocal is not None:
    # System has confocal
    ...

# Safe channel access
obj_config = config_repo.get_objective_config("20x")
if obj_config is None:
    # Fall back to general-only
    channels = list(config_repo.get_general_config().channels)
```

---

## Testing

### Using Test Fixtures

```python
import tempfile
from pathlib import Path
from squid.core.config.repository import ConfigRepository

def test_config_loading():
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # Create structure
        (base / "machine_configs").mkdir()
        (base / "user_profiles" / "test" / "channel_configs").mkdir(parents=True)
        (base / "user_profiles" / "test" / "laser_af_configs").mkdir(parents=True)

        # Create minimal illumination config
        (base / "machine_configs" / "illumination_channel_config.yaml").write_text("""
version: 1
controller_port_mapping: {}
channels: []
""")

        config_repo = ConfigRepository(base_path=base)
        config_repo.set_profile("test")

        assert config_repo.get_illumination_config() is not None
```

### Mocking ConfigRepository

```python
from unittest.mock import Mock
from squid.core.config.repository import ConfigRepository

def test_widget_with_mocked_config():
    mock_repo = Mock(spec=ConfigRepository)
    mock_repo.get_merged_channels.return_value = [
        Mock(name="Test Channel", display_color="#FF0000")
    ]

    # Test your widget/controller with mock
```

---

## See Also

- [Configuration System](configuration-system.md) - User-facing documentation
- [Configuration Migration](configuration-migration.md) - Upgrading from legacy format
- Source: `squid/core/config/repository.py`
- Models: `squid/core/config/models/`
- Service: `squid/backend/managers/channel_config_service.py`
