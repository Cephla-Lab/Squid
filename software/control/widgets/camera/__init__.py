# Camera widgets package
from control.widgets.camera.settings import CameraSettingsWidget
from control.widgets.camera.live_control import LiveControlWidget
from control.widgets.camera.recording import RecordingWidget, MultiCameraRecordingWidget

__all__ = [
    "CameraSettingsWidget",
    "LiveControlWidget",
    "RecordingWidget",
    "MultiCameraRecordingWidget",
]
