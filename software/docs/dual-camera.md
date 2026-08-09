# Dual camera (one active at a time)

Run two cameras on one microscope where **only one images at a time** — typically a
monochrome camera for fluorescence and a color camera for brightfield/histology, sharing
the optical path through a beam splitter. Nothing moves optically when you switch; the
switch is pure software.

Each acquisition channel is **bound to a camera**. Selecting a channel in live view
switches to its camera automatically, and a multipoint acquisition may mix channels from
both cameras — the worker switches per channel.

This is not the "simultaneous multi-camera" feature: the two cameras never stream at the
same time.

## 1. Declare the cameras — `machine_configs/cameras.yaml`

```yaml
version: 1.0
cameras:
  - name: "Main Camera"
    id: 1
    serial_number: "12345ABC"
    model: "ITR3CMOS26000KMA"
    type: "Toupcam"
    hardware_trigger: true
  - name: "Side Camera"
    id: 2
    serial_number: "67890XYZ"
    model: "E3ISPM"
    type: "Toupcam"
    hardware_trigger: false
    default_pixel_format: "RGB24"
```

See `machine_configs/cameras.yaml.example` for the full field list. Key points:

- **`id: 1` is the primary camera.** It is the active camera at startup, and every channel
  with no camera binding images on it. The primary must exist, and should be the
  hardware-triggered camera.
- **`type` is required when more than one camera is declared** (`Toupcam`, `FLIR`,
  `Hamamatsu`, `iDS`, `TIS`, `Tucsen`, `Photometrics`, `Andor`, `Default`) — the single INI
  `camera_type` can't describe two different cameras.
- **`hardware_trigger: false`** means this camera's trigger line is not wired; it runs in
  software-trigger mode only.
- Optional per-camera overrides — `rotate_image_angle`, `flip`, `crop_width`,
  `crop_height`, `default_pixel_format`, `default_binning` — fall back to the INI
  `[CAMERA_CONFIG]` values when absent. Give a color camera an RGB `default_pixel_format`.

### Upgrading an existing `cameras.yaml`

`type` is new and **required with more than one camera**. A deployed multi-camera
`cameras.yaml` written before this feature will fail validation: the software logs a
warning (`Config validation failed for …/cameras.yaml: … missing required 'type'`) and
falls back to the single INI camera. It does not crash, but you silently get one camera.
**Add `type:` to every entry** when upgrading.

### Single-camera systems

Nothing changes. With no `cameras.yaml`, or with one declared camera, the imaging camera
comes entirely from the INI `[CAMERA_CONFIG]` section — a **1-camera `cameras.yaml` is
effectively ignored** (there is no serial-number-based camera selection in v1). The
registry only takes over when it declares more than one camera.

## 2. Bind channels to cameras — Settings ▸ Channel Configuration…

The **Camera** column in the channel editor *is* the binding. Pick the camera by name; the
file stores its **id**. `(None)` means the primary camera. There are no auto-generated
channels for a newly declared camera — add and name the rows yourself. The binding lives
in `general.yaml` only (per-objective overrides can't change it).

Once more than one camera is configured, every channel entry in the live dropdown, the
napari live widget and the multipoint channel lists gets:

- a small **colored dot** identifying its camera (fixed palette, keyed by camera id), and
- for non-primary channels, a **`— <camera name>` suffix**, e.g. `BF Color — Side Camera`.

This is decoration only. The canonical channel name is stored in the item's data, so saved
acquisition YAMLs, name-based selection and the MCP/TCP APIs keep using bare names.

Channels whose camera is declared but **unavailable** (it failed to open, or the id is
unknown) are **greyed out, not hidden**, with the tooltip *"This channel's camera is
declared in cameras.yaml but is not available."* Use the existing per-channel **Enabled**
checkbox to hide a channel.

If a loaded acquisition YAML names a channel whose camera is unavailable, the multipoint
panel shows a notice listing the dropped channels (`Not acquired (camera unavailable):
…`) — the rest of the run still proceeds.

## 3. Live view, triggers and camera settings

- **Selecting a channel switches cameras** before exposure/gain/illumination are applied.
  Color frames go through the existing RGB display path.
- **The trigger dropdown adapts to the active camera.** `Hardware Trigger` is offered only
  while a camera with `hardware_trigger: true` is active; on the secondary camera the
  dropdown offers Software (and Continuous, if recording is enabled) only. Each camera
  **remembers its own trigger mode**: switch to the color camera and back, and the primary
  returns to Hardware Trigger.
- **Misconfiguration that crashes startup:** a primary camera with
  `hardware_trigger: false` combined with `DEFAULT_TRIGGER_MODE = Hardware Trigger` in the
  INI. Startup applies the INI default trigger mode to the active (primary) camera, and a
  camera built without a hardware-trigger function cannot enter that mode, so the
  application **aborts before the window appears** with:

  ```
  ValueError: Cannot set HARDWARE_TRIGGER camera acquisition mode without a hw_trigger_fn.
  You must provide one when constructing the camera.
  ```

  Nothing catches this — there is no warning and no fallback. Fix it in configuration:
  either make the hardware-wired camera `id: 1` in `cameras.yaml`, or set the INI default
  trigger mode to Software Trigger. (Clamping gracefully at startup, the way the trigger
  dropdown already clamps on a runtime camera switch, would be a reasonable future
  improvement — it is **not** current behavior.)
- **Camera settings tabs:** each camera gets its own settings tab, labeled with its
  `cameras.yaml` name (`Main Camera`, `Side Camera`) instead of the single `Camera` tab.
  Each tab talks to its own camera.
- **Settings cache:** `cache/camera_settings.yaml` is keyed by serial number, so binning
  and pixel format are remembered per camera. A legacy flat cache file (no `cameras:` key)
  is still read, and applies to *every* camera that asks for it until each writes its own
  per-serial entry on the next shutdown.
- Switching cameras also re-clamps the exposure spinbox to the new camera's limits and
  redraws the navigation viewer's FOV rectangle (sensor size and pixel size differ per
  camera).

