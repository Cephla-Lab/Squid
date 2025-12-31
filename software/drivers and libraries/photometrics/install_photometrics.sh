#!/bin/bash
wget https://flir.netx.net/file/asset/68075/original/attachment
mv attachment PVCAM-Linux-3-10-2-5.zip
mkdir -p PVCAM-Linux-3-10-2-5
unzip PVCAM-Linux-3-10-2-5.zip -d PVCAM-Linux-3-10-2-5
rm PVCAM-Linux-3-10-2-5.zip
cd PVCAM-Linux-3-10-2-5
cd pvcam
bash pvcam__install_helper-Ubuntu.sh
cd ..
cd pvcam-sdk
bash pvcam-sdk__install_helper-Ubuntu.sh
cd ../..
pip install PyVCAM
