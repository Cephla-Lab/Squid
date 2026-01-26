#!/bin/bash
wget https://flir.netx.net/file/asset/66369/original/attachment/spinnaker_python-3.2.0.62-cp310-cp310-linux_x86_64.tar.gz
mkdir PySpin
tar -xvf spinnaker_python-3.2.0.62-cp310-cp310-linux_x86_64.tar.gz -C PySpin
python3 -m pip install --user PySpin/spinnaker_python-3.2.0.62-cp310-cp310-linux_x86_64.whl
