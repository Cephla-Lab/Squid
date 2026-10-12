#!/bin/bash
set -eo pipefail

# Squid setup script for Ubuntu 26.04 LTS.
#
# Differences from setup_22.04.sh, all driven by Ubuntu 26.04 shipping
# Python 3.14 as the system interpreter:
#
#   1. System pip is "externally managed" (PEP 668), so plain `pip3 install`
#      into the system Python is rejected. We pass --break-system-packages.
#      Without sudo, these land in the user site (~/.local), not the
#      apt-managed system site, so apt's Python packages are left intact.
#
#   2. numpy is no longer capped at <2: NumPy 1.x has no Python 3.14 wheels
#      (1.26 is the last 1.x and tops out at 3.12), so we use numpy 2.x.
#
#   3. napari==0.5.4 is unpinned: that pin predates Python 3.14, so we take
#      the latest napari, which publishes 3.14 wheels.
#
#   4. aicsimageio and basicpy are NOT installed. Their only importer is
#      control/stitcher.py, which nothing in the codebase imports (the
#      active stitcher is tools/stitcher.py, an ImageJ/Fiji-based path).
#      They were the main 3.14 blocker (aicsimageio is in maintenance mode
#      with no 3.14 wheels), so dropping the dead dependency de-risks the
#      install. If control/stitcher.py is ever revived, add them back here.
#
# Qt is still installed from apt (python3-pyqt5*, python3-pyqtgraph): apt
# packages are built against the system Python 3.14 and are not subject to
# PEP 668, so this is the lowest-risk way to get a working PyQt5.

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

# Ubuntu 26.04's system Python (3.14) is externally managed (PEP 668); pip
# refuses to touch it without this flag. Installing without sudo keeps these
# in the user site rather than clobbering apt-managed system packages.
readonly PIP_INSTALL="pip3 install --break-system-packages"

# update
sudo apt update

# install packages
sudo apt install python3-pip -y
sudo apt install python3-pyqtgraph python3-pyqt5 python3-pyqt5.qtsvg -y

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

# install libraries
# numpy unpinned to 2.x (1.x has no Python 3.14 wheels); napari unpinned
# (the old 0.5.4 pin predates 3.14). aicsimageio/basicpy intentionally
# omitted (dead dependency, see header).
$PIP_INSTALL qtpy pyserial pandas imageio crc==1.3.0 lxml tifffile scipy pyreadline3 numpy
$PIP_INSTALL opencv-python-headless opencv-contrib-python-headless
$PIP_INSTALL napari scikit-image dask_image ome_zarr pytest pytest-qt pytest-xvfb gitpython matplotlib pydantic_xml pyvisa hidapi filelock lxml_html_clean psutil mcp ndv

# install camera drivers
cd "$DAHENG_CAMERA_DRIVER_ROOT"
./Galaxy_camera.run
cd "$DAHENG_CAMERA_DRIVER_API_ROOT"
python3 setup.py build
sudo python3 setup.py install
cd "$SQUID_SOFTWARE_ROOT"
sudo cp "$TOUPCAM_UDEV_RULE_PATH" /etc/udev/rules.d

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
Exec=ptyxis --new-window --working-directory="$SQUID_SOFTWARE_ROOT" -- /usr/bin/env python3 $SQUID_SOFTWARE_ROOT/main_hcs.py
Type=Application
Terminal=false
EOF
chmod u+rwx "$DESKTOP_FILE"
# mark as trusted on GNOME
gio set "$DESKTOP_FILE" metadata::trusted true 2>/dev/null || true
echo "Desktop shortcut created at: $DESKTOP_FILE"
