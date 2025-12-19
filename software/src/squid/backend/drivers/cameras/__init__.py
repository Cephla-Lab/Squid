# Camera drivers module
#
# Camera drivers:
#   - andor.py: Andor cameras
#   - flir.py: FLIR cameras
#   - hamamatsu.py: Hamamatsu cameras (uses dcam, dcamapi4)
#   - ids.py: IDS cameras
#   - photometrics.py: Photometrics cameras
#   - simulated.py: Simulated cameras (SimulatedFocusCamera, SimulatedMainCamera)
#   - tis.py: TIS cameras
#   - toupcam.py: ToupCam cameras (uses toupcam_sdk, toupcam_exceptions)
#   - tucsen.py: Tucsen cameras (uses tucam_sdk)
#
# Utilities:
#   - camera_utils.py: get_camera() factory function and camera_registry
#   - base.py: DefaultCamera base implementation
#   - cell_renderer.py: Cell field renderers for SimulatedMainCamera
#
# SDK/support files:
#   - dcam.py: DCAM wrapper for Hamamatsu
#   - dcamapi4.py: DCAM API bindings
#   - toupcam_sdk.py: ToupCam SDK bindings
#   - toupcam_exceptions.py: ToupCam exception helpers
#   - tucam_sdk.py: Tucsen TUCam SDK bindings
