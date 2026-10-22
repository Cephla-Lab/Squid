#!/bin/bash
set -eo pipefail

if [[ -n "$TRACE" ]]; then
  echo "TRACE variable non-empty, turning on script tracing."
  set -x
fi

SQUID_REPO_PATH="$HOME/Desktop/Squid"

for i in "$@"; do
  case $i in
    -r=*|--repo_path=*)
      SQUID_REPO_PATH="$(cd "${i#*=}" && pwd)"
      shift
      ;;
    -*|--*)
      echo "Unknown option $i"
      exit 1
      ;;
    *)
      ;;
  esac
done

echo "Using SQUID_REPO_PATH='${SQUID_REPO_PATH}'"

readonly SQUID_REPO_HTTP="https://github.com/Cephla-Lab/Squid.git"
readonly SQUID_SOFTWARE_ROOT="${SQUID_REPO_PATH}/software"
readonly SQUID_REPO_PATH_PARENT="$(dirname "${SQUID_REPO_PATH}")"
readonly DAHENG_CAMERA_DRIVER_ROOT="$SQUID_SOFTWARE_ROOT/drivers and libraries/daheng camera/Galaxy_Linux-x86_Gige-U3_32bits-64bits_1.2.1911.9122"
readonly DAHENG_CAMERA_DRIVER_API_ROOT="$SQUID_SOFTWARE_ROOT/drivers and libraries/daheng camera/Galaxy_Linux_Python_1.0.1905.9081/api"
readonly TOUPCAM_UDEV_RULE_PATH="$SQUID_SOFTWARE_ROOT/drivers and libraries/toupcam/linux/udev/99-toupcam.rules"
readonly PI_UDEV_RULE_DIR="$SQUID_SOFTWARE_ROOT/drivers and libraries/pi/udev"
# update
sudo apt update

# install packages
sudo apt install python3-pip -y
# Do not apt-install python3-pyqtgraph: it pulls in python3-pyqt5 (see the Qt-binding note
# below). pyqtgraph comes from pip.

sudo apt-get install git -y
## clone the repo if we don't already have it.
# No matter, make sure the repo's parent dir is there
mkdir -p "${SQUID_REPO_PATH_PARENT}"
if [[ ! -d "${SQUID_REPO_PATH}" ]]; then
  git clone "$SQUID_REPO_HTTP" "${SQUID_REPO_PATH}"
else
  echo "Using existing repo at '${SQUID_REPO_PATH}' at HEAD=$(cd "${SQUID_REPO_PATH}" && git rev-parse HEAD)"
fi


cd "$SQUID_SOFTWARE_ROOT"
mkdir -p "$SQUID_SOFTWARE_ROOT/cache"

# Ubuntu 22.04 ships an old pip; upgrade it before resolving the dependency graph.
python3 -m pip install --upgrade pip

# Qt binding: the app targets PyQt6 (QT_API=pyqt6). Exactly ONE Qt binding may be installed:
# PyQt5 and PyQt6 side by side break napari/vispy OpenGL rendering (blank canvases, "Cannot
# SIZE object N because it does not exist"), so remove any pre-existing PyQt5 first.
sudo apt remove -y python3-pyqt5 python3-pyqt5.qtsvg 2>/dev/null || true
pip3 uninstall -y PyQt5 PyQt5-Qt5 PyQt5-sip 2>/dev/null || true

# install libraries. No "numpy<2" pin: napari 0.7 only needs numpy>=1.24, and the rest of
# the current stack (opencv-python 5.x, pyqtgraph 0.14, ...) targets NumPy 2.
#
# aicsimageio and basicpy are deliberately NOT installed: basicpy pins scipy<1.13, which
# drags the environment back to numpy 1.26 and breaks napari and opencv (see the commit
# "stop installing basicpy/aicsimageio"). Their only consumer, control/stitcher.py, is not
# imported by the application; install them into a separate venv if you need it.
pip3 install pyqtgraph qtpy pyserial pandas imageio crc==1.3.0 lxml numpy tifffile scipy pyreadline3
pip3 install opencv-python-headless opencv-contrib-python-headless
# napari and PyQt6 pinned to the tested combination and resolved in one pip call, so the
# [pyqt6] extra checks the PyQt6 pins against napari's Qt blocklist (napari 0.7.1 blocks
# PyQt6-Qt6 6.11.0 and 6.11.1, napari/napari#9052): bump them together.
# tensorstore is required by control/ndviewer_light and by tests/control/core/test_zarr_writer.py,
# which begins with pytest.importorskip("tensorstore") -- without it ~79 zarr tests silently skip.
pip3 install "napari[pyqt6]==0.7.1" "PyQt6==6.11.0" "PyQt6-Qt6==6.11.2" "PyQt6-sip==13.12.0" scikit-image dask_image ome_zarr tensorstore pytest pytest-qt pytest-xvfb gitpython matplotlib pydantic_xml pyvisa hidapi filelock lxml_html_clean psutil mcp ndv

