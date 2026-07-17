#!/usr/bin/env bash
# Control panel firmware flashing script
#   ./download.sh            interactive selection
#   ./download.sh cn         domestic build (default, /8 sensitivity)
#   ./download.sh overseas   overseas build (/4 sensitivity, 2x output)
set -e
cd "$(dirname "$0")"

choice="${1:-}"
if [ -z "$choice" ]; then
    echo "Select the build to flash:"
    echo "  1) domestic (cn)       - joystick X/Y sensitivity factor /8"
    echo "  2) overseas            - joystick X/Y sensitivity factor /4"
    read -rp "Enter 1 or 2: " ans
    case "$ans" in
        1|cn|CN)        choice=cn ;;
        2|overseas|OS)  choice=overseas ;;
        *) echo "Invalid selection: $ans" >&2; exit 1 ;;
    esac
fi

case "$choice" in
    cn)        env=teensyLC ;;
    overseas)  env=teensyLC_overseas ;;
    *) echo "Unknown build: $choice (allowed: cn | overseas)" >&2; exit 1 ;;
esac

echo ">>> Flashing env=$env"
pio run -e "$env" -t upload
