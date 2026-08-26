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
# NOTE: do NOT install apt's python3-pyqtgraph / python3-pyqt5 here. python3-pyqtgraph
# pulls in python3-pyqt5 as a dependency, and having BOTH PyQt5 and PyQt6 present in the
# same environment causes conflicting Qt libraries that break napari's OpenGL rendering
# (blank/failed canvases, "Cannot SIZE object N because it does not exist"). We install
# pyqtgraph and PyQt6 via pip below so exactly one Qt binding is present.

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

# Qt binding: the app targets PyQt6 (QT_API=pyqt6; napari itself supports either binding).
# Exactly ONE Qt binding may be installed — PyQt5 and PyQt6 in the same environment
# conflict and break napari/vispy OpenGL rendering. Remove any pre-existing PyQt5
# (e.g. pulled in by an apt package) first.
# Pinned to the tested combination. napari 0.7.1 blocks PyQt6-Qt6 6.11.0 and 6.11.1
# (dock-widget and resizing bugs, napari/napari#9052); the napari[pyqt6] extra installed
# below makes pip re-check these pins against napari's own Qt constraints, so bump them
# together.
sudo apt remove -y python3-pyqt5 python3-pyqt5.qtsvg 2>/dev/null || true
pip3 uninstall -y PyQt5 PyQt5-Qt5 PyQt5-sip 2>/dev/null || true
pip3 install "PyQt6==6.11.0" "PyQt6-Qt6==6.11.2" "PyQt6-sip==13.12.0"

# install libraries. No "numpy<2" pin: napari 0.7 only needs numpy>=1.24, but the rest of
# the current stack (opencv-python 5.x, pyqtgraph 0.14, ...) targets NumPy 2 and the
# pinned-back packages aicsimageio drags in (tifffile 2023.2, zarr 2.15, lxml 4.9) were
# verified to work with it.
pip3 install pyqtgraph qtpy pyserial pandas imageio crc==1.3.0 lxml numpy tifffile scipy pyreadline3
pip3 install opencv-python-headless opencv-contrib-python-headless
# napari pinned to a tested release (patch releases change its vispy/Qt constraints). The
# [pyqt6] extra is what makes pip enforce napari's Qt blocklist against the PyQt6 above.
pip3 install "napari[pyqt6]==0.7.1" scikit-image dask_image ome_zarr aicsimageio basicpy pytest pytest-qt pytest-xvfb gitpython matplotlib pydantic_xml pyvisa hidapi filelock lxml_html_clean psutil mcp ndv

# Optional: PI V-308 / C-414 focus stage (USE_PI_FOCUS_STAGE). Safe to skip if unused;
# squid.stage.pi imports it lazily and only needs it to connect to real hardware, so
# `||` keeps an install failure non-fatal under set -e.
pip3 install pipython || echo "WARNING: pipython install failed; continuing (only needed for USE_PI_FOCUS_STAGE)." >&2

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
