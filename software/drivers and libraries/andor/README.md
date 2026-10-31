# Andor SDK3 cameras

`control/camera_andor.py` drives Andor sCMOS cameras (Zyla / ZL41 Cell, Sona, Marana, ...) through
Andor SDK3 and its Python binding `pyAndorSDK3`. Both ship with Andor's SDK3 download (Andor /
Oxford Instruments account required) and are not on PyPI, so nothing here can be fetched by script.

If `pyAndorSDK3` is missing, `squid.camera.utils.get_camera` logs a warning (`Failed to import
control.camera_andor`) and falls back to `DefaultCamera`, the Daheng/gxipy driver, which then
fails to find a camera of the configured model. That warning in the log is the sign the SDK
binding is not installed.

## Linux

1. Download "Andor SDK3 for Linux" and unpack it.
2. `bash install_andor.sh /path/to/unpacked-sdk` - runs the SDK's `install_andor` (libraries and
   USB udev rules) and pip installs `pyAndorSDK3` from the SDK's `python/` folder.
3. For a USB 3 camera, follow the SDK's post-install USB notes (`USBManualConfig.txt` in the
   unpacked SDK) - the installer alone does not finish the USB setup.
4. Re-plug the camera (the udev rules apply to new connections).

## Windows

1. Install Andor SDK3 (the installer puts the DLLs under the SDK folder and installs the USB driver).
2. From the SDK's `Python` folder: `pip install pyAndorSDK3-<version>.whl` (or the package directory).

The driver points `pyAndorSDK3` at the libraries it bundles under `libs/<Windows|Linux>/64` when
that folder exists; an SDK installed system-wide is found without it.

## Configuration

In the `.ini`, under `[GENERAL]`:

```ini
camera_type = Andor
main_camera_model = ZYLA-4.2P-USB3-C   ; see AndorCameraModel in squid/config.py
use_andor_active_blanking_two_camera = False   ; True: AUX OUT follows the "all rows exposing" window
```

plus the usual `[CAMERA_CONFIG]` section (pixel format, binning, crop, ROI defaults). Resolutions
and pixel size are read from the camera. `use_andor_active_blanking_two_camera = True` sets the
camera's AUX OUT source to SDK3 `FireAll` so the laser engine wired to that pin is blanked during
readout; it is off by default and only makes sense with that wiring.
