# Multi-Camera Support Plan

This document outlines the implementation plan for multi-camera support in Squid's configuration system. The changes introduce camera and filter wheel registries, simplify channel-to-camera assignments, and add channel groups for synchronized multi-camera acquisition.

## Overview

### Goals

1. **Single camera per channel**: Each acquisition channel is assigned to exactly one camera
2. **User-friendly hardware references**: Users see camera/filter wheel names, not serial numbers or IDs
3. **Channel groups**: Group channels for simultaneous (multi-camera) or sequential acquisition
4. **Timing control**: Per-channel trigger offsets for simultaneous acquisition
5. **Zero-config for single-camera**: Single-camera systems work without any new configuration files

### Version

All configuration files will be updated to **version 1.1**.

### Single-Camera vs Multi-Camera Behavior

| Scenario | cameras.yaml | Channel `camera` field | Behavior |
|----------|--------------|------------------------|----------|
| Single-camera (default) | Not present | `null` or omitted | Auto-uses the only available camera |
| Single-camera (explicit) | Present with 1 camera | Camera name | Uses named camera |
| Multi-camera | Required | Camera name (required) | Uses specified camera |

**Key Design Decision**: The `camera` field is **optional**. When omitted or `null`:
- If no `cameras.yaml` exists → use the single available camera
- If `cameras.yaml` exists with one camera → use that camera
- If `cameras.yaml` exists with multiple cameras → validation error (must specify)

---

## Schema Changes

### New Machine Config: `cameras.yaml` (Optional)

Defines available cameras with user-friendly names mapped to hardware identifiers.

**This file is only required for multi-camera systems.** Single-camera systems work without it.

```yaml
version: 1.1
cameras:
  - name: "Main Camera"           # User-friendly name (shown in UI)
    serial_number: "ABC12345"     # Hardware identifier
    model: "Hamamatsu C15440"     # Optional: for display

  - name: "Side Camera"
    serial_number: "DEF67890"
    model: "Basler acA2040"
```

**Location**: `machine_configs/cameras.yaml`

### New Machine Config: `filter_wheels.yaml`

Defines available filter wheels with positions and filter names.

```yaml
version: 1.1
filter_wheels:
  - name: "Emission Filter Wheel"   # User-friendly name
    id: 1                            # Hardware ID for controller
    positions:
      1: "Empty"
      2: "BP 525/50"
      3: "BP 600/50"
      4: "BP 700/75"
      5: "LP 650"

  - name: "Excitation Filter Wheel"
    id: 2
    positions:
      1: "Empty"
      2: "BP 470/40"
```

**Location**: `machine_configs/filter_wheels.yaml`

### Updated Channel Config: `general.yaml`

Key changes from v1.0:
- `display_color` moved from `camera_settings` to channel level
- `camera` field references camera by name (optional for single-camera systems)
- `camera_settings` is now a single object (not `Dict[str, CameraSettings]`)
- `filter_wheel` and `filter_position` replace `emission_filter_wheel_position`
- New `channel_groups` section

#### Single-Camera Example (no cameras.yaml needed)

```yaml
version: 1.1

channels:
  - name: BF LED matrix full
    display_color: '#FFFFFF'              # MOVED: was in camera_settings
    # camera: null                        # OPTIONAL: omit for single-camera systems
    camera_settings:                       # CHANGED: single object, not Dict
      exposure_time_ms: 20.0
      gain_mode: 10.0
      pixel_format: null
    filter_wheel: null                     # NEW: references filter_wheels.yaml by name
    filter_position: null                  # NEW: position in that wheel
    illumination_settings:
      illumination_channels:
        - BF LED matrix full
      intensity:
        BF LED matrix full: 20.0
      z_offset_um: 0.0
    confocal_settings: null
    confocal_override: null

channel_groups: []                         # Empty for single-camera, no groups needed
```

#### Multi-Camera Example (requires cameras.yaml)

