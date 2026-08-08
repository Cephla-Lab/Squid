#!/usr/bin/env bash
# Octoaxes main-controller firmware flashing script
#   ./download.sh                interactive selection
#   ./download.sh safe           enable the laser interlock (pin 2 must be wired to the interlock signal; standard factory build)
#   ./download.sh nointerlock    disable the laser interlock (for laser-free stations, otherwise the D1-D5 fluorescence channels will not light)
set -e
cd "$(dirname "$0")"

choice="${1:-}"
if [ -z "$choice" ]; then
    echo "Select the build to flash:"
    echo "  1) interlock enabled (safe)        - pin 2 must be wired to the laser interlock signal to enable the D1-D5 TTL ports"
    echo "  2) interlock disabled (nointerlock) - skips the interlock check, for laser-free stations; the LED matrix is unaffected"
    read -rp "Enter 1 or 2: " ans
    case "$ans" in
        1|safe|SAFE)              choice=safe ;;
        2|nointerlock|NOINTERLOCK) choice=nointerlock ;;
        *) echo "Invalid choice: $ans" >&2; exit 1 ;;
    esac
fi

case "$choice" in
    safe)         env=teensy41 ;;
    nointerlock)  env=teensy41_nointerlock ;;
    *) echo "Unknown build: $choice (allowed: safe | nointerlock)" >&2; exit 1 ;;
esac

echo ">>> flashing env=$env"
pio run -e "$env" -t upload
