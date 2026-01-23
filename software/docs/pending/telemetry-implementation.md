# Telemetry System Implementation Plan

> **First step**: Save this document to `software/docs/pending/telemetry-implementation.md`

## Overview
Add opt-in telemetry to Squid microscope software using **Sentry** for unified error tracking and usage analytics. Users are prompted for consent on first launch and can change the setting anytime in Preferences.

---

## Sentry vs Self-Hosting

| Aspect | **Sentry (Managed)** | **Self-Hosted** |
|--------|---------------------|-----------------|
| **Setup time** | 30 min | 1-3 days |
| **Maintenance** | None | You handle updates, backups |
| **Cost** | ~$50-150/mo for 50-500 scopes | Server (~$50-200/mo) + your time |
| **Dashboard** | Built-in, polished | Same (Sentry OSS) or build custom |
| **Data privacy** | On Sentry servers (US/EU) | Full control |
| **Data retention** | 90 days (Team plan) | Unlimited |

**Recommendation:** Start with managed Sentry. If privacy concerns arise, migrate to self-hosted later (same SDK, just change DSN).

**Self-hosted options if needed:**
- **Sentry OSS** - Same software, Docker + PostgreSQL + Redis (~8GB RAM)
- **GlitchTip** - Lighter Sentry-compatible alternative (~2GB RAM)

---

## Microscope Identification