# Optional: PI V-308 / C-414 focus stage (USE_PI_FOCUS_STAGE). Safe to skip if unused;
# squid.stage.pi imports it lazily and only needs it to connect to real hardware, so
# `||` keeps an install failure non-fatal under set -e.
pip3 install pipython || echo "WARNING: pipython install failed; continuing (only needed for USE_PI_FOCUS_STAGE)." >&2

# Fluidics library (RUN_FLUIDICS), vendored as the fluidics_v2 submodule. It declares PyQt5 as a
# dependency, so install it --no-deps to keep pip from pulling a PyQt5 wheel over apt's; its real
# runtime deps follow. `||` keeps a checkout without submodules working (fluidics simply stays off).
pip3 install cobs platformdirs ruamel.yaml pyyaml "pydantic>=2,<3"
pip3 install --no-deps -e fluidics_v2/software || echo "WARNING: fluidics library install failed; continuing (only needed for RUN_FLUIDICS)." >&2

# install camera drivers
cd "$DAHENG_CAMERA_DRIVER_ROOT"
./Galaxy_camera.run
cd "$DAHENG_CAMERA_DRIVER_API_ROOT"
python3 setup.py build
sudo python3 setup.py install
cd "$SQUID_SOFTWARE_ROOT"
sudo cp "$TOUPCAM_UDEV_RULE_PATH" /etc/udev/rules.d

# PI C-414 focus stage (USE_PI_FOCUS_STAGE): bind the custom-VID FTDI to ftdi_sio so
# /dev/ttyUSB* appears, and lower the latency timer. Reload + trigger so it applies now.
sudo cp "$PI_UDEV_RULE_DIR/98-pi-c414-bind.rules" "$PI_UDEV_RULE_DIR/99-pi-ftdi-latency.rules" /etc/udev/rules.d
# Trigger with --action=add: the bind rule matches ACTION=="add", so a plain `udevadm
# trigger` (which defaults to action=change) would NOT bind an already-connected C-414.
sudo udevadm control --reload-rules && sudo udevadm trigger --action=add --subsystem-match=usb --subsystem-match=usb-serial --subsystem-match=tty

# enable access to serial ports without sudo
sudo usermod -aG dialout $USER

sudo apt autoremove -y

echo "Holding kernel packages to prevent automatic updates..."
if sudo apt-mark hold \
  linux-image-generic linux-headers-generic linux-generic \
  "linux-image-$(uname -r)" "linux-headers-$(uname -r)"; then
  echo "Kernel packages held. Run 'sudo apt-mark unhold linux-image-generic linux-headers-generic linux-generic linux-image-$(uname -r) linux-headers-$(uname -r)' to re-enable."
else
  echo "Warning: Failed to hold kernel packages; automatic kernel updates remain enabled." >&2
fi

# create desktop shortcut
mkdir -p "$HOME/Desktop"
DESKTOP_FILE="$HOME/Desktop/Squid_hcs.desktop"
ICON_PATH="$SQUID_SOFTWARE_ROOT/icon/cephla_logo.svg"
cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Name=Squid_hcs
Icon=$ICON_PATH
Exec=gnome-terminal --working-directory="$SQUID_SOFTWARE_ROOT" -e "/usr/bin/env python3 $SQUID_SOFTWARE_ROOT/main_hcs.py"
Type=Application
Terminal=true
EOF
chmod u+rwx "$DESKTOP_FILE"
# mark as trusted on GNOME
gio set "$DESKTOP_FILE" metadata::trusted true 2>/dev/null || true
echo "Desktop shortcut created at: $DESKTOP_FILE"
