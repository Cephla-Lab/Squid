#!/bin/bash
# Installs Andor SDK3 and its Python binding (pyAndorSDK3) for control/camera_andor.py.
#
# SDK3 is licensed and not downloadable without an Andor account, so unlike the other vendor
# scripts this one does not fetch anything: download "Andor SDK3 for Linux" from Andor/Oxford
# Instruments, unpack it, and pass the unpacked directory:
#
#   bash install_andor.sh /path/to/andor-sdk3-linux
#
# The SDK's own installer sets up the libraries and the USB udev rules; pyAndorSDK3 is pip
# installed from the wheel (or source package) the SDK ships under its python/ folder.
set -euo pipefail

if [ $# -ne 1 ] || [ ! -d "$1" ]; then
    echo "usage: $0 <unpacked Andor SDK3 for Linux directory>" >&2
    exit 1
fi
SDK_DIR="$(cd "$1" && pwd)"

if [ -x "$SDK_DIR/install_andor" ]; then
    (cd "$SDK_DIR" && sudo ./install_andor)
else
    echo "no install_andor in $SDK_DIR - is this the unpacked SDK3 for Linux?" >&2
    exit 1
fi

WHEEL="$(find "$SDK_DIR" -name 'pyAndorSDK3*.whl' | head -n 1 || true)"
if [ -n "$WHEEL" ]; then
    pip install "$WHEEL"
elif [ -f "$SDK_DIR/python/pyAndorSDK3/setup.py" ]; then
    pip install "$SDK_DIR/python/pyAndorSDK3"
else
    echo "pyAndorSDK3 not found under $SDK_DIR/python - install it by hand from the SDK's Python folder" >&2
    exit 1
fi

python3 -c "import pyAndorSDK3; print('pyAndorSDK3', getattr(pyAndorSDK3, '__version__', 'installed'))"
