## Setting up the environments
Run the setup script for your Ubuntu release in a terminal to clone the repo and set up the environment.

Ubuntu 22.04:
```
wget https://raw.githubusercontent.com/Cephla-Lab/Squid/master/software/setup_22.04.sh
chmod +x setup_22.04.sh
./setup_22.04.sh
```

Ubuntu 24.04:
```
wget https://raw.githubusercontent.com/Cephla-Lab/Squid/master/software/setup_24.04.sh
chmod +x setup_24.04.sh
./setup_24.04.sh
```
The two scripts install the same packages; the 24.04 one only adds what Python 3.12 requires (pip's `--break-system-packages`, apt setuptools). If you pip-install extras by hand on 24.04, add `--break-system-packages` to the pip command.

Reboot the computer to finish the installation.

The scripts also create `machine_configs/illumination_channel_config.yaml` from its example if it is missing, and put a **Squid** shortcut on the Desktop.

### Windows
Windows 10 and 11. In PowerShell (no administrator rights needed):
```
Invoke-WebRequest https://raw.githubusercontent.com/Cephla-Lab/Squid/master/software/setup_windows.ps1 -OutFile setup_windows.ps1
powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1
```
The script:
- installs Git and Python 3.12 with `winget` if they are missing;
- clones the repo to `Desktop\Squid` (or uses the checkout it is run from);
- installs the same Python packages as the Ubuntu scripts, into Python 3.12;
- creates `machine_configs\illumination_channel_config.yaml` from its example if it is missing;
- puts a **Squid** shortcut on the Desktop.

The script is meant for a fresh computer. Squid uses Python 3.12; if another Python is already installed, it is left alone. If 3.12 is the newest Python on the machine, `py` runs it; otherwise use `py -3.12`. The script says which at the end.

For a Toupcam camera, run the script from an administrator PowerShell to also install the Toupcam USB driver, or run the `pnputil` command it prints. For other cameras, install the driver from the camera maker (the steps in the next section are for Linux only), then restart the computer.

## Optional or Hardware-specific dependencies
The camera driver and udev steps below are for Linux only. The fluidics module works on Windows too.

<details>
<summary>Installing drivers and libraries for FLIR camera support</summary>
Go to Teledyne's page for downloading Spinnaker SDK (https://www.teledynevisionsolutions.com/support/support-center/software-firmware-downloads/iis/spinnaker-sdk-download/spinnaker-sdk--download-files/) and register.

Open the `software/drivers and libraries/flir` folder in terminal and run the following (for Ubuntu 22.04)
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
<summary>Add udev rules for Thorlabs power meters (Illumination Power Calibration)</summary>

```
sudo cp drivers\ and\ libraries/thorlabs/linux/udev/99-thorlabs-pm16.rules /etc/udev/rules.d
```
Then unplug and re-plug the meter. Setup on macOS and Windows: [docs/illumination-power-calibration.md](docs/illumination-power-calibration.md).
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

Link to the fluidics software repo: https://github.com/Cephla-Lab/Squid-Fluidics (vendored as the `software/fluidics_v2` submodule).

The setup scripts leave it out. In the Squid repo, run
```
git submodule update --init software/fluidics_v2
cd software && pip3 install --no-deps -e fluidics_v2/software
```
(on Windows, `py -3.12 -m pip` in place of `pip3`). Then set `RUN_FLUIDICS = True` and `FLUIDICS_CONFIG_PATH` (the default is `machine_configs/fluidics_config.yaml`) under `[GENERAL]` in your `configuration*.ini`. Start the config from one of the samples in `fluidics_v2/software/sample_config/` (`flow_cell_config.yaml`, `open_chamber_config.yaml`).

</details>

## Configuring the software
Copy the `.ini` file for your microscope from `software/configurations/` into the `software` folder itself; Squid reads the `configuration*.ini` it finds there. Make modifications as needed (e.g. `camera_type`, `support_laser_autofocus`,`focus_camera_exposure_time_ms`)

## Using the software
Start Squid from the **Squid** shortcut on the Desktop, or from the `software` folder:
```
python3 main_hcs.py
```
To start the program when no hardware is connected, use
```
python3 main_hcs.py --simulation
```
On Windows, use `py -3.12` (or `py`, if the setup script said it runs Python 3.12) in place of `python3`.

### Appearance
**Settings > Appearance** offers two looks: **Classic** (the default) and **Dark**. Switching between them takes effect after a restart. The choice is saved in the machine `.ini` as `ui_theme` under `[GENERAL]` (`classic` or `dark`). Developers adding GUI code should read [docs/ui-theme.md](docs/ui-theme.md) for the colour rules every theme relies on.

## Updating an existing install
`filter_wheels.yaml`, `illumination_channel_config.yaml`, and
`Xeryon_settings.txt` were tracked in older revisions and are now
gitignored (PR #546). A `git pull` that crosses that commit will delete any
unmodified local copy. If yours were deleted, restore the last tracked
versions from git history (run from the repo root):

```bash
git restore --source=af71d2ef^ -- \
  software/control/Xeryon_settings.txt \
  software/machine_configs/filter_wheels.yaml \
  software/machine_configs/illumination_channel_config.yaml
```

The restored content is the last version that was in git, not your personal
calibration — verify it matches your hardware before running acquisitions.
Subsequent pulls leave the files alone.
