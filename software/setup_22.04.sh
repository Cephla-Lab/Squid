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
readonly THORLABS_PM_UDEV_RULE_PATH="$SQUID_SOFTWARE_ROOT/drivers and libraries/thorlabs/linux/udev/99-thorlabs-pm16.rules"
readonly PI_UDEV_RULE_DIR="$SQUID_SOFTWARE_ROOT/drivers and libraries/pi/udev"
# update
sudo apt update

# install packages
sudo apt install python3-pip -y
# PyQt5 from apt (Squid's binding); pyqtgraph comes from pip below. apt's python3-pyqtgraph drags in
# Ubuntu 22.04's matplotlib 3.5 / scipy 1.8 / numpy 1.21, and its mpl_toolkits is a regular package that
# shadows the one in pip's matplotlib (mplot3d then imports a name removed in matplotlib 3.8).
sudo apt install python3-pyqt5 -y
sudo apt install python3-pyqt5.qtsvg

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

# Ubuntu 22.04 ships pip 22.0.2, whose resolver can't handle the
# napari==0.5.4 dependency graph on current PyPI (hits ResolutionTooDeep
# after hours of backtracking). Upgrade pip before installing libraries.
python3 -m pip install --upgrade pip

# install libraries
# numpy>=2: the bundled viewer (control/ndviewer_light) needs ndv>=0.5, whose ihist dependency requires
# numpy 2; the numpy<2 pin from #501 (scipy 1.12 wheels built against numpy 1) is no longer needed.
# floors: an existing install still has Ubuntu's matplotlib 3.5 / scipy 1.8 / pyqtgraph 0.12 (numpy 1
# builds) from the old apt line; pip then installs numpy 2 builds that shadow them (user site comes first).
# colorama>=0.4.6: pyqtgraph >= 0.13 imports it; apt's 0.4.4 satisfies a bare `colorama` and is removed
# again by the autoremove below, so the floor makes pip install its own copy.
pip3 install qtpy pyserial pandas imageio crc==1.3.0 lxml "numpy>=2" tifffile "scipy>=1.13" "matplotlib>=3.9" "pyqtgraph>=0.13.4" "colorama>=0.4.6" pyreadline3
# opencv<5: OpenCV 5 (2026-07) is what pip picks with numpy 2; Squid is tested on the 4.x API.
pip3 install "opencv-python-headless<5" "opencv-contrib-python-headless<5"
# mcp<2: mcp 2.0 dropped the decorator API that the MCP bridge (mcp_microscope_server.py) is written against
# ndv>=0.5,<0.6 + pyconify: what control/ndviewer_light requires (pyconify through superqt[iconify]).
# aicsimageio is gone: its only user was control/stitcher.py, which nothing imports.
pip3 install napari==0.5.4 scikit-image dask_image ome_zarr basicpy pytest pytest-qt pytest-xvfb gitpython pydantic_xml pyvisa pyvisa-py pyusb hidapi filelock lxml_html_clean psutil "mcp<2" "ndv>=0.5,<0.6" pyconify

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
# Thorlabs power meter for Utils > Illumination Power Calibration (pyvisa-py opens it without root)
sudo cp "$THORLABS_PM_UDEV_RULE_PATH" /etc/udev/rules.d

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