## 4. Acquisition

The worker switches to `channel.camera` (or the primary, when unbound) before each channel;
channel order is preserved — no reordering to minimize switches. Contrast AF switches to
the AF channel's camera first. Laser AF uses its own separate focus camera and is
unaffected.

**Per-channel pixel sizes** are recorded for mixed runs: `acquisition parameters.json`
gains a `channel_pixel_sizes_um` map (channel name → µm/px) alongside the existing
single-camera `sensor_pixel_size_um`.

**Camera warm-up:** on multi-camera systems, one warm-up frame is grabbed per camera used
by the run. This happens on the GUI **Start** path with saving enabled (it rides along with
the disk-space estimate). Runs that skip saving, plus snap, fluidics and headless paths,
skip the warm-up — the same as single-camera behavior today.

### File format guidance for runs that mix cameras

| Saving option | Channels spanning cameras with different frame geometry |
|---|---|
| **Individual images** (default) | **Use this.** Each frame is its own file, so shapes and dtypes may differ freely. |
| **OME-TIFF** | **Not supported.** Every channel in a run must produce the same frame shape (see below). |
| **Zarr v3** | **Blocked** by the Start guard for mixed frame geometry (see below). |
| **Multi-page TIFF** | Not blocked, but not exercised by this feature — prefer individual images. |

**OME-TIFF requires every selected channel to produce the same frame shape.** One stack
file is opened per region + FOV, and its shape and dtype are fixed by the first plane
written, so:

- **RGB is ruled out entirely** — the writer is 2D-grayscale-only and raises
  `NotImplementedError: OME-TIFF saving currently supports 2D grayscale images only`.
- **Two mono cameras of different Y×X also fail** — the second write raises
  `ValueError: Image dimensions do not match existing OME memmap stack`.
- **Two mono cameras of matching Y×X but different bit depth are silently re-cast** to the
  first plane's dtype (`.astype()`), with no error and no log line. This one corrupts data
  quietly rather than failing, so do not rely on OME-TIFF to catch it.

For **any** camera mismatch — mono + color, or mismatched mono — use **individual images**.

A **Zarr** store allocates one uniform array per region/FOV: shape and dtype are fixed by
the first frame written, and a single `pixel_size_um` is recorded. So when the file saving
option is Zarr **and** the checked channels span cameras that differ in frame size,
color-ness, storage bit depth or binned pixel size, the multipoint panel shows a persistent
red warning and **disables Start** until you switch to individual images, uncheck one
camera's channels, or make the cameras match via binning/crop. Two cameras with *identical*
geometry may still use Zarr.

The same check runs at acquisition start as a backstop for headless/MCP runs, which bypass
the widget: the run fails fast with the same message. A selection containing an unavailable
camera's channel is rejected the same way, naming the channels.

> **Note:** that warning's suggested remedy ("switch the file saving option to OME-TIFF")
> is misleading. OME-TIFF cannot hold this selection either — the geometry differences the
> guard rejects (frame size, color-ness, bit depth) are exactly the ones OME-TIFF fails or
> silently mis-casts on. Use **individual images** instead.

Zarr remains fully valid — and selectable — for single-camera runs.

## 5. Failures and edge cases

- **A declared camera fails to open at startup:** the software logs it and continues with
  the cameras that did open; channels bound to the missing camera are greyed out, and an
  acquisition that includes them is rejected with a message naming them. No startup crash.
  If the **primary** (id 1) fails to open, startup fails — there is no imaging camera.
- **A channel references an unknown camera id:** treated the same as unavailable.
- **Hardware trigger on a non-wired camera:** never offered in the GUI; a programmatic
  attempt raises `ValueError`.
- **Mid-run disconnect:** handled by the existing driver error paths (the acquisition
  aborts). No new machinery.

## 6. v1 limits

- **Tracking** and **Simple Recording** are primary-camera-only. Opening either tab
  auto-switches to the primary camera (live is stopped first) and logs a notice.
- **Multipoint with Fluidics** has no Start-button guard and no error dialog (unlike the
  Flexible and Wellplate panels). Its only net is the acquisition-start backstop, which
  aborts the run and writes the reason to the log.
- **No per-camera Zarr stores.** v1 validates the mismatch instead of splitting stores.
- **No serial-number camera opening (except FLIR).** Most drivers open the "first camera
  found", so two cameras of the **same vendor type** are not reliably distinguishable yet —
  the serial numbers are recorded in `cameras.yaml` but not yet plumbed through the
  Toupcam/Hamamatsu/Tucsen drivers. Different-vendor pairs and simulation are fine.
- **No switch-minimizing channel reordering** — your channel order is preserved.
- **`hardware_bindings.yaml` emission-wheel dispatch** is not wired per camera.

## Trying it in simulation

Put the two-camera `cameras.yaml` above in `software/machine_configs/` with any distinct
serial numbers, give camera 2 `default_pixel_format: "RGB24"`, and run:

```bash
python3 main_hcs.py --simulation
```

The simulated camera serves RGB frames when configured with a color pixel format, so the
mono + color mix is testable without hardware.
