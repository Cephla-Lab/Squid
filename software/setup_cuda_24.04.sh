#!/bin/bash
# Ubuntu 24.04 variant of setup_cuda_22.04.sh: NVIDIA's ubuntu2404 apt repo, and pip needs
# --break-system-packages on 24.04 (PEP 668), set here through pip's environment variable.
export PIP_BREAK_SYSTEM_PACKAGES=1
sudo apt update
# sudo apt install nvidia-driver-530
cd ~/Downloads
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update
sudo apt-get -y install cuda
pip install cuda-python
pip install cupy-cuda12x
pip3 install torch torchvision torchaudio

sudo apt autoremove -y