```yaml
version: 1.1

channels:
  - name: BF LED matrix full
    display_color: '#FFFFFF'
    camera: "Main Camera"                  # REQUIRED for multi-camera: references cameras.yaml
    camera_settings:
      exposure_time_ms: 20.0
      gain_mode: 10.0
      pixel_format: null
    filter_wheel: null
    filter_position: null
    illumination_settings:
      illumination_channels:
        - BF LED matrix full
      intensity:
        BF LED matrix full: 20.0
      z_offset_um: 0.0
    confocal_settings: null
    confocal_override: null

  - name: Fluorescence 488 nm Ex
    display_color: '#1FFF00'
    camera: "Side Camera"                  # Different camera for simultaneous capture
    camera_settings:
      exposure_time_ms: 20.0
      gain_mode: 10.0
      pixel_format: null
    filter_wheel: "Emission Filter Wheel"
    filter_position: 2
    illumination_settings:
      illumination_channels:
        - Fluorescence 488 nm Ex
      intensity:
        Fluorescence 488 nm Ex: 20.0
      z_offset_um: 0.0
    confocal_settings: null
    confocal_override: null

channel_groups:
  - name: "Dual BF + GFP"
    synchronization: simultaneous
    channels:
      - name: "BF LED matrix full"         # Main Camera
        offset_us: 0                       # Reference channel
      - name: "Fluorescence 488 nm Ex"     # Side Camera
        offset_us: 100                     # 100μs delay

  - name: "Standard Fluorescence"
    synchronization: sequential
    channels:
      - name: "Fluorescence 488 nm Ex"
      - name: "Fluorescence 561 nm Ex"
      - name: "Fluorescence 638 nm Ex"
```

### Objective Config Updates

Objective-specific files (`{objective}.yaml`) follow the same v1.1 schema but typically only override:
- `camera_settings` (exposure, gain)
- `illumination_settings.intensity`
- `confocal_override`

Channel groups are defined only in `general.yaml` (shared across objectives).

---

## Pydantic Models

### New: Camera Registry

**File**: `control/models/camera_registry.py`

```python
from typing import List, Optional
from pydantic import BaseModel, Field


class CameraDefinition(BaseModel):
    """A camera in the system."""
    name: str = Field(..., description="User-friendly camera name")
    serial_number: str = Field(..., description="Hardware serial number")
    model: Optional[str] = Field(None, description="Camera model for display")

    model_config = {"extra": "forbid"}


class CameraRegistryConfig(BaseModel):
    """Registry of available cameras."""
    version: float = Field(1.1)
    cameras: List[CameraDefinition] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    def get_camera_by_name(self, name: str) -> Optional[CameraDefinition]:
        """Get camera definition by user-friendly name."""
        for camera in self.cameras:
            if camera.name == name:
                return camera
        return None

    def get_camera_by_sn(self, serial_number: str) -> Optional[CameraDefinition]:
        """Get camera definition by serial number."""
        for camera in self.cameras:
            if camera.serial_number == serial_number:
                return camera
        return None

    def get_camera_names(self) -> List[str]:
        """Get list of all camera names for UI dropdowns."""
        return [camera.name for camera in self.cameras]
```

### New: Filter Wheel Registry

**File**: `control/models/filter_wheel_config.py`

```python
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class FilterWheelDefinition(BaseModel):
    """A filter wheel in the system."""
    name: str = Field(..., description="User-friendly filter wheel name")
    id: int = Field(..., description="Hardware ID for controller")
    positions: Dict[int, str] = Field(..., description="Slot number -> filter name")

    model_config = {"extra": "forbid"}

    def get_filter_name(self, position: int) -> Optional[str]:
        """Get filter name at a position."""
        return self.positions.get(position)

    def get_position_by_filter(self, filter_name: str) -> Optional[int]:
        """Get position number for a filter name."""
        for pos, name in self.positions.items():
            if name == filter_name:
                return pos
        return None


class FilterWheelRegistryConfig(BaseModel):
    """Registry of available filter wheels."""
    version: float = Field(1.1)
    filter_wheels: List[FilterWheelDefinition] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    def get_wheel_by_name(self, name: str) -> Optional[FilterWheelDefinition]:
        """Get filter wheel by user-friendly name."""
        for wheel in self.filter_wheels:
            if wheel.name == name:
                return wheel
        return None

    def get_wheel_by_id(self, wheel_id: int) -> Optional[FilterWheelDefinition]:
        """Get filter wheel by hardware ID."""
        for wheel in self.filter_wheels:
            if wheel.id == wheel_id:
                return wheel
        return None

    def get_wheel_names(self) -> List[str]:
        """Get list of all filter wheel names for UI dropdowns."""
        return [wheel.name for wheel in self.filter_wheels]
```

### Updated: Acquisition Config

**File**: `control/models/acquisition_config.py`

