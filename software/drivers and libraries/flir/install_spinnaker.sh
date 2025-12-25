#!/bin/bash
wget https://flir.netx.net/file/asset/66370/original/attachment/spinnaker-3.2.0.62-amd64-pkg.22.04.tar.gz
tar -xvf spinnaker-3.2.0.62-amd64-pkg.22.04.tar.gz
sudo apt-get install libavcodec58 libavformat58 \
libswscale5 libswresample3 libavutil56 libusb-1.0-0 \
libpcre2-16-0 libdouble-conversion3 libxcb-xinput0 \
libxcb-xinerama0
cd spinnaker-3.2.0.62-amd64
sudo sh install_spinnaker.sh
sudo sh configure_usbfs.sh
