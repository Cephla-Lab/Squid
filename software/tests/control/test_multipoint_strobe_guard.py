"""The worker must report every hardware trigger to the LiveController so
set_microscope_mode() can wait out the strobe window on firmware < 1.5."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from control import utils
from control._def import TriggerMode
from control.core.multi_point_worker import MultiPointWorker


class _FakeCamera:
    def __init__(self):
        self.triggers = 0

    def get_exposure_time(self):
        return 2.0

    def get_strobe_time(self):
        return 1.0

    def get_total_frame_time(self):
        return 3.0

    def get_ready_for_trigger(self):
        return True

    def send_trigger(self, illumination_time=None):
        self.triggers += 1


def test_acquire_camera_image_notes_hw_trigger():
    w = MultiPointWorker.__new__(MultiPointWorker)
    w._log = MagicMock()
    w._timing = utils.TimingManager("test timing")
    w._ready_for_next_trigger = threading.Event()
    w._ready_for_next_trigger.set()
    w._backpressure = SimpleNamespace(should_throttle=lambda: False)
    w.liveController = SimpleNamespace(trigger_mode=TriggerMode.HARDWARE, note_hardware_trigger_sent=MagicMock())
    w.stage = SimpleNamespace(get_pos=lambda: "pos")
    w.use_piezo = False
    w.z_piezo_um = None
    w.time_point = 0
    w._current_capture_info = None
    w._select_config = lambda config: None
    w.camera = _FakeCamera()

    w.acquire_camera_image(
        SimpleNamespace(name="ch", id=0), file_ID="f", current_path="/tmp", k=0, region_id=0, fov=0, config_idx=0
    )

    assert w.camera.triggers == 1
    w.liveController.note_hardware_trigger_sent.assert_called_once()
