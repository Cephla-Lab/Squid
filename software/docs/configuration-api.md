# Configuration API Reference

This document provides a technical reference for developers working with Squid's configuration system. It covers the `ConfigRepository` class, Pydantic models, and best practices for accessing configurations in code.

## Overview

The configuration system is built on:

- **ConfigRepository**: Centralized config I/O with caching (pure Python, no Qt)
- **Pydantic Models**: Type-safe configuration validation
- **Hierarchical Merge**: Combines general and objective-specific settings

### Key Design Decisions

1. **Pure Python**: ConfigRepository has no Qt dependencies, enabling use in subprocesses
2. **Lazy Loading**: Configs loaded on first access, cached for performance
3. **Profile Isolation**: Switching profiles clears cache to ensure fresh data
4. **Single Source of Truth**: All config access should go through ConfigRepository

---

## ConfigRepository

The `ConfigRepository` class (`control/core/config/repository.py`) provides all configuration I/O operations.

### Initialization

```python
from control.core.config import ConfigRepository

# Default: uses software/ as base path
config_repo = ConfigRepository()

# Custom base path (for testing)
config_repo = ConfigRepository(base_path=Path("/custom/path"))
```

**Directory structure assumed:**
```
base_path/
├── machine_configs/
└── user_profiles/
```

### Profile Management

```python
# List available profiles
profiles = config_repo.get_available_profiles()
# Returns: ['default', 'experiment_1', 'high_throughput']

# Get current profile
current = config_repo.current_profile  # May be None

# Set profile (validates existence, clears cache)
config_repo.set_profile("my_profile")

# Load profile with default config generation
config_repo.load_profile("my_profile", objectives=["20x", "40x", "60x"])
# - Creates default configs if profile has none
# - Sets profile as current

# Create empty profile
config_repo.create_profile("new_profile")

# Copy profile (for "Save As")
config_repo.copy_profile("source_profile", "destination_profile")

# Check if profile exists
if config_repo.profile_exists("my_profile"):
    ...

# Check if profile has configs
if config_repo.profile_has_configs("my_profile"):
    ...

# Get profile path
path = config_repo.get_profile_path("my_profile")
# Returns: Path("user_profiles/my_profile")
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

# Camera mappings
camera_config = config_repo.get_camera_mappings()

# Save machine configs (updates cache)
config_repo.save_illumination_config(ill_config)
config_repo.save_confocal_config(confocal_config)
config_repo.save_camera_mappings(camera_config)
```

### Channel Configs

Channel configs are cached per-profile. Cache is cleared on profile switch.

```python
# Get general config (raw, no merge)
general = config_repo.get_general_config()

# Get objective config (raw, no merge)
obj_config = config_repo.get_objective_config("20x")

# Get merged channels for an objective (recommended)
channels = config_repo.get_merged_channels(
    objective="20x",
    profile=None,           # Uses current profile
    confocal_mode=False     # Apply confocal overrides?
)
# Returns: List[AcquisitionChannel]

# Get available objectives for profile
objectives = config_repo.get_available_objectives()
# Returns: ['10x', '20x', '40x', '60x']

# Save configs
config_repo.save_general_config("my_profile", general)
config_repo.save_objective_config("my_profile", "20x", obj_config)
```

### Convenience Methods

```python
# Update a single channel setting (creates objective config if needed)
success = config_repo.update_channel_setting(
    objective="20x",
    channel_name="Fluorescence 488 nm Ex",
    setting="ExposureTime",           # "ExposureTime", "AnalogGain", "IlluminationIntensity"
    value=50.0,
    profile=None                      # Uses current profile
)
```

**Supported settings:**

| Setting | Model Field |
|---------|-------------|
| `"ExposureTime"` | `camera_settings.exposure_time_ms` |
| `"AnalogGain"` | `camera_settings.gain_mode` |
| `"IlluminationIntensity"` | `illumination_settings.intensity` |

### Laser AF Configs

```python
# Get laser AF config for objective
laser_af = config_repo.get_laser_af_config("20x")
if laser_af:
    print(f"Pixel to um: {laser_af.pixel_to_um}")

# Save laser AF config
config_repo.save_laser_af_config("my_profile", "20x", laser_af)
```

### Acquisition Output

Save acquisition settings to experiment directories for reproducibility.

