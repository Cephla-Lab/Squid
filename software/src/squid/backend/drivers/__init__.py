# Hardware peripherals module
#
# Serial base:
#   - serial_base.py: SerialDevice, SerialDeviceError
#
# Lighting (squid.backend.drivers.lighting):
#   - led.py: IlluminationController, LightSourceType, IntensityControlMode, ShutterControlMode
#   - celesta.py: Celesta illumination
#   - xlight.py: XLight spinning disk
#   - dragonfly.py: Dragonfly confocal
#   - ldi.py: LDI illumination
#   - cellx.py: CellX illumination
#   - sci_led_array.py: SciMicroscopy LED array
#
# Stage/motion:
#   - xeryon.py: Xeryon stage controller
#   - piezo.py: PiezoStage
#   - objective_changer.py: Objective changer controller
#
# Illumination:
#   - illumination_andor.py: Andor illumination
#   - nl5.py: NL5 laser
#   - rcm.py: RCM API
#
# Other:
#   - fluidics.py: Fluidics control
#   - spectrometer_oceanoptics.py: Ocean Optics spectrometer

from squid.backend.drivers.peripherals.serial_base import SerialDevice, SerialDeviceError

__all__ = [
    "SerialDevice",
    "SerialDeviceError",
]