```python
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class CameraSettings(BaseModel):
    """Camera settings for an acquisition channel."""
    # NOTE: display_color removed - now at AcquisitionChannel level
    exposure_time_ms: float = Field(..., description="Exposure time in milliseconds")
    gain_mode: float = Field(..., description="Analog gain value")
    pixel_format: Optional[str] = Field(None, description="Pixel format (e.g., 'Mono12')")

    model_config = {"extra": "forbid"}


class IlluminationSettings(BaseModel):
    """Illumination settings for an acquisition channel."""
    illumination_channels: Optional[List[str]] = Field(
        None, description="Names of illumination channels (only in general.yaml)"
    )
    intensity: Dict[str, float] = Field(..., description="Channel name -> intensity %")
    z_offset_um: float = Field(0.0, description="Z offset in micrometers")

    model_config = {"extra": "forbid"}


class ConfocalSettings(BaseModel):
    """Confocal-specific settings (part of confocal unit hardware).

    Note: Confocal filter wheel is separate from body filter wheel.
    Body filter wheel uses channel-level filter_wheel/filter_position fields.
    """
    # Confocal unit filter wheel (separate from body filter wheel)
    confocal_filter_wheel: Optional[str] = Field(
        None, description="Confocal filter wheel name (references filter_wheels.yaml)"
    )
    confocal_filter_position: Optional[int] = Field(
        None, ge=1, description="Position in confocal filter wheel"
    )
    # Iris settings
    illumination_iris: Optional[float] = Field(None, description="Illumination iris setting")
    emission_iris: Optional[float] = Field(None, description="Emission iris setting")

    model_config = {"extra": "forbid"}


class AcquisitionChannelOverride(BaseModel):
    """Override settings for confocal mode."""
    illumination_settings: Optional[IlluminationSettings] = None
    camera_settings: Optional[CameraSettings] = None
    confocal_settings: Optional[ConfocalSettings] = None

    model_config = {"extra": "forbid"}


class AcquisitionChannel(BaseModel):
    """A single acquisition channel configuration."""
    name: str = Field(..., description="Channel display name")
    display_color: str = Field('#FFFFFF', description="Hex color for UI visualization")

    # Camera assignment (optional for single-camera systems)
    camera: Optional[str] = Field(
        None,
        description="Camera name (references cameras.yaml). Optional for single-camera systems."
    )
    camera_settings: CameraSettings = Field(..., description="Camera settings")

    # Filter wheel assignment (optional)
    filter_wheel: Optional[str] = Field(
        None, description="Filter wheel name (references filter_wheels.yaml)"
    )
    filter_position: Optional[int] = Field(
        None, description="Position in filter wheel"
    )

    # Illumination
    illumination_settings: IlluminationSettings

    # Confocal (optional)
    confocal_settings: Optional[ConfocalSettings] = None
    confocal_override: Optional[AcquisitionChannelOverride] = None

    model_config = {"extra": "forbid"}

    # Convenience properties
    @property
    def exposure_time(self) -> float:
        return self.camera_settings.exposure_time_ms

    @exposure_time.setter
    def exposure_time(self, value: float) -> None:
        self.camera_settings.exposure_time_ms = value

    @property
    def analog_gain(self) -> float:
        return self.camera_settings.gain_mode

    @analog_gain.setter
    def analog_gain(self, value: float) -> None:
        self.camera_settings.gain_mode = value


# Channel Groups

class SynchronizationMode(str, Enum):
    """Synchronization mode for channel groups."""
    SIMULTANEOUS = "simultaneous"  # Multi-camera parallel capture with timing offsets
    SEQUENTIAL = "sequential"       # One channel after another


class ChannelGroupEntry(BaseModel):
    """A channel entry within a channel group."""
    name: str = Field(..., description="Channel name (must exist in channels list)")
    offset_us: float = Field(
        0.0,
        description="Trigger offset in microseconds (only used for simultaneous mode)"
    )

    model_config = {"extra": "forbid"}


class ChannelGroup(BaseModel):
    """A group of channels to be acquired together."""
    name: str = Field(..., description="Group name for UI")
    synchronization: SynchronizationMode = Field(
        SynchronizationMode.SEQUENTIAL,
        description="Capture mode: simultaneous or sequential"
    )
    channels: List[ChannelGroupEntry] = Field(..., description="Channels in this group")

    model_config = {"extra": "forbid"}

    def get_channel_names(self) -> List[str]:
        """Get list of channel names in this group."""
        return [entry.name for entry in self.channels]

    def get_channel_offset(self, channel_name: str) -> float:
        """Get offset for a channel in microseconds."""
        for entry in self.channels:
            if entry.name == channel_name:
                return entry.offset_us
        return 0.0

    def get_channels_sorted_by_offset(self) -> List[ChannelGroupEntry]:
        """Get channels sorted by trigger offset (for simultaneous mode)."""
        return sorted(self.channels, key=lambda c: c.offset_us)


class GeneralChannelConfig(BaseModel):
    """general.yaml - shared settings across all objectives."""
    version: float = Field(1.1, description="Configuration format version")
    channels: List[AcquisitionChannel] = Field(default_factory=list)
    channel_groups: List[ChannelGroup] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    def get_channel_by_name(self, name: str) -> Optional[AcquisitionChannel]:
        for ch in self.channels:
            if ch.name == name:
                return ch
        return None

    def get_group_by_name(self, name: str) -> Optional[ChannelGroup]:
        for group in self.channel_groups:
            if group.name == name:
                return group
        return None


class ObjectiveChannelConfig(BaseModel):
    """{objective}.yaml - objective-specific overrides."""
    version: float = Field(1.1, description="Configuration format version")
    channels: List[AcquisitionChannel] = Field(default_factory=list)
    # Note: channel_groups not included - defined only in general.yaml

    model_config = {"extra": "forbid"}

    def get_channel_by_name(self, name: str) -> Optional[AcquisitionChannel]:
        for ch in self.channels:
            if ch.name == name:
                return ch
        return None
```

