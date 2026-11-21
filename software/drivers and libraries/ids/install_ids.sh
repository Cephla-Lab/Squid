#!/bin/bash
# Installs the iDS peak SDK next to this script and adds its library paths to ~/.bashrc.
# Works from any directory and with `sh ./install_ids.sh` (the README's command), so it uses $0, not BASH_SOURCE.
set -e
IDS_DIR="$(cd "$(dirname "$0")" && pwd)"
IDS_PEAK="ids-peak_2.11.0.0-178_amd64"
cd "$IDS_DIR"

wget "https://en.ids-imaging.com/files/downloads/ids-peak/software/linux-desktop/$IDS_PEAK.tgz"
tar -xvzf "$IDS_PEAK.tgz"
sudo sh "$IDS_PEAK/local/scripts/ids_install_udev_rule.sh"
python3 -m pip install ids_peak_ipl
python3 -m pip install ids_peak
python3 -m pip install ids_peak_afl

# The paths point at this checkout. Older versions of this script wrote ~/Desktop/octopi-research paths under the
# marker "IDS camera library paths"; those are left in place (harmless when the folder does not exist).
MARKER="IDS camera library paths (Squid)"
content="
# $MARKER
export LD_LIBRARY_PATH=\"$IDS_DIR/$IDS_PEAK/lib:\$LD_LIBRARY_PATH\"
export GENICAM_GENTL32_PATH=\"\$GENICAM_GENTL32_PATH:$IDS_DIR/$IDS_PEAK/lib/ids/cti\"
export GENICAM_GENTL64_PATH=\"\$GENICAM_GENTL64_PATH:$IDS_DIR/$IDS_PEAK/lib/ids/cti\"
"

if ! grep -qF "$MARKER" ~/.bashrc 2>/dev/null; then
    echo "$content" >> ~/.bashrc
    echo "iDS peak library paths added to ~/.bashrc"
else
    echo "iDS peak library paths already in ~/.bashrc"
fi

echo "Finished installation for iDS camera. Open a new terminal (or log out and back in) before starting Squid."
