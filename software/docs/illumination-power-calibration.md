# Illumination Power Calibration

**Utils > Illumination Power Calibration...** measures each DAC-driven illumination channel with a Thorlabs power
meter at the sample plane and saves a calibration, so that a channel's intensity setting means % of its maximum power
instead of % of DAC output. The headless equivalent is `tools/generate_intensity_calibrations.py` (run it with the
Squid GUI closed; `--help` lists its options). Files are described in `machine_configs/README.md`.

## Power meter setup

The calibration talks to the meter over USB through pyvisa. The meter used first is the **PM16-121** (USB ID
`1313:807B`); other Thorlabs meters that speak the PM SCPI set (PM100D, PM100USB, PM400) are expected to work and are
reported as "model not validated" until one has been run on a bench.

Close Thorlabs' own software (Optical Power Monitor) before calibrating: it holds the meter.

### Ubuntu 22.04

`setup_22.04.sh` does this. By hand:

```
pip3 install pyvisa pyvisa-py pyusb
sudo cp "drivers and libraries/thorlabs/linux/udev/99-thorlabs-pm16.rules" /etc/udev/rules.d
```

Then unplug and re-plug the meter. The udev rule lets Squid open the meter without root. Another Thorlabs meter needs
its own product ID in the rule (`lsusb` shows it).

### macOS

```
brew install libusb
pip3 install pyvisa pyvisa-py pyusb
```

### Windows

1. Bind the WinUSB driver to the meter with [Zadig](https://zadig.akeo.ie/) (it needs administrator rights):
   Options > List All Devices, pick the meter (e.g. PM16-121, USB ID `1313 807B`), select WinUSB, Install Driver.
   Thorlabs' own software no longer sees the meter until its driver is put back.
2. `pip install pyvisa pyvisa-py pyusb libusb-package`
3. Put `libusb-1.0.dll` on PATH. pyusb looks for it only there, so installing libusb-package is not enough by itself:
   copy `libusb-1.0.dll` from `site-packages\libusb_package\` into a directory on PATH, such as the Python
   installation directory. In a conda environment, `conda install -c conda-forge libusb` does this instead.

Tested 2026-10-09 with a PM16-121 on Windows, Python 3.12 from python.org.

Alternatively, install NI-VISA and Thorlabs' driver (Optical Power Monitor; its Power Meter Driver Switcher selects
the NI-VISA driver) and `pip install pyvisa`: pyvisa then uses NI-VISA, and steps 1 and 3 are not needed.

### Check

```
python -c "import pyvisa; print(pyvisa.ResourceManager().list_resources())"
```

should list the meter: `USB0::4883::32891::<serial number>::0::INSTR` with pyvisa-py (decimal IDs: 4883 = 0x1313,
32891 = 0x807B for a PM16-121), `USB0::0x1313::0x807B::<serial number>::INSTR` with NI-VISA. Squid uses the first
resource with Thorlabs' vendor ID; `--resource` picks another in the headless tool.

## Running a calibration

1. Put the sensor at the sample plane with a low-magnification objective and the beam defocused over the sensor, so
   the power density stays within the sensor's rating. Follow your laser safety rules.
2. Open the dialog, **Connect** the meter (it reads the sensor's maximum power and wavelength range; the sensor limit
   can only be lowered), and use **Test beam** to centre the sensor.
3. Pick channels and **Run**. The light is pulsed for each reading; the dialog shows each channel's curve, its
   verification (every setpoint from 10 % within ±5 %) and any warnings.
4. **Save** writes the calibration and points the channel at it; it applies at once. A replaced calibration moves to
   `machine_configs/intensity_calibrations/backup/`.

Live view, laser-AF live view and acquisitions must be stopped first.

## When the meter is not found

| Message | Cause |
|---|---|
| pyvisa is not installed | Install the packages for your OS above |
| no VISA backend | pyvisa-py (or NI-VISA) is missing, or on Windows/macOS libusb is not found |
| No Thorlabs power meter found | Cable; on Linux the udev rule; on Windows the WinUSB driver (or NI-VISA's); Thorlabs' software holding the meter |
| could not open the power meter | On Linux the udev rule (re-plug after installing it); another program holding the meter |