### Machine Fingerprint
Generate persistent ID from hardware characteristics:
```python
def generate_machine_fingerprint() -> str:
    """Generate unique fingerprint from hardware characteristics."""
    import hashlib
    import uuid

    # Combine: MAC address + hostname + platform
    mac = uuid.getnode()  # MAC address as int
    hostname = socket.gethostname()
    platform_info = f"{sys.platform}-{platform.machine()}"

    raw = f"{mac}-{hostname}-{platform_info}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

### User-Editable Metadata
Add optional fields in first-run dialog and Settings:
- **Microscope Name** (e.g., "Lab 201 Squid")
- **Institution** (e.g., "Stanford University")
- **Lab/Group** (e.g., "Smith Lab")
- **Location** (e.g., "Building A, Room 201")

These are stored locally and sent as Sentry tags for filtering/correlation.

---

## Configuration Tracking

### Files Tracked
| File Type | Examples | What's Captured |
|-----------|----------|-----------------|
| Machine INI | `configuration_Squid+.ini` | All sections/values |
| Channel configs | `channel_configs/*.yaml` | Per-objective channel settings |
| Illumination | `illumination_channel_config.yaml` | Light source mappings |
| Laser AF | `laser_af_configs/*.yaml` | Autofocus settings |
| Cache files | `multipoint_widget_config.yaml`, `camera_settings.yaml` | Current UI state |

### Capture Strategy
1. **Session start**: Full snapshot of all config files (hash + key values)
2. **On change**: Capture diff when user saves settings
3. **With errors**: Include relevant config as context

### Implementation
```python
def capture_config_snapshot() -> dict:
    """Capture current state of all config files."""
    config_snapshot = {
        "ini": _read_ini_config(),
        "channel_configs": _read_channel_configs(),
        "illumination": _read_yaml_safe("machine_configs/illumination_channel_config.yaml"),
        "laser_af": _read_laser_af_configs(),
        "cache": {
            "multipoint": _read_yaml_safe("cache/multipoint_widget_config.yaml"),
            "camera": _read_yaml_safe("cache/camera_settings.yaml"),
        }
    }
    return config_snapshot

def capture_config_change(file_path: str, old_values: dict, new_values: dict):
    """Track a config file change."""
    sentry_sdk.capture_message("config_changed", level="info")
    sentry_sdk.set_context("config_change", {
        "file": file_path,
        "changes": _compute_diff(old_values, new_values)
    })
```

---

## Implementation

### 1. New Files

#### `control/telemetry.py` - Core telemetry module
```
- TelemetryConsent enum: UNKNOWN, OPTED_IN, OPTED_OUT
- TelemetryConfig dataclass: consent, machine_fingerprint, user_metadata
- generate_machine_fingerprint() - Persistent hardware-based ID
- load/save_telemetry_config() - JSON in ~/.local/share/squid/
- capture_config_snapshot() - Full config state
- capture_config_change() - Track config diffs
- TelemetryManager singleton:
  - initialize(is_simulation)
  - set_hardware_context(microscope)
  - set_user_metadata(name, institution, lab, location)
  - capture_session_start() - Includes config snapshot
  - capture_session_end()
  - capture_acquisition_start/end()
  - capture_feature_usage()
  - add_breadcrumb()
  - capture_exception()
```

#### `control/telemetry_consent_dialog.py` - First-run dialog
```
- Modal QDialog with two pages:
  Page 1: Consent explanation + Yes/No buttons
  Page 2 (if Yes): Optional metadata fields
    - Microscope Name (text)
    - Institution (text)
    - Lab/Group (text)
    - Location (text)
    - "Skip" button to leave blank
- Expandable "What data is collected?" section
- Footer: "You can change this in Settings > Preferences > Advanced"
```

### 2. Files to Modify

#### `main_hcs.py` (lines 58-96)
1. Load telemetry config
2. If consent == UNKNOWN, show consent dialog
3. Initialize TelemetryManager
4. After microscope built: `telemetry.set_hardware_context(microscope)`
5. Capture config snapshot: `telemetry.capture_config_snapshot()`
6. After `win.show()`: `telemetry.capture_session_start()`
7. Register `app.aboutToQuit.connect(telemetry.capture_session_end)`

#### `control/gui_hcs.py`
- `_signal_acquisition_start_fn()`: Capture acquisition start with params
- `_signal_acquisition_finished_fn()`: Capture acquisition end
- `make_connections()`: Connect signals for feature usage tracking

#### `control/widgets.py` - PreferencesDialog
1. Add "Telemetry" section in Advanced tab:
   - Checkbox: "Send Anonymous Usage Data"
   - Button: "Edit Microscope Info" (opens metadata dialog)
   - Link to privacy policy
2. Hook `_apply_settings()` to capture config changes:
   ```python
   old_config = capture_config_snapshot()
   # ... apply settings ...
   new_config = capture_config_snapshot()
   if old_config != new_config:
       telemetry.capture_config_change(old_config, new_config)
   ```

#### `squid/logging.py`
Extend exception handler to send to Sentry with config context.

#### `control/_def.py`
Add `IS_SIMULATION = False` flag.

---

## Data Collected

### Microscope Identity
- Machine fingerprint (SHA256 hash of hardware characteristics)
- User-provided: name, institution, lab, location (optional)
- Hardware config: camera type, stage type, addons

### Session Events
- App version, git commit, OS, Python version
- Simulation mode flag
- Full config snapshot at session start
- Session duration

### Config Changes
- File changed, old values, new values
- Timestamp

### Acquisition Events
- Channel count and names, NZ, Nt, deltaZ
- Autofocus usage, XY mode
- Duration, image count, success/failure

### Feature Usage
- Live view, autofocus, objective changes, profile switches

### Errors
- Exception type, message, stack trace
- Breadcrumbs (last 50 actions)
- Hardware state + config at time of error

### NOT Collected
- Personal information beyond what user provides
- File paths (scrubbed)
- Sample images, experiment data

---

## Sentry Configuration

```python
sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN"),
    environment="simulation" if is_simulation else "production",
    release=version,
    traces_sample_rate=0.1,
    send_default_pii=False,
    before_send=scrub_file_paths,
    max_breadcrumbs=50,
)

# Set microscope identity
sentry_sdk.set_user({
    "id": machine_fingerprint,
    "username": user_metadata.get("microscope_name"),  # Optional
})

# Tags for filtering
sentry_sdk.set_tag("institution", user_metadata.get("institution", "unknown"))
sentry_sdk.set_tag("lab", user_metadata.get("lab", "unknown"))
sentry_sdk.set_tag("simulation_mode", is_simulation)
sentry_sdk.set_tag("hw_camera_type", camera_type)
# ... more hardware tags
```

---

## Testing Strategy

1. **Unit tests** (`tests/control/test_telemetry.py`):
   - Machine fingerprint generation is deterministic
   - Config snapshot captures all files
   - Config diff computation
   - Consent persistence
   - SDK initialization respects consent

2. **Integration tests**:
   - First-run dialog flow
   - Settings toggle behavior
   - Config change detection

3. **Manual verification**:
   - Run app, verify consent dialog + metadata fields
   - Change settings, verify config_changed event in Sentry
   - Trigger error, verify full context including config

---

## Verification Checklist

- [ ] First launch shows consent dialog with metadata fields
- [ ] Machine fingerprint is persistent across restarts
- [ ] User metadata appears in Sentry dashboard
- [ ] Config snapshot captured at session start
- [ ] Config changes tracked with old/new values
- [ ] Microscopes distinguishable in Sentry by fingerprint + name
- [ ] Settings toggle changes consent immediately
- [ ] All config files (.ini + .yaml) captured
- [ ] Errors include config context
- [ ] File paths scrubbed

---

## Dependencies

```
sentry-sdk>=1.40.0
```

---

## Files Summary

| File | Action |
|------|--------|
| `control/telemetry.py` | **Create** - Core module with fingerprint, config tracking |
| `control/telemetry_consent_dialog.py` | **Create** - First-run consent + metadata UI |
| `main_hcs.py` | Modify - Init telemetry, consent flow, config snapshot |
| `control/gui_hcs.py` | Modify - Acquisition events, feature usage |
| `control/widgets.py` | Modify - Telemetry toggle + config change tracking |
| `squid/logging.py` | Modify - Send exceptions to Sentry |
| `control/_def.py` | Modify - Add IS_SIMULATION flag |
| `tests/control/test_telemetry.py` | **Create** - Unit tests |