---

## Validation

### Channel Group Validation

```python
def validate_channel_group(
    group: ChannelGroup,
    channels: List[AcquisitionChannel]
) -> List[str]:
    """Validate channel group configuration.

    Returns list of error messages (empty if valid).
    """
    errors = []

    # Track cameras used
    cameras_used = []
    for entry in group.channels:
        channel = next((c for c in channels if c.name == entry.name), None)
        if channel is None:
            errors.append(f"Channel '{entry.name}' not found in channels list")
            continue
        cameras_used.append(channel.camera)

        # Warn if offset specified for sequential mode
        if group.synchronization == SynchronizationMode.SEQUENTIAL and entry.offset_us != 0:
            errors.append(
                f"Channel '{entry.name}' has offset_us={entry.offset_us} "
                f"but group '{group.name}' is sequential (offset will be ignored)"
            )

    # For simultaneous mode, all cameras must be different
    if group.synchronization == SynchronizationMode.SIMULTANEOUS:
        if len(cameras_used) != len(set(cameras_used)):
            duplicate_cameras = [c for c in set(cameras_used) if cameras_used.count(c) > 1]
            errors.append(
                f"Group '{group.name}' uses simultaneous mode but has "
                f"multiple channels on same camera: {duplicate_cameras}"
            )

    return errors


def validate_channel_references(
    config: GeneralChannelConfig,
    camera_registry: CameraRegistryConfig,
    filter_wheel_registry: FilterWheelRegistryConfig
) -> List[str]:
    """Validate that all camera and filter wheel references exist."""
    errors = []

    camera_names = set(camera_registry.get_camera_names())
    wheel_names = set(filter_wheel_registry.get_wheel_names())

    for channel in config.channels:
        # Validate camera reference
        if channel.camera not in camera_names:
            errors.append(
                f"Channel '{channel.name}' references camera '{channel.camera}' "
                f"which does not exist in cameras.yaml"
            )

        # Validate filter wheel reference
        if channel.filter_wheel is not None:
            if channel.filter_wheel not in wheel_names:
                errors.append(
                    f"Channel '{channel.name}' references filter wheel "
                    f"'{channel.filter_wheel}' which does not exist in filter_wheels.yaml"
                )
            elif channel.filter_position is None:
                errors.append(
                    f"Channel '{channel.name}' has filter_wheel but no filter_position"
                )

    return errors
```

---

## Migration

### v1.0 to v1.1 Migration

```python
def migrate_channel_config_v1_to_v1_1(
    config: dict,
    default_camera: str = "Main Camera"
) -> dict:
    """Migrate channel config from v1.0 to v1.1.

    Args:
        config: v1.0 config dict
        default_camera: Camera name to assign to all channels

    Returns:
        v1.1 config dict
    """
    for channel in config.get('channels', []):
        # Extract camera_settings dict
        old_camera_settings = channel.pop('camera_settings', {})

        # Get first (and only) camera's settings
        camera_id = next(iter(old_camera_settings.keys()), '1')
        cam_settings = old_camera_settings.get(camera_id, {})

        # Move display_color to channel level
        channel['display_color'] = cam_settings.pop('display_color', '#FFFFFF')

        # Add camera reference
        channel['camera'] = default_camera

        # Flatten camera_settings to single object
        channel['camera_settings'] = {
            'exposure_time_ms': cam_settings.get('exposure_time_ms', 20.0),
            'gain_mode': cam_settings.get('gain_mode', 10.0),
            'pixel_format': cam_settings.get('pixel_format'),
        }

        # Convert emission_filter_wheel_position to filter_wheel + filter_position
        old_filter = channel.pop('emission_filter_wheel_position', None)
        if old_filter:
            wheel_id = next(iter(old_filter.keys()), 1)
            # Note: Actual wheel name must be set by user or derived from filter_wheels.yaml
            channel['filter_wheel'] = None  # Requires manual mapping
            channel['filter_position'] = old_filter.get(wheel_id, 1)
        else:
            channel['filter_wheel'] = None
            channel['filter_position'] = None

    # Initialize empty channel_groups
    if 'channel_groups' not in config:
        config['channel_groups'] = []

    config['version'] = 1.1

    return config
```

