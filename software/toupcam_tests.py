import squid.config
import squid.camera.utils
import time

camera_config = squid.config.get_camera_config()
camera = squid.camera.utils.get_camera(camera_config)

camera.set_gain_mode("HCG")

camera.set_resolution(2000, 2000)

camera.set_continuous_acquisition()

camera.start_streaming()

time.sleep(0.5)


camera.set_resolution(camera.res_list[1][0], camera.res_list[1][1])

time.sleep(0.5)

camera.set_pixel_format("MONO16")

time.sleep(0.5)

print(camera.get_awb_ratios())

time.sleep(0.5)

camera.set_ROI(10, 10, 32, 32)

time.sleep(0.5)

myframe = camera.read_frame()
print(myframe)
print(myframe.shape)
print(myframe.dtype)
camera.set_pixel_format("MONO8")
time.sleep(0.5)

myframe2 = camera.read_frame()
print(myframe2)
print(myframe2.shape)
print(myframe2.dtype)

time.sleep(1.0)


myframe2 = camera.read_frame()
print(myframe2)
print(myframe2.shape)
print(myframe2.dtype)


camera.set_ROI(0, 0, 0, 0)

time.sleep(0.5)

camera.set_ROI(2500, 2500, 3000, 3000)

time.sleep(1.0)

myframe2 = camera.read_frame()
print(myframe2)
print(myframe2.shape)
print(myframe2.dtype)


camera.close()
