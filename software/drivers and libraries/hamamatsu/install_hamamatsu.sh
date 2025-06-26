#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
wget https://www.hamamatsu.com/content/dam/hamamatsu-photonics/sites/static/sys/dcam-api-for-linux/tar-gz/DCAM-API_Lite_for_Linux_v24.4.6764.tar.gz
tar -xvf DCAM-API_Lite_for_Linux_v24.4.6764.tar.gz
ln -s "$SCRIPT_DIR/DCAM-API_Lite_for_Linux_v24.4.6764" /tmp/dcam_lib
cd /tmp/dcam_lib
./api/install.sh usb3
cd "$SCRIPT_DIR"
rm /tmp/dcam_lib
