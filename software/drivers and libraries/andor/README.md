# Andor SDK3 cameras

`control/camera_andor.py` drives Andor sCMOS cameras (Zyla / ZL41 Cell, Sona, Marana, ...) through
Andor SDK3 and its Python binding `pyAndorSDK3`. Both ship with Andor's SDK3 download (Andor /
Oxford Instruments account required) and are not on PyPI, so nothing here can be fetched by script.

If `pyAndorSDK3` is missing, `squid.camera.utils.get_camera` logs a warning and falls back to the
simulated `DefaultCamera`; a camera that "works" in the GUI but shows synthetic frames is the usual
sign the import failed.

## Linux

1. Download "Andor SDK3 for Linux" and unpack it.
2. `bash install_andor.sh /path/to/unpacked-sdk` - runs the SDK's `install_andor` (libraries and
   USB udev rules) and pip installs `pyAndorSDK3` from the SDK's `python/` folder.
3. Re-plug the camera (the udev rules apply to new connections).

## Windows

1. Install Andor SDK3 (the installer puts the DLLs under the SDK folder and installs the USB driver).
2. From the SDK's `Python` folder: `pip install pyAndorSDK3-<version>.whl` (or the package directory).

The driver points `pyAndorSDK3` at the libraries it bundles under `libs/<Windows|Linux>/64` when
that folder exists; an SDK installed system-wide is found without it.

## Configuration

In the `.ini`: `camera_type = Andor`, `main_camera_model = ZYLA-4.2P-USB3-C` (see
`AndorCameraModel` in `squid/config.py`), and the usual `[CAMERA_CONFIG]` section. Resolutions and
pixel size are read from the camera. `andor_aux_output_source = "FireAll"` drives the camera's
AUX OUT pin from the exposure window (high only while every row is exposing) for laser blanking;
leave it unset to keep the camera's own setting.
