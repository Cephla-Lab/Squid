# March 2026 Upstream Ports

**Status:** COMPLETED
**Started:** 2026-03-05

## Upstream Commits

### Port
- [ ] `2408902a` - fix: prevent level trigger from never ending when retriggered too fast
- [ ] `bcad6087` - feat: Disable TEC when default_temperature is None for Toupcam
- [ ] `08ef8c5e` - fix: XLight/Cicero filter wheel integration

### Skip
- [ ] `84b49652` - fix: Move z_offset_um to AcquisitionChannel level (`already-fixed` — our schema already has z_offset_um on AcquisitionChannel with explicit comment)
- [ ] `5e5fb921` - fix: Pin numpy<2 (`not-applicable` — build scripts only)
- [ ] `623c2017` - fix: Use cropped image dimensions for NDViewer (`already-fixed` — our `_publish_ndviewer_start()` already calls `get_crop_size()`)
- [ ] `e5e07d25` - feat: Add Anthropic API key management (`not-applicable` — GUI convenience for upstream main_hcs.py)

---

## Phase 1: Firmware Trigger Fix (`2408902a`)

The firmware changes (commands.cpp, functions.cpp) apply directly — they're outside the arch_v2 Python scope. The software-side fix adds `microcontroller.set_trigger_mode()` during init when `DEFAULT_TRIGGER_MODE == HARDWARE`.

### Changes

**`application.py`** — `_initialize_hardware()` (line ~240) and `_setup_camera_callbacks_only()` (line ~295):
- [ ] After `camera_service.set_acquisition_mode(HARDWARE_TRIGGER)`, call microcontroller `set_trigger_mode`:
  ```python
  if getattr(_config, "DEFAULT_TRIGGER_MODE") == TriggerMode.HARDWARE:
      camera_service.set_acquisition_mode(CameraAcquisitionMode.HARDWARE_TRIGGER)
      # Set microcontroller trigger mode to match
      microcontroller = self._services.get("microcontroller")
      if microcontroller is not None:
          from _def import HARDWARE_TRIGGER_MODE, HardwareTriggerMode
          microcontroller.set_trigger_mode(HARDWARE_TRIGGER_MODE.value)
  ```
- [ ] Apply same pattern in both `_initialize_hardware()` and `_setup_camera_callbacks_only()`

### Notes
- `HARDWARE_TRIGGER_MODE` is already defined in `_def.py:1187` as `HardwareTriggerMode.EDGE`
- `set_trigger_mode()` is already on the microcontroller (`microcontroller.py:563-571`), just never called during init
- Need to check how microcontroller is accessed — it may be in `self._hardware` rather than services

---

## Phase 2: Toupcam TEC Disable (`bcad6087`)

Guard `set_temperature()` in `_configure_camera()` to handle `None` by disabling TEC instead.

### Changes

**`backend/drivers/cameras/toupcam.py`** — `_configure_camera()` (line ~424-426):
- [ ] Replace unconditional `set_temperature()` with None-aware guard:
  ```python
  self._set_fan_speed(self._config.default_fan_speed)

  # Disable TEC when default_temperature is None, otherwise set target
  if self._config.default_temperature is None:
      if self._capabilities.has_TEC:
          self._camera.put_Option(toupcam.TOUPCAM_OPTION_TEC, 0)
          self._log.info("TEC disabled (default_temperature is None)")
  else:
      self.set_temperature(self._config.default_temperature)
  ```

### Notes
- `has_TEC` is already in capabilities (line 233)
- `TOUPCAM_OPTION_TEC` needs to exist in `toupcam_sdk.py` — verify before implementing
- `default_temperature` is already `Optional[float]` in config schema

---

## Phase 3: XLight/Cicero Filter Wheel (`08ef8c5e`)

Four sub-issues to fix:

### 3a. Serial timing — use `sleep_time_for_wheel` instead of hardcoded 0.01

**`backend/drivers/lighting/xlight.py`** — 4 locations:
- [ ] `set_emission_filter()` line 212: `read_delay=0.01` → `read_delay=self.sleep_time_for_wheel`
- [ ] `get_emission_filter()` line 224: `read_delay=0.01` → `read_delay=self.sleep_time_for_wheel`
- [ ] `set_dichroic()` line 238: `read_delay=0.01` → `read_delay=self.sleep_time_for_wheel`
- [ ] `get_dichroic()` line 245: `read_delay=0.01` → `read_delay=self.sleep_time_for_wheel`

### 3b. Configurable emission filter positions

**`_def.py`**:
- [ ] Add `XLIGHT_EMISSION_FILTER_POSITIONS = 8` (after `XLIGHT_VALIDATE_WHEEL_POS`, line ~1024)
- [ ] Remove dead `XLIGHT_EMISSION_FILTER_MAPPING` dict (lines 1015-1021)

**`backend/drivers/lighting/xlight.py`** — `set_emission_filter()` line 203:
- [ ] Replace hardcoded `["1", "2", "3", "4", "5", "6", "7", "8"]` with dynamic validation:
  ```python
  from _def import XLIGHT_EMISSION_FILTER_POSITIONS
  valid_positions = [str(i + 1) for i in range(XLIGHT_EMISSION_FILTER_POSITIONS)]
  if str(position) not in valid_positions:
      raise ValueError(f"Invalid emission filter position {position}, must be 1-{XLIGHT_EMISSION_FILTER_POSITIONS}")
  ```

### 3c. Conditional widget creation for Cicero (no dichroic wheel)

**`ui/widgets/hardware/confocal.py`** — `__init__()` lines 47-55:
- [ ] Guard emission filter init on `has_emission_filters_wheel`:
  ```python
  if self.xlight.has_emission_filters_wheel:
      self.dropdown_emission_filter.setCurrentText(str(self.xlight.get_emission_filter()))
      self.dropdown_emission_filter.currentIndexChanged.connect(self.set_emission_filter)

  if self.xlight.has_dichroic_filters_wheel:
      self.dropdown_dichroic.setCurrentText(str(self.xlight.get_dichroic()))
      self.dropdown_dichroic.currentIndexChanged.connect(self.set_dichroic)
  ```

**`ui/widgets/hardware/confocal.py`** — `init_ui()` lines 115-125:
- [ ] Only create `dropdown_emission_filter` if `has_emission_filters_wheel`
- [ ] Only create `dropdown_dichroic` if `has_dichroic_filters_wheel`
- [ ] Use `XLIGHT_EMISSION_FILTER_POSITIONS` for emission filter item count instead of hardcoded 8

**`ui/widgets/hardware/confocal.py`** — `enable_all_buttons()` lines 188-198:
- [ ] Guard `dropdown_emission_filter` and `dropdown_dichroic` access with `hasattr` or capability check

### 3d. Improved serial response logging

**`backend/drivers/peripherals/serial_base.py`** — `write_and_check()` line 124:
- [ ] Change `log.warning(response)` to `log.warning(f"Serial response mismatch: got '{response}', expected '{expected_response}'")`

---

## Tests

- [ ] Verify existing XLight simulation tests still pass
- [ ] Run `pytest tests/unit -v` for regressions
- [ ] Run `pytest tests/integration -v` for integration regressions

---

## Verification

- [ ] Update `upstream-status.yaml` for all 7 commits (3 ported, 4 skipped)
- [ ] Create commit tracking file in `commits/`
- [ ] Run `python tools/upstream_tracking.py verify`
