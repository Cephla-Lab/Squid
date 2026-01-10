# Squid
[![scopes](https://github.com/Alpaca233/assets/blob/main/Squid/scopes.png)](https://cephla.com/)

The Squid repo provides firmware and software for controlling [Cephla](https://cephla.com/)'s Squid microscope.

Applications include:
- Slide scanning
- Live Cell Imaging
- High Content Screening
- Optical Pooled Screen
- Spatial Omics (with fluidics integration)
- Organoids
- Expansion Microscopy
and more...

Specifications of Squid microscope: [link](https://drive.google.com/file/d/17UNSiwup-NDPrC1WH6AqDNlK4GmBZlK2/view)

Follow the most recent development and share how you use Squid on [Cephla forum](https://forum.squid-imaging.org/)

See related work and applications on: www.cephla.com, www.squid-imaging.org
## User Interface
![ui](https://github.com/Alpaca233/assets/blob/main/Squid/gui.png)

## How to Start
### Software
#### Setting up and run Squid software on Linux
Ubuntu 22.04 is the most tested platform. Other Linux systems should work but performance is not guaranteed.

See [README](https://github.com/Cephla-Lab/Squid/blob/master/software/README.md) in `/software` directory for instructions. Toupcam and laser auto-focus camera dependencies will be installed automatically when you run the setup script.

After installation, you can run `python3 /software/tools/script_create_desktop_shortcut.py` to create a shortcut on Desktop.
#### Setting up and run Squid software on Windows
See this [post](https://forum.squid-imaging.org/t/setting-up-the-software-on-a-windows-computer/77) on Cephla forum for Windows instructions.

If your Squid has a laser auto-focus module, you will need to install the [driver](https://drive.google.com/drive/folders/1wq0QocIqeD-ZyYgHUPIJ1efOPiPq-fom?usp=sharing) for the laser auto-focus camera and **reboot** the computer. You may also need to install the driver for the main camera you use.

After installation, you can follow this [post](https://forum.squid-imaging.org/t/setting-up-desktop-shortcut-on-a-windows-computer/94) to create a shortcut on Desktop.
#### Setting up Cephla image stitcher
See the [image-stitcher repo](https://github.com/Cephla-Lab/image-stitcher)

#### User manual
Software SOP: [link](https://cephla.notion.site/Squid-user-manual-2025-06-2102dfbf6ae48034bb3bf56641f1c8c7?pvs=143)

Fluidics acquisition SOP: [link](https://cephla.notion.site/User-manual-for-fluidics-imaging-21c2dfbf6ae48036aa0ef633ef155530)

New version coming soon!

### Firmware
Usually firmware should be already uploaded to the controller. If you do need to re-upload firmware, see the [firmware README](firmware/README.md) for instructions.

### Git Submodules
This repository uses git submodules for external dependencies. After cloning, initialize submodules:

```bash
# Clone with submodules (recommended)
git clone --recursive https://github.com/Cephla-Lab/Squid.git

# Or initialize after clone
git submodule update --init --recursive
```

**Submodules:**
| Path | Repository | Description |
|------|------------|-------------|
| `software/control/ndviewer_light` | [ndviewer_light](https://github.com/Cephla-Lab/ndviewer_light) | Lightweight NDV-based image viewer |
| `software/fluidics_v2` | [fluidics_v2](https://github.com/Alpaca233/fluidics_v2) | Fluidics control |

**Updating submodules:**
```bash
# Update a specific submodule to latest
git submodule update --remote software/control/ndviewer_light
git add software/control/ndviewer_light
git commit -m "chore: update ndviewer_light submodule"
```

## Open-source Assets for the original Squid
![alt text](https://i.imgur.com/Gjwh02y.png)
- tracking software repo: [GitHub](https://github.com/prakashlab/squid-tracking)
- CAD models/photos of assembled squids: [Google Drive](https://drive.google.com/drive/folders/1JdVp34HtERGpBCBlFX6jFDwMUdeBLCEx?usp=sharing)
- BOM for the microscope, including CAD files for CNC machining: [link](https://docs.google.com/spreadsheets/d/1WA64HySj9I7XROtTXuaRvjlbhHXRGspvoxb_20CWDR8/edit?usp=drivesdk)
- BOM for the multicolor laser engine: [link](https://docs.google.com/spreadsheets/d/1hEM6PsxZPTp1LY3cpxUJOS3Q1YLQN-xniF33ZddFj9U/edit#gid=1175873468)
- BOM for the control panel: [link](https://docs.google.com/spreadsheets/d/1z2HjibIG9PHffiDsbuzQXmvf2gSFMduHrXkPwDbcXRY/edit?usp=sharing)
  
## References
[1] Hongquan Li, Deepak Krishnamurthy, Ethan Li, Pranav Vyas, Nibha Akireddy, Chew Chai, Manu Prakash, "**Squid: Simplifying Quantitative Imaging Platform Development and Deployment**." BiorXiv [ link | [website](https://squid-imaging.org)]

[2] Deepak Krishnamurthy, Hongquan Li, François Benoit du Rey, Pierre Cambournac, Adam G. Larson, Ethan Li, and Manu Prakash. "**Scale-free vertical tracking microscopy.**" Nature Methods 17, no. 10 (2020): 1040-1051. [ [link](https://www.nature.com/articles/s41592-020-0924-7) | [website](https://gravitymachine.org) ]

## Acknowledgement
The Original Squid software was developed with structuring inspiration from [Tempesta-RedSTED](https://github.com/jonatanalvelid/Tempesta-RedSTED). The laser engine is inspired by [https://github.com/ries-lab/LaserEngine](https://github.com/ries-lab/LaserEngine). 
