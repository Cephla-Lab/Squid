import squid.config
import control.peripherals.cameras.camera_utils

camera_config = squid.config.get_camera_config()
camera = control.peripherals.cameras.camera_utils.get_camera(camera_config)
