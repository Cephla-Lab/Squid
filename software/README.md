# Squid Microscope Control Software

Python microscopy control system for Cephla Squid microscopes. PyQt5 GUI with napari/pyqtgraph visualization.

**Supported workflows:** Slide scanning, live cell imaging, high content screening, spatial omics.

**Hardware support:** 8+ camera vendors (FLIR, Hamamatsu, iDS, ToupTek, Tucsen, Photometrics, etc.), multiple stage and filter wheel types, full simulation mode for offline development.

## Architecture Overview

Squid uses a **3-layer architecture** that separates concerns and enables clean communication patterns:

```
     ui (Layer 2)        ← Pure PyQt5 widgets, no business logic
        │
        ▼ (events only)
    backend (Layer 1)    ← Hardware + orchestration
        │
        ▼ (implements ABCs)
     core (Layer 0)      ← Foundation: ABCs, events, config
```

### Layer 0: Core (`squid/core/`)

Foundation layer with no dependencies on other squid modules:

- **`abc.py`** - Hardware ABCs (`AbstractCamera`, `AbstractStage`, `LightSource`)
- **`events.py`** - `EventBus` and typed event dataclasses
- **`config/`** - Pydantic configuration models
- **`utils/`** - Thread-safe utilities (`ThreadSafeValue`, `safe_callback`)

### Layer 1: Backend (`squid/backend/`)

All hardware interaction and orchestration:

| Directory | Purpose | Examples |
|-----------|---------|----------|
| `drivers/` | Vendor-specific hardware implementations | Camera SDKs, stage controllers |
| `services/` | Thread-safe wrappers with `threading.RLock()` | `CameraService`, `StageService` |
| `controllers/` | State machines for workflows | `LiveController`, `AutoFocusController` |
| `managers/` | Stateful configuration managers | `ObjectiveStore`, `ChannelConfigurationManager` |
| `processing/` | Image processing and tracking algorithms | Autofocus metrics, stitching |
| `io/` | Frame streaming and file writers | `StreamHandler`, OME-ZARR writers |

### Layer 2: UI (`squid/ui/`)

Pure PyQt5 widgets organized by domain:

- Widgets communicate **exclusively** via EventBus events
- Publish **Commands** when users interact
- Subscribe to **State** events for display updates
- **No direct hardware access** - always go through services or publish commands

### Communication Patterns

**EventBus** (`core/events.py`) - Control plane for decoupled communication:
- Commands flow from UI → backend
- State events flow from backend → UI

**StreamHandler** (`backend/io/stream_handler.py`) - Data plane for 60fps camera frames, separate from EventBus to prevent frame floods.

### Threading Model

- **GUI Thread:** Qt event loop. Never block.
- **EventBus Thread:** Processes event queue. Handlers must return quickly.
- **Camera Thread:** SDK callbacks. Hand off to StreamHandler immediately.
- **Worker Threads:** Long operations via services with internal locks.

### Directory Structure

```
software/src/squid/
├── core/              # Layer 0: Foundation
│   ├── abc.py             # Hardware ABCs
│   ├── events.py          # EventBus + typed events
│   ├── config/            # Pydantic config models
│   └── utils/             # Thread-safe utilities
│
├── backend/           # Layer 1: Hardware + orchestration
│   ├── microscope.py      # Hardware orchestrator
│   ├── microcontroller.py # Teensy serial protocol
│   ├── drivers/           # Vendor implementations
│   ├── services/          # Thread-safe wrappers
│   ├── controllers/       # Workflow state machines
│   ├── managers/          # Stateful managers
│   ├── processing/        # Algorithms
│   └── io/                # Data I/O
│
├── ui/                # Layer 2: Frontend
│   ├── main_window.py     # Main PyQt5 window
│   ├── widgets/           # Pure UI by domain
│   └── ui_event_bus.py    # Thread-safe UI event wrapper
│
└── application.py     # DI container
```

## Running the Software

**With hardware:**
```bash
cd software
python main_hcs.py
```

**Simulation mode (no hardware needed):**
```bash
python main_hcs.py --simulation
```

## Testing

```bash
cd software
pytest tests/unit -v             # Fast unit tests
pytest tests/integration -v      # Simulated hardware tests
pytest -m "not slow" tests/      # Skip slow tests
```