```python
# Save settings used during acquisition
config_repo.save_acquisition_output(
    output_dir="/path/to/experiment",
    objective="20x",
    channels=channels,          # List[AcquisitionChannel]
    confocal_mode=False
)
# Creates: /path/to/experiment/acquisition_channels.yaml
```

### Cache Management

```python
# Clear profile cache (done automatically on profile switch)
config_repo.clear_profile_cache()

# Clear all caches (rarely needed)
config_repo.clear_all_cache()
```

### Generic I/O

Save any Pydantic model to an arbitrary path:

```python
config_repo.save_to_path(Path("/custom/path/config.yaml"), my_model)
```

---

## Pydantic Models

All configuration models are in `control/models/`.

### IlluminationChannelConfig

```python
from control.models import (
    IlluminationChannelConfig,
    IlluminationChannel,
    IlluminationType,
)

# Load from file
config = config_repo.get_illumination_config()

# Access channels
for channel in config.channels:
    print(f"{channel.name}: {channel.type}, port={channel.controller_port}")

# Get channel by name
channel = config.get_channel_by_name("Fluorescence 488 nm Ex")

# Get source code for channel
source_code = config.get_source_code(channel)

# Get channel by source code
channel = config.get_channel_by_source_code(12)  # 488nm laser

# Get available ports
ports = config.get_available_ports()
# Returns: ['USB1', 'USB2', ..., 'D1', 'D2', ...]
```

**IlluminationType enum:**
```python
class IlluminationType(str, Enum):
    EPI_ILLUMINATION = "epi_illumination"      # Lasers
    TRANSILLUMINATION = "transillumination"    # LED matrix
```

### AcquisitionChannel

The main model for acquisition channel settings.

```python
from control.models import AcquisitionChannel

# Get merged channels
channels = config_repo.get_merged_channels("20x")
channel = channels[0]

# Convenience properties (single-camera, single-illumination)
exposure = channel.exposure_time           # Primary camera exposure (ms)
gain = channel.analog_gain                 # Primary camera gain
color = channel.display_color              # Hex color string
intensity = channel.illumination_intensity # Primary illumination intensity
z_offset = channel.z_offset                # Z offset (um)
ill_name = channel.primary_illumination_channel  # Primary illumination channel name

# Setters (modify in-place)
channel.exposure_time = 50.0
channel.analog_gain = 5.0
channel.illumination_intensity = 30.0

# Get illumination source code (requires IlluminationChannelConfig)
ill_config = config_repo.get_illumination_config()
source_code = channel.get_illumination_source_code(ill_config)
wavelength = channel.get_illumination_wavelength(ill_config)

# Apply confocal override
effective = channel.get_effective_settings(confocal_mode=True)
# Returns new AcquisitionChannel with confocal_override applied
```

**Nested structures:**
```python
# Camera settings (by camera ID)
for cam_id, cam_settings in channel.camera_settings.items():
    print(f"Camera {cam_id}:")
    print(f"  Exposure: {cam_settings.exposure_time_ms}")
    print(f"  Gain: {cam_settings.gain_mode}")
    print(f"  Color: {cam_settings.display_color}")

# Illumination settings
ill = channel.illumination_settings
for ch_name, intensity in ill.intensity.items():
    print(f"{ch_name}: {intensity}%")

# Confocal settings (if present)
if channel.confocal_settings:
    print(f"Filter wheel: {channel.confocal_settings.filter_wheel_id}")
    print(f"Filter position: {channel.confocal_settings.emission_filter_wheel_position}")
```

### GeneralChannelConfig & ObjectiveChannelConfig

```python
from control.models import (
    GeneralChannelConfig,
    ObjectiveChannelConfig,
    merge_channel_configs,
    validate_illumination_references,
)

# Load configs
general = config_repo.get_general_config()
objective = config_repo.get_objective_config("20x")

# Get channel by name
channel = general.get_channel_by_name("Fluorescence 488 nm Ex")

# Merge configs manually
merged_channels = merge_channel_configs(general, objective)
# Returns: List[AcquisitionChannel]

# Validate illumination references
ill_config = config_repo.get_illumination_config()
errors = validate_illumination_references(general, ill_config)
if errors:
    for error in errors:
        print(f"Validation error: {error}")
```

### LaserAFConfig

