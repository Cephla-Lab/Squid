# Squid (formerly [octopi-research](https://github.com/hongquanli/octopi-research))
![alt text](https://cdn.prod.website-files.com/68228d7b099844b42c2ee743/682db8eed5a5528c985453ce_1-p-500.png)![alt text](https://cdn.prod.website-files.com/68228d7b099844b42c2ee743/682db8eda67fddf09aa92047_2-p-500.png)
TODO: images

The Squid repo provides firmware and software for controlling [Cephla](https://cephla.com/)'s Squid microscope.

Applications include:
- slide scanner for digital pathology
- time lapse imaging with 2D or 3D tiling
- spatial omics that involves multicolor and multi-round imaging
- tracking microscopy
- computational microscopy, including label free microscopy using phase/polarization/reflectance + deep learning
- super resolution microscopy
- light sheet microscopy

Specifications of Squid microscope: [link](https://drive.google.com/file/d/17UNSiwup-NDPrC1WH6AqDNlK4GmBZlK2/view)

Follow the most recent development and share how you use Squid on [Cephla forum](https://forum.squid-imaging.org/)

See related Work and applications on: www.squid-imaging.org
## User Interface and Features
TODO: images

Acquisition

Mosaic view

Laser auto-focus

Fluidics control
## How to Start
### Software
#### Setting up and run Squid software on Linux
Ubuntu 22.04 is the most tested platform. Other Linux systems should work but performance is not guaranteed.

See [README](https://github.com/Cephla-Lab/Squid/blob/master/software/README.md) in `/software` directory for instructions.

After installation, you can run `python3 /software/tools/script_create_desktop_shortcut.py` to create a shortcut on Desktop.
#### Setting up and run Squid software on Windows
See this [post](https://forum.squid-imaging.org/t/setting-up-the-software-on-a-windows-computer/77) on Cephla forum for Windows instructions.

If your Squid has a laser auto-focus module, you will need to install the driver for the laser auto-focus camera and reboot the computer. [Download](TODO: link)

After installation, you can follow this [post](https://forum.squid-imaging.org/t/setting-up-desktop-shortcut-on-a-windows-computer/94) to create a shortcut on Desktop.
#### Setting up Cephla image stitcher
See the [image-stitcher repo](https://github.com/Cephla-Lab/image-stitcher)

### Firmware
Usually firmware should be already uploaded to the controller. If you do need to re-upload firmware, you may follow the instructions in this [post](https://forum.squid-imaging.org/t/setting-up-arduino-teensyduino-ide-for-uploading-firmware/36).

Latest firmware for main controller: https://github.com/Cephla-Lab/Squid/tree/master/firmware/octopi_firmware_v2/main_controller_teensy41
Latest firmware for joystick controller: https://github.com/Cephla-Lab/Squid/tree/master/firmware/octopi_firmware_v2/control_panel_teensyLC

## Open-source Assets
![alt text](https://i.imgur.com/Gjwh02y.png)
- tracking software repo: [GitHub](https://github.com/prakashlab/squid-tracking)
- CAD models/photos of assembled squids: [Google Drive](https://drive.google.com/drive/folders/1JdVp34HtERGpBCBlFX6jFDwMUdeBLCEx?usp=sharing)
- BOM for the microscope, including CAD files for CNC machining: [link](https://docs.google.com/spreadsheets/d/1WA64HySj9I7XROtTXuaRvjlbhHXRGspvoxb_20CWDR8/edit?usp=drivesdk)
- BOM for the multicolor laser engine: [link](https://docs.google.com/spreadsheets/d/1hEM6PsxZPTp1LY3cpxUJOS3Q1YLQN-xniF33ZddFj9U/edit#gid=1175873468)
- BOM for the control panel: [link](https://docs.google.com/spreadsheets/d/1z2HjibIG9PHffiDsbuzQXmvf2gSFMduHrXkPwDbcXRY/edit?usp=sharing)
  
## References
[1] Hongquan Li, Deepak Krishnamurthy, Ethan Li, Pranav Vyas, Nibha Akireddy, Chew Chai, Manu Prakash, "**Squid: Simplifying Quantitative Imaging Platform Development and Deployment**." BiorXiv [ link | [website](https://squid-imaging.org)]

For scale-free vertical tracking microscopy, check out our work at:

[2] Deepak Krishnamurthy, Hongquan Li, François Benoit du Rey, Pierre Cambournac, Adam G. Larson, Ethan Li, and Manu Prakash. "**Scale-free vertical tracking microscopy.**" Nature Methods 17, no. 10 (2020): 1040-1051. [ [link](https://www.nature.com/articles/s41592-020-0924-7) | [website](https://gravitymachine.org) ]

## Acknowledgement
The Squid software was developed with structuring inspiration from [Tempesta-RedSTED](https://github.com/jonatanalvelid/Tempesta-RedSTED). The laser engine is inspired by [https://github.com/ries-lab/LaserEngine](https://github.com/ries-lab/LaserEngine). 