---

## Implementation Phases

### Phase 1: Schema and Models ✅ COMPLETE

**Scope**: Update Pydantic models and add new config files without breaking existing functionality.

**Files created**:
- ✅ `control/models/camera_registry.py`
- ✅ `control/models/filter_wheel_config.py`
- ✅ `machine_configs/cameras.yaml.example`
- ✅ `machine_configs/filter_wheels.yaml.example`

**Files modified**:
- ✅ `control/models/acquisition_config.py` - Updated models with v1.1 schema
- ✅ `control/models/__init__.py` - Export new models
- ✅ `control/core/config/repository.py` - Added loaders for new configs

**Migration strategy**:
- ✅ Backward compatibility with v1.0 configs
- ✅ Auto-detect version and migrate on load
- ✅ Write v1.1 format on save

### Phase 2: UI Integration (In Progress)

**Scope**: Update UI to use camera/filter wheel names and support channel groups.

**Completed**:
- ✅ **Acquisition Channel Configuration dialog** (Settings > Advanced > Acquisition Channel Configuration)
  - Edit channel parameters (name, LED, exposure, analog gain, illumination intensity)
  - Filter wheel and position selection (when `USE_EMISSION_FILTER_WHEEL=True`)
  - Enable/disable channels
  - Add/remove channels
  - Export/Import channel configurations to/from YAML files
- ✅ **Filter Wheel Configuration dialog** (Settings > Advanced > Filter Wheel Configuration)
  - Configure filter position names (e.g., "DAPI emission" instead of "Position 1")
  - Saves to `machine_configs/filter_wheels.yaml`
- ✅ Filter wheel/position selector in Add Channel dialog
- ✅ Disabled channels filtered from live controller dropdown

**Remaining**:
- [ ] Camera selector dropdown in channel configuration (for multi-camera)
- [ ] Channel group editor widget
- [ ] Acquisition setup to select channel groups

### Phase 3: Acquisition Engine

**Scope**: Implement multi-camera acquisition with channel groups.

**Changes**:
- [ ] `LiveController` - Support multiple camera instances
- [ ] `MultiPointWorker` - Process channel groups with synchronization
- [ ] Hardware triggering with timing offsets

### Phase 4: Testing and Documentation

**Scope**: Comprehensive testing and user documentation.

**Deliverables**:
- ✅ Unit tests for new models (v1.1 schema validation)
- [ ] Integration tests for multi-camera acquisition
- [ ] User documentation updates

---

## File Summary

### New Files

| File | Description |
|------|-------------|
| `machine_configs/cameras.yaml` | Camera registry |
| `machine_configs/filter_wheels.yaml` | Filter wheel registry |
| `control/models/camera_registry.py` | CameraRegistryConfig model |
| `control/models/filter_wheel_config.py` | FilterWheelRegistryConfig model |

### Modified Files

| File | Changes |
|------|---------|
| `control/models/acquisition_config.py` | Restructure for v1.1 schema |
| `control/models/__init__.py` | Export new models |
| `control/core/config/repository.py` | Load new config files |
| `control/core/config/utils.py` | Migration utilities |
| `user_profiles/*/channel_configs/*.yaml` | Schema v1.0 → v1.1 |

---

## Design Decisions

### Resolved

1. **Default camera behavior**: ✅ **RESOLVED**
   - The `camera` field is **optional** (`Optional[str]`)
   - Single-camera systems: No `cameras.yaml` needed, `camera` field can be omitted
   - Multi-camera systems: `cameras.yaml` required, `camera` field required per channel
   - Validation: If multiple cameras exist and `camera` is `None`, raise error

### Open Questions

1. **Filter wheel association**: Should filter wheels be associated with specific cameras in the registry?

2. **Confocal integration**: ✅ **RESOLVED**
   - Confocal filter wheel is **separate** from body filter wheel
   - Body filter wheel: `AcquisitionChannel.filter_wheel` / `filter_position`
   - Confocal filter wheel: `ConfocalSettings.confocal_filter_wheel` / `confocal_filter_position`
   - Rationale: Physical distinction - confocal filter wheels are built into the confocal unit

3. **Migration UX**: Should migration be automatic on load, or require explicit user action?