```python
from control.models import LaserAFConfig
import numpy as np

# Load config
laser_af = config_repo.get_laser_af_config("20x")

# Access parameters
print(f"Range: {laser_af.laser_af_range} um")
print(f"Averaging: {laser_af.laser_af_averaging_n}")
print(f"Mode: {laser_af.spot_detection_mode}")

# Get spot detection mode as enum
from control._def import SpotDetectionMode
mode = laser_af.get_spot_detection_mode()
if mode == SpotDetectionMode.DUAL_RIGHT:
    ...

# Reference image (base64 encoded/decoded automatically)
if laser_af.has_reference:
    image = laser_af.reference_image_cropped  # numpy array
    print(f"Reference shape: {image.shape}")

# Set reference image
new_reference = np.zeros((256, 1536), dtype=np.uint16)
laser_af.set_reference_image(new_reference)
# Automatically encodes to base64 for YAML storage

# Clear reference
laser_af.set_reference_image(None)
```

### ConfocalConfig

```python
from control.models import ConfocalConfig

config = config_repo.get_confocal_config()
if config:
    # Get filter name
    filter_name = config.get_filter_name(wheel_id=1, slot=2)

    # Check if property is available
    if config.has_property("illumination_iris"):
        ...
```

### CameraMappingsConfig

```python
from control.models import CameraMappingsConfig

config = config_repo.get_camera_mappings()
if config:
    # Get hardware info
    hw_info = config.get_hardware_info("1")

    # Get property bindings
    bindings = config.get_bindings("camera_1")

    # Check for confocal in light path
    if config.has_confocal_in_light_path("camera_1"):
        ...
```

---

## Utility Functions

Located in `control/core/config/utils.py`:

```python
from control.core.config.utils import (
    apply_confocal_override,
    get_effective_channels,
    copy_profile_configs,
)

# Apply confocal override to channel list
effective_channels = apply_confocal_override(channels, confocal_mode=True)

# Get effective channels (merge + confocal in one call)
channels = get_effective_channels(general, objective, confocal_mode=True)

# Copy profile configs (alternative to config_repo.copy_profile)
copy_profile_configs(config_repo, "source", "destination")
```

---

## Default Config Generation

Located in `control/default_config_generator.py`:

```python
from control.default_config_generator import (
    ensure_default_configs,
    generate_default_configs,
    has_legacy_configs_to_migrate,
)

# Ensure profile has defaults (called automatically by load_profile)
generated = ensure_default_configs(
    config_repo,
    profile="my_profile",
    objectives=["20x", "40x"]
)

# Check for legacy configs that need migration
if has_legacy_configs_to_migrate("my_profile", base_path=Path("software/")):
    print("Run migration script first")

# Generate configs programmatically
general, objectives = generate_default_configs(
    illumination_config,
    confocal_config,
    objectives=["20x", "40x"],
    camera_id="1"
)
```

---

## Access Patterns

### In Widgets (Qt)

Use `LiveController.get_channels(objective)` for UI access:

```python
class MyWidget(QWidget):
    def __init__(self, liveController, objectiveStore):
        self.liveController = liveController
        self.objectiveStore = objectiveStore

    def update_channels(self):
        # Gets merged channels for current objective with confocal mode applied
        objective = self.objectiveStore.current_objective
        channels = self.liveController.get_channels(objective)

        for channel in channels:
            self.add_channel_button(channel.name, channel.display_color)
```

### In Controllers

Use ConfigRepository directly when you need low-level access:

```python
class MyController:
    def __init__(self, microscope):
        self.config_repo = microscope.config_repo

    def get_all_objectives(self):
        return self.config_repo.get_available_objectives()

    def update_setting(self, objective, channel, setting, value):
        self.config_repo.update_channel_setting(
            objective, channel, setting, value
        )
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

# FileNotFoundError: Required config missing
try:
    ensure_default_configs(config_repo, "my_profile")
except FileNotFoundError as e:
    print(f"Missing config: {e}")

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

def test_config_loading():
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # Create structure
        (base / "machine_configs").mkdir()
        (base / "user_profiles" / "test").mkdir(parents=True)
        (base / "user_profiles" / "test" / "channel_configs").mkdir()

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
from unittest.mock import Mock, patch

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
- Source: `control/core/config/repository.py`
- Models: `control/models/`
