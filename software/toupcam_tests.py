import squid.config
import squid.camera.utils

camera_config = squid.config.get_camera_config()
camera = squid.camera.utils.get_camera(camera_config)