## Configuration

Copy the `.ini` file associated with your microscope configuration to the software folder. Modify as needed (e.g. `camera_type`, `support_laser_autofocus`, `focus_camera_exposure_time_ms`).

Configuration files are located in `configurations/`.

## Setting up the Environment

Run the following script in terminal to clone the repo and set up the environment
```
wget https://raw.githubusercontent.com/Cephla-Lab/Squid/master/software/setup_22.04.sh
chmod +x setup_22.04.sh
./setup_22.04.sh
```

Reboot the computer to finish the installation.

## Optional and Hardware-Specific Dependencies

<details>
<summary>image stitching dependencies (optional)</summary>
For optional image stitching using ImageJ, additionally run the following:

```
sudo apt-get update
sudo apt-get install openjdk-11-jdk
sudo apt-get install maven
pip3 install pyimagej
pip3 instlal scyjava
pip3 install tifffile
pip3 install imagecodecs
```

Then, add the following line to the top of `/etc/environment` (needs to be edited with `sudo [your text editor]`):
```
JAVA_HOME=/usr/lib/jvm/default-java
```
Then, add the following lines to the top of `~/.bashrc` (or whichever file your terminal sources upon startup):
```
source /etc/environment
export JAVA_HOME = $JAVA_HOME
export PATH=$JAVA_HOME/bin:$PATH
```
</details>

<details>
<summary>Installing drivers and libraries for FLIR camera support</summary>
Go to FLIR's page for downloading their Spinnaker SDK (https://www.flir.com/support/products/spinnaker-sdk/) and register.

Open the `software/drivers and libraries/flir` folder in terminal and run the following
```
sh ./install_spinnaker.sh
sh ./install_PySpin.sh
```
</details>

<details>
<summary>Add udev rules for ToupTek cameras</summary>

```
sudo cp drivers\ and\ libraries/toupcam/linux/udev/99-toupcam.rules /etc/udev/rules.d
```
</details>

<details>
<summary>Installing drivers and libraries for Hamamatsu camera support</summary>

Open the `software/drivers and libraries/hamamatsu` folder in terminal and run the following
```
sh ./install_hamamatsu.sh
```
</details>

<details>
<summary>Installing drivers and libraries for iDS camera support</summary>

- Software:

Go to iDS's page for downloading their software (https://en.ids-imaging.com/download-details/1009877.html?os=linux&version=&bus=64&floatcalc=). Register and log in.

Open the `software/drivers and libraries/ids` folder in terminal and run the following
```
sh ./install_ids.sh
```

You will be asked to enter sudo password.

- Firmware (optional):

If you would like to update the firmware of the camera (optional), download the Vision firmware update (GUF file) on the same page.

Open the `software/drivers and libraries/ids/ids-peak_2.11.0.0-178_amd64/bin` folder in terminal and run the following
```
./ids_peak_cockpit
```

This will start the iDS peak Cockpit software. Then: 
1. Open the camera manager by clicking on the camera manager icon in the main menu.
2. Select the camera in the camera manager.
3. Click on the firmware update icon in the menu to open the dialog for selecting the update file for the Vision firmware (*.guf).
4. Select the update file.
5. Click on "Open".

The update is started and the camera is updated. Note: If you select an incorrect update file by mistake, you will see the message "The update file is incompatible".
After the update is complete, you can close the iDS peak Cockpit software. (Reference: https://en.ids-imaging.com/tl_files/downloads/usb3-vision/firmware/ReadMe.html)

</details>

<details>
<summary>Installing drivers and libraries for Tucsen camera support</summary>

Open the `software/drivers and libraries/tucsen` folder in terminal and run the following to log in as a root user
```
sudo -s
```

The following steps should be run as root user
```
sh ./install_tucsen.sh
```

After installation, run the following to log out
```
exit
```

</details>

<details>
<summary>Installing drivers and libraries for Kinetix camera support</summary>

Open the `software/drivers and libraries/photometrics` folder in terminal and run the following command
```
sh ./install_photometrics.sh
```
Follow the instructions during the installation.

</details>

<details>
<summary>Installing fluidics module</summary>

Link to the fluidics software repo: https://github.com/Alpaca233/fluidics_v2

In the Squid repo, run
```
git submodule init
git submodule update
```

</details>
