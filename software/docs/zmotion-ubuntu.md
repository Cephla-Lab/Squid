# Zmotion scanner on Ubuntu

How to run the Zmotion-controller spike (`main_zmotion_spike.py`) on Ubuntu x86-64. Everything else is
the same as on the Windows bench PC.

## Network

The controller is at `10.0.0.100`. Give the PC a static address in `10.0.0.x/24` on the interface
cabled to it (for example `10.0.0.10`; Settings > Network > IPv4 > Manual, or
`nmcli con mod "<connection>" ipv4.method manual ipv4.addresses 10.0.0.10/24`), then check with
`ping 10.0.0.100`. `ZMOTION_IP=<address>` points the launcher at a different controller address.

## Controller library

The vendored Linux build is `software/drivers and libraries/zmotion/linux/x64/libzmotion.so`
(x86-64, glibc >= 2.17; versions and hashes in that folder's `README.md`). Nothing has to be installed:
the loader finds it there and calls `ZMC_LinuxLibInit` once before anything else, as the vendor requires.
`ZMOTION_LIB=/path/to/libzmotion.so` overrides the path.

Check the library without hardware: `python -m pytest tests/control/test_zmotion_library.py -q`
(from `software/`).

## Launch

From `software/`:

```bash
cp configurations/configuration_Zmotion_spike.ini .
python main_zmotion_spike.py                # real controller
python main_zmotion_spike.py --simulation   # simulated camera and controller, no hardware
```

All of `main_hcs.py`'s options work (`--verbose`, `--skip-init`, `--start-server`, `--qt-api`).
The ini selects PyQt6 (`qt_api = pyqt6`); `setup_22.04.sh` installs PyQt5 only, so either install PyQt6
in the Python environment or pass `--qt-api pyqt5`.

## Camera (ToupCam ITR10CMOS26000KMA)

This camera needs ToupCam SDK 60 or newer: the SDK 59 libraries do not contain its model (no
`ITR10CMOS26000KMA` in the 59.30239 builds), which is why the SDK 59 Windows DLL could not enumerate it.
The vendored `drivers and libraries/toupcam/linux/x64/libtoupcam.so` is already SDK **60.32891.20261012**
(updated with the Windows x64 DLL, 60.32887.20261012, in PR #707) and contains the model, so Ubuntu
needs no newer library from the camera vendor. The Python wrapper `control/toupcam.py` is still 59.30239;
PR #707 ran it against the SDK 60 library.

USB access as a normal user needs `drivers and libraries/toupcam/linux/udev/99-toupcam.rules`
(installed by `setup_22.04.sh`), which matches USB vendor ID `0547`. If the camera is not found, check
its vendor ID with `lsusb`; a rebranded camera with another ID needs its own rule.

## Known gaps

- Not yet run on the instrument from Ubuntu: the library loads and initialises in the test, but no
  connection, motion, trigger or camera frame has been checked on Linux.
- The ToupCam SDK 60 library is only vendored for Linux x64 and Windows x64; other Linux architectures
  (arm64, armhf, ...) are still SDK 59 and would not see this camera.
